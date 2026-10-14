#!/usr/bin/env python3
"""One real workspace rendered by independent native actors on both hosts."""
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
from google.protobuf import text_format

import actor_session_client_pb2 as actor_client
import core_pb2 as core
import geometry_native_pb2 as witness
import grants_pb2 as grants
import host_actor_controller_pb2 as transitions
import host_session_pb2 as host
import world_session_pb2 as session
import test_world_authority as world_authority
import test_workspace_authority as workspace_authority
from test_workspace_presentation_actor import pose
from harness.wire import frame
from geometry_host_control_handoff import adopt_initial_host_control
from test_geometry_native_crosshost import NativeCrossHostTests
from test_world_identity import WORLD

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'native'))


def exact(fd, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError('Native observer frame deadline')
        part = os.read(fd, count - len(result))
        if not part:
            raise EOFError('Native observer stream ended')
        result.extend(part)
    return bytes(result)


class LocalNativeObserver:
    def __init__(self, fixture):
        self.fixture = fixture
        self.process = None
        self.log = None
        self.ready_valid = False
        self.resource_receipt = b''
        self.unit = None
        self.evidence = None
        self.close_started = False
        fixture.addCleanup(self.close)
        self.sequence = 0
        self.host_sequence = 0
        self.actor_sequence = 0
        self.generation = 0
        root = Path(__file__).resolve().parents[2]
        self.profiles = fixture.root / 'authority-native-profiles'
        self.profiles.mkdir(mode=0o700)
        files = [(fixture.service_cert, 'client.crt'), (fixture.service_key, 'client.key'),
                 (fixture.third_key, 'actor.key')]
        for name, world in fixture.active_worlds.items():
            files.append((world.server_cert, 'server-' + name + '.crt'))
        for source, name in files:
            target = self.profiles / name
            target.write_bytes(Path(source).read_bytes())
            target.chmod(0o600)
        for name, world in fixture.active_worlds.items():
            profile = actor_client.ActorSessionClientTlsConfig(protocol_version=1,
                address=fixture.address, port=world.port, server_name='world.test',
                expected_world_id=world.management('world.info')['world_id'],
                certificate_path=str(self.profiles / 'client.crt'),
                private_key_path=str(self.profiles / 'client.key'),
                server_ca_path=str(self.profiles / ('server-' + name + '.crt')),
                actor_signing_key_path=str(self.profiles / 'actor.key'),
                actor_credential=fixture.third_record.credential, io_timeout_ms=3000)
            path = self.profiles / ('actor-' + name + '.textproto')
            path.write_text(text_format.MessageToString(profile))
            path.chmod(0o600)
        self.evidence = fixture.evidence / 'authority-native-observer'
        self.evidence.mkdir(parents=True, exist_ok=False)
        env = os.environ.copy()
        env['PYTHONPATH'] = ':'.join(filter(None, (env.get('PYTHONPATH', ''), str(root),
            str(root / 'tests/native'), str(root / 'tests/core'))))
        env['SPATIAL_SECURITY_EVIDENCE'] = str(self.evidence)
        env['SPATIAL_TEST_XSERVER'] = os.environ['SPATIAL_TEST_XSERVER']
        env['ASAN_OPTIONS'] = 'detect_leaks=1:abort_on_error=1'
        env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
        unit = 'spatial-m03-resources-m06-geometry-' + os.urandom(16).hex() + '.service'
        self.unit = unit
        executable = root / 'tests/native/geometry_native_host.py'
        bin_dir = WORLD.parent
        argv = [sys.executable, str(root / 'tools/geometry_native_delegate.py'), unit, '--',
            sys.executable, str(root / 'tools/with_adapter_resources.py'), '--', sys.executable,
            str(executable), str(root), str(bin_dir / 'spatial-authority-launch-probe'),
            str(bin_dir / 'spatial-security-auth'), str(bin_dir / 'identityd'),
            str(bin_dir / 'spatial-issuer-client'), str(bin_dir / 'spatial-host-session'), str(self.profiles)]
        self.log = (self.evidence / 'observer.stderr').open('wb')
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, env=env)
        prefix = bytearray()
        deadline = time.monotonic() + 90
        while not prefix.endswith(b'\n'):
            prefix.extend(exact(self.process.stdout.fileno(), 1, deadline))
            if len(prefix) > 65536:
                raise AssertionError('Oversized native resource receipt')
        self.resource_receipt = bytes(prefix)
        (self.evidence / 'observer-resource-launch.json').write_bytes(self.resource_receipt)
        receipt = json.loads(prefix)
        if receipt.get('unit') != unit:
            raise AssertionError('Native resource unit did not match the chosen unit')
        self.ready = self.receive(deadline)
        (self.evidence / 'observer-ready.frame').write_bytes(frame(self.ready))
        if (self.ready.request_id != 0 or not self.ready.HasField('control') or
                len(self.ready.processes) != 2 or self.ready.error or self.ready.pixels or self.ready.cleanup_complete):
            raise AssertionError('Authority native observer did not report readiness')
        self.ready_valid = True
        cursor = adopt_initial_host_control(self.ready.control, expected_request_id=1)
        self.host_sequence = cursor.sequence
        self.generation = cursor.generation
        self.identity = list(self.exchange(identity=True).processes)

    def receive(self, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + 90
        header = exact(self.process.stdout.fileno(), 4, deadline)
        size, = struct.unpack('!I', header)
        if not 0 < size <= 1024 * 1024:
            raise AssertionError('Invalid native observer protobuf size')
        return witness.NativeGeometryWitnessReply.FromString(exact(self.process.stdout.fileno(), size, deadline))

    def exchange(self, **operation):
        self.sequence += 1
        request = witness.NativeGeometryWitnessRequest(request_id=self.sequence, **operation)
        self.process.stdin.write(frame(request))
        self.process.stdin.flush()
        reply = self.receive()
        if reply.request_id != self.sequence or reply.error:
            raise AssertionError('Native observer request failed: ' + str(reply))
        return reply

    def control(self, **operation):
        self.host_sequence += 1
        request = host.HostControlRequest(protocol_version=1, request_id=self.host_sequence,
            generation=self.generation, **operation)
        reply = self.exchange(control=request).control
        if reply.protocol_version != 1 or reply.request_id != self.host_sequence:
            raise AssertionError('Wrong authority observer control reply identity')
        self.generation = reply.generation
        return reply

    def transition(self, world):
        result = self.control(transition=transitions.HostActorTransitionRequest(protocol_version=1,
            transition_id=os.urandom(32), departure_sequence=100 + self.host_sequence,
            destination_world_id=world.management('world.info')['world_id']))
        if not result.accepted:
            raise AssertionError('Authority native actor could not enter shared world: ' + str(result))

    def status(self, revision=None, sequence=None):
        deadline = time.monotonic() + 20
        while True:
            reply = self.control(geometry_status=True)
            if not reply.accepted:
                raise AssertionError('Native geometry status rejected: ' + str(reply))
            value = reply.geometry_status
            if value.available and (revision is None or value.world_revision >= revision) and (
                    sequence is None or value.view_sequence > sequence):
                return value
            if time.monotonic() >= deadline:
                raise AssertionError('Authority native compositor did not display current geometry')
            time.sleep(.1)

    def observation(self):
        self.actor_sequence += 1
        reply = self.control(action=core.ActorActionRequest(seq=self.actor_sequence,
            observe=core.ActorObserveRequest(width=512, height=384)))
        if not reply.accepted or not reply.action.HasField('action') or not reply.action.action.accepted:
            raise AssertionError('Authority native actor observation rejected: ' + str(reply))
        return reply.action.action.observation

    def pixels(self):
        return self.exchange(capture=True).pixels

    def stable_pixels(self):
        deadline = time.monotonic() + 10
        while True:
            before = self.pixels()
            time.sleep(.2)
            after = self.pixels()
            if before == after and (any(after[0::4]) or any(after[1::4]) or any(after[2::4])):
                return after
            if time.monotonic() >= deadline:
                raise AssertionError('Authority native compositor has no stable nonblank baseline')

    def changed(self, before):
        deadline = time.monotonic() + 15
        while True:
            value = self.pixels()
            if value != before:
                return value
            if time.monotonic() >= deadline:
                raise AssertionError('Authority native pixels did not change')
            time.sleep(.1)

    def close(self):
        if self.close_started:
            return
        self.close_started = True
        failures = []
        terminal = []
        process = self.process
        def attempt(label, operation):
            try:
                return operation()
            except BaseException as error:
                failures.append((label, repr(error)))
                return None
        graceful_ok = False
        recovery_attempted = False
        def recover_unit():
            nonlocal recovery_attempted
            if self.unit is None or process is None or recovery_attempted:
                return
            recovery_attempted = True
            recovery = attempt('unit recovery', lambda: subprocess.run(
                ['sudo', '-n', 'systemctl', 'stop', self.unit], capture_output=True, timeout=20))
            if recovery is not None:
                terminal.append({'unit': self.unit, 'recovery_returncode': recovery.returncode,
                                 'stdout': recovery.stdout.decode('utf-8', 'replace'),
                                 'stderr': recovery.stderr.decode('utf-8', 'replace')})
                if recovery.returncode:
                    failures.append(('unit recovery status', repr(terminal[-1])))
        if process is not None and process.poll() is not None and self.ready_valid:
            failures.append(('observer early exit', str(process.returncode)))
        if process is not None and process.poll() is None and self.ready_valid:
            from geometry_process_identity import finish_observer
            graceful_ok = attempt('graceful shutdown', lambda: (
                finish_observer(True, self.exchange, lambda: None), True)[1]) is True
        if not graceful_ok:
            recover_unit()
        cleanup_receipts_valid = False
        if process is not None:
            if process.stdin is not None:
                attempt('stdin close', process.stdin.close)
                process.stdin = None
            try:
                remaining_out, remaining_err = process.communicate(timeout=45)
                terminal.append({'returncode': process.returncode,
                                 'stdout': remaining_out.decode('utf-8', 'replace'),
                                 'stderr': (remaining_err or b'').decode('utf-8', 'replace')})
                records = []
                for line in remaining_out.splitlines():
                    if line:
                        try:
                            records.append(json.loads(line))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                adapter = [item for item in records if 'scope_removed' in item]
                unit_records = [item for item in records if 'unit_after' in item]
                if graceful_ok:
                    if len(adapter) != 1 or len(unit_records) != 1:
                        failures.append(('cleanup receipts', repr(records)))
                    else:
                        unit_record = unit_records[0]
                        cleanup_receipts_valid = (adapter[0].get('scope_removed') is True and
                            type(adapter[0].get('unexpected_population')) is bool and
                            adapter[0].get('unexpected_population') is False and
                            adapter[0].get('exit_code') == 0 and
                            unit_record.get('unit') == self.unit and unit_record.get('cgroup_exists') is False and
                            unit_record.get('query_exit') in (0, 4) and
                            'LoadState=not-found' in unit_record.get('unit_after', '') and
                            'ActiveState=inactive' in unit_record.get('unit_after', ''))
                        if (adapter[0].get('scope_removed') is not True or
                                type(adapter[0].get('unexpected_population')) is not bool or
                                adapter[0].get('unexpected_population') is not False or
                                adapter[0].get('exit_code') != 0):
                            failures.append(('adapter cleanup receipt', repr(adapter[0])))
                        if (unit_record.get('unit') != self.unit or unit_record.get('cgroup_exists') is not False or
                                unit_record.get('query_exit') not in (0, 4) or
                                'LoadState=not-found' not in unit_record.get('unit_after', '') or
                                'ActiveState=inactive' not in unit_record.get('unit_after', '')):
                            failures.append(('unit cleanup receipt', repr(unit_record)))
                if self.evidence is not None:
                    (self.evidence / 'observer-raw.stdout').write_bytes(self.resource_receipt + remaining_out)
                if process.returncode != 0:
                    failures.append(('observer exit', str(process.returncode)))
            except BaseException as error:
                failures.append(('observer wait', repr(error)))
                recover_unit()
                attempt('observer kill', process.kill)
                attempt('observer reap', lambda: process.wait(timeout=10))
            if graceful_ok and not cleanup_receipts_valid:
                recover_unit()
            if process.stdout is not None and not process.stdout.closed:
                attempt('stdout close', process.stdout.close)
        if self.log is not None and not self.log.closed:
            attempt('stderr close', self.log.close)
        if self.evidence is not None and self.evidence.exists():
            attempt('cleanup receipt write', lambda: (self.evidence / 'cleanup-receipts.json').write_text(
                json.dumps(terminal, indent=2) + '\n'))
        if failures:
            raise AssertionError('Authority native observer cleanup failures: ' + repr(failures))



class WorkspaceGeometryNativeCrossHostTests(NativeCrossHostTests):
    workspace_witness = True
    principal = world_authority.WorldAuthorityTests.principal
    scoped_world = world_authority.WorldAuthorityTests.scoped_world
    def terms(self, claims=None, rights=None, depth=3, components=(), delegable=None):
        exact_rights = world_authority.m07_presentation_rights()
        return world_authority.WorldAuthorityTests.terms(
            self, claims=claims, rights=exact_rights if rights is None else rights,
            depth=depth, components=components, delegable=delegable)

    def workspace_terms(self, claims=None, rights=None, depth=3, components=(), delegable=None):
        if rights is None:
            rights = list(workspace_authority.RIGHTS) + [grants.WORKSPACE_PRESENTATION_ATTACH]
        return workspace_authority.WorkspaceAuthorityTests.workspace_terms(
            self, claims=claims, rights=rights, depth=depth, components=components, delegable=delegable)
    workspace = workspace_authority.WorkspaceAuthorityTests.workspace
    launch_process = workspace_authority.WorkspaceAuthorityTests.launch_process
    wait_job = workspace_authority.WorkspaceAuthorityTests.wait_job
    select = workspace_authority.WorkspaceAuthorityTests.select

    def setUp(self):
        super().setUp()
        self.world_info = self.active_worlds['b'].management('world.info')
        self.native_action_sequence = 0

    def grant_workspace_access(self, peer, claims):
        # Keep workspace routing explicit. WorldAuthorityTests owns self.own/delegate.
        reply = peer.call(grants=session.WorldGrantList(workspace='one'))
        self.assertTrue(reply.HasField('grants'), reply)
        page = reply.grants
        parent = next(item for item in page.grants if not item.parent_id and
            item.terms.scope.domain.kind == grants.WORKSPACE and
            item.terms.scope.domain.id == 'one' and grants.WORKSPACE_ACCESS in item.terms.delegable_rights)
        terms = self.workspace_terms(claims=claims, rights=[grants.WORKSPACE_ACCESS], depth=0)
        peer.action_sequence += 1
        delegated = peer.call(delegate=session.WorldGrantDelegate(workspace='one', seq=peer.action_sequence,
            parent_id=parent.id, terms=terms, expected_revision=page.revision))
        self.assertTrue(delegated.HasField('grant'), delegated)
        self.assertEqual(delegated.grant.parent_id, parent.id)
        self.assertEqual(delegated.grant.terms, terms)
        return delegated

    @staticmethod
    def world_actor_id(claims):
        return 'identity:' + claims.issuer_id.hex() + ':' + claims.actor_id.hex()

    def native_observation(self):
        self.native_action_sequence += 1
        reply = self.control(action=core.ActorActionRequest(seq=self.native_action_sequence,
            observe=core.ActorObserveRequest(width=512, height=384)))
        self.assertTrue(reply.accepted, reply)
        self.assertTrue(reply.action.HasField('action'), reply)
        self.assertTrue(reply.action.action.accepted, reply)
        return reply.action.action.observation

    def assert_native_representation(self, representation, present=True):
        for observation, claims in ((self.native_observation(), self.second),
                                     (self.local_observer.observation(), self.third)):
            self.assertEqual(observation.actor, self.world_actor_id(claims))
            self.assertEqual(observation.room, self.active_worlds['b'].management('world.info')['entry_room'])
            matches = [item for item in observation.presentation_views
                       if item.representation_id == representation.representation_id]
            if not present:
                self.assertFalse(matches)
                continue
            self.assertEqual(len(matches), 1)
            observed = matches[0]
            self.assertEqual(observed, representation)
            self.assertFalse(observed.locked)
            self.assertEqual({field.name for field in observed.DESCRIPTOR.fields},
                {'representation_id', 'room_id', 'transform', 'generation', 'locked'})
            geometry_id = 'workspace-presentation.v1/' + representation.representation_id.hex()
            visible = next(item for item in observation.visible if item.id == geometry_id)
            self.assertGreater(visible.visible_pixels, 0)
            self.assertEqual(visible.generation, representation.generation)

    def presentation_row(self, world, representation_id):
        import sqlite3
        from contextlib import closing
        uri = 'file:' + str(world.state_path / 'world.db') + '?mode=ro'
        self.assertEqual(len(representation_id), 16)
        with closing(sqlite3.connect(uri, uri=True)) as db:
            return db.execute('SELECT workspace_id,room_id,pose,generation FROM workspace_presentations WHERE id=?',
                              (representation_id.hex(),)).fetchone()

    def execution_inventory(self):
        import sqlite3
        from contextlib import closing
        uri = 'file:' + str(self.execution / 'execution.db') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            repositories = tuple(db.execute('SELECT id,path,state FROM repositories ORDER BY id'))
            worktrees = tuple(db.execute('SELECT id,repo,path,state FROM worktrees ORDER BY id'))
            workspaces = tuple(db.execute('SELECT id,worktree,repo FROM workspaces ORDER BY id'))
            processes = tuple(db.execute('SELECT id,workspace,state FROM processes ORDER BY id'))
        return repositories, worktrees, workspaces, processes

    def authority_process_identity(self, process):
        pid = process if isinstance(process, int) else process.pid
        proc = Path('/proc') / str(pid)
        stat = (proc / 'stat').read_text()
        fields = stat[stat.rfind(')') + 2:].split()
        executable = (proc / 'exe').stat()
        return (pid, int(fields[19]), os.readlink(proc / 'exe'), executable.st_dev,
                executable.st_ino, tuple(os.readlink(proc / 'ns' / name)
                for name in ('mnt', 'pid', 'user')), (proc / 'cgroup').read_bytes())

    def broker_descendants(self):
        broker_pid = getattr(self.broker, 'pid', None)
        if broker_pid is None:
            broker_pid = self.broker.proc.pid
        todo = [broker_pid]
        found = set()
        while todo:
            parent = todo.pop()
            try:
                children = (Path(f'/proc/{parent}/task/{parent}/children')
                    .read_text().split())
            except (FileNotFoundError, ProcessLookupError):
                continue
            for child in map(int, children):
                if child not in found:
                    found.add(child)
                    todo.append(child)
        return found

    def process_identity(self, token):
        matches = []
        for pid in self.broker_descendants():
            proc = Path(f'/proc/{pid}')
            try:
                argv = (proc / 'cmdline').read_bytes().split(b'\0')
                exe = os.readlink(proc / 'exe')
                if (argv[0] != b'/usr/bin/python3' or len(argv) < 3 or argv[1] != b'-c' or
                        token.encode() not in argv[2] or not Path(exe).name.startswith('python3')):
                    continue
                stat_fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                executable = (proc / 'exe').stat()
                namespaces = tuple(os.readlink(proc / 'ns' / name) for name in ('mnt', 'pid', 'user'))
                cgroup = (proc / 'cgroup').read_bytes()
                matches.append((pid, int(stat_fields[19]), exe, executable.st_dev, executable.st_ino,
                                namespaces, cgroup))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        self.assertEqual(len(matches), 1, f'Expected one exact confined Python payload, found {matches}')
        return matches[0]

    def assert_workspace_process_unchanged(self, process, token, identity, nonce, nonce_identity,
                                           inventory, broker_identity, world_identity):
        self.assertEqual(self.process_identity(token), identity)
        self.assertEqual((nonce.read_bytes(), nonce.stat().st_dev, nonce.stat().st_ino), nonce_identity)
        self.assertEqual(self.execution_inventory(), inventory)
        self.assertEqual(len(inventory[0]), 1)
        self.assertEqual(len(inventory[1]), 1)
        self.assertEqual(inventory[1][0][2], str(self.project.resolve()))
        self.assertEqual(inventory[2], (('one', inventory[1][0][0], inventory[0][0][0]),))
        self.assertEqual(inventory[3], ((process.process_id, 'one', 'running'),))
        self.assertEqual(self.authority_process_identity(self.broker.pid), broker_identity)
        self.assertEqual(self.authority_process_identity(self.active_worlds['b'].proc), world_identity)

    def navigate_actor_to_room(self, peer, room_id):
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                state = peer.call(inspect=session.WorldSessionInspect()).state
                if state.room == room_id:
                    break
                action = peer.act(input=core.ActorInputRequest(axes=[0, 0, -1, 0, 0, 0]))
                self.assertTrue(action.HasField('action'), action)
                time.sleep(.05)
            else:
                raise AssertionError('Actor did not reach the requested room through its portal')
        finally:
            stopped = peer.act(input=core.ActorInputRequest(axes=[0, 0, 0, 0, 0, 0]))
            self.assertTrue(stopped.HasField('action'), stopped)
        final_state = peer.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(final_state.room, room_id)
        self.assertFalse(any(final_state.axes))
        return final_state

    def test_shared_workspace_geometry_changes_both_native_hosts(self):
        world = self.active_worlds['b']
        owner = self.owner_channel('b')
        self.local_observer = LocalNativeObserver(self)
        remote_initial_identity = list(self.exchange(identity=True).processes)
        local_initial_identity = list(self.local_observer.exchange(identity=True).processes)
        broker_identity = self.authority_process_identity(self.broker.pid)
        world_identity = self.authority_process_identity(world.proc)
        initial_world_id = self.active_worlds['a'].management('world.info')['world_id']
        remote_status = self.displayed()
        local_status = self.local_observer.status()
        self.assertEqual(remote_status.world_id, initial_world_id)
        self.assertEqual(local_status.world_id, initial_world_id)
        self.assertEqual(remote_status.actor_id, self.world_actor_id(self.second))
        self.assertEqual(local_status.actor_id, self.world_actor_id(self.third))
        self.assertTrue(self.transition('b').accepted)
        self.local_observer.transition(world)
        expected_world_id = world.management('world.info')['world_id']
        remote_status = self.displayed(after_sequence=remote_status.view_sequence)
        local_status = self.local_observer.status(sequence=local_status.view_sequence)
        self.assertEqual(remote_status.world_id, expected_world_id)
        self.assertEqual(local_status.world_id, expected_world_id)
        self.assertEqual(remote_status.actor_id, self.world_actor_id(self.second))
        self.assertEqual(local_status.actor_id, self.world_actor_id(self.third))
        self.assertEqual(list(self.exchange(identity=True).processes), remote_initial_identity)
        self.assertEqual(list(self.local_observer.exchange(identity=True).processes), local_initial_identity)
        self.assertEqual(self.authority_process_identity(self.broker.pid), broker_identity)
        self.assertEqual(self.authority_process_identity(world.proc), world_identity)
        self.stable_pixels()
        self.select(owner)
        token = 'm07-crosshost-nonce-' + os.urandom(12).hex()
        code = ("from pathlib import Path\nimport time\n" + f"token={token!r}\n"
                "Path('/project/m07-crosshost-nonce').write_text(token)\n"
                "Path('/project/m07-crosshost-ready').touch()\n"
                "while not Path('/project/m07-crosshost-release').exists(): time.sleep(.02)\n")
        process = self.launch_process(owner, code, operation='m07-crosshost-' + os.urandom(8).hex())
        release = self.project / 'm07-crosshost-release'
        self.addCleanup(lambda: release.touch(exist_ok=True))
        self.until(lambda: (self.project / 'm07-crosshost-ready').exists(), bool)
        nonce = self.project / 'm07-crosshost-nonce'
        process_identity = self.process_identity(token)
        nonce_identity = (nonce.read_bytes(), nonce.stat().st_dev, nonce.stat().st_ino)
        self.assertEqual(nonce_identity[0], token.encode())
        repo_path = self.project.resolve()
        self.assertTrue(repo_path.is_dir())
        execution_inventory = self.execution_inventory()
        self.assertEqual(len(execution_inventory[0]), 1)
        self.assertEqual(len(execution_inventory[1]), 1)
        self.assertEqual(execution_inventory[2], (('one', execution_inventory[1][0][0], execution_inventory[0][0][0]),))
        self.assertEqual(execution_inventory[3], ((process.process_id, 'one', 'running'),))
        self.assertEqual(execution_inventory[1][0][2], str(repo_path))
        remote_identity = list(self.exchange(identity=True).processes)
        local_identity = list(self.local_observer.exchange(identity=True).processes)

        viewer = world.connect(self)
        self.assertTrue(self.authenticate(viewer, self.fourth_record, self.fourth_key,
                                          self.fourth_public, pose=pose(3, 0)).HasField('opened'))
        viewer_grants = viewer.call(grants=session.WorldGrantList(workspace='one'))
        self.assertTrue(viewer_grants.HasField('grants'), viewer_grants)
        self.assertFalse(viewer_grants.grants.grants)
        self.denied(viewer.act(workspace_select=core.WorkspaceIdRequest(id='one')), core.FORBIDDEN)
        remote_target_generation = remote_status.presentation_generation
        local_target_generation = local_status.presentation_generation
        self.assertGreater(remote_target_generation, 0)
        self.assertGreater(local_target_generation, 0)
        info = world.management('world.info')
        remote_before = self.stable_pixels()
        local_before = self.local_observer.stable_pixels()
        created = owner.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one',
            room_id=info['entry_room'], transform=pose(.6), expected_world_revision=info['world_revision']))
        self.assertTrue(created.HasField('action'), created)
        self.assertTrue(created.action.HasField('presentation'), created)
        representation = created.action.presentation
        self.assertFalse(representation.locked)
        self.assert_native_representation(representation)
        row = self.presentation_row(world, representation.representation_id)
        self.assertEqual(row[:2], ('one', info['entry_room']))
        self.assertEqual(row[3], 1)
        view = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.assertTrue(any(v.representation_id == representation.representation_id and not v.locked
                            for v in view.presentation_views))
        locked_view = viewer.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        locked = next(v for v in locked_view.presentation_views
                      if v.representation_id == representation.representation_id)
        self.assertTrue(locked.locked)
        self.assertEqual({field.name for field in locked.DESCRIPTOR.fields},
            {'representation_id', 'room_id', 'transform', 'generation', 'locked'})
        # Exact public schema plus full-message string checks cover all metadata.
        self.assertEqual({field.name for field in locked_view.DESCRIPTOR.fields},
            {'world_id', 'actor_id', 'view_id', 'authority_generation', 'authority_service_incarnation',
             'sequence', 'world_revision', 'room', 'camera', 'config', 'panels', 'barriers',
             'portals', 'objects', 'actors', 'presentation_views'})
        def strings(message):
            for field, value in message.ListFields():
                values = value if field.label == field.LABEL_REPEATED else [value]
                for item in values:
                    if field.type == field.TYPE_STRING:
                        yield item
                    elif field.type == field.TYPE_MESSAGE:
                        yield from strings(item)
        filtered_strings = tuple(strings(locked_view))
        self.assertNotIn('one', filtered_strings)
        self.assertFalse(any(str(self.project) in value or 'm07-crosshost-nonce' in value
                             for value in filtered_strings))
        self.assertNotIn(str(self.project).encode(), locked_view.SerializeToString())
        self.assertGreater(view.world_revision, remote_status.world_revision)
        remote_status = self.displayed(revision=view.world_revision, after_sequence=remote_status.view_sequence)
        local_status = self.local_observer.status(revision=view.world_revision, sequence=local_status.view_sequence)
        self.assertEqual(remote_status.presentation_generation, remote_target_generation)
        self.assertEqual(local_status.presentation_generation, local_target_generation)
        self.changed(remote_before)
        self.local_observer.changed(local_before)
        self.assertEqual(list(self.exchange(identity=True).processes), remote_identity)
        self.assertEqual(list(self.local_observer.exchange(identity=True).processes), local_identity)
        self.assert_workspace_process_unchanged(process, token, process_identity, nonce, nonce_identity, execution_inventory,
                                                broker_identity, world_identity)

        destination = 'm07-crosshost-placement-room'
        world.management('world.create', entity={'id': destination, 'kind': 'room', 'label': destination})
        remote_before = self.stable_pixels()
        local_before = self.local_observer.stable_pixels()
        moved = owner.act(presentation_move=core.PresentationMoveRequest(protocol_version=1,
            operation_id=os.urandom(16), representation_id=representation.representation_id,
            room_id=destination, transform=pose(1.1), expected_representation_generation=1))
        self.assertTrue(moved.HasField('action'), moved)
        representation = moved.action.presentation
        self.assertEqual(representation.generation, 2)
        self.assert_native_representation(representation, present=False)
        self.assertEqual(self.presentation_row(world, representation.representation_id)[:2], ('one', destination))
        self.assertEqual(self.presentation_row(world, representation.representation_id)[3], 2)
        view = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        remote_status = self.displayed(revision=view.world_revision, after_sequence=remote_status.view_sequence)
        local_status = self.local_observer.status(revision=view.world_revision, sequence=local_status.view_sequence)
        self.assertEqual(remote_status.presentation_generation, remote_target_generation)
        self.assertEqual(local_status.presentation_generation, local_target_generation)
        self.changed(remote_before)
        self.local_observer.changed(local_before)
        self.assertEqual(list(self.exchange(identity=True).processes), remote_identity)
        self.assertEqual(list(self.local_observer.exchange(identity=True).processes), local_identity)
        self.assert_workspace_process_unchanged(process, token, process_identity, nonce, nonce_identity, execution_inventory,
                                                broker_identity, world_identity)
        before_rejected_row = self.presentation_row(world, representation.representation_id)
        before_rejected_revision = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view.world_revision
        rejected_remote_pixels = self.stable_pixels()
        rejected_local_pixels = self.local_observer.stable_pixels()
        rejected_move = owner.act(presentation_move=core.PresentationMoveRequest(protocol_version=1,
            operation_id=os.urandom(16), representation_id=representation.representation_id,
            room_id=info['entry_room'], transform=pose(.2), expected_representation_generation=2))
        self.denied(rejected_move, core.NOT_VISIBLE)
        self.assertEqual(self.presentation_row(world, representation.representation_id), before_rejected_row)
        self.assertEqual(owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view.world_revision,
                         before_rejected_revision)
        self.assertEqual(self.capture(), rejected_remote_pixels)
        self.assertEqual(self.local_observer.pixels(), rejected_local_pixels)
        owner_state = owner.call(inspect=session.WorldSessionInspect()).state
        q = owner_state.pose.orientation
        forward_x = -2 * (q.x * q.z + q.w * q.y)
        forward_z = -(1 - 2 * (q.x * q.x + q.y * q.y))
        portal_pose = pose(owner_state.pose.position.x + .75 * forward_x,
                           owner_state.pose.position.z + .75 * forward_z)
        world.management('world.create', entity={'id': 'm07-crosshost-outbound', 'kind': 'portal',
            'room': info['entry_room'], 'pose': {'p': [portal_pose.position.x, portal_pose.position.y,
                portal_pose.position.z], 'q': [portal_pose.orientation.x, portal_pose.orientation.y,
                portal_pose.orientation.z, portal_pose.orientation.w]},
            'size': [2, 2], 'destination': {'room': destination,
                'pose': {'p': [.8, 0, -1.5], 'q': [0, 0, 0, 1]}}})
        self.navigate_actor_to_room(owner, destination)
        remote_before = self.stable_pixels()
        local_before = self.local_observer.stable_pixels()
        move_back = owner.act(presentation_move=core.PresentationMoveRequest(protocol_version=1,
            operation_id=os.urandom(16), representation_id=representation.representation_id,
            room_id=info['entry_room'], transform=pose(.2), expected_representation_generation=2))
        self.assertTrue(move_back.HasField('action'), move_back)
        representation = move_back.action.presentation
        self.assertEqual(representation.generation, 3)
        self.assert_native_representation(representation)
        row = self.presentation_row(world, representation.representation_id)
        self.assertEqual(row[:2], ('one', info['entry_room']))
        self.assertEqual(row[3], 3)
        view = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        remote_status = self.displayed(revision=view.world_revision, after_sequence=remote_status.view_sequence)
        local_status = self.local_observer.status(revision=view.world_revision, sequence=local_status.view_sequence)
        self.assertEqual(remote_status.presentation_generation, remote_target_generation)
        self.assertEqual(local_status.presentation_generation, local_target_generation)
        self.changed(remote_before)
        self.local_observer.changed(local_before)
        self.assert_workspace_process_unchanged(process, token, process_identity, nonce, nonce_identity, execution_inventory,
                                                broker_identity, world_identity)
        geometry_before_off = self.capture()
        local_before_off = self.local_observer.pixels()
        world.management('workspace.available', id='one', available=False)
        self.assertFalse(owner.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        self.denied(owner.act(workspace_select=core.WorkspaceIdRequest(id='one')), core.UNAVAILABLE)
        unavailable_view = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.displayed(revision=unavailable_view.world_revision)
        self.local_observer.status(revision=unavailable_view.world_revision)
        self.assertEqual(self.capture(), geometry_before_off)
        self.assertEqual(self.local_observer.pixels(), local_before_off)
        world.management('workspace.available', id='one', available=True)
        available_view = owner.call(geometry_view=session.WorldGeometryViewRequest()).geometry_view
        self.displayed(revision=available_view.world_revision)
        self.local_observer.status(revision=available_view.world_revision)
        self.assertEqual(self.capture(), geometry_before_off)
        self.assertEqual(self.local_observer.pixels(), local_before_off)
        self.assertFalse(owner.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        self.assert_workspace_process_unchanged(process, token, process_identity, nonce, nonce_identity, execution_inventory,
                                                broker_identity, world_identity)
        self.select(owner)
        self.assertTrue(owner.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        self.assertEqual(self.capture(), geometry_before_off)
        self.assertEqual(self.local_observer.pixels(), local_before_off)
        cleared = owner.act(workspace_clear=core.ActorStopRequest())
        self.assertTrue(cleared.HasField('action'), cleared)
        self.assertFalse(owner.call(inspect=session.WorldSessionInspect()).state.HasField('workspace'))
        release.touch()
        self.wait_job(world, process)


for _name in dir(NativeCrossHostTests):
    if _name.startswith('test_'):
        setattr(WorkspaceGeometryNativeCrossHostTests, _name, None)


if __name__ == '__main__':
    suite = unittest.TestSuite([WorkspaceGeometryNativeCrossHostTests(
        'test_shared_workspace_geometry_changes_both_native_hosts')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
