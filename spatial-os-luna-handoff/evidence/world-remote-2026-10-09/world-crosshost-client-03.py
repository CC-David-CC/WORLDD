"""Independent sb16 TLS actor. Every world request uses the production protobuf interface."""
import hashlib,json,pathlib,socket,ssl,struct,subprocess,sys,tarfile,time
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-world-remote-source-02';build=root/'local/m01-world-remote-debug-02/build'
sys.path[:0]=[str(build/'python'),str(source)]
import core_pb2 as core
import issuer_pb2 as issuer
import key_pb2 as keys
import world_session_pb2 as session
from harness.wire import frame
out=root/'local/m01-world-crosshost-client-03';out.mkdir();private=out/'private';private.mkdir(mode=0o700)
with tarfile.open(root/'local/world-crosshost-bundle-03.tar') as archive:
    members=archive.getmembers();assert {m.name for m in members}=={'server.crt','client.crt','client.key','actor.key','credential.pb','fixture.json'}
    for member in members:
        assert member.isfile() and member.size<65536
        target=private/member.name;target.write_bytes(archive.extractfile(member).read());target.chmod(0o600)
config=json.loads((private/'fixture.json').read_text())
assert config['address']=='10.0.7.94' and config['server_name']=='world.test'
context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.minimum_version=context.maximum_version=ssl.TLSVersion.TLSv1_3
context.load_verify_locations(cafile=str(private/'server.crt'));context.load_cert_chain(str(private/'client.crt'),str(private/'client.key'))
stream=context.wrap_socket(socket.create_connection((config['address'],config['port']),timeout=5),server_hostname=config['server_name'])
sequence=0
def request(**operation):
    global sequence
    sequence+=1;stream.sendall(frame(session.WorldSessionRequest(protocol_version=1,request_id=sequence,**operation)))
    def exact(n):
        data=b''
        while len(data)<n:
            part=stream.recv(n-len(data));assert part,'World closed remote actor channel';data+=part
        return data
    size=struct.unpack('!I',exact(4))[0];assert size<=1048576
    reply=session.WorldSessionReply.FromString(exact(size))
    assert reply.protocol_version==1 and reply.request_id==sequence,reply
    return reply
try:
    denied=request(inspect=session.WorldSessionInspect());assert denied.HasField('error') and denied.error.code==core.UNAUTHORIZED
    credential=issuer.SignedCredential.FromString((private/'credential.pb').read_bytes())
    begin=request(begin=session.WorldAuthenticationBegin(credential=credential));assert begin.HasField('challenge'),begin
    challenge=begin.challenge;content=challenge.signing_request
    transcript=b'spatial.identity.key-proof/v1\0'+struct.pack('!II',content.purpose,len(content.payload))+content.payload
    (private/'transcript').write_bytes(transcript)
    signed=subprocess.run(['openssl','pkeyutl','-sign','-rawin','-inkey',str(private/'actor.key'),'-in',str(private/'transcript')],capture_output=True,timeout=5)
    assert signed.returncode==0 and len(signed.stdout)==64,signed.stderr
    proof=keys.KeyProof(purpose=content.purpose,public_key=bytes.fromhex(config['actor_public']),signature=signed.stdout)
    opened=request(complete=issuer.AuthenticationComplete(nonce=challenge.nonce,proof=proof));assert opened.HasField('opened'),opened
    assert opened.opened.world_id==config['world_id'] and opened.opened.identity.actor_id.hex()==config['actor_id']
    inspected=request(inspect=session.WorldSessionInspect());assert inspected.HasField('state'),inspected
    before=inspected.state;assert not before.workspace
    action=request(action=core.ActorActionRequest(seq=1,input=core.ActorInputRequest(axes=[1,0,0,0,0,0])));assert action.HasField('action'),action
    time.sleep(.12)
    action=request(action=core.ActorActionRequest(seq=2,stop=core.ActorStopRequest()));assert action.HasField('action'),action
    inspected=request(inspect=session.WorldSessionInspect());assert inspected.HasField('state'),inspected
    after=inspected.state;assert after.pose.position.x>before.pose.position.x and not any(after.axes) and not after.workspace
    report={'host':'sb16','status':'passed','world_id':opened.opened.world_id,'actor_id':opened.opened.actor_id,
        'identity_actor_id':opened.opened.identity.actor_id.hex(),'client_endpoint':stream.getsockname(),'server_endpoint':stream.getpeername(),
        'tls_version':stream.version(),'cipher':stream.cipher()[0],'transport_only_identity_denied':True,
        'final_position':[after.pose.position.x,after.pose.position.y,after.pose.position.z],
        'world_session_schema_sha256':hashlib.sha256((source/'proto/spatial/identity/v1/world_session.proto').read_bytes()).hexdigest()}
    closed=request(close=session.WorldSessionClose());assert closed.HasField('closed'),closed
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
finally:stream.close()
