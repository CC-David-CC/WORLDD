import os,pathlib,select,signal,socket,subprocess,time,json
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
r,w=os.pipe2(os.O_CLOEXEC)
command=['/usr/local/libexec/spatial-bwrap','--unshare-all','--unshare-user','--disable-userns','--die-with-parent','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--block-fd',str(r),'--','/usr/bin/sleep','30']
p=subprocess.Popen(command,pass_fds=(r,),stdout=subprocess.PIPE,stderr=subprocess.PIPE);os.close(r);pidfd=None;result={}
try:
    deadline=time.monotonic()+3
    while True:
        children=pathlib.Path(f'/proc/{p.pid}/task/{p.pid}/children').read_text().split()
        if children:break
        assert p.poll() is None and time.monotonic()<deadline,(p.returncode,p.stderr.read() if p.poll() is not None else 'deadline')
        time.sleep(.005)
    assert len(children)==1,children
    child=int(children[0]);pidfd=os.pidfd_open(child)
    status=dict(line.split(':',1) for line in pathlib.Path(f'/proc/{child}/status').read_text().splitlines())
    assert int(status['PPid'])==p.pid and [int(n) for n in status['NSpid'].split()][-1]==1
    result=dict(host=socket.gethostname(),parent=p.pid,namespace_init=child,NSpid=status['NSpid'].split(),before_exit_ready=bool(select.select([pidfd],[],[],0)[0]))
    os.write(w,b'1');os.close(w);w=-1;time.sleep(.05)
    signal.pidfd_send_signal(pidfd,signal.SIGKILL);p.wait(timeout=3)
    result['after_exit_ready']=bool(select.select([pidfd],[],[],1)[0]);assert result['after_exit_ready']
    print(json.dumps(result),flush=True)
finally:
    if pidfd is not None:
        try:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
        except ProcessLookupError:pass
        os.close(pidfd)
    if p.poll() is None:p.kill();p.wait(timeout=3)
    if w>=0:os.close(w)
