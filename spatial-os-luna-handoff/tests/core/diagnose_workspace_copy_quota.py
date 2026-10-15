"""Opt-in quota evidence cases. This module is outside the mandatory copy loader."""
import json
import os
from pathlib import Path
import select
import sqlite3
import time
import unittest
import uuid
from contextlib import closing
import test_workspace_copy_engine as engine

CONTROL='test_no_copy_live_writers_sixty_second_diagnostic'
POSITIVE='test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch'
NEGATIVE='test_small_source_quota_rejects_actual_writer_and_preserves_peer_application'


def atomic_json(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex)
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
    try:
        raw=json.dumps(value,sort_keys=True,indent=2).encode()+b'\n';used=0
        while used<len(raw):
            count=os.write(descriptor,raw[used:])
            if count<=0:raise OSError('Diagnostic JSON write made no progress')
            used+=count
        os.fsync(descriptor)
    finally:os.close(descriptor)
    os.replace(temporary,path)
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


class QuotaDiagnosticTests(engine.WorkspaceCopyEngineTests):
    SOURCE_STATE_BYTES_BY_SCENARIO={**engine.WorkspaceCopyEngineTests.SOURCE_STATE_BYTES_BY_SCENARIO,CONTROL:8*2**20}

    def setUp(self):
        self.diagnostic_started=time.monotonic();self.context_sequence=0;self.observer_fd=None
        self.observer_directory=Path(os.environ['SPATIAL_COPY_QUOTA_DIAGNOSTIC_DIR']).resolve()
        self.assertTrue(self.observer_directory.is_dir() and self.observer_directory.is_relative_to(Path.home()/'spatial-artifacts'))
        self.mode=os.environ['SPATIAL_COPY_QUOTA_DIAGNOSTIC_MODE']
        self.assertEqual(self.mode,{CONTROL:'nocopy',POSITIVE:'copy',NEGATIVE:'smallquota'}[self._testMethodName])
        super().setUp()
        self.addCleanup(self.finish_observer)
        volume=json.loads((self.copy_evidence/'volume.json').read_text())
        self.filesystem_id=volume['created']['filesystem_id'];self.case_id=self.name
        self.assertEqual(len(self.filesystem_id),32);int(self.filesystem_id,16)
        request=dict(protocol_version=1,case_id=self.case_id,mode=self.mode,filesystem_id=self.filesystem_id,
            fixture_pid=os.getpid(),fixture_start_ticks=self.start_time(os.getpid()),
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),volume_name=self.name,
            mount_path=str(self.execution_override),volume_image=str(self.entry/'volume.img'))
        atomic_json(self.observer_directory/'observer-request.json',request)
        deadline=time.monotonic()+15;ready_path=self.observer_directory/'observer-ready.json'
        while not ready_path.exists():
            self.assertLess(time.monotonic(),deadline,'Observer did not attach before application launch');time.sleep(.02)
        self.assertFalse(ready_path.is_symlink());ready=json.loads(ready_path.read_text())
        self.assertEqual(ready['protocol_version'],1);self.assertEqual(ready['case_id'],self.case_id)
        self.assertEqual(ready['filesystem_id'],self.filesystem_id);self.assertTrue(ready['attached'])
        self.assertEqual(set(ready['attached_probes']),{'qgroup_reserve_fexit','qgroup_update_reserve'})
        self.assertEqual(self.start_time(ready['observer_pid']),str(ready['observer_start_ticks']))
        self.observer_fd=os.pidfd_open(ready['observer_pid']);self.check_observer()
        (self.copy_evidence/'observer-ready.json').write_bytes(ready_path.read_bytes())

    def check_observer(self):
        self.assertIsNotNone(self.observer_fd)
        poll=select.poll();poll.register(self.observer_fd,select.POLLIN)
        self.assertEqual(poll.poll(0),[],'Attached observer exited during the diagnostic')

    def observer_context(self):
        if not hasattr(self,'case_id') or not hasattr(self,'execution'):return
        record=dict(protocol_version=1,case_id=self.case_id,filesystem_id=self.filesystem_id,
            sequence=self.context_sequence+1,workspace_id='one',repository_id=None,project_id=None,
            configured_application_states=[],processes=[],context_errors=[])
        bindings={}
        try:
            with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=1)) as db:
                row=db.execute("SELECT r.path,t.path FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id='one'").fetchone()
                if row:record.update(repository_id=Path(row[0]).name.removesuffix('.git'),project_id=Path(row[1]).name)
                record['configured_application_states']=[dict(key=key,state_id=identity) for key,identity in db.execute("SELECT key,state_id FROM configured_application_states WHERE workspace='one' ORDER BY key")]
                bindings={identity:key for identity,key in db.execute("SELECT process,key FROM configured_process_states WHERE workspace='one'")}
            identities={}
            for pid in self.descendants():
                try:
                    raw=Path(f'/proc/{pid}/stat').read_text();fields=raw[raw.rfind(')')+2:].split()
                    argv=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                    runtime=next((identity for identity in bindings if str(self.execution/'applications'/identity/'tmp').encode() in argv),None)
                    identities[pid]=dict(pid=pid,parent=int(fields[1]),start_ticks=fields[19],
                        exe=os.readlink(f'/proc/{pid}/exe'),cgroup=Path(f'/proc/{pid}/cgroup').read_text().strip(),runtime_id=runtime)
                except OSError as error:record['context_errors'].append(dict(pid=pid,error=repr(error)))
            for item in identities.values():
                parent=item['parent'];seen=set()
                while item['runtime_id'] is None and parent in identities and parent not in seen:
                    seen.add(parent);item['runtime_id']=identities[parent]['runtime_id'];parent=identities[parent]['parent']
                item['process_id']=item['runtime_id'];item['key']=bindings.get(item['runtime_id'])
                record['processes'].append(item)
        except Exception as error:record['context_errors'].append(repr(error))
        atomic_json(self.observer_directory/'observer-context.json',record);self.context_sequence+=1

    def launch_configured(self,*args,**kwargs):
        self.check_observer();reply=super().launch_configured(*args,**kwargs);self.observer_context();return reply

    def retain_process_lifecycle(self,*args,**kwargs):
        super().retain_process_lifecycle(*args,**kwargs)
        try:self.observer_context()
        except Exception as error:print('Observer context unavailable: '+repr(error),flush=True)

    def finish_observer(self):
        status=dict(protocol_version=1,case_id=getattr(self,'case_id',None),
            filesystem_id=getattr(self,'filesystem_id',None),ack_confirmed=False,diagnostic_errors=[])
        try:
            if hasattr(self,'lifecycle_processes'):
                self.retain_process_lifecycle('diagnostic-final',self.lifecycle_owned,self.lifecycle_processes)
            try:self.observer_context()
            except Exception as error:status['diagnostic_errors'].append('context: '+repr(error))
            if hasattr(self,'case_id'):
                atomic_json(self.observer_directory/'observer-release.json',dict(protocol_version=1,
                    case_id=self.case_id,filesystem_id=self.filesystem_id,mode=self.mode,reason='fixture-finally',monotonic=time.monotonic()))
                deadline=time.monotonic()+10;done_path=self.observer_directory/'observer-done.json'
                while not done_path.exists() and time.monotonic()<deadline:time.sleep(.02)
                if not done_path.exists():raise TimeoutError('Observer completion acknowledgement exceeded ten seconds')
                if done_path.is_symlink():raise ValueError('Observer completion must be a regular owned receipt')
                done=json.loads(done_path.read_text())
                if done.get('protocol_version')!=1 or done.get('case_id')!=self.case_id or done.get('filesystem_id')!=self.filesystem_id:
                    raise ValueError('Observer completion identity does not match this fixture')
                status['observer_done']=done
                status['ack_confirmed']=done.get('complete') is True and done.get('dataset_valid') is True
                if not status['ack_confirmed']:status['diagnostic_errors'].append('Observer reported incomplete or invalid capture')
        except Exception as error:status['diagnostic_errors'].append(repr(error))
        finally:
            if self.observer_fd is not None:os.close(self.observer_fd);self.observer_fd=None
            status['dataset_valid']=status['ack_confirmed'] and not status['diagnostic_errors']
            try:atomic_json(self.observer_directory/'observer-finish-status.json',status)
            except Exception as error:print('Observer completion receipt failed: '+repr(error),flush=True)

    def test_no_copy_live_writers_sixty_second_diagnostic(self):
        self.configure_state_writers()
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        processes={key:self.launch_configured(peer,key,'diagnostic-'+key) for key in ('editor','terminal')}
        paths=self.configured_paths();self.assertEqual(set(paths),set(processes));self.wait_state_writes(paths)
        for key in processes:self.assert_overlap_denied_without_effects(peer,key)
        owned={pid:self.start_time(pid) for pid in self.descendants()}
        self.lifecycle_processes=processes;self.lifecycle_owned=owned
        initial={key:{name:int((directory/name/'counter').read_text()) for name in ('data','config')}
                 for key,(directory,contract) in paths.items()}
        started=time.monotonic();samples=[]
        try:
            while time.monotonic()-started<60:
                self.assertLess(time.monotonic(),self.diagnostic_started+90,'Diagnostic total exceeded ninety seconds')
                self.check_observer()
                for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'No-copy source PID/start changed')
                with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=1)) as db:
                    rows=db.execute('SELECT id,state,exit_code FROM processes ORDER BY id').fetchall()
                    self.assertEqual({(identity,state,code) for identity,state,code in rows},
                        {(process.process_id,'running',None) for process in processes.values()})
                self.select(peer)
                samples.append(dict(monotonic=time.monotonic(),processes=rows))
                atomic_json(self.copy_evidence/'nocopy-progress.json',dict(qualification='UNQUALIFIED_DIAGNOSTIC',
                    live_observation_seconds=time.monotonic()-started,samples=samples))
                time.sleep(.25)
            for key,(directory,contract) in paths.items():
                for name in ('data','config'):self.assertGreater(int((directory/name/'counter').read_text()),initial[key][name])
        finally:self.retain_process_lifecycle('nocopy-observation-finished',owned,processes)
        for key,process in processes.items():
            (self.project/('stop-'+key)).touch();self.wait_job(world,process)
        self.assertLess(time.monotonic(),self.diagnostic_started+90,'Diagnostic active test exceeded ninety seconds')
        self.check_observer()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(QuotaDiagnosticTests(name) for name in (
        'test_no_copy_live_writers_sixty_second_diagnostic',
        'test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch',
        'test_small_source_quota_rejects_actual_writer_and_preserves_peer_application'))


if __name__=='__main__':unittest.main()
