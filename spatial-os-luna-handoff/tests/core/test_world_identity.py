"""Real issuer proofs enter worldd and use its native actor implementation."""
import fcntl
import os
import hashlib
import io
import json
import grants_pb2 as grants
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as pb
import world_session_pb2 as session
from harness.wire import frame, make_request, result_value, read_message
from harness.oracles import pose
from harness.probe import Probe
from test_issuer_client import ClientFixture

WORLD = Path(sys.argv.pop(1))


def receive(read, ready, message):
    deadline = time.monotonic()+5
    def exact(size):
        data = b''
        while len(data) < size:
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([ready], [], [], remaining)[0]:
                raise AssertionError('World response deadline expired')
            try:
                part = read(size-len(data))
            except (BlockingIOError, InterruptedError):
                continue
            if not part: raise EOFError('worldd closed the channel')
            data += part
        return data
    size = struct.unpack('!I',exact(4))[0]
    if size > 1048576: raise AssertionError('Excessive world response')
    return message.FromString(exact(size))


class SessionPeer:
    def __init__(self, stream): self.stream=stream; self.sequence=0; self.action_sequence=0
    def close(self): self.stream.close()
    def request(self, request):
        self.sequence+=1; request.request_id=self.sequence
        self.stream.sendall(frame(request))
        reply=receive(self.stream.recv,self.stream,session.WorldSessionReply)
        if reply.protocol_version!=1 or reply.request_id!=self.sequence: raise AssertionError('Wrong session reply identity')
        return reply
    def call(self, **operation): return self.request(session.WorldSessionRequest(protocol_version=1,**operation))
    def act(self, **action):
        self.action_sequence+=1
        return self.call(action=core.ActorActionRequest(seq=self.action_sequence,**action))


