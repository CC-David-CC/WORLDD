import argparse,hashlib,json,os,pwd,signal,socket,struct,subprocess,sys,time,traceback
from pathlib import Path

def receive(ch):
    prefix=bytearray()
    while len(prefix)<4:
        part=ch.recv(4-len(prefix))
        if not part and not prefix:return None
        if not part:raise RuntimeError('truncated frame prefix')
        prefix.extend(part)
    size=struct.unpack('!I',prefix)[0]
    if not 0<size<=1024*1024:raise RuntimeError('invalid frame size')
    payload=bytearray()
    while len(payload)<size:
        part=ch.recv(size-len(payload))
        if not part:raise RuntimeError('truncated frame payload')
        payload.extend(part)
    return bytes(payload)

def send(ch,payload):ch.sendall(struct.pack('!I',len(payload))+payload)

def main():
    p=argparse.ArgumentParser()
    for name,kind in (('--channel-fd',int),('--build',Path),('--config',Path),('--audit',Path)):
        p.add_argument(name,type=kind,required=True)
    p.add_argument('--mode',choices=('delay-copy-replies','hold-copy-seal','pass-through','drop-publication-ack'),required=True);a=p.parse_args()
    if os.getuid()!=0 or socket.gethostname().split('.')[0] not in ('sb16','sb16-2'):raise RuntimeError('approved root test host required')
    sys.path.insert(0,str(a.build/'python'));import storage_pb2 as storage
    operator=pwd.getpwnam('dflanag3');client=socket.socket(fileno=a.channel_fd);client.settimeout(190)
    relay,broker=socket.socketpair();upstream,owner_channel=socket.socketpair()
    for ch in (relay,broker,upstream,owner_channel):ch.settimeout(190)
    upstream.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1);owner_channel.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    child=os.fork()
    if child==0:
        client.close();relay.close();owner_channel.close()
        try:
            os.setgroups([]);os.setgid(operator.pw_gid);os.setuid(operator.pw_uid)
            while True:
                data=receive(broker)
                if data is None:break
                send(upstream,data);reply=receive(upstream)
                if reply is None:raise RuntimeError('owner closed before reply')
                send(broker,reply)
            # The owner pins this sender PID. Keep it alive until the owner
            # consumes EOF and closes its channel, avoiding dead-peer races.
            upstream.shutdown(socket.SHUT_WR)
            if receive(upstream) is not None:raise RuntimeError('unexpected owner frame after clean EOF')
            os._exit(0)
        except BaseException:traceback.print_exc();sys.stderr.flush();os._exit(2)
    broker.close();upstream.close();owner=None;waited=False
    stem=a.audit.with_suffix('');held=stem.with_suffix('.seal-held');release=stem.with_suffix('.seal-release')
    methods={'snapshot_prepare','copy_destination_prepare','copy_stage_begin','copy_stage_start','copy_stage_seal'}
    audit={'mode':a.mode,'requests':[],'copy_responses':[],'mutations':[],'client_eof':False,
        'peer_uid':os.getuid(),'peer_pid':os.getpid(),'broker_uid':operator.pw_uid,'broker_pid':child,
        'upstream_passcred':True,'owner_channel_passcred':True}
    try:
        cfg=storage.StorageOwnerConfig.FromString(a.config.read_bytes());cfg.broker_uid=operator.pw_uid;cfg.broker_pid=child
        a.config.write_bytes(cfg.SerializeToString(deterministic=True));a.config.chmod(0o600)
        owner=subprocess.Popen([str(a.build/'spatial-storage-owner'),'--channel-fd',str(owner_channel.fileno()),'--config',str(a.config)],
            pass_fds=[owner_channel.fileno()],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        audit['owner_pid']=owner.pid;owner_channel.close()
        while True:
            data=receive(client)
            if data is None:audit['client_eof']=True;break
            req=storage.StorageOwnerRequest.FromString(data);method=req.WhichOneof('operation');audit['requests'].append(method)
            exchange_start=time.monotonic()
            send(relay,data);wire_reply=receive(relay);owner_reply_at=time.monotonic()
            if wire_reply is None:raise RuntimeError('broker closed before owner reply')
            reply=storage.StorageOwnerReply.FromString(wire_reply)
            if a.mode=='drop-publication-ack' and method=='copy_destination_publish':
                if not reply.HasField('copy_destination') or reply.copy_destination.state!=storage.StorageCopyDestinationReceipt.PUBLISHED:
                    raise RuntimeError('fault requires an actual successful PUBLISHED receipt')
                witness=a.audit.with_suffix('.publication-held')
                release_publication=a.audit.with_suffix('.publication-release')
                record=dict(method=method,request_sha256=hashlib.sha256(data).hexdigest(),
                    reply_sha256=hashlib.sha256(wire_reply).hexdigest(),
                    owner_reply_monotonic_ns=time.monotonic_ns(),state=reply.copy_destination.state,
                    request_hex=data.hex(),reply_hex=wire_reply.hex())
                pending=witness.with_name(witness.name+'.pending')
                pending.write_text(json.dumps(record,sort_keys=True)+'\n')
                os.replace(pending,witness)
                audit['dropped_publication']=record
                # The fixture kills its exact broker before this release.
                until=time.monotonic()+4
                while not release_publication.exists() and time.monotonic()<until:time.sleep(.005)
                if not release_publication.exists():raise TimeoutError('Publication fault release was not witnessed')
                record['release_monotonic_ns']=time.monotonic_ns()
                record['elapsed_seconds']=time.monotonic()-exchange_start
                if record['elapsed_seconds']>=5:raise TimeoutError('Publication fault exceeded owner exchange budget')
                record['forwarded']=False
                continue
            if method in methods:
                record={'method':method,'request_sha256':hashlib.sha256(data).hexdigest(),
                    'reply_sha256':hashlib.sha256(wire_reply).hexdigest(),'start_monotonic':exchange_start,
                    'owner_reply_monotonic':owner_reply_at}
                if a.mode=='delay-copy-replies':
                    until=time.monotonic()+4.1
                    while time.monotonic()<until:time.sleep(min(.02,max(0,until-time.monotonic())))
                elif a.mode=='hold-copy-seal' and method=='copy_stage_seal':
                    if not reply.HasField('copy_stage') or held.exists():raise RuntimeError('invalid or duplicate SEAL receipt')
                    record['held_receipt_sha256']=hashlib.sha256(wire_reply).hexdigest()
                    held.write_text(json.dumps(record,sort_keys=True)+'\n')
                    until=time.monotonic()+40
                    while not release.exists() and time.monotonic()<until:time.sleep(.02)
                    if not release.exists():raise TimeoutError('SEAL release was not witnessed')
                record['end_monotonic']=time.monotonic();record['elapsed_seconds']=record['end_monotonic']-record['start_monotonic']
                record['forwarded_reply_sha256']=hashlib.sha256(wire_reply).hexdigest()
                audit['copy_responses'].append(record);send(client,wire_reply);continue
            if reply.HasField('identity'):
                if reply.identity.process_id!=owner.pid:raise RuntimeError('unexpected owner identity')
                reply.identity.process_id=os.getpid();send(client,reply.SerializeToString(deterministic=True))
            else:send(client,wire_reply)
        relay.close();_,status=os.waitpid(child,0);waited=True
        audit['broker_exit']=os.waitstatus_to_exitcode(status);audit['owner_stderr']=owner.communicate(timeout=10)[1];audit['owner_exit']=owner.returncode
        audit['forwarder_shutdown']='owner-eof-before-forwarder-exit'
        if audit['broker_exit'] or owner.returncode:raise RuntimeError('relay or owner failed')
    except BaseException as error:audit['relay_failure']=repr(error);raise
    finally:
        errors=[]
        for ch in (client,relay,owner_channel):
            try:ch.close()
            except BaseException as error:errors.append(repr(error))
        if not waited:
            try:
                found,status=os.waitpid(child,os.WNOHANG)
                if found==0:os.kill(child,signal.SIGKILL);_,status=os.waitpid(child,0);audit['broker_cleanup_killed']=True
                audit['broker_exit']=os.waitstatus_to_exitcode(status)
            except BaseException as error:errors.append('forwarder: '+repr(error))
        if owner:
            try:
                if owner.poll() is None:owner.kill();audit['owner_cleanup_killed']=True
                audit['owner_stderr']=owner.communicate(timeout=10)[1];audit['owner_exit']=owner.returncode
            except BaseException as error:errors.append('owner: '+repr(error))
        audit['cleanup_errors']=errors;a.audit.write_text(json.dumps(audit,sort_keys=True,indent=2)+'\n')
    if errors:raise RuntimeError('relay cleanup failed: '+repr(errors))

if __name__=='__main__':main()
