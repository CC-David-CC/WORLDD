"""Real cabinet files through one human owner and two ordinary local actor adapters."""
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import signal
import sqlite3
import time
import unittest
from google.protobuf import text_format

import test_scripted_actor as existing
import core_pb2 as core
import grants_pb2 as grants
import actor_session_client_pb2 as client
import file_actions_pb2 as files
import scripted_actor_pb2 as scripted
import world_session_pb2 as world
from harness.wire import frame,read_message

WORKSPACE='workspace-one'


class ThreeActorFileTests(existing.ScriptedActorTests):
    def setUp(self):
        super().setUp();self.local_paths={};self.read_only_second=False
        self.presentation_continuations_override=True

    def prepare(self, **options):
        config, descriptors = super().prepare(**options)
        self.assertTrue(config.enable_presentation_continuations)
        return config, descriptors

    def config_file(self,name,value):
        if isinstance(value,world.WorldAuthoritySeed) and name.startswith('scripted-world-seed'):
            copy=world.WorldAuthoritySeed();copy.CopyFrom(value)
            for root in copy.roots:
                if grants.WORLD_ENTER in root.rights:
                    root.rights.extend([grants.WORLD_PLACEMENT_CREATE,grants.REPRESENTATION_VIEW,grants.REPRESENTATION_USE])
            value=copy
        if isinstance(value,world.WorldIdentityConfig) and name.startswith('scripted-world-policy'):
            copy=world.WorldIdentityConfig();copy.CopyFrom(value)
            path=self.root/('local-'+str(len(self.local_paths))+'.sock')
            copy.local_actor_socket_path=str(path);self.local_paths[self.world_domain]=path;value=copy
        if isinstance(value,client.ActorSessionClientTlsConfig) and name.startswith('scripted-'):
            copy=client.ActorSessionClientTlsConfig();copy.CopyFrom(value)
            for field in ['address','port','server_name','certificate_path','private_key_path','server_ca_path']:copy.ClearField(field)
            members=self.retained_pids()[copy.expected_world_id];self.assertEqual(len(members),1)
            pid=members[0];copy.local_endpoint.socket_path=str(self.local_paths[copy.expected_world_id])
            copy.local_endpoint.server_pid=pid;copy.local_endpoint.server_start_ticks=int(self.process_start(pid));value=copy
        return super().config_file(name,value)

    def workspace_grant_roots(self):
        rights=[grants.WORKSPACE_ACCESS,grants.FILE_BROWSE,grants.FILE_READ,grants.FILE_CREATE,grants.FILE_EDIT,
            grants.FILE_RENAME,grants.FILE_MOVE,grants.FILE_COPY,grants.FILE_TRASH,grants.FILE_RESTORE,
            grants.FILE_PERMANENT_DELETE,grants.PROCESS_EXECUTE,
            grants.APPLICATION_OBSERVE,grants.APPLICATION_CONTROL,grants.WORKSPACE_PRESENTATION_ATTACH]
        result=[]
        for index,principal in enumerate([self.principal]+[item[2] for item in self.agents]):
            allowed=[grants.WORKSPACE_ACCESS,grants.FILE_BROWSE,grants.FILE_READ] if self.read_only_second and index==2 else rights
            result.append(grants.GrantTerms(subject=principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORKSPACE,id=WORKSPACE)),rights=allowed))
        return result

    def make_world(self,suffix,**options):
        configured,listener,descriptor=super().make_world(suffix,**options)
        definition=configured.supervisor
        reply=subprocess.run([definition.launch.executable,'--state-dir',definition.launch.arguments[1]],
            input=frame(core.Request(protocol_version=1,request_id=1,world_info=core.WorldInfoRequest())),
            capture_output=True,timeout=8,check=True)
        info=read_message(io.BytesIO(reply.stdout),core.Reply)
        self.assertTrue(info.HasField('world_info'),info)
        configured.initial_workspaces.append(WORKSPACE)
        configured.expected_initial_world_revision=info.world_info.world_revision
        return configured,listener,descriptor

    def human(self,**action):
        reply=self.shared('propose',value=core.ActorActionRequest(**action))
        self.assertTrue(reply.accepted,reply);self.assertTrue(reply.proposed.accepted,reply)
        return reply.proposed

    def cabinet(self,actor,target,**action):
        request=files.ActorCabinetRequest(representation_id=target.representation_id,
            expected_generation=target.generation,operation_id=os.urandom(16),**action)
        result=actor(cabinet=request).cabinet
        self.assertFalse(result.HasField('error'),result)
        return result

    def prepare_cabinet(self):
        initial=self.ready_host();producer=self.producer()
        peers=[self.scripted_peer(0),self.scripted_peer(1)]
        states=[initial.inspection.state]+[peer.initial.state for peer in peers]
        self.assertEqual(len({state.id for state in states}),3)
        self.assertTrue(all(not state.workspace for state in states),'Availability selected a workspace implicitly')
        actors=[self.human]+[lambda peer=peer,**action:self.accepted(peer.act(**action)) for peer in peers]
        for act in actors:act(workspace_select=core.WorkspaceIdRequest(id=WORKSPACE))
        target=self.human(presentation_create=core.PresentationCreateRequest(protocol_version=1,
            operation_id=os.urandom(16),workspace_id=WORKSPACE,room_id=states[0].room,
            transform=core.Pose(position=core.Vec3(x=.8,z=-2),orientation=core.Quaternion(w=1)),
            expected_world_revision=self.human(observe=core.ActorObserveRequest()).observation.world_revision,
            kind=core.PRESENTATION_KIND_CABINET)).presentation
        for act in actors:
            observation=act(observe=core.ActorObserveRequest(width=128,height=128)).observation
            views=[v for v in observation.presentation_views if v.representation_id==target.representation_id]
            self.assertEqual(len(views),1,observation);self.assertFalse(views[0].cabinet_locked)
        return producer,peers,actors,target

    def test_human_and_two_local_agents_create_edit_rename_and_read_real_shared_files(self):
        producer,peers,actors,target=self.prepare_cabinet()
        for index,act in enumerate(actors):
            root=self.cabinet(act,target,browse=files.FileBrowse(page_size=32)).directory
            content=('created by actor '+str(index)+'\n').encode();name=('actor-'+str(index)+'.txt').encode()
            created=self.cabinet(act,target,create=files.FileCreate(directory_handle=root.handle,basename=name,content=content)).object
            self.assertEqual((self.project/os.fsdecode(name)).read_bytes(),content)
            self.assertTrue(created.handle)
        # Each actor changes another actor's real file through its own signed session.
        for index,act in enumerate(actors):
            page=self.cabinet(act,target,browse=files.FileBrowse(page_size=32)).directory
            old=next(entry for entry in page.entries if entry.name==('actor-'+str((index+1)%3)+'.txt').encode())
            content=('edited by actor '+str(index)+'\n').encode()
            changed=self.cabinet(act,target,edit=files.FileEdit(file_handle=old.handle,
                expected_generation=old.generation,content=content)).object
            name=('checked-'+str(index)+'.txt').encode()
            renamed=self.cabinet(act,target,rename=files.FileRename(file_handle=changed.handle,
                expected_generation=changed.generation,new_basename=name)).object
            self.assertEqual(self.cabinet(act,target,read=files.FileRead(file_handle=renamed.handle,maximum_bytes=128)).content.data,content)
            self.assertEqual((self.project/os.fsdecode(name)).read_bytes(),content)
        marker=self.root/'ungranted-three-actor-host-file';marker.write_text('host-only fixture data')
        for index,act in enumerate(actors):
            code=("import json,os\nfrom pathlib import Path\nfds=[]\n"
                "for fd in range(3,256):\n try:os.fstat(fd);fds.append(fd)\n except OSError:pass\n"
                "status=Path('/proc/self/status').read_text()\n"
                "result=dict(cwd=os.getcwd(),mnt=os.readlink('/proc/self/ns/mnt'),fds=fds,"
                "no_new_privs='NoNewPrivs:\\t1' in status,host_file_visible=Path("+repr(str(marker))+").exists())\n"
                "target=Path('/project/job-"+str(index)+".json');temporary=target.with_suffix('.tmp')\n"
                "temporary.write_text(json.dumps(result));temporary.replace(target)\n")
            launched=act(process_launch=core.ProcessLaunchRequest(operation_id='three-actor-job-'+str(index),
                profile='development',argv=['/usr/bin/python3','-c',code])).process
            self.assertTrue(launched.process_id);self.assertEqual(launched.workspace,WORKSPACE)
            result_path=self.project/('job-'+str(index)+'.json')
            self.until(result_path.exists,lambda value:value)
            record=json.loads(result_path.read_text())
            self.assertEqual(record['cwd'],'/project');self.assertEqual(record['fds'],[])
            self.assertTrue(record['no_new_privs']);self.assertFalse(record['host_file_visible'])
            self.assertNotEqual(record['mnt'],os.readlink('/proc/self/ns/mnt'))
        before=[self.state()]+[peer.call(inspect=True).state for peer in peers]
        self.wait_processed(producer.key('w'))
        motions=[peers[0].motion([-.8,0,0,0,0,0]),peers[1].motion([.8,0,0,0,0,0])]
        self.until(lambda:self.state(),lambda state:state.pose.position.z<before[0].pose.position.z-.05)
        self.wait_processed(producer.key('w',False));self.stop()
        for motion in motions:motion.close()
        for peer in peers:self.accepted(peer.act(stop=core.ActorStopRequest()))
        after=[self.state()]+[peer.call(inspect=True).state for peer in peers]
        for old,new in zip(before,after):
            self.assertNotEqual(old.pose,new.pose);self.assertEqual(new.workspace,WORKSPACE);self.assertEqual(list(new.axes),[0]*6)
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_read_only_local_agent_cannot_create_a_file_or_launch_a_process(self):
        self.read_only_second=True
        producer,peers,actors,target=self.prepare_cabinet()
        root=self.cabinet(actors[2],target,browse=files.FileBrowse(page_size=32)).directory
        denied=self.accepted(peers[1].act(cabinet=files.ActorCabinetRequest(representation_id=target.representation_id,
            expected_generation=target.generation,operation_id=os.urandom(16),
            create=files.FileCreate(directory_handle=root.handle,basename=b'forbidden.txt',content=b'no'))))
        self.assertEqual(denied.cabinet.error.code,files.FileActionError.UNAUTHORIZED)
        self.assertFalse((self.project/'forbidden.txt').exists())
        denied=peers[1].act(process_launch=core.ProcessLaunchRequest(operation_id='denied-local-agent',profile='development',argv=['/usr/bin/true']))
        self.assertEqual(denied.error.code,core.FORBIDDEN);self.assertFalse(denied.context.available)
        fenced=peers[1].act(process_launch=core.ProcessLaunchRequest(operation_id='after-denied-local-agent',
            profile='development',argv=['/usr/bin/true']))
        self.assertTrue(fenced.HasField('error'));self.assertFalse(fenced.HasField('action'))
        self.assertFalse(fenced.context.available)
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_wrong_pinned_local_server_never_authenticates_or_falls_back(self):
        self.ready_host();self.producer();before=self.state()
        retained=self.retained_pids();value=self.worlds[0][0]
        tls=text_format.Parse(Path(value.actor_tls_config).read_text(),client.ActorSessionClientTlsConfig())
        tls.actor_signing_key_path=str(self.agents[0][0]);tls.actor_credential.CopyFrom(self.agents[0][1].credential)
        path=self.config_file('scripted-wrong-server',tls)
        tls=text_format.Parse(Path(path).read_text(),client.ActorSessionClientTlsConfig())
        tls.local_endpoint.server_pid=self.host_process.pid
        tls.local_endpoint.server_start_ticks=int(self.process_start(self.host_process.pid))
        Path(path).write_text(text_format.MessageToString(tls))
        config=scripted.ScriptedActorConfig(protocol_version=1,initial_world_id=value.world_id)
        config.worlds.add(world_id=value.world_id,tls_config_path=str(path))
        configured=self.config_file('wrong-local-scripted-actor',config)
        result=subprocess.run([str(existing.SCRIPTED),'--config',str(configured)],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn(b'Local actor endpoint identity changed',result.stderr)
        self.assertEqual(self.state().pose,before.pose);self.assertEqual(self.retained_pids(),retained)
        self.shutdown_checked()

    def test_local_actor_reconnects_across_worlds_and_restores_only_that_worlds_selection(self):
        self.ready_host(second=True);self.producer();peer=self.scripted_peer(0)
        a,b=[item[0].world_id for item in self.worlds]
        self.accepted(peer.act(workspace_select=core.WorkspaceIdRequest(id=WORKSPACE)))
        entered=peer.call(transition=scripted.ScriptedActorTransition(transition_id=os.urandom(32),destination_world_id=b))
        self.assertTrue(entered.transition.destination_activation_confirmed,entered)
        self.assertEqual(peer.call(1,inspect=True).state.workspace,'')
        self.accepted(peer.act(1,workspace_select=core.WorkspaceIdRequest(id=WORKSPACE)))
        returned=peer.call(1,transition=scripted.ScriptedActorTransition(transition_id=os.urandom(32),destination_world_id=a))
        self.assertTrue(returned.transition.destination_activation_confirmed,returned)
        state=peer.call(2,inspect=True).state
        self.assertEqual(state.id,peer.initial.state.id);self.assertEqual(state.workspace,WORKSPACE)
        self.accepted(peer.act(2,stop=core.ActorStopRequest()));peer.close();self.shutdown_checked()
        self.assertTrue(all(not path.exists() for path in self.local_paths.values()))

    def test_remote_tls_keeps_native_input_disabled_while_local_transport_uses_native_checks(self):
        self.ready_host();self.producer();retained=self.worlds[0][0]
        tls=text_format.Parse(Path(retained.actor_tls_config).read_text(),client.ActorSessionClientTlsConfig())
        tls.actor_signing_key_path=str(self.agents[0][0]);tls.actor_credential.CopyFrom(self.agents[0][1].credential)
        path=self.config_file('remote-agent-profile',tls)
        configuration=scripted.ScriptedActorConfig(protocol_version=1,initial_world_id=retained.world_id)
        configuration.worlds.add(world_id=retained.world_id,tls_config_path=str(path))
        remote=existing.ScriptedPeer(self,99,self.config_file('remote-agent',configuration))
        denied=remote.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertEqual(denied.error.code,core.UNIMPLEMENTED,denied)
        self.assertFalse(denied.HasField('action'))
        self.assertFalse(denied.cancellation_confirmed);self.assertFalse(denied.context.available)
        actor=remote.initial.state.id;remote.close()
        local=self.scripted_peer(0);self.assertEqual(local.initial.state.id,actor)
        checked=local.act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        # Authenticated actors conceal missing application targets with FORBIDDEN.
        self.assertEqual(checked.error.code,core.FORBIDDEN,checked)
        self.assertFalse(checked.HasField('action'))
        self.assertTrue(checked.cancellation_confirmed);self.assertTrue(checked.context.available)
        self.accepted(local.act(stop=core.ActorStopRequest()))
        inspected=local.call(inspect=True);self.assertTrue(inspected.context.available)
        self.assertEqual(inspected.state.id,actor)
        local.close();self.shutdown_checked()

    def test_replaced_local_socket_is_preserved_and_shutdown_reports_uncertainty(self):
        self.ready_host();self.producer();path=next(iter(self.local_paths.values()))
        self.assertTrue(path.is_socket());path.unlink();path.write_text('replacement fixture file')
        self.host_process.send_signal(signal.SIGTERM)
        self.assertFalse(self.terminal_cleanup_reply().cleanup_complete)
        self.assertNotEqual(self.host_process.wait(timeout=60),0,self.host_log.read_text())
        self.assertEqual(path.read_text(),'replacement fixture file')
        self.assert_checked_cleanup()
        self.assertIn('Local actor endpoint was replaced before retirement',self.host_log.read_text())

    def test_initial_workspace_setup_requires_its_exact_world_revision(self):
        configuration,descriptors=self.prepare()
        configuration.worlds[0].expected_initial_world_revision+=1
        self.launch_host(configuration,descriptors)
        self.assertTrue(self.terminal_cleanup_reply().cleanup_complete)
        self.assertNotEqual(self.host_process.wait(timeout=60),0,self.host_log.read_text())
        with sqlite3.connect('file:'+str(self.root/'world/world.db')+'?mode=ro',uri=True) as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM world_workspaces').fetchone()[0],0)
        self.assert_checked_cleanup()
        self.assertIn('Initial workspace setup world revision changed',self.host_log.read_text())


if __name__=='__main__':
    names=[name for name in ThreeActorFileTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(ThreeActorFileTests(name) for name in names)).wasSuccessful())