class WorldProcess:
    def __init__(self, fixture, config, state, native=False, count=2, runtime_config=None, broker=None, fixture_faults=False, input_witness_fd=None, presentation=None):
        self.log=(fixture.root/f'world-{len(fixture.worlds)}.log').open('wb'); self.sequence=0
        pairs=[socket.socketpair() for _ in range(count)]
        self.peers=[SessionPeer(pair[0]) for pair in pairs]
        command=[str(WORLD),'--state-dir',str(state),'--identity-config',str(config)]
        if runtime_config: command+=['--config',str(runtime_config)]
        if fixture_faults: command+=['--fixture-faults']
        if native: command+=['--display',os.environ['SPATIAL_TEST_DISPLAY']]
        workspace_channel = None; workspace_channel_owned = False; self.broker_channel = None
        self.broker_workspace_fd = None
        if broker:
            workspace_channel, self.broker_channel, workspace_channel_owned = self.workspace_channel(broker)
            self.broker_workspace_fd = workspace_channel.fileno()
            command += ['--workspace-fd', str(workspace_channel.fileno()), '--workspace-pid',
                        str(getattr(broker, 'pid', broker.proc.pid))]
        for _,server in pairs: command+=['--actor-fd',str(server.fileno())]
        extra_fds = (tuple([workspace_channel.fileno()]) if workspace_channel else ())
        if input_witness_fd is not None:
            command += ['--input-witness-fd',str(input_witness_fd)]
            extra_fds += (input_witness_fd,)
        if presentation is not None:
            command += presentation.world_arguments()
            extra_fds += presentation.world_descriptors()
        try:
            self.proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,
                pass_fds=tuple(server.fileno() for _,server in pairs)+extra_fds)
            self._initialize_process_lifecycle()
            if presentation is not None:presentation.attach(self)
        except BaseException:
            if presentation is not None and hasattr(self,'start_ticks'):self.close()
            for peer in self.peers:peer.close()
            for _,server in pairs:server.close()
            if workspace_channel_owned: workspace_channel.close()
            if self.broker_channel: self.broker_channel.close(); self.broker_channel = None
            raise
        if workspace_channel_owned: workspace_channel.close()
        for _,server in pairs: server.close()
    @staticmethod
    def process_start_ticks(pid):
        try:
            stat=Path(f'/proc/{pid}/stat').read_text()
            fields=stat[stat.rfind(')')+2:].split()
            return int(fields[19])
        except (OSError,ValueError,IndexError):
            return None

    def _initialize_process_lifecycle(self):
        self.start_ticks = self.process_start_ticks(self.proc.pid)
        self.lifecycle_evidence = None
        self.lifecycle_before_close = None

    def lifecycle_snapshot(self):
        result={'pid':self.proc.pid,'start_ticks':self.start_ticks,'poll':self.proc.poll()}
        result['returncode']=self.proc.returncode
        return result

    def record_final_lifecycle(self):
        if self.log.closed:return
        if self.lifecycle_evidence is None or not self.lifecycle_evidence.exists():return
        try:
            self.log.flush()
            log_path=Path(self.log.name)
            digest=hashlib.sha256()
            lifecycle_tail=[]
            with log_path.open('rb') as stream:
                for line in stream:
                    digest.update(line)
                    if b'worldd: actor_lifecycle ' in line:
                        lifecycle_tail.append(line.decode('utf-8','replace').rstrip('\r\n')[:512])
                        if len(lifecycle_tail)>24:lifecycle_tail.pop(0)
            evidence=json.loads(self.lifecycle_evidence.read_text())
            lifecycle=evidence.setdefault('worldd_lifecycle',{})
            if self.lifecycle_before_close is not None:
                lifecycle['before_world_close']=self.lifecycle_before_close
            lifecycle['after_world_close']=self.lifecycle_snapshot()
            lifecycle['stderr_sha256']=digest.hexdigest()
            lifecycle['stderr_lifecycle_tail']=lifecycle_tail
            self.lifecycle_evidence.write_text(json.dumps(evidence,sort_keys=True,indent=2)+'\n')
        except Exception as error:
            print('World process lifecycle diagnostic write failed: '+type(error).__name__,file=sys.stderr)

    @staticmethod
    def workspace_channel(broker):
        factory = getattr(broker, 'world_channel_factory', None)
        if getattr(broker, 'world_channel_opt_in', False) and callable(factory):
            stream, channel = factory()
            return stream, channel, True
        return getattr(broker, 'stream', None), None, False

    def capture_management_eof(self, method):
        evidence_root=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        process=getattr(self,'proc',None)
        if not evidence_root or process is None:return
        target=Path(evidence_root)/('worldd-startup-'+str(os.getpid())+'-'+method.replace('.','-'))
        target.mkdir(parents=True,exist_ok=True)
        def fd_state(fd):
            item={'fd':fd}
            try:item['flags']=fcntl.fcntl(fd,fcntl.F_GETFD)
            except (OSError,ValueError) as error:item['fcntl_error']=str(error)
            try:
                info=os.fstat(fd);item['mode']=info.st_mode;item['device']=info.st_dev;item['inode']=info.st_ino
            except (OSError,ValueError) as error:item['fstat_error']=str(error)
            try:item['link']=os.readlink('/proc/self/fd/'+str(fd))
            except OSError as error:item['link_error']=str(error)
            return item
        try:poll_result=process.poll()
        except Exception as error:poll_result='poll-error:'+type(error).__name__
        result={'diagnostic':'test-only-worldd-management-eof','method':method,
            'parent_pid':os.getpid(),'worldd_argv':getattr(process,'args',None),
            'worldd_pid':getattr(process,'pid',None),'worldd_poll':poll_result,
            'worldd_returncode':getattr(process,'returncode',None),
            'worldd_proc_stat':None,'worldd_proc_cmdline':None,'worldd_proc_exe':None,
            'worldd_fds':[],'management_fds':{},'actor_sockets':[],'copied_logs':[]}
        pid=result['worldd_pid']
        if isinstance(pid,int):
            proc_root=Path('/proc')/str(pid)
            for key,path in (('worldd_proc_stat',proc_root/'stat'),('worldd_proc_cmdline',proc_root/'cmdline'),
                             ('worldd_proc_exe',proc_root/'exe')):
                try:
                    if key=='worldd_proc_exe':result[key]=os.readlink(path)
                    elif key=='worldd_proc_cmdline':result[key]=path.read_bytes().replace(b'\0',b' ').decode('utf-8','replace')
                    else:result[key]=path.read_text(errors='replace')
                except OSError as error:result[key+'_error']=str(error)
            try:
                fd_entries=[]
                for item in (proc_root/'fd').iterdir():
                    fd_entries.append(item)
                    if len(fd_entries)>128:break
                result['worldd_fds']=[{'fd':item.name,'target':os.readlink(item)} for item in fd_entries[:128]]
                result['worldd_fds_truncated']=len(fd_entries)>128
            except OSError as error:result['worldd_fds_error']=str(error)
        for label in ('stdin','stdout'):
            stream=getattr(process,label,None)
            if stream is None:
                result['management_fds'][label]={'unavailable':True}
                continue
            try:result['management_fds'][label]=fd_state(stream.fileno())
            except Exception as error:result['management_fds'][label]={'state_error':type(error).__name__}
        for index,peer in enumerate(getattr(self,'peers',()) or ()):
            item={'index':index}
            sock=getattr(peer,'stream',None)
            if sock is None:
                item['unavailable']=True
                result['actor_sockets'].append(item)
                continue
            try:item['fd']=sock.fileno()
            except Exception as error:item['fd_error']=type(error).__name__
            try:item['flags']=fcntl.fcntl(sock.fileno(),fcntl.F_GETFD)
            except Exception as error:item['fcntl_error']=type(error).__name__
            try:item['local']=sock.getsockname();item['peer']=sock.getpeername()
            except Exception as error:item['address_error']=type(error).__name__
            try:item['so_error']=sock.getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)
            except Exception as error:item['socket_error']=type(error).__name__
            result['actor_sockets'].append(item)
        paths=set()
        log=getattr(self,'log',None)
        if log is not None:
            try:log.flush()
            except Exception:pass
            try:paths.update(Path(log.name).parent.glob('*.log'))
            except Exception as error:result['fixture_log_root_error']=type(error).__name__
        scope=os.environ.get('SPATIAL_SCOPE_EVIDENCE')
        if scope:
            try:paths.update(Path(scope).glob('adapter-provisioning-*.json'))
            except OSError as error:result['adapter_evidence_error']=type(error).__name__
        paths=sorted((path for path in paths if path.is_file()),key=str)
        result['log_files_found']=len(paths)
        result['log_files_truncated']=len(paths)>48
        total_bytes=0
        for index,path in enumerate(paths[:48]):
            try:
                size=path.stat().st_size
                if size>4*1024*1024 or total_bytes+size>16*1024*1024:
                    result['copied_logs'].append({'source':str(path),'size':size,'skipped':'diagnostic size cap'})
                    continue
                digest=hashlib.sha256()
                name=f'{index:03d}-{hashlib.sha256(str(path).encode()).hexdigest()[:12]}-{path.name}'
                with path.open('rb') as source,(target/name).open('wb') as destination:
                    while True:
                        chunk=source.read(65536)
                        if not chunk:break
                        digest.update(chunk);destination.write(chunk)
                total_bytes+=size
                result['copied_logs'].append({'source':str(path),'copy':name,'size':size,
                    'sha256':digest.hexdigest()})
            except OSError as error:
                result['copied_logs'].append({'source':str(path),'copy_error':type(error).__name__})
        result['log_collection_limits']={'files_considered_max':48,'file_bytes_max':4194304,
            'total_bytes_max':16777216,'bytes_copied':total_bytes}
        temporary=target/'worldd-startup-diagnostic.json.tmp'
        temporary.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        temporary.replace(target/'worldd-startup-diagnostic.json')

    def management(self, method, **args):
        request=make_request(method,args); self.sequence+=1;request.request_id=self.sequence
        self.proc.stdin.write(frame(request));self.proc.stdin.flush()
        try:
            reply=receive(lambda n:os.read(self.proc.stdout.fileno(),n),self.proc.stdout,core.Reply)
        except EOFError:
            try:self.capture_management_eof(method)
            except Exception as diagnostic_error:
                print('World startup diagnostic capture failed: '+type(diagnostic_error).__name__,file=sys.stderr)
            raise
        if reply.HasField('error'): raise AssertionError(reply)
        if reply.request_id!=self.sequence: raise AssertionError('Wrong management reply identity')
        return result_value(reply)
    def close(self):
        if self.lifecycle_before_close is None:
            self.lifecycle_before_close=self.lifecycle_snapshot()
        for peer in self.peers: peer.close()
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill();self.proc.wait(timeout=3)
                self.record_final_lifecycle()
                raise
        self.record_final_lifecycle()
        for stream in (self.proc.stdin,self.proc.stdout,self.log):
            if not stream.closed:stream.close()
        if self.broker_channel: self.broker_channel.close(); self.broker_channel = None


