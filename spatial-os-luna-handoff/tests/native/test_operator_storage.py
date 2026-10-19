"""Actual operator provisioning, production broker launch, and persistent Git recovery."""
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time
import unittest
import uuid
from google.protobuf import text_format
import broker_pb2 as broker
import core_pb2 as core
import storage_pb2 as storage
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request, result_value

BUILD = Path(sys.argv.pop(1)).resolve()
SOURCE = Path(sys.argv.pop(1)).resolve()


class OperatorStorageTests(unittest.TestCase):
    def setUp(self):
        evidence = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
        self.assertTrue(evidence.is_absolute(), 'Operator storage checks require an absolute evidence directory')
        self.root = evidence/self._testMethodName
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.name = 'm03-qual-'+uuid.uuid4().hex[:20]
        self.tool = SOURCE/'tools/provision_storage.py'
        self.entry = Path('/var/lib/spatial/volumes')/self.name
        self.created = False; self.child = None; self.log = None; self.sequence = 0
        self.actions = []; self.addCleanup(self.cleanup)
        self.definition = broker.StorageVolumeDefinition()
        text_format.Parse((SOURCE/'config/operator-storage.textproto').read_text(), self.definition)
        self.definition.name = self.name; self.definition.owner_policy.broker_uid = os.getuid()
        policy = self.definition.owner_policy
        policy.volume_bytes = 512*2**20; policy.control_plane_bytes = policy.filesystem_reserve_bytes = 64*2**20
        policy.default_policy.total_bytes = 256*2**20; policy.default_policy.actor_state_default_bytes = 48*2**20
        for profile in policy.default_policy.profiles: profile.state_bytes = 32*2**20
        self.definition_path = self.root/'definition.textproto'
        self.definition_path.write_text(text_format.MessageToString(self.definition))

    def provision(self, operation, success=True):
        command = ['sudo','-n',sys.executable,str(self.tool),operation,'--bindings-dir',str(BUILD/'python')]
        command += ['--definition',str(self.definition_path)] if operation == 'create' else ['--name',self.name]
        if operation != 'inspect': command.append('--apply')
        result = subprocess.run(command, capture_output=True, text=True, timeout=75)
        self.actions.append(dict(command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
        (self.root/'provision-actions.json').write_text(json.dumps(self.actions,indent=2))
        self.assertEqual(result.returncode == 0, success, result.stdout+result.stderr)
        return json.loads(result.stdout) if success else result

    def record_bytes(self):
        return subprocess.check_output(['sudo','-n','cat',str(self.entry/'record.pb')],timeout=5)

    def replace_record(self, payload):
        code = 'import sys; from pathlib import Path; p=Path(sys.argv[1]); assert p.parent.parent==Path("/var/lib/spatial/volumes") and p.parent.name.startswith("m03-qual-") and p.name=="record.pb"; p.write_bytes(sys.stdin.buffer.read())'
        subprocess.run(['sudo','-n',sys.executable,'-c',code,str(self.entry/'record.pb')],input=payload,check=True,timeout=5)

    def start(self):
        runtime = broker.StorageVolumeRuntime.FromString((self.entry/'runtime.pb').read_bytes())
        self.assertTrue(runtime.require_confinement)
        volume_root = Path(runtime.volume_root)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        root_fd = os.open(volume_root, flags)
        try:
            root_info = os.fstat(root_fd)
            path_info = volume_root.lstat()
            witness = dict(volume_root=str(volume_root), broker_uid=self.definition.owner_policy.broker_uid,
                           current_euid=os.geteuid(), root_uid=root_info.st_uid, root_gid=root_info.st_gid,
                           root_mode=oct(root_info.st_mode & 0o7777), root_device=root_info.st_dev,
                           root_inode=root_info.st_ino,
                           path_matches_fd=(path_info.st_dev, path_info.st_ino) ==
                                           (root_info.st_dev, root_info.st_ino))
            witness_path = self.root/f'volume-root-owner-{len(self.actions)}.json'
            witness_path.write_text(json.dumps(witness, indent=2))
            self.assertTrue(witness['path_matches_fd'], witness)
            self.assertEqual(root_info.st_uid, self.definition.owner_policy.broker_uid, witness)
            self.assertEqual(root_info.st_mode & 0o777, 0o700, witness)
        finally:
            os.close(root_fd)
        original = broker.BrokerProcessConfig()
        text_format.Parse(Path(os.environ['SPATIAL_TEST_BROKER_CONFIG']).read_text(), original)
        config = broker.BrokerProcessConfig(protocol_version=1,delegated_cgroup=original.delegated_cgroup,
                                            resources=runtime.resources,storage_owner=runtime.storage_owner,require_confinement=True)
        path = self.root/f'broker-{len(self.actions)}.textproto'; path.write_text(text_format.MessageToString(config)); path.chmod(0o600)
        self.log_path = self.root/f'broker-{len(self.actions)}.log'; self.log = self.log_path.open('wb')
        self.child = subprocess.Popen([str(BUILD/'spatial-storage-broker-launch-probe'),runtime.volume_root,str(path)],
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log)
        self.call('system.hello')
        return Path(runtime.volume_root)

    def call(self, method, **parameters):
        self.sequence += 1; request = make_request(method,parameters); request.request_id = self.sequence
        self.child.stdin.write(frame(request)); self.child.stdin.flush()
        def exact(size):
            data = b''; deadline = time.monotonic()+20
            while len(data) < size:
                self.assertLess(time.monotonic(),deadline,method)
                if not select.select([self.child.stdout],[],[],.05)[0]: continue
                chunk = os.read(self.child.stdout.fileno(),size-len(data)); self.assertTrue(chunk,self.log_path.read_text())
                data += chunk
            return data
        size = struct.unpack('!I',exact(4))[0]; self.assertLessEqual(size,2**20)
        reply = core.Reply.FromString(exact(size)); self.assertEqual(reply.request_id,self.sequence)
        self.assertFalse(reply.HasField('error'),reply)
        (self.root/f'reply-{self.sequence:03}.pb').write_bytes(reply.SerializeToString())
        return result_value(reply)

    def stop(self):
        if self.child:
            self.child.stdin.close(); self.assertEqual(self.child.wait(timeout=15),0)
            self.child.stdout.close(); self.child = None; self.log.close(); self.log = None
            contents = self.log_path.read_text()
            self.assertIn('Storage owner unit:',contents)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'): self.assertNotIn(marker,contents)

    def cleanup(self):
        try: self.stop()
        finally:
            if self.created: self.provision('unmount')

    def test_owner_catalog_v4_requires_exact_stopped_schema_migration(self):
        self.provision('create'); self.created = True
        volume = self.start()
        repo = GitRepo(self.root)
        self.call('git.repo.import',id='schema-repo',path=str(repo.path))
        tree = self.call('git.worktree.create',repo='schema-repo',id='schema-tree',
                         branch='schema-branch',start='main',operation_id='schema-tree')
        self.call('workspace.create',id='schema-workspace',worktree='schema-tree',
                  policy=['workspace.select','files.read','files.write','process.launch'])
        marker = Path(tree['path'])/'migration-persistent.txt'
        marker.write_bytes(b'actual persistent workspace bytes')
        owner = BUILD/'spatial-storage-owner'
        private = self.entry/'owner.pb'
        def migration(flag,success,reason=None):
            result = subprocess.run(['sudo','-n',str(owner),'--config',str(private),flag],
                                    capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
            if reason:self.assertIn(reason,result.stderr)
            self.actions.append(dict(command=[str(owner),flag],exit_code=result.returncode,
                                     stdout=result.stdout,stderr=result.stderr))
            (self.root/'provision-actions.json').write_text(json.dumps(self.actions,indent=2))
        migration('--migrate-v4',False,'requires the broker to be stopped')
        self.stop()
        database = volume/'.storage-owner/catalog.db'
        def catalog(mode):
            # Recreate only the exact predecessor table shape. Policy and object rows remain actual owner records.
            code = ('import hashlib,json,sqlite3,sys;from pathlib import Path;'
                    'p=Path(sys.argv[1]);assert p.name=="catalog.db" and p.parent.name==".storage-owner";'
                    'db=sqlite3.connect(p);mode=sys.argv[2];'
                    'assert mode!="v4" or db.execute("SELECT count(*) FROM copy_destinations").fetchone()[0]==0;'
                    'before={n:db.execute("SELECT * FROM "+n+" ORDER BY rowid").fetchall() for n in ("metadata","domains","objects","actors","snapshots","copy_stages")};'
                    'db.executescript("DROP TABLE copy_destinations;DROP TABLE configured_states;DROP TABLE configured_state_revisions;DROP TABLE configured_state_uses;DROP TABLE copy_exports;DROP TABLE configured_launch_allocations;PRAGMA user_version=4") if mode=="v4" else None;'
                    'out=dict(version=db.execute("PRAGMA user_version").fetchone()[0],rows_sha256=hashlib.sha256(json.dumps(before,sort_keys=True,default=lambda b:b.hex()).encode()).hexdigest(),policy_sha256=hashlib.sha256(db.execute("SELECT policy FROM metadata").fetchone()[0]).hexdigest(),schema=db.execute("SELECT name,sql FROM sqlite_schema WHERE type=\'table\' ORDER BY name").fetchall());db.close();print(json.dumps(out))')
            result = subprocess.run(['sudo','-n',sys.executable,'-c',code,str(database),mode],
                                    capture_output=True,text=True,check=True,timeout=10)
            return json.loads(result.stdout)
        predecessor = catalog('v4')
        self.assertEqual(predecessor['version'],4)
        configuration = storage.StorageOwnerConfig.FromString(
            subprocess.check_output(['sudo','-n','cat',str(private)],timeout=5))
        configuration.broker_pid = os.getpid()
        local = self.root/'schema-owner.pb';local.write_bytes(configuration.SerializeToString());local.chmod(0o600)
        test_config = self.entry/'schema-owner.pb'
        subprocess.run(['sudo','-n','install','-m','600',str(local),str(test_config)],check=True,timeout=5)
        left,right = socket.socketpair()
        try:
            code = 'import os,sys;os.dup2(0,3);os.set_inheritable(3,True);os.execv(sys.argv[1],sys.argv[1:])'
            result = subprocess.run(['sudo','-n',sys.executable,'-c',code,str(owner),
                                     '--channel-fd','3','--config',str(test_config)],
                                    stdin=right,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('exact stopped --migrate-v1/2/3/4',result.stderr)
            (self.root/'schema-startup-refusal.log').write_text(result.stderr)
        finally:left.close();right.close()
        self.assertEqual(catalog('inspect'),predecessor,'Denied ordinary startup mutated catalog data/schema')
        migration('--migrate-v3',False,'exact stopped --migrate-v1/2/3/4')
        self.assertEqual(catalog('inspect'),predecessor,'Wrong migration flag mutated catalog data/schema')
        migration('--migrate-v4',True)
        current = catalog('inspect')
        self.assertEqual(current['version'],6)
        self.assertEqual(current['rows_sha256'],predecessor['rows_sha256'])
        self.assertEqual(current['policy_sha256'],predecessor['policy_sha256'])
        migration('--migrate-v4',False,'does not match the existing version')
        self.assertEqual(catalog('inspect'),current,'Repeated migration flag mutated catalog data/schema')
        self.start()
        self.assertEqual(marker.read_bytes(),b'actual persistent workspace bytes')
        self.assertEqual(self.call('workspace.get',id='schema-workspace')['id'],'schema-workspace')
        self.stop()
        (self.root/'schema-migration-result.json').write_text(json.dumps(
            dict(predecessor=predecessor,current=current,policy_unchanged=True,
                 exact_migration='--migrate-v4',status='passed'),indent=2))

    def test_persistent_identity_git_state_and_fail_closed_recovery(self):
        initial = self.provision('create'); self.created = True
        raw = self.record_bytes(); record = broker.StorageVolumeRecord.FromString(raw)
        (self.root/'persistent-record.pb').write_bytes(raw)
        image_inode = (self.entry/'volume.img').stat().st_ino
        self.provision('create',success=False)
        self.assertEqual(self.record_bytes(),raw)
        volume = self.start()
        repo = GitRepo(self.root)
        imported = self.call('git.repo.import',id='repo',path=str(repo.path))
        work = self.call('git.worktree.create',repo='repo',id='tree',branch='persistent',start='main',operation_id='create')
        workspace = self.call('workspace.create',id='workspace',worktree='tree',policy=['workspace.select','files.read','files.write','process.launch'])
        marker = Path(work['path'])/'operator-checkpoint.txt'; marker.write_text('persistent project data\n')
        marker_inode = marker.stat().st_ino; digest = hashlib.sha256(marker.read_bytes()).hexdigest()
        self.provision('unmount',success=False)
        self.assertEqual(marker.read_text(),'persistent project data\n')
        self.stop(); self.provision('unmount')
        changed = broker.StorageVolumeRecord(); changed.CopyFrom(record); changed.filesystem_id = uuid.uuid4().bytes
        self.replace_record(changed.SerializeToString())
        try:
            failure = self.provision('mount',success=False)
            self.assertIn('Filesystem identity differs',failure.stderr)
            self.assertFalse((self.entry/'runtime.pb').exists())
        finally: self.replace_record(raw)
        resumed = self.provision('mount')
        self.assertEqual(initial['filesystem_id'],resumed['filesystem_id'])
        self.assertEqual(image_inode,(self.entry/'volume.img').stat().st_ino)
        self.start()
        self.assertEqual(self.call('git.repo.import',id='repo',path=str(repo.path)),imported)
        self.assertEqual(self.call('git.worktree.create',repo='repo',id='tree',branch='persistent',start='main',operation_id='create'),work)
        self.assertEqual(self.call('workspace.get',id='workspace'),workspace)
        self.assertEqual(marker.stat().st_ino,marker_inode)
        self.assertEqual(hashlib.sha256(marker.read_bytes()).hexdigest(),digest)
        self.assertEqual(repo.branch(work['path']),'persistent')
        self.stop(); self.provision('unmount')
        (self.root/'result.json').write_text(json.dumps(dict(filesystem_id=initial['filesystem_id'],image_inode=image_inode,
            project_inode=marker_inode,project_sha256=digest,status='passed',volume=str(volume)),indent=2))


if __name__ == '__main__': unittest.main()
