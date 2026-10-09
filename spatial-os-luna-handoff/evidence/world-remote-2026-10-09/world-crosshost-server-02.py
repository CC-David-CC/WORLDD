"""Test supervisor: real sb16 authority with an independently connected sb16-2 actor."""
import hashlib,json,os,pathlib,socket,ssl,sys,tarfile,time
from unittest.mock import patch
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-world-remote-source-02';build=root/'local/m01-world-remote-debug-02/build'
sys.path[:0]=[str(build/'python'),str(source),str(source/'tests/core')]
sys.argv=[sys.argv[0],*[str(build/n) for n in ('identityd','spatial-issuer-client','worldd')]]
import core_pb2 as core
import grants_pb2 as grant
import world_session_pb2 as session
from test_world_bootstrap import BootstrapWorldFixture
from test_world_remote import RemoteWorldProcess,TlsActorPeer

out=root/'local/m01-world-crosshost-server-02';out.mkdir();private=out/'private';private.mkdir(mode=0o700)
address='10.0.7.49'
class LanSocket(socket.socket):
    def bind(self,where):return super().bind((address,where[1]) if where==('127.0.0.1',0) else where)
fixture=BootstrapWorldFixture('runTest')
try:
    fixture.setUp();local=fixture.scoped_world();owner=local.peers[0]
    remote=fixture.enroll_bootstrap(name='crosshost-actor',profile='empty-v1')
    delegated=fixture.delegate(owner,fixture.root_grant(owner),fixture.terms(remote[3],
        rights=(grant.WORLD_ENTER,grant.WORLD_NAVIGATE,grant.WORLD_OBSERVE),depth=0))
    assert delegated.HasField('grant'),delegated
    local.close()
    with patch('test_world_remote.socket.socket',LanSocket):world=RemoteWorldProcess(fixture,local)
    fixture.worlds.append(world);world.management('system.hello')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.minimum_version=context.maximum_version=ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=str(world.server_cert));context.load_cert_chain(str(fixture.service_cert),str(fixture.service_key))
    peer=TlsActorPeer(context.wrap_socket(socket.create_connection((address,world.port),timeout=5),server_hostname='world.test'))
    world.peers.append(peer);opened=fixture.authenticate(peer);assert opened.HasField('opened'),opened
    local_actor=opened.opened.actor_id
    assert remote[3].actor_id!=fixture.identity.actor_id
    for name,path in [('server.crt',world.server_cert),('client.crt',fixture.service_cert),
            ('client.key',fixture.service_key),('actor.key',remote[1])]:
        target=private/name;target.write_bytes(path.read_bytes());target.chmod(0o600)
    (private/'credential.pb').write_bytes(remote[0].credential.SerializeToString());(private/'credential.pb').chmod(0o600)
    config={'address':address,'port':world.port,'server_name':'world.test','world_id':fixture.world_info['world_id'],
        'actor_public':remote[2].hex(),'actor_id':remote[3].actor_id.hex(),'local_actor':local_actor}
    (private/'fixture.json').write_text(json.dumps(config)+'\n');(private/'fixture.json').chmod(0o600)
    bundle=private/'bundle.tar'
    with tarfile.open(bundle,'w') as archive:
        for name in ('server.crt','client.crt','client.key','actor.key','credential.pb','fixture.json'):archive.add(private/name,arcname=name)
    bundle.chmod(0o600)
    ready={'host':'sb16','world_id':config['world_id'],'address':address,'port':world.port,
        'bundle':str(bundle),'worldd_sha256':hashlib.sha256((build/'worldd').read_bytes()).hexdigest()}
    (out/'ready.json').write_text(json.dumps(ready,indent=2)+'\n');print('Cross-host world ready on sb16',flush=True)
    # The controller transfers client-result.json.tmp, then atomically renames it.
    deadline=time.monotonic()+600
    while not (out/'client-result.json').exists():
        assert world.proc.poll() is None,'World exited while waiting for second host'
        assert time.monotonic()<deadline,'Cross-host client result deadline expired'
        time.sleep(.1)
    result=json.loads((out/'client-result.json').read_text())
    assert result['host']=='sb16-2' and result['status']=='passed' and result['world_id']==config['world_id']
    assert result['actor_id']!=local_actor and result['identity_actor_id']==remote[3].actor_id.hex()
    entity=world.management('world.get',id=result['actor_id'])
    actual=[entity['pose']['p'][i] for i in range(3)]
    assert actual==result['final_position'],(actual,result)
    assert result['client_endpoint'][0]!=result['server_endpoint'][0]
    assert peer.call(inspect=session.WorldSessionInspect()).state.id==local_actor
    assert fixture.navigation(peer).HasField('action');peer.act(stop=core.ActorStopRequest())
    world.management('world.checkpoint')
    report={'status':'passed','authority_host':'sb16','remote_actor_host':'sb16-2','world_id':config['world_id'],
        'distinct_actor_identities':True,'same_authoritative_actor_state':True,'independent_local_actor_usable':True,
        'checkpoint_completed':True,'remote_application_streaming':False,'worldd_sha256':ready['worldd_sha256'],
        'limits':'One cross-host transport scenario; not complete shared-world or milestone qualification.'}
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
except BaseException as error:
    (out/'failure.txt').write_text(type(error).__name__+': '+str(error)+'\n');raise
finally:
    fixture.doCleanups()