class WorldIdentityFixture(ClientFixture):
    def setUp(self):
        super().setUp();self.worlds=[];self.registered_services={};self.registry_revision=0
        self.addCleanup(lambda:[world.close() for world in reversed(self.worlds)])
        self.owner_peer=self.ready()
        self.record=self.decide(self.owner_peer,self.enroll(self.owner_peer)).enrollment
        self.identity=self.claims(self.record,self.info)
        def retain_service_logs():
            if not os.environ.get('SPATIAL_SECURITY_EVIDENCE'):return
            target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
            for path in self.root.glob('*.log'):
                (target/(type(self).__name__+'-'+self._testMethodName+'-'+path.name)).write_bytes(path.read_bytes())
        self.addCleanup(retain_service_logs)
    def configure_participant(self, config, domains):
        # Registration is explicit fixture provisioning. Production still rejects missing service configuration.
        identity=tuple(sorted(domain.SerializeToString() for domain in domains))
        if identity not in self.registered_services:
            number=len(self.registered_services)
            key,public=self.key('service-'+str(number))
            definition=pb.AuthorityParticipant(id=os.urandom(32),public_key=public,domains=domains)
            peer=self.connect()
            try:
                op=pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(
                    participant=definition,expected_registry_revision=self.registry_revision))
                reply=self.execute(peer,self.challenge(peer,op));self.assertTrue(reply.HasField('participant'),reply)
            finally:peer.close()
            self.registry_revision=reply.participant.registered_revision
            self.registered_services[identity]=(number,key,definition.id,self.registry_revision)
        number,key,participant_id,revision=self.registered_services[identity]
        client=self.root/('registered-client-'+str(number)+'.textproto')
        client.write_bytes(Path(config.issuer_client_config).read_bytes());client.chmod(0o600)
        config.issuer_client_config=str(client)
        participant=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,issuer_client_config=str(client),
            private_key_path=str(key),participant_id=participant_id,registered_revision=revision)
        path=self.root/('registered-participant-'+str(number)+'.textproto')
        path.write_text(text_format.MessageToString(participant));path.chmod(0o600)
        config.participant_runtime_config=str(path)
    def configure_world_participant(self, config, state):
        # Initialize or read the real stopped world before registering its persisted identity.
        result=subprocess.run([str(WORLD),'--state-dir',str(state)],input=frame(make_request('world.info',{})),
            capture_output=True,timeout=6)
        self.assertEqual(result.returncode,0,result.stderr)
        reply=read_message(io.BytesIO(result.stdout),core.Reply);self.assertFalse(reply.HasField('error'),reply)
        self.configure_participant(config,[grants.Domain(kind=grants.WORLD,id=result_value(reply)['world_id'])])
    def world(self, admission=session.EXPLICIT_ACTORS, admitted=True, grants=(), native=False, state=None, count=2, fixture_faults=False):
        client_path=self.config_file()
        client=text_format.Parse(client_path.read_text(),pb.IssuerTlsClientConfig())
        client.ClearField('io_timeout_ms')
        client_path.write_text(text_format.MessageToString(client))
        config=session.WorldIdentityConfig(protocol_version=1,issuer_client_config=str(client_path),admission=admission)
        if admitted:config.admitted_actor_ids.append(self.identity.actor_id)
        if grants:config.actor_grants.add(actor_id=self.identity.actor_id,grants=grants)
        state=state or self.root/f'world-state-{len(self.worlds)}'
        self.configure_world_participant(config,state)
        path=self.root/f'world-{len(self.worlds)}.textproto';path.write_text(text_format.MessageToString(config));path.chmod(0o600)
        world=WorldProcess(self,path,state,native,count=count,fixture_faults=fixture_faults)
        world.state_path=state; world.config_path=path
        self.worlds.append(world);world.management('system.hello');return world
    def authenticate(self, peer, record=None, key=None, public=None, **spawn):
        result=peer.call(begin=session.WorldAuthenticationBegin(credential=(record or self.record).credential,**spawn))
        self.assertTrue(result.HasField('challenge'),result)
        proof=self.proof(result.challenge,key or self.actor_key,public or self.actor_public)
        return peer.call(complete=pb.AuthenticationComplete(nonce=result.challenge.nonce,proof=proof))
    def revoke(self, kind=pb.CREDENTIAL_TARGET, revision=0):
        target=pb.IdentityReference(kind=kind,id=self.identity.credential_id if kind==pb.CREDENTIAL_TARGET else self.identity.actor_id)
        operation=pb.IssuerOperation(identity_revoke=pb.IdentityRevoke(target=target,expected_security_revision=revision,reason='world regression'))
        # Fixture setup can outlive the issuer's idle timeout. Authenticate each
        # consequential owner operation on a fresh, independently verified TLS peer.
        peer=self.connect()
        try:
            reply=self.execute(peer,self.challenge(peer,operation))
        finally:
            peer.close()
        self.assertTrue(reply.HasField('identity_status'),reply)
    def until(self, fn, predicate):
        deadline=time.monotonic()+4
        while True:
            result=fn()
            if predicate(result):return result
            if time.monotonic()>deadline:self.fail(f'World condition expired: {result}')
            time.sleep(.01)
    def held(self):
        world=self.world(grants=['navigate','interact','observe'],native=True);peer=world.peers[0]
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        probe=Probe(self.root,xi2=True,display=os.environ['SPATIAL_TEST_DISPLAY']);self.addCleanup(probe.close)
        found=self.until(lambda:world.management('surface.find',probe_id=probe.id),lambda r:r['surface'] is not None)['surface']
        room=world.management('world.info')['entry_room']
        world.management('surface.place',id=found['id'],room=room,pose=pose((0,0,-2)),size=[2,2])
        self.assertTrue(peer.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1))).HasField('action'))
        self.assertTrue(peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)).HasField('action'))
        self.assertTrue(peer.act(key=core.ActorKeyRequest(keysym='Shift_L',pressed=True)).HasField('action'))
        state=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertTrue(state.held_keys);self.assertTrue(state.held_buttons)
        return world,peer,probe
    def released(self, world, probe):
        def events():world.management('runtime.fence');return probe.input_events()
        records=self.until(events,lambda rows:any(e['event']=='key' and not e['pressed'] for e in rows) and
                          any(e['event']=='button' and not e['pressed'] for e in rows))
        self.assertIsNone(probe.proc.poll())
        return records


