"""Bounded native-filesystem capability experiment. Not M03 qualification."""
import errno,hashlib,json,os,re,socket,subprocess,time
from pathlib import Path
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2') and os.getuid()==1000
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m03-storage-backend-01';assert not root.exists();root.mkdir(mode=0o700)
volume=root/'volume';volume.mkdir();image=root/'volume.img'
record=dict(host=host,status='running',started=time.time(),qualification=False,commands=[],results={},runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
loop=None;mounted=False

def save():(root/'review.json').write_text(json.dumps(record,indent=2)+'\n')
def run(command,expected=0):
    process=subprocess.run(command,capture_output=True,text=True,timeout=60)
    item=dict(argv=command,exit_code=process.returncode,stdout=process.stdout,stderr=process.stderr);record['commands'].append(item);save()
    if expected is not None:assert process.returncode==expected,item
    return process

def admin(*arguments):return run(['sudo','-n','btrfs',*map(str,arguments)])
def identify(path):
    output=run(['btrfs','subvolume','show',str(path)]).stdout
    return '0/'+re.search(r'Subvolume ID:\s+(\d+)',output).group(1)
def subvolume(name,parents,limit=None):
    path=volume/name;admin('subvolume','create',path);run(['sudo','-n','chown','1000:1000',str(path)])
    group=identify(path)
    for parent in parents:admin('qgroup','assign',group,parent,volume)
    if limit:admin('qgroup','limit',str(limit),group,volume)
    return path,group

def fill(path,maximum):
    fd=os.open(path,os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600);written=0;error=None;sync_error=None
    try:
        while written<maximum:
            try:
                size=os.write(fd,b'x'*min(1024*1024,maximum-written));assert size>0;written+=size
            except OSError as failure:error=failure.errno;break
        try:os.fsync(fd)
        except OSError as failure:sync_error=failure.errno
    finally:os.close(fd)
    result=dict(bytes_written=written,write_errno=error,fsync_errno=sync_error)
    save();return result

try:
    record['package_versions']=run(['dpkg-query','-W','-f=${Package} ${Version}\n','btrfs-progs','libreiserfscore0t64']).stdout
    record['root_mount_before']=run(['findmnt','-no','SOURCE,FSTYPE,OPTIONS','/']).stdout
    assert subprocess.check_output(['df','--output=avail','-B1',str(root)]).decode().splitlines()[-1].strip().isdigit()
    assert int(subprocess.check_output(['df','--output=avail','-B1',str(root)]).decode().splitlines()[-1])>2*2**30
    run(['fallocate','-l',str(512*2**20),str(image)])
    run(['mkfs.btrfs','-f','-d','single','-m','single','-L','spatial-m03-probe',str(image)])
    loop=run(['sudo','-n','losetup','--find','--show',str(image)]).stdout.strip();assert re.fullmatch(r'/dev/loop\d+',loop)
    record['loop']=loop;record['recovery']=[['sudo','-n','umount',str(volume)],['sudo','-n','losetup','-d',loop]];save()
    run(['sudo','-n','mount','-t','btrfs','-o','rw,nodev,nosuid,compress=no',loop,str(volume)]);mounted=True
    assert run(['findmnt','-no','SOURCE','--target',str(volume)]).stdout.strip()==loop
    run(['sudo','-n','chown','1000:1000',str(volume)])
    admin('quota','enable',volume)
    for group,limit in [('2/100',64*2**20),('1/200',32*2**20),('2/101',64*2**20)]:
        admin('qgroup','create',group,volume);admin('qgroup','limit',str(limit),group,volume)
    admin('qgroup','assign','1/200','2/100',volume)
    project,project_group=subvolume('project',['2/100'])
    repository,repository_group=subvolume('repository',['2/100'])
    application,application_group=subvolume('application',['1/200'],24*2**20)
    application2,application2_group=subvolume('application2',['1/200'],24*2**20)
    sibling,sibling_group=subvolume('sibling',['2/101'])
    admin('quota','rescan','-w',volume)
    # Observe whether ordinary user creation escapes the configured quota graph.
    escape=application/'unconfined-subvolume'
    created=run(['btrfs','subvolume','create',str(escape)],expected=None)
    record['results']['unprivileged_subvolume_creation']=dict(exit_code=created.returncode)
    if created.returncode==0:record['results']['unprivileged_subvolume_creation']['group']=identify(escape)
    admin('quota','rescan','-w',volume)
    record['results']['application_limit']=fill(application/'large',64*2**20)
    assert record['results']['application_limit']['write_errno']==errno.EDQUOT
    admin('filesystem','sync',volume)
    record['results']['actor_aggregate_limit']=fill(application2/'large',64*2**20)
    assert record['results']['actor_aggregate_limit']['write_errno']==errno.EDQUOT
    assert record['results']['actor_aggregate_limit']['bytes_written']<24*2**20
    record['results']['workspace_aggregate_limit']=fill(project/'large',128*2**20)
    assert record['results']['workspace_aggregate_limit']['write_errno']==errno.EDQUOT
    record['results']['independent_workspace']=fill(sibling/'independent',4*2**20)
    assert record['results']['independent_workspace']['bytes_written']==4*2**20
    if created.returncode==0:
        record['results']['unconfined_quota_bypass']=fill(escape/'escape',4*2**20)
    project.joinpath('large').unlink();admin('filesystem','sync',volume)
    record['results']['capacity_recovered']=fill(project/'after-delete',2**20)
    assert record['results']['capacity_recovered']['bytes_written']==2**20
    record['qgroups_before_remount']=admin('qgroup','show','--sync','--raw','-p','-r',volume).stdout
    # Read-only remount confirms durable quota configuration without granting application control.
    run(['sudo','-n','umount',str(volume)]);mounted=False
    run(['sudo','-n','mount','-t','btrfs','-o','rw,nodev,nosuid,compress=no',loop,str(volume)]);mounted=True
    record['qgroups_after_remount']=admin('qgroup','show','--sync','--raw','-p','-r',volume).stdout
    record['root_mount_after']=run(['findmnt','-no','SOURCE,FSTYPE,OPTIONS','/']).stdout
    assert record['root_mount_after']==record['root_mount_before']
    record['status']='capability_checks_complete'
except BaseException as error:record.update(status='failed',error=repr(error));raise
finally:
    if mounted:
        assert run(['findmnt','-no','SOURCE','--target',str(volume)]).stdout.strip()==loop
        run(['sudo','-n','umount',str(volume)]);mounted=False
    if loop:run(['sudo','-n','losetup','-d',loop])
    record.update(ended=time.time(),unmounted=not mounted,image_bytes=image.stat().st_size if image.exists() else 0)
    record['mount_after_cleanup']=run(['findmnt','-no','SOURCE,FSTYPE','--target',str(volume)]).stdout
    record['loop_after_cleanup']=run(['losetup','-j',str(image)]).stdout
    assert record['loop_after_cleanup']==''
    save();print(json.dumps(dict(host=host,status=record['status'],results=record['results'],path=str(root/'review.json'))),flush=True)