class WorldIdentityTests(WorldIdentityFixture):
    def test_world_session_derives_identity_and_uses_real_actor(self):
        world=self.world(grants=['navigate']);peer=world.peers[0];opened=self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'),opened);self.assertEqual(opened.opened.identity.actor_id,self.identity.actor_id)
        state=peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(state.id,opened.opened.actor_id);self.assertEqual(state.room,world.management('world.info')['entry_room'])
        self.assertEqual(world.management('world.get',id=state.id)['id'],state.id)
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_private_world_requires_separate_admission(self):
        world=self.world(admitted=False,grants=['navigate']);self.denied(self.authenticate(world.peers[0]),core.FORBIDDEN)
        self.assertEqual(len(world.management('world.list')['entities']),1)

    def test_public_admission_does_not_grant_action_authority(self):
        peer=self.world(admission=session.REGISTERED_ACTORS,admitted=False).peers[0]
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        self.denied(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])),core.FORBIDDEN)
        self.denied(peer.act(observe=core.ActorObserveRequest()),core.FORBIDDEN)
        self.denied(peer.call(action=core.ActorActionRequest(seq=2,stop=core.ActorStopRequest())),core.STALE_SEQUENCE)
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_anonymous_channel_cannot_act_or_inspect(self):
        peer=self.world().peers[0]
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.denied(peer.act(stop=core.ActorStopRequest()),core.UNAUTHORIZED)

    def test_actor_channel_does_not_expose_management(self):
        peer=self.world().peers[0]
        self.denied(peer.request(core.Request(protocol_version=1,actor_open=core.ActorOpenRequest(id='forged',grants=['interact']))),core.INVALID_ARGUMENT)

    def test_caller_token_cannot_select_another_actor(self):
        peer=self.world().peers[0];self.authenticate(peer)
        self.denied(peer.call(action=core.ActorActionRequest(token='injected',seq=1,stop=core.ActorStopRequest())),core.INVALID_ARGUMENT)

    def test_wrong_key_and_replayed_completion_fail(self):
        peer=self.world().peers[0]
        challenge=peer.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)).challenge
        completion=pb.AuthenticationComplete(nonce=challenge.nonce,proof=self.proof(challenge,self.owner,self.owner_public))
        self.denied(peer.call(complete=completion),core.UNAUTHORIZED)
        completion.proof.CopyFrom(self.proof(challenge,self.actor_key,self.actor_public))
        self.denied(peer.call(complete=completion),core.UNAUTHORIZED)

    def test_proof_is_bound_to_its_channel(self):
        world=self.world();first,second=world.peers
        challenge=first.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)).challenge
        other=second.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)).challenge
        self.assertNotEqual(challenge.nonce,other.nonce)
        completion=pb.AuthenticationComplete(nonce=challenge.nonce,proof=self.proof(challenge,self.actor_key,self.actor_public))
        self.denied(second.call(complete=completion),core.UNAUTHORIZED)
        self.assertTrue(first.call(complete=completion).HasField('opened'))

    def test_world_audience_and_random_binding_are_checked(self):
        first=self.world();second=self.world();challenges=[];replies=[]
        for world in (first,second):
            reply=world.peers[0].call(begin=session.WorldAuthenticationBegin(credential=self.record.credential))
            replies.append(reply)
            challenges.append(pb.AuthenticationChallenge.FromString(reply.challenge.signing_request.payload))
        self.assertNotEqual(challenges[0].request.audience,challenges[1].request.audience)
        self.assertNotEqual(challenges[0].request.channel_binding,challenges[1].request.channel_binding)
        for world,challenge in zip((first,second),challenges):
            self.assertIn(world.management('world.info')['world_id'],challenge.request.audience)
        completion=pb.AuthenticationComplete(nonce=replies[0].challenge.nonce,
            proof=self.proof(replies[0].challenge,self.actor_key,self.actor_public))
        self.denied(second.peers[0].call(complete=completion),core.UNAUTHORIZED)
        self.assertTrue(first.peers[0].call(complete=completion).HasField('opened'))

    def test_idle_session_keeps_issuer_checks_available(self):
        peer=self.world().peers[0];self.assertTrue(self.authenticate(peer).HasField('opened'))
        time.sleep(4.1) # Exceed the independent issuer fixture's three-second idle timeout.
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_revoked_credential_cannot_enter(self):
        self.revoke();peer=self.world().peers[0]
        self.denied(peer.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)),core.UNAUTHORIZED)

    def test_revocation_between_challenge_and_completion_prevents_entry(self):
        peer=self.world().peers[0];challenge=peer.call(begin=session.WorldAuthenticationBegin(credential=self.record.credential)).challenge
        self.revoke()
        self.denied(peer.call(complete=pb.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge,self.actor_key,self.actor_public))),core.UNAUTHORIZED)

    def test_current_status_blocks_actions_after_committed_revocation(self):
        peer=self.world(grants=['navigate']).peers[0];self.authenticate(peer);self.revoke()
        self.denied(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])),core.UNAUTHORIZED)

    def test_credential_revocation_preserves_another_credential_for_actor(self):
        key,public=self.key('second-actor-key')
        record=self.decide(self.owner_peer,self.enroll(self.owner_peer,self.intent(public),key,public),pb.APPROVE_EXISTING,self.identity.actor_id).enrollment
        world=self.world();self.authenticate(world.peers[0]);self.revoke()
        self.denied(world.peers[0].call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.assertTrue(self.authenticate(world.peers[1],record,key,public).HasField('opened'))

    def test_prepared_destination_cannot_act_or_create_an_actor_until_activation(self):
        world=self.world(grants=['navigate']);peer=world.peers[0]
        before=world.management('world.snapshot')
        prepared=self.authenticate(peer,transition_id=b't'*32)
        self.assertTrue(prepared.HasField('prepared'),prepared)
        self.assertEqual(prepared.prepared.transition_id,b't'*32)
        self.assertEqual(world.management('world.snapshot'),before)
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)
        self.denied(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])),core.UNAUTHORIZED)
        self.denied(peer.call(activate=session.WorldSessionActivate(transition_id=b'x'*32)),core.UNAUTHORIZED)
        reply=peer.call(activate=session.WorldSessionActivate(transition_id=b't'*32))
        self.assertTrue(reply.HasField('opened'),reply)
        self.assertEqual(reply.opened.actor_id,prepared.prepared.actor.actor_id)
        self.assertEqual(reply.opened.world_id,prepared.prepared.actor.world_id)
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        self.denied(peer.call(activate=session.WorldSessionActivate(transition_id=b't'*32)),core.UNAUTHORIZED)

    def test_destination_denial_preserves_source_actor(self):
        source=self.world(grants=['navigate']);peer=source.peers[0]
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        target=self.world(admitted=False)
        self.denied(self.authenticate(target.peers[0],transition_id=b't'*32),core.FORBIDDEN)
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_prepared_credential_revocation_prevents_activation(self):
        peer=self.world().peers[0]
        self.assertTrue(self.authenticate(peer,transition_id=b't'*32).HasField('prepared'))
        self.revoke()
        self.denied(peer.call(activate=session.WorldSessionActivate(transition_id=b't'*32)),core.UNAUTHORIZED)

    def test_closing_prepared_destination_discards_activation(self):
        world=self.world();peer=world.peers[0]
        self.assertTrue(self.authenticate(peer,transition_id=b't'*32).HasField('prepared'))
        self.assertTrue(peer.call(close=session.WorldSessionClose()).closed.closed)
        self.denied(peer.call(activate=session.WorldSessionActivate(transition_id=b't'*32)),core.UNAUTHORIZED)
        self.assertEqual(len(world.management('world.list')['entities']),1)
        self.assertTrue(self.authenticate(peer).HasField('opened'))

    def test_departure_requires_authenticated_session_and_sequence(self):
        world=self.world(grants=['navigate']);peer=world.peers[0]
        operation=session.WorldSessionDepart(seq=1,transition_id=b'd'*32)
        self.denied(peer.call(depart=operation),core.UNAUTHORIZED)
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))
        self.denied(peer.call(depart=operation),core.STALE_SEQUENCE)
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        operation.seq=2;operation.transition_id=b'short'
        self.denied(peer.call(depart=operation),core.INVALID_ARGUMENT)
        operation.transition_id=b'd'*32
        reply=peer.call(depart=operation);self.assertTrue(reply.HasField('departed'),reply)
        self.assertEqual(reply.departed.transition_id,b'd'*32)
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)

    def test_departure_saves_motion_and_restores_context_without_explicit_checkpoint(self):
        state=self.root/'departed-world';world=self.world(state=state,grants=['navigate']);peer=world.peers[0]
        opened=self.authenticate(peer).opened
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        time.sleep(.08)
        reply=peer.call(depart=session.WorldSessionDepart(seq=2,transition_id=b'd'*32))
        self.assertTrue(reply.HasField('departed'),reply)
        self.assertEqual(reply.departed.actor_id,opened.actor_id)
        self.assertEqual(reply.departed.world_id,opened.world_id)
        saved=world.management('world.get',id=opened.actor_id)
        self.assertGreater(saved['pose']['p'][0],0)
        import sqlite3
        with sqlite3.connect('file:'+str(state/'world.db')+'?mode=ro',uri=True) as db:
            durable=core.Entity.FromString(db.execute('SELECT data FROM entities WHERE id=?',(opened.actor_id,)).fetchone()[0])
        self.assertAlmostEqual(durable.pose.position.x,saved['pose']['p'][0])
        self.assertEqual(durable.room,saved['room'])
        self.assertEqual(reply.departed.world_revision,world.management('world.info')['world_revision'])
        world.close();restored=self.world(state=state)
        self.assertTrue(self.authenticate(restored.peers[0]).HasField('opened'))
        actual=restored.peers[0].call(inspect=session.WorldSessionInspect()).state
        self.assertAlmostEqual(actual.pose.position.x,saved['pose']['p'][0])
        self.assertEqual(list(actual.axes),[0]*6)

    def test_departure_receipt_follows_native_input_release(self):
        world,peer,probe=self.held()
        peer.action_sequence+=1
        reply=peer.call(depart=session.WorldSessionDepart(seq=peer.action_sequence,transition_id=b'n'*32))
        self.assertTrue(reply.HasField('departed'),reply)
        self.released(world,probe)
        self.assertTrue(self.authenticate(world.peers[1]).HasField('opened'))

    def test_session_reconnect_restores_saved_room_and_pose(self):
        state=self.root/'restored-world';world=self.world(state=state)
        world.management('world.create',entity={'id':'elsewhere','kind':'room','label':'Elsewhere'})
        opened=self.authenticate(world.peers[0],room='elsewhere',pose=core.Pose(position=core.Vec3(x=3),orientation=core.Quaternion(w=1))).opened
        world.management('world.checkpoint');world.close()
        restored=self.world(state=state);reply=self.authenticate(restored.peers[0]);self.assertEqual(reply.opened.actor_id,opened.actor_id)
        actual=restored.peers[0].call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(actual.room,'elsewhere');self.assertEqual(actual.pose.position.x,3)

    def test_disconnect_releases_real_native_input_and_keeps_application(self):
        world,peer,probe=self.held();peer.close();self.released(world,probe)
        self.assertTrue(self.authenticate(world.peers[1]).HasField('opened'))

    def test_credential_revocation_releases_native_input_without_another_actor_request(self):
        world,peer,probe=self.held();self.revoke();self.released(world,probe)
        self.denied(peer.call(inspect=session.WorldSessionInspect()),core.UNAUTHORIZED)

    def test_actor_revocation_releases_native_input(self):
        world,peer,probe=self.held();self.revoke(pb.ACTOR_TARGET);self.released(world,probe)
        self.denied(peer.act(observe=core.ActorObserveRequest()),core.UNAUTHORIZED)

    def test_issuer_failure_cancels_live_control_and_does_not_kill_application(self):
        world,peer,probe=self.held();self.stop_daemons();self.released(world,probe)
        self.denied(peer.act(key=core.ActorKeyRequest(keysym='a',pressed=True)),core.UNAVAILABLE)

    def test_excessive_frame_closes_actor_channel_and_releases_input(self):
        world,peer,probe=self.held();peer.stream.sendall(struct.pack('!I',1048577))
        self.released(world,probe)
        self.assertEqual(peer.stream.recv(1),b'')


class ReceiveTests(unittest.TestCase):
    def test_receive_retries_would_block_after_readiness(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        writer.sendall(struct.pack('!I', 0))
        reads = 0

        def read(size):
            nonlocal reads
            reads += 1
            if reads == 1:
                raise BlockingIOError(11, 'simulated readiness race')
            return reader.recv(size)

        reply = receive(read, reader, session.WorldSessionReply)
        self.assertEqual(reply, session.WorldSessionReply())
        self.assertEqual(reads, 2)

    def test_receive_repeated_would_block_stops_at_absolute_deadline(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        clock = [0.0]
        attempts = []

        def monotonic():
            value = clock[0]
            clock[0] += 1.0
            return value

        def always_ready(*_args):
            return ([reader], [], [])

        def would_block(_size):
            attempts.append(None)
            raise BlockingIOError(11, 'simulated repeated readiness race')

        with patch.object(time, 'monotonic', side_effect=monotonic), \
             patch.object(select, 'select', side_effect=always_ready):
            with self.assertRaisesRegex(AssertionError, 'World response deadline expired'):
                receive(would_block, reader, session.WorldSessionReply)
        self.assertEqual(len(attempts), 4)


if __name__=='__main__':unittest.main(verbosity=2)
