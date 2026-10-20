"""Real retained actors, bounded writers, issuer commits, and revocation during Git."""
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import launch_pb2 as launch
import session_probe_pb2 as probe
from harness.git_fixture import GitRepo
from harness.wire import frame, make_request
import test_storage_session as existing
from test_issuer_client import ClientProcess


_ACK_TIMELINE_LIMIT = 256
_ACK_TIMELINE_ESSENTIAL_RESERVE = 16
_ACK_TIMELINE_GIT_SAMPLE_LIMIT = 64
_ACK_TIMELINE_NOISY_STAGES = frozenset(('git_presence_sample', 'final_barrier'))
_RESOURCE_DIAGNOSTIC_MAX_CGROUPS = 64
_RESOURCE_DIAGNOSTIC_MAX_DEPTH = 4
_RESOURCE_DIAGNOSTIC_FILES = (
    'memory.current', 'memory.max', 'memory.events', 'memory.events.local',
    'pids.current', 'pids.max', 'pids.events', 'cgroup.events',
)


def _resource_number_file(path):
    try:
        value = path.read_text().strip()
    except OSError as error:
        return dict(error=errno.errorcode.get(error.errno, 'OS_ERROR'))
    if not value:
        return None
    try:
        return int(value) if value.isascii() and value.isdigit() else value
    except (TypeError, ValueError):
        return 'UNPARSEABLE'


def _resource_event_file(path):
    try:
        fields = path.read_text().splitlines()
    except OSError as error:
        return dict(error=errno.errorcode.get(error.errno, 'OS_ERROR'))
    values = {}
    for field in fields[:16]:
        parts = field.split()
        if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit():
            continue
        values[parts[0]] = int(parts[1])
    return values


def _snapshot_fixture_resource_cgroups(root):
    """Read only the bounded cgroup subtree owned by this native test's broker."""
    root = Path(root)
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise RuntimeError('Broker resource root is not a canonical absolute path')
    root_stat = root.stat()
    if not root.is_dir() or not (root/'cgroup.controllers').is_file():
        raise RuntimeError('Broker resource root is not a cgroup directory')
    pending = [(root, 0)]
    entries = []
    truncated = False
    while pending:
        path, depth = pending.pop(0)
        if len(entries) >= _RESOURCE_DIAGNOSTIC_MAX_CGROUPS:
            truncated = True
            break
        info = path.stat()
        if info.st_dev != root_stat.st_dev:
            raise RuntimeError('Broker cgroup subtree crossed a device boundary')
        relative = '.' if path == root else str(path.relative_to(root))
        entry = dict(path=relative, device=info.st_dev, inode=info.st_ino,
                     files={})
        procs = path/'cgroup.procs'
        try:
            with procs.open('rb') as stream:
                raw = stream.read(8192)
            entry['cgroup_procs_count'] = len(raw.split())
            entry['cgroup_procs_sample_complete'] = len(raw) < 8192
        except OSError as error:
            entry['cgroup_procs_error'] = errno.errorcode.get(error.errno, 'OS_ERROR')
        for name in _RESOURCE_DIAGNOSTIC_FILES:
            file = path/name
            if name.endswith('.events') or name.endswith('.events.local'):
                value = _resource_event_file(file)
            else:
                value = _resource_number_file(file)
            if value is not None:
                entry['files'][name] = value
        entries.append(entry)
        if depth < _RESOURCE_DIAGNOSTIC_MAX_DEPTH:
            children = []
            try:
                for child in path.iterdir():
                    if child.name in ('cgroup.procs', 'cgroup.threads'):
                        continue
                    if child.is_dir() and not child.is_symlink():
                        children.append(child)
            except OSError as error:
                entry['children_error'] = errno.errorcode.get(error.errno, 'OS_ERROR')
            pending.extend((child, depth+1) for child in sorted(children, key=lambda item: item.name))
    return dict(root_device=root_stat.st_dev, root_inode=root_stat.st_ino,
                cgroup_count=len(entries), truncated=truncated, cgroups=entries)


def _resource_budget_values(budget):
    return dict(memory_bytes=int(budget.memory_bytes), swap_bytes=int(budget.swap_bytes),
                processes=int(budget.processes), cpu_microseconds=int(budget.cpu_microseconds))


def _fixture_resource_limits(configuration, workspace, profile_name):
    resources = configuration.resources
    selected = next((item for item in resources.workspaces if item.workspace == workspace),
                    resources.default_policy)
    profile = next(item.maximum for item in selected.profiles if item.name == profile_name)
    actor = selected.actor_default
    return dict(
        broker=_resource_budget_values(resources.broker_budget),
        manager=_resource_budget_values(resources.manager_budget),
        workspace_total=_resource_budget_values(selected.total),
        actor_default=_resource_budget_values(actor),
        actor_override_count=len(selected.actors),
        actor_override_budgets=[_resource_budget_values(item.maximum) for item in selected.actors],
        requested_profile=profile_name,
        requested_application=_resource_budget_values(profile),
        effective_actor_profile=dict(
            memory_bytes=min(selected.total.memory_bytes, actor.memory_bytes, profile.memory_bytes),
            swap_bytes=min(selected.total.swap_bytes, actor.swap_bytes, profile.swap_bytes),
            processes=min(selected.total.processes, actor.processes, profile.processes),
            cpu_microseconds=min(selected.total.cpu_microseconds, actor.cpu_microseconds,
                                 profile.cpu_microseconds)),
    )


def _record_bounded_ack_timeline(measurements, stage, monotonic_ns, *, essential=False, **fields):
    events = measurements.setdefault('ack_timeline', [])
    event = dict(monotonic_ns=monotonic_ns, stage=stage, **fields)
    if not essential and len(events) >= _ACK_TIMELINE_LIMIT-_ACK_TIMELINE_ESSENTIAL_RESERVE:
        measurements['ack_timeline_truncated'] = True
        measurements['ack_timeline_dropped_count'] = measurements.get('ack_timeline_dropped_count', 0) + 1
        return False
    if len(events) >= _ACK_TIMELINE_LIMIT:
        measurements['ack_timeline_truncated'] = True
        measurements['ack_timeline_dropped_count'] = measurements.get('ack_timeline_dropped_count', 0) + 1
        if essential:
            replacement = next((index for index, existing in enumerate(events)
                                if existing['stage'] in _ACK_TIMELINE_NOISY_STAGES), None)
            if replacement is None:
                return False
            events.pop(replacement)
        else:
            return False
    events.append(event)
    return True


def _cgroup_identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def _has_finite_cpu_quota(value):
    fields = value.split()
    if len(fields) != 2:
        return False
    quota, period = fields
    if quota == 'max' or not quota.isascii() or not quota.isdigit():
        return False
    if not period.isascii() or not period.isdigit():
        return False
    return int(quota) > 0 and int(period) > 0


def _read_cgroup_procs(path):
    """Read one stable cgroup snapshot; skip only a confirmed retired path."""
    for attempt in range(2):
        try:
            before = _cgroup_identity(path)
        except FileNotFoundError:
            return None
        try:
            contents = (path / 'cgroup.procs').read_text()
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENODEV):
                raise
            try:
                after = _cgroup_identity(path)
            except FileNotFoundError:
                return None
            if after != before:
                continue
            if error.errno == errno.ENOENT or attempt == 1:
                raise
            continue
        try:
            after = _cgroup_identity(path)
        except FileNotFoundError:
            return None
        if after == before:
            return contents.split()
    raise RuntimeError('cgroup identity changed during bounded sample')


def _read_process_comm(path):
    """Return None only when Linux reports that the sampled process retired."""
    try:
        return path.read_text().strip()
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ESRCH):
            return None
        raise


class _CgroupSamplePath:
    """Small filesystem stand-in for deterministic retirement regressions."""
    def __init__(self, stats, reads):
        self.stats = iter(stats)
        self.reads = iter(reads)
        self.read_count = 0

    def stat(self):
        result = next(self.stats)
        if isinstance(result, BaseException):
            raise result
        return SimpleNamespace(st_dev=result[0], st_ino=result[1])

    def __truediv__(self, name):
        if name != 'cgroup.procs':
            raise AssertionError(name)
        return self

    def read_text(self):
        self.read_count += 1
        result = next(self.reads)
        if isinstance(result, BaseException):
            raise result
        return result


WRITER = """import json,os,time
from pathlib import Path
payload=b'x'*2**20
completed=0
deadline=time.monotonic()+40
while time.monotonic()<deadline and not Path('/state/data/stop').exists():
    with open('/state/data/payload','wb',buffering=0) as file:
        for i in range(4): file.write(payload)
        os.fsync(file.fileno())
    completed+=1
    with open('/state/data/writes.json','w') as file:
        json.dump(dict(completed=completed,bytes=completed*4*2**20),file)
        file.flush();os.fsync(file.fileno())
"""


class StorageContentionTests(existing.NativeStorageSessionTests):
    def start_coordinator(self, *args, **kwargs):
        result = super().start_coordinator(*args, **kwargs)
        if self._testMethodName == 'test_native_storage_actor_revocation_during_bounded_git_import':
            self._lifecycle_coordinator = result
            # Cleanup callbacks run last-in, first-out. This capture runs before
            # the inherited coordinator cleanup closes and persists its log.
            self.addCleanup(self.capture_revocation_lifecycle)
        return result

    @staticmethod
    def _process_witness(pid):
        witness = dict(pid=pid, start_ticks=None, exit_observed=None, exit_code=None)
        descriptor = None
        try:
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                witness['exit_observed'] = True
                witness['witness'] = 'pidfd_esrch'
                return witness
            except OSError as error:
                witness['witness_error'] = errno.errorcode.get(error.errno, 'os_error')
                return witness
            try:
                stat = Path('/proc/' + str(pid) + '/stat').read_text()
                fields = stat[stat.rfind(')') + 2:].split()
                witness['start_ticks'] = int(fields[19])
                witness['exit_observed'] = bool(select.select([descriptor], [], [], 0)[0])
            except FileNotFoundError:
                witness['exit_observed'] = bool(select.select([descriptor], [], [], 0)[0])
                if not witness['exit_observed']:
                    witness['witness_error'] = 'proc_unavailable'
            except PermissionError:
                witness['witness_error'] = 'permission'
            except (IndexError, ValueError):
                witness['witness_error'] = 'stat_unavailable'
            except OSError as error:
                witness['witness_error'] = errno.errorcode.get(error.errno, 'os_error')
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return witness

    def _revocation_service_witnesses(self):
        services = {}
        world_ids = getattr(self, 'world_ids', None)
        if world_ids is not None:
            for name in ('worldd_pid', 'workspaced_pid'):
                services[name.removesuffix('_pid')] = self._process_witness(getattr(world_ids, name))
        coordinator = getattr(self, '_lifecycle_coordinator', None)
        if coordinator is not None:
            witness = self._process_witness(coordinator.pid)
            witness['exit_code'] = coordinator.poll()
            services['coordinator'] = witness
        return services

    def capture_revocation_start(self):
        # Capture stable PID plus /proc start ticks before any writer or Git load.
        try:
            self._revocation_services_at_workload_start = self._revocation_service_witnesses()
        except Exception as error:
            self._revocation_services_at_workload_start = {}
            record = dict(error=type(error).__name__)
            if isinstance(error, OSError):
                record['errno'] = errno.errorcode.get(error.errno, 'os_error')
            self.measurements['revocation_start_capture_error'] = record
            try:
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                (target/'revocation-start-capture-error.json').write_text(
                    json.dumps(record, indent=2)+'\n')
            except Exception:
                pass

    def capture_revocation_lifecycle(self):
        try:
            self._capture_revocation_lifecycle()
        except Exception as error:
            # Cleanup diagnostics must not replace the original test assertion.
            self.measurements['revocation_lifecycle_capture_error'] = type(error).__name__
            try:
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                error_record = dict(error=type(error).__name__)
                if isinstance(error, OSError):
                    error_record['errno'] = errno.errorcode.get(error.errno, 'os_error')
                (target/'revocation-lifecycle-capture-error.json').write_text(
                    json.dumps(error_record, indent=2)+'\n')
            except Exception:
                pass

    def _capture_revocation_lifecycle(self):
        target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        before_cleanup = self._revocation_service_witnesses()
        at_start = getattr(self, '_revocation_services_at_workload_start', {})
        identity_matches = {}
        for name, current in before_cleanup.items():
            initial = at_start.get(name)
            if initial is None or initial['start_ticks'] is None or current['start_ticks'] is None:
                identity_matches[name] = None
            else:
                identity_matches[name] = (
                    current['pid'] == initial['pid'] and
                    current['start_ticks'] == initial['start_ticks'])
        log_path = getattr(self, 'logpath', None)
        digest = hashlib.sha256()
        relevant = bytearray()
        total = 0
        truncated_line = False
        stderr_error = None
        full_stderr_path = target/'revocation-broker-stderr.log'
        full_stderr_error = None
        if log_path is not None:
            try:
                shutil.copyfile(log_path, full_stderr_path)
                full_stderr_path.chmod(0o600)
                with full_stderr_path.open('rb') as source:
                    while True:
                        chunk = source.readline(4096)
                        if not chunk:
                            break
                        digest.update(chunk)
                        total += len(chunk)
                        if truncated_line:
                            if chunk.endswith(b'\n'):
                                truncated_line = False
                            continue
                        if b'authority_diag ' in chunk and len(relevant) + len(chunk) <= 262144:
                            relevant.extend(chunk)
                        if not chunk.endswith(b'\n'):
                            truncated_line = True
            except OSError as error:
                stderr_error = errno.errorcode.get(error.errno, 'os_error')
                full_stderr_error = stderr_error
        else:
            stderr_error = 'not_available'
            full_stderr_error = stderr_error
        evidence = dict(
            phase='before_fixture_cleanup',
            services_at_workload_start=at_start,
            services_before_cleanup=before_cleanup,
            identity_matches_workload_start=identity_matches,
            application_handles_before_import=getattr(self, '_application_handle_identities', []),
            application_exit_observations=self.measurements.get('application_exit_observations', []),
            broker_kernel_wait=self.measurements.get('broker_kernel_wait'),
            stderr=dict(bytes=total, sha256=digest.hexdigest() if total else None,
                        retained_relevant_bytes=len(relevant),
                        relevant_file='revocation-authority-diags.log',
                        full_file=full_stderr_path.name if full_stderr_error is None and log_path is not None else None,
                        full_file_mode='0600' if full_stderr_error is None and log_path is not None else None,
                        full_file_error=full_stderr_error, read_error=stderr_error))
        try:
            (target/'revocation-authority-diags.log').write_bytes(relevant)
            (target/'revocation-lifecycle.json').write_text(json.dumps(evidence, indent=2)+'\n')
        except OSError as error:
            self.measurements['revocation_lifecycle_capture_error'] = errno.errorcode.get(error.errno, 'os_error')

    def volume_command(self, operation, *arguments):
        if operation=='prepare':arguments+=('--volume-bytes',str(1024*2**20))
        return super().volume_command(operation,*arguments)

    def setUp(self):
        super().setUp()
        self._ack_timeline_enabled = False
        self._ack_final_barrier_phase = False
        self._ack_git_sample_count = 0
        self._ack_git_discovered = False
        original=self.issuer.config_file
        def production_configuration(*arguments,**overrides):
            path=original(*arguments,**overrides)
            if 'io_timeout_ms' not in overrides:
                configuration=text_format.Parse(path.read_text(),issuer.IssuerTlsClientConfig())
                configuration.ClearField('io_timeout_ms')
                path.write_text(text_format.MessageToString(configuration))
            return path
        # This new pressure fixture uses production defaults for every issuer caller.
        # Existing short-deadline fixtures and their tests remain unchanged.
        self.issuer.config_file=production_configuration
        self.measurements = dict(samples=[], phase_complete=False)
        self.addCleanup(self.save_measurements)

    def record_ack_timeline(self, stage, *, essential=False, **fields):
        return _record_bounded_ack_timeline(
            self.measurements,
            stage,
            time.monotonic_ns(),
            essential=essential,
            **fields,
        )

    def record_git_process_sample(self, pids):
        self._ack_git_sample_count += 1
        discovered = bool(pids) and not self._ack_git_discovered
        if discovered:
            self._ack_git_discovered = True
        elif self._ack_git_sample_count > _ACK_TIMELINE_GIT_SAMPLE_LIMIT:
            self.measurements['ack_timeline_truncated'] = True
            self.measurements['ack_timeline_dropped_count'] = (
                self.measurements.get('ack_timeline_dropped_count', 0) + 1
            )
            return
        self.record_ack_timeline(
            'git_discovered' if discovered else 'git_presence_sample',
            essential=discovered,
            sample_index=self._ack_git_sample_count,
            present=bool(pids),
            process_count=len(pids),
        )

    def save_measurements(self):
        (self.volume_root/'contention.json').write_text(json.dumps(self.measurements,indent=2)+'\n')
        for path in self.issuer.root.glob('*.log'):
            (self.volume_root/('issuer-fixture-'+path.name)).write_bytes(path.read_bytes())

    def capture_resource_rejection(self, request_id, operation_id, requested_profile, error):
        """Preserve one bounded pre-cleanup snapshot without replacing the launch failure."""
        target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        broker_root = self.broker_process_group().parent
        record = dict(
            phase='after_failed_application_launch',
            test_method=self._testMethodName,
            request_id=request_id,
            operation_id=operation_id,
            error_type=type(error).__name__,
            resource_limits=_fixture_resource_limits(self.configuration, existing.existing.WORKSPACE, requested_profile),
            production_held_reservation_counters='UNOBSERVED',
            cgroups=_snapshot_fixture_resource_cgroups(broker_root),
        )
        path = target/('resource-rejection-'+self._testMethodName+'.json')
        path.write_text(json.dumps(record, indent=2)+'\n')
        path.chmod(0o600)
        print('storage_resource_rejection_evidence '+json.dumps(
            dict(path=str(path), phase=record['phase'], request_id=request_id,
                 cgroup_count=record['cgroups']['cgroup_count'],
                 truncated=record['cgroups']['truncated'])), file=sys.stderr, flush=True)

    def start_writers(self):
        self.writers = []
        # The native application and writer each reserve 128 MiB. Actor capacity is 256 MiB.
        for number in range(1):
            operation_id=f'writer-{number}'
            try:
                result = self.act(process_launch=core.ProcessLaunchRequest(operation_id=operation_id,
                    profile='development', argv=['/usr/bin/python3','-c',WRITER]))
            except AssertionError as error:
                try:
                    self.capture_resource_rejection(
                        request_id=self.action_sequence, operation_id=operation_id,
                        requested_profile='development', error=error)
                except Exception as diagnostic_error:
                    print('storage_resource_rejection_evidence_error '+json.dumps(
                        dict(error_type=type(diagnostic_error).__name__)),
                        file=sys.stderr, flush=True)
                raise
            self.writers.append(self.execution/'applications'/result.process.process_id/'data')
        self.until(lambda: all((path/'writes.json').exists() for path in self.writers),bool)
        group = self.broker_process_group().parent
        control_names = ('memory.max', 'pids.max', 'cpu.max', 'io.max')
        directories = [group, *(path for path in group.rglob('*') if path.is_dir())]
        controls = {}
        payloads = []
        for path in directories:
            values = {}
            missing = []
            for name in control_names:
                control = path / name
                if control.is_file():
                    values[name] = control.read_text().strip()
                else:
                    values[name] = None
                    missing.append(name)
            if missing:
                # ProcessScope deliberately leaves controllers disabled below its
                # budgeted application scope. Only its populated payload child may
                # lack these files; all limits must exist on that immediate parent.
                parent = path.parent
                self.assertEqual(path.name, 'payload', f'Unexpected missing cgroup controls at {path}: {missing}')
                self.assertIn(parent, directories, f'Payload has no observed budgeted parent: {path}')
                self.assertTrue(all((parent / name).is_file() for name in control_names),
                                f'Payload parent lacks effective controls: {parent}')
                parent_limits = {name: (parent / name).read_text().strip() for name in control_names}
                self.assertNotIn(parent_limits['memory.max'], ('', 'max'),
                                 f'Payload ancestor has no memory ceiling: {parent_limits}')
                self.assertNotIn(parent_limits['pids.max'], ('', 'max'),
                                 f'Payload ancestor has no process ceiling: {parent_limits}')
                self.assertTrue(_has_finite_cpu_quota(parent_limits['cpu.max']),
                                f'Payload ancestor has no finite CPU ceiling: {parent_limits}')
                self.assertTrue(parent_limits['io.max'],
                                f'Payload ancestor has no I/O ceiling: {parent_limits}')
                self.assertEqual(set(missing), set(control_names), f'Partial controller exposure at {path}')
                payload_pids = _read_cgroup_procs(path)
                self.assertTrue(payload_pids, f'Expected application processes in payload: {path}')
                payloads.append(str(path.relative_to(group)))
            controls[str(path.relative_to(group))] = values
        self.assertTrue(payloads, 'Expected a nested application payload under budgeted scopes')
        self.measurements['payloads_without_child_controls'] = payloads
        self.measurements['controls'] = controls
        self.sample_writes()
        return group

    def sample_writes(self):
        values=[]
        for path in self.writers:
            # A live producer can replace its JSON contents between reads.
            def read():
                try: return json.loads((path/'writes.json').read_text())
                except json.JSONDecodeError: return None
            values.append(self.until(read,lambda value:value is not None))
        self.measurements['samples'].append(dict(time=time.monotonic(),writers=values))
        self.save_measurements()
        return values

    def issuer_operation(self, client, operation):
        started=time.monotonic()
        if not self._ack_timeline_enabled:
            reply=self.issuer.execute(client,self.issuer.challenge(client,operation))
        else:
            challenge=self.issuer.challenge(client,operation)
            request_started_ns=time.monotonic_ns()
            reply=None
            try:
                reply=self.issuer.execute(client,challenge)
            finally:
                reply_received_ns=time.monotonic_ns()
                action=operation.WhichOneof('action')
                if action=='identity_revoke':
                    security_revision=None
                    if reply is not None and reply.HasField('identity_status'):
                        security_revision=reply.identity_status.security_revision
                    self.record_ack_timeline(
                        'revocation_request',
                        essential=True,
                        request_started_ns=request_started_ns,
                        reply_received_ns=reply_received_ns,
                        reply_received=reply is not None,
                        reply_has_error=bool(reply is not None and reply.HasField('error')),
                        security_revision=security_revision,
                    )
                elif self._ack_final_barrier_phase and action=='barrier_read':
                    status=None
                    if reply is not None and reply.HasField('authority_barrier'):
                        status=reply.authority_barrier
                    applied_revisions=[] if status is None else [
                        item.applied_revision for item in status.obligations
                    ]
                    self.record_ack_timeline(
                        'final_barrier',
                        request_started_ns=request_started_ns,
                        reply_received_ns=reply_received_ns,
                        request_revision=operation.barrier_read.revision,
                        reply_received=reply is not None,
                        revision=None if status is None else status.revision,
                        participant_count=None if status is None else status.participant_count,
                        pending_count=None if status is None else status.pending_count,
                        complete=None if status is None else status.complete,
                        applied_revisions=applied_revisions,
                        applied_obligation_count=sum(
                            value >= operation.barrier_read.revision for value in applied_revisions
                        ),
                    )
        elapsed=time.monotonic()-started
        self.assertFalse(reply.HasField('error'),reply)
        self.measurements.setdefault('issuer_operations',[]).append(dict(
            operation=operation.WhichOneof('action'),seconds=elapsed,reply=reply.SerializeToString().hex()))
        self.save_measurements()
        self.assertLess(elapsed,5,'Issuer operation exceeded the accepted production deadline')
        return reply

    def default_client(self):
        # This new performance check uses the production default. Fault fixtures keep 700 ms.
        self.issuer.peer.close()
        path=self.issuer.config_file()
        configuration=text_format.Parse(path.read_text(),issuer.IssuerTlsClientConfig())
        configuration.ClearField('io_timeout_ms')
        path.write_text(text_format.MessageToString(configuration))
        self.assertFalse(configuration.HasField('io_timeout_ms'))
        client=ClientProcess(path,self.issuer.root/'contention-client.log')
        self.issuer.connections.append(client)
        return client

    def durable_updates(self, client):
        before=self.sample_writes()
        policy=self.issuer.policy(client)
        for number in range(3):
            operation=issuer.IssuerOperation(policy_set=issuer.PolicySet(policy=policy.policy,
                expected_revision=policy.revision))
            policy=self.issuer_operation(client,operation).policy
            with sqlite3.connect('file:'+str(self.issuer.state/'identity.db')+'?mode=ro',uri=True) as db:
                revision=db.execute('SELECT revision FROM issuer').fetchone()[0]
            self.assertEqual(revision,policy.revision,'Acknowledged policy was not independently committed')
            self.sample_writes()
        self.measurements['durable_policy_revision']=policy.revision
        self.until(self.sample_writes,lambda values:all(a['completed']>b['completed'] for a,b in zip(values,before)))

    def revoke_actor(self, client, kind):
        target=self.claims.actor_id if kind==issuer.ACTOR_TARGET else self.claims.credential_id
        operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
            target=issuer.IdentityReference(kind=kind,id=target),expected_security_revision=0,
            reason='bounded storage contention regression'))
        result=self.issuer_operation(client,operation)
        self.assertTrue(result.HasField('identity_status'),result)
        return result.identity_status.security_revision

    def barrier(self, client, revision):
        reply=self.issuer_operation(client,issuer.IssuerOperation(barrier_read=issuer.AuthorityBarrierRead(revision=revision)))
        self.assertTrue(reply.HasField('authority_barrier'),reply)
        return reply.authority_barrier

    @staticmethod
    def _proc_observation(path, *, limit=4096):
        try:
            value = path.read_text()
            return dict(state='observed', value=value[:limit], truncated=len(value) > limit)
        except OSError as error:
            return dict(state='unavailable', errno=errno.errorcode.get(error.errno, 'OS_ERROR'))

    def _application_process_identity(self, pid, pidfd):
        proc = Path('/proc') / str(pid)
        result = dict(pid=pid, pidfd=pidfd, sample_monotonic_ns=time.monotonic_ns())
        stat = self._proc_observation(proc/'stat')
        result['stat'] = stat
        if stat['state'] == 'observed':
            try:
                fields = stat['value'][stat['value'].rfind(')') + 2:].split()
                result['state'] = fields[0]
                result['start_ticks'] = int(fields[19])
            except (IndexError, ValueError):
                result['stat_parse'] = 'malformed'
        for name in ('comm', 'cgroup', 'status'):
            result[name] = self._proc_observation(proc/name, limit=8192)
        try:
            result['exe'] = dict(state='observed', value=os.readlink(proc/'exe')[:4096])
        except OSError as error:
            result['exe'] = dict(state='unavailable', errno=errno.errorcode.get(error.errno, 'OS_ERROR'))
        namespaces = {}
        for name in ('pid', 'mnt', 'user', 'cgroup', 'net', 'ipc', 'uts', 'time'):
            try:
                namespaces[name] = dict(state='observed', value=os.readlink(proc/'ns'/name)[:128])
            except OSError as error:
                namespaces[name] = dict(state='unavailable', errno=errno.errorcode.get(error.errno, 'OS_ERROR'))
        result['namespaces'] = namespaces
        status = result['status']
        if status['state'] == 'observed':
            result['nspid'] = next((line.split()[1:] for line in status['value'].splitlines()
                                    if line.startswith('NSpid:')), None)
        if pidfd is not None:
            result['pidfd_ready'] = bool(select.select([pidfd], [], [], 0)[0])
        return result

    def pins(self, group):
        pids={int(pid) for path in [group,*(p for p in group.rglob('*') if p.is_dir())]
              for pid in (path/'cgroup.procs').read_text().split()}
        pids.discard(self.world_ids.workspaced_pid)
        # Before administrative work starts, the other populated groups are application scopes.
        app_pids=sorted(pids)
        self.assertTrue(app_pids,dict(all_pids=app_pids))
        pins=[]
        identities=[]
        for pid in app_pids:
            before = self._application_process_identity(pid, None)
            try:
                pin = os.pidfd_open(pid)
            except OSError as error:
                identities.append(dict(pid=pid, pidfd_state='unavailable',
                                       errno=errno.errorcode.get(error.errno, 'OS_ERROR'), before=before))
                self.measurements['application_handles_before_import'] = identities
                self.save_measurements()
                raise
            pins.append(pin)
            self.addCleanup(os.close, pin)
            after = self._application_process_identity(pid, pin)
            before_ticks=before.get('start_ticks')
            after_ticks=after.get('start_ticks')
            identities.append(dict(pid=pid, pidfd=pin, pidfd_state='retained', before=before, after=after,
                                   start_ticks_match=(before_ticks == after_ticks if before_ticks is not None and
                                                      after_ticks is not None else None)))
        self._application_pidfds = pins
        self._application_handle_identities = identities
        self.measurements['application_pids']=app_pids
        self.measurements['application_handles_capture_phase']='before_git_import'
        self.measurements['application_handles_before_import'] = identities
        self.save_measurements()
        self.assertEqual(select.select(pins,[],[],0)[0],[])
        return pins

    def _application_exit_observations(self, exited_fds, sample_ns):
        exited = set(exited_fds)
        records = []
        for identity in getattr(self, '_application_handle_identities', []):
            pin = identity['pidfd']
            ready = pin in exited
            records.append(dict(pid=identity['pid'], pidfd=pin,
                                exit_state='exited' if ready else 'live_at_sample',
                                sample_monotonic_ns=sample_ns,
                                identity=identity.get('after', {})))
        return records

    def finish_checked(self):
        stopped=self.request(stop_world=self.owner).cleanup
        self.assertEqual(stopped.stage,launch.OWNED_SCOPES_STOPPED)
        (self.volume_root/'world-cleanup.pb').write_bytes(stopped.SerializeToString())
        self.finish_session()
        (self.volume_root/'broker-cleanup.pb').write_bytes(self.checked_cleanup().SerializeToString())

    def test_native_storage_actor_revocation_and_durable_issuer_under_writes(self):
        self.held_application();group=self.start_writers();pins=self.pins(group)
        client=self.default_client();self.durable_updates(client)
        revision=self.revoke_actor(client,issuer.ACTOR_TARGET)
        completed=self.until(lambda:self.barrier(client,revision),lambda value:value.complete)
        self.assertEqual(completed.pending_count,0)
        self.assertEqual(select.select(pins,[],[],0)[0],pins,'Revocation completed before application exit')
        self.measurements['revocation_complete']=True
        self.assertIsNone(self.coordinator.poll())
        self.finish_checked()

    def test_native_storage_credential_revocation_preserves_execution_under_writes(self):
        self.held_application();group=self.start_writers();pins=self.pins(group)
        client=self.default_client();self.durable_updates(client)
        revision=self.revoke_actor(client,issuer.CREDENTIAL_TARGET)
        self.until(lambda:self.barrier(client,revision),lambda value:value.complete)
        self.released()
        before=self.sample_writes()
        after=self.until(self.sample_writes,lambda values:all(a['completed']>b['completed'] for a,b in zip(values,before)))
        self.assertEqual(select.select(pins,[],[],0)[0],[],'Credential loss killed actor-owned execution')
        self.measurements['credential_loss_execution_continues']=True
        self.finish_checked()

    def test_native_storage_actor_revocation_during_bounded_git_import(self):
        self._ack_timeline_enabled=True
        repository=GitRepo(self.volume_root)
        with (repository.path/'large.bin').open('wb') as file:
            for number in range(80):file.write(os.urandom(2**20))
            file.flush();os.fsync(file.fileno())
        repository.run('config','core.fsync','all')
        repository.run('add','large.bin');repository.run('commit','-m','bounded import witness')
        self.issuer.peer.close();self.issuer.peer=self.issuer.connect()
        self.held_application();self.capture_revocation_start();group=self.start_writers();pins=self.pins(group)
        client=self.default_client()
        self.coordinator_sequence+=1
        request=probe.SessionProbeRequest(protocol_version=1,request_id=self.coordinator_sequence,
            broker=make_request('git.repo.import',dict(id='pressure-import',path=str(repository.path))))
        self.coordinator.stdin.write(frame(request));self.coordinator.stdin.flush()
        self.record_ack_timeline('git_import_request_sent',essential=True,request_id=request.request_id)
        def git_running():
            values=[]
            for path in group.rglob('authority-*'):
                pids=_read_cgroup_procs(path)
                if pids is None:continue # A confirmed retired cgroup is absent from this independent sample.
                for pid in pids:
                    comm = _read_process_comm(Path('/proc/'+pid+'/comm'))
                    if comm == 'git': values.append(int(pid))
            self.record_git_process_sample(values)
            return values
        def observe_git():
            values=git_running()
            if not values and select.select([self.coordinator.stdout],[],[],0)[0]:
                reply=self.message(self.coordinator.stdout,probe.SessionProbeReply)
                (self.volume_root/'early-git-import-reply.pb').write_bytes(reply.SerializeToString())
                self.fail('Git import returned before the pending-worker witness: '+str(reply))
            return values
        observed=self.until(observe_git,bool)
        self.measurements['git_pids_at_revocation']=observed
        self.action_sequence+=1
        nested=self.actor_call(self.stream,action=core.ActorActionRequest(seq=self.action_sequence,
            process_launch=core.ProcessLaunchRequest(operation_id='during-import',profile='development',
                argv=['/usr/bin/python3','-c',"from pathlib import Path;Path('/project/nested-launch').touch()"])))
        self.assertTrue(nested.HasField('error'),nested)
        self.assertEqual(nested.error.code,core.BUSY,nested)
        self.assertFalse((self.project/'nested-launch').exists())
        with sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processes WHERE operation=?',('during-import',)).fetchone()[0],0)
        self.measurements['nested_execution_rejected_without_effect']=True
        revision=self.revoke_actor(client,issuer.ACTOR_TARGET)
        started_ns=time.monotonic_ns()
        started=time.monotonic()
        exited=select.select(pins,[],[],2)[0]
        exit_sample_ns=time.monotonic_ns()
        # Select may report the first exit. Check each retained handle within the same bound.
        while len(exited)<len(pins) and time.monotonic()-started<2:
            time.sleep(.01);exited=select.select(pins,[],[],0)[0];exit_sample_ns=time.monotonic_ns()
        elapsed_seconds=time.monotonic()-started
        git_still_running=git_running()
        pending_reply=not bool(select.select([self.coordinator.stdout],[],[],0)[0])
        exit_observations=self._application_exit_observations(exited,exit_sample_ns)
        self.measurements['application_exit_observations']=exit_observations
        self.measurements['exit_during_git']=dict(seconds=elapsed_seconds,exited=len(exited),expected=len(pins),
            git_still_running=git_still_running,pending_reply=pending_reply,
            handles=exit_observations)
        self.record_ack_timeline(
            'application_exit_sample',
            essential=True,
            sample_started_ns=started_ns,
            sample_seconds=elapsed_seconds,
            exited=len(exited),
            expected=len(pins),
            git_process_count=len(git_still_running),
            pending_reply=pending_reply,
        )
        inspect = r'''import json, os, sys
from pathlib import Path
MAX_OUTPUT = 262144
MAX_PROCESSES = 12
MAX_THREADS = 32
MAX_FDS_PER_PROCESS = 24
MAX_TEXT = 1024
class CappedStderr:
    def __init__(self): self.remaining = 8192
    def write(self, value):
        kept = value[:self.remaining]
        self.remaining -= len(kept)
        return len(value)
    def flush(self): pass
sys.stderr = CappedStderr()
broker_pid = int(sys.argv[1])
expected_exe = str(Path(sys.argv[2]).resolve())
applications = json.loads(sys.argv[3])
records = [{'pid': broker_pid, 'role': 'workspaced', 'expected_exe': expected_exe}]
records.extend({'pid': item['pid'], 'role': 'application', 'start_ticks': item.get('start_ticks'),
                'pidfd': item.get('pidfd')} for item in applications if len(records) < MAX_PROCESSES)

def read(path, limit=MAX_TEXT):
    try:
        value = Path(path).read_text()
        return {'state': 'observed', 'value': value[:limit], 'truncated': len(value) > limit}
    except OSError as error:
        return {'state': 'unavailable', 'errno': getattr(error, 'errno', None),
                'error': type(error).__name__}

def link(path):
    try:
        value = os.readlink(path)
        return {'state': 'observed', 'value': value[:512], 'truncated': len(value) > 512}
    except OSError as error:
        return {'state': 'unavailable', 'errno': getattr(error, 'errno', None),
                'error': type(error).__name__}

def start_ticks(stat_text):
    try:
        return int(stat_text[stat_text.rfind(')') + 2:].split()[19])
    except (IndexError, ValueError):
        return None

result = {'status': 'complete', 'truncated': False, 'processes': [], 'thread_count': 0}
thread_budget = MAX_THREADS
for item in records:
    pid = item['pid']
    proc = Path('/proc') / str(pid)
    exe = link(proc/'exe')
    stat = read(proc/'stat')
    row = {'pid': pid, 'pidfd': item.get('pidfd'), 'role': item['role'], 'exe': exe, 'stat': stat,
           'start_ticks': start_ticks(stat.get('value', '')) if stat['state'] == 'observed' else None,
           'cgroup': read(proc/'cgroup', 8192)}
    if item['role'] == 'workspaced' and exe.get('value') != expected_exe:
        row['identity_state'] = 'executable_mismatch'
        result['processes'].append(row)
        continue
    if item['role'] == 'application' and item.get('start_ticks') is not None and row['start_ticks'] != item['start_ticks']:
        row['identity_state'] = 'retired_or_pid_reused'
        result['processes'].append(row)
        continue
    row['identity_state'] = 'matched'
    try:
        tids = sorted((int(path.name) for path in (proc/'task').iterdir() if path.name.isdigit()))
    except OSError as error:
        tids = []
        row['thread_list_error'] = {'state':'unavailable','errno':getattr(error,'errno',None),
                                    'error':type(error).__name__}
    threads = []
    for tid in tids[:thread_budget]:
        task = proc/'task'/str(tid)
        syscall = read(task/'syscall', 512)
        thread = {'tid': tid, 'stat': read(task/'stat'), 'wchan': read(task/'wchan', 128),
                  'syscall': syscall, 'stack': read(task/'stack', 1024)}
        if syscall['state'] == 'observed':
            parts = syscall['value'].split()
            if parts:
                try: thread['syscall_number'] = int(parts[0], 0)
                except ValueError: thread['syscall_number'] = None
                if len(parts) > 1:
                    try: thread['first_argument'] = int(parts[1], 0)
                    except ValueError: thread['first_argument'] = None
                fdatasync_nr = {'x86_64': 75, 'aarch64': 83}.get(os.uname().machine)
                if fdatasync_nr is not None and thread.get('syscall_number') == fdatasync_nr:
                    thread['fdatasync_fd'] = thread.get('first_argument')
        threads.append(thread)
    thread_budget -= len(threads)
    if len(tids) > len(threads): result['truncated'] = True
    row['threads'] = threads
    try:
        fd_paths = sorted((path for path in (proc/'fd').iterdir() if path.name.isdigit()), key=lambda path:int(path.name))
    except OSError as error:
        fd_paths = []
        row['fd_list_error'] = {'state':'unavailable','errno':getattr(error,'errno',None),
                                 'error':type(error).__name__}
    fd_by_number = {int(path.name):path for path in fd_paths}
    active_numbers = {thread.get('fdatasync_fd') for thread in threads if isinstance(thread.get('fdatasync_fd'), int)}
    selected_numbers = list(dict.fromkeys([int(path.name) for path in fd_paths[:MAX_FDS_PER_PROCESS]] +
                                          sorted(number for number in active_numbers if number in fd_by_number)))
    fds = [{'fd':number, **link(fd_by_number[number])} for number in selected_numbers]
    if len(fd_paths) > len(fds): result['truncated'] = True
    row['fds'] = fds
    active = [thread.get('fdatasync_fd') for thread in threads if 'fdatasync_fd' in thread]
    row['fdatasync_targets'] = [{'fd': fd, 'target': next((entry for entry in fds if entry['fd'] == fd),
                                                          {'state':'not_captured'})} for fd in active]
    result['processes'].append(row)
    result['thread_count'] += len(threads)

encoded = json.dumps(result, separators=(',', ':'))
while len(encoded.encode()) > MAX_OUTPUT and result['processes']:
    result['truncated'] = True
    last = result['processes'][-1]
    if last.get('fds'):
        last['fds'].pop()
    elif last.get('threads'):
        last['threads'].pop()
        result['thread_count'] -= 1
    else:
        removed = result['processes'].pop()
        result['thread_count'] -= len(removed.get('threads', []))
    encoded = json.dumps(result, separators=(',', ':'))
if len(encoded.encode()) > MAX_OUTPUT:
    encoded = json.dumps({'status':'output_limit','truncated':True,'process_count':len(records)})
print(encoded)
'''
        affected = [item for item in getattr(self, '_application_handle_identities', [])
                    if item.get('pidfd') is not None and
                    not select.select([item['pidfd']], [], [], 0)[0]]
        snapshot_args = ['sudo','-n',sys.executable,'-c',inspect,str(self.world_ids.workspaced_pid),
                         str(self.build/'workspaced'),json.dumps([
                             dict(pid=item['pid'],pidfd=item['pidfd'],
                                  start_ticks=item.get('after',{}).get('start_ticks')) for item in affected])]
        try:
            snapshot=subprocess.run(snapshot_args,capture_output=True,text=True,timeout=5)
            self.measurements['broker_kernel_wait']=dict(exit_code=snapshot.returncode,stdout=snapshot.stdout[:262144],
                                                          stderr=snapshot.stderr[:8192],
                                                          affected_pids=[item['pid'] for item in affected],
                                                          output_truncated=len(snapshot.stdout)>262144 or len(snapshot.stderr)>8192)
        except subprocess.TimeoutExpired as error:
            def output_text(value, limit):
                if isinstance(value, bytes): value=value.decode(errors='replace')
                return (value or '')[:limit]
            self.measurements['broker_kernel_wait']=dict(state='timeout',timeout_seconds=5,
                stdout=output_text(error.stdout,262144),stderr=output_text(error.stderr,8192),
                affected_pids=[item['pid'] for item in affected])
        self.save_measurements()
        # Drain the real pending operation before reporting its independent lifetime assertion.
        deadline=time.monotonic()+305
        while not select.select([self.coordinator.stdout],[],[],.1)[0]:
            self.assertLess(time.monotonic(),deadline,'Bounded import reply deadline')
            self.barrier(client,revision)
        reply=self.message(self.coordinator.stdout,probe.SessionProbeReply)
        reply_received_ns=time.monotonic_ns()
        self.record_ack_timeline(
            'git_import_reply_received',
            essential=True,
            reply_received_ns=reply_received_ns,
            request_id=reply.request_id,
            request_id_matches=reply.request_id==request.request_id,
            has_error=reply.HasField('error'),
            result_has_error=reply.HasField('result') and reply.result.HasField('error'),
        )
        self.assertEqual(reply.request_id,request.request_id)
        (self.volume_root/'git-import-reply.pb').write_bytes(reply.SerializeToString())
        self.assertFalse(reply.HasField('error'),reply)
        self.assertFalse(reply.result.HasField('error'),reply)
        self._ack_final_barrier_phase=True
        self.record_ack_timeline('final_barrier_wait_started',essential=True)
        try:
            completed=self.until(lambda:self.barrier(client,revision),lambda value:value.complete)
        except Exception as error:
            self.record_ack_timeline('final_barrier_failed',essential=True,error_type=type(error).__name__)
            raise
        self.record_ack_timeline(
            'final_barrier_complete',
            essential=True,
            revision=completed.revision,
            participant_count=completed.participant_count,
            pending_count=completed.pending_count,
            complete=completed.complete,
            applied_revisions=[item.applied_revision for item in completed.obligations],
            applied_obligation_count=sum(
                item.applied_revision >= revision for item in completed.obligations
            ),
        )
        self.finish_checked()
        self.assertTrue(self.measurements['exit_during_git']['pending_reply'],'Import finished before the concurrent lifetime witness')
        self.assertEqual(len(exited),len(pins),'Busy administrative Git delayed actor revocation')


class CgroupCpuQuotaValidationTests(unittest.TestCase):
    def test_finite_quota_and_period_are_accepted(self):
        self.assertTrue(_has_finite_cpu_quota('100000 100000'))

    def test_unlimited_quota_is_rejected(self):
        self.assertFalse(_has_finite_cpu_quota('max 100000'))

    def test_malformed_quota_values_are_rejected(self):
        for value in ('', '100000', '100000 100000 extra', 'quota 100000', '100000 period'):
            with self.subTest(value=value):
                self.assertFalse(_has_finite_cpu_quota(value))

    def test_zero_quota_or_period_is_rejected(self):
        for value in ('0 100000', '100000 0', '0 0'):
            with self.subTest(value=value):
                self.assertFalse(_has_finite_cpu_quota(value))


class _ProcessCommSamplePath:
    """A deterministic stand-in for one /proc/<pid>/comm read."""
    def __init__(self, result):
        self.result = result

    def read_text(self):
        if isinstance(self.result, OSError):
            raise self.result
        return self.result


class ProcessCommReadingTests(unittest.TestCase):
    def test_normal_comm_is_trimmed(self):
        self.assertEqual(_read_process_comm(_ProcessCommSamplePath('git\n')), 'git')

    def test_enoent_means_process_retired(self):
        path = _ProcessCommSamplePath(FileNotFoundError(errno.ENOENT, 'process retired'))
        self.assertIsNone(_read_process_comm(path))

    def test_esrch_means_process_retired(self):
        path = _ProcessCommSamplePath(ProcessLookupError(errno.ESRCH, 'process retired'))
        self.assertIsNone(_read_process_comm(path))

    def test_eacces_propagates(self):
        path = _ProcessCommSamplePath(PermissionError(errno.EACCES, 'access denied'))
        with self.assertRaises(OSError) as raised:
            _read_process_comm(path)
        self.assertEqual(raised.exception.errno, errno.EACCES)

    def test_eio_propagates(self):
        path = _ProcessCommSamplePath(OSError(errno.EIO, 'read failure'))
        with self.assertRaises(OSError) as raised:
            _read_process_comm(path)
        self.assertEqual(raised.exception.errno, errno.EIO)


class AckTimelineRecorderTests(unittest.TestCase):
    def test_ack_timeline_stays_bounded_and_marks_truncation(self):
        measurements={}
        for index in range(_ACK_TIMELINE_LIMIT):
            _record_bounded_ack_timeline(
                measurements,'critical_context',index,essential=True,request_index=index
            )
        for index in range(2):
            _record_bounded_ack_timeline(measurements,'final_barrier',index)
        self.assertEqual(len(measurements['ack_timeline']),_ACK_TIMELINE_LIMIT)
        self.assertTrue(measurements['ack_timeline_truncated'])
        self.assertEqual(measurements['ack_timeline_dropped_count'],2)

    def test_essential_ack_event_survives_a_full_sample_buffer(self):
        measurements={}
        for index in range(_ACK_TIMELINE_LIMIT-_ACK_TIMELINE_ESSENTIAL_RESERVE):
            _record_bounded_ack_timeline(measurements,'final_barrier',index,request_index=index)
        for index in range(_ACK_TIMELINE_ESSENTIAL_RESERVE):
            _record_bounded_ack_timeline(
                measurements,'critical_context',index,essential=True,request_index=index
            )
        accepted=_record_bounded_ack_timeline(
            measurements,'git_import_reply_received',_ACK_TIMELINE_LIMIT,essential=True,request_id=7
        )
        self.assertTrue(accepted)
        self.assertEqual(len(measurements['ack_timeline']),_ACK_TIMELINE_LIMIT)
        self.assertEqual(measurements['ack_timeline'][-1]['stage'],'git_import_reply_received')
        self.assertTrue(measurements['ack_timeline_truncated'])

    def test_late_first_git_discovery_survives_sample_truncation(self):
        case=object.__new__(StorageContentionTests)
        case.measurements={}
        case._ack_git_sample_count=0
        case._ack_git_discovered=False
        for _ in range(_ACK_TIMELINE_GIT_SAMPLE_LIMIT+1):
            case.record_git_process_sample([])
        case.record_git_process_sample([123])
        timeline=case.measurements['ack_timeline']
        self.assertTrue(case.measurements['ack_timeline_truncated'])
        self.assertEqual(timeline[-1]['stage'],'git_discovered')
        self.assertTrue(timeline[-1]['present'])
        self.assertEqual(timeline[-1]['process_count'],1)


class CgroupProcessSamplingTests(unittest.TestCase):
    def test_stable_cgroup_returns_process_ids(self):
        path = _CgroupSamplePath([(1, 10), (1, 10)], ['101\n202\n'])
        self.assertEqual(_read_cgroup_procs(path), ['101', '202'])

    def test_absent_cgroup_is_retired(self):
        path = _CgroupSamplePath([FileNotFoundError(errno.ENOENT, 'gone')], [])
        self.assertIsNone(_read_cgroup_procs(path))

    def test_enoent_with_confirmed_retirement_is_ignored(self):
        path = _CgroupSamplePath([(1, 10), FileNotFoundError(errno.ENOENT, 'gone')],
                                 [FileNotFoundError(errno.ENOENT, 'gone')])
        self.assertIsNone(_read_cgroup_procs(path))

    def test_enodev_with_confirmed_retirement_is_ignored(self):
        path = _CgroupSamplePath([(1, 10), FileNotFoundError(errno.ENOENT, 'gone')],
                                 [OSError(errno.ENODEV, 'retired kernfs node')])
        self.assertIsNone(_read_cgroup_procs(path))

    def test_enodev_is_resampled_when_identity_remains_live(self):
        path = _CgroupSamplePath([(1, 10)] * 4,
                                 [OSError(errno.ENODEV, 'transient'), '303\n'])
        self.assertEqual(_read_cgroup_procs(path), ['303'])
        self.assertEqual(path.read_count, 2)

    def test_persistent_enodev_on_live_identity_raises(self):
        path = _CgroupSamplePath([(1, 10)] * 4,
                                 [OSError(errno.ENODEV, 'persistent'),
                                  OSError(errno.ENODEV, 'persistent')])
        with self.assertRaises(OSError) as raised:
            _read_cgroup_procs(path)
        self.assertEqual(raised.exception.errno, errno.ENODEV)
        self.assertEqual(path.read_count, 2)

    def test_enoent_on_live_directory_raises(self):
        path = _CgroupSamplePath([(1, 10), (1, 10)],
                                 [FileNotFoundError(errno.ENOENT, 'file missing')])
        with self.assertRaises(FileNotFoundError):
            _read_cgroup_procs(path)

    def test_unexpected_io_error_propagates(self):
        path = _CgroupSamplePath([(1, 10)], [OSError(errno.EIO, 'unexpected')])
        with self.assertRaises(OSError) as raised:
            _read_cgroup_procs(path)
        self.assertEqual(raised.exception.errno, errno.EIO)

    def test_identity_change_discards_old_snapshot_and_resamples(self):
        path = _CgroupSamplePath([(1, 10), (1, 11), (1, 11), (1, 11)],
                                 ['stale\n', '404\n'])
        self.assertEqual(_read_cgroup_procs(path), ['404'])

    def test_repeated_identity_change_is_bounded(self):
        path = _CgroupSamplePath([(1, 10), (1, 11), (1, 11), (1, 12)],
                                 ['stale-one\n', 'stale-two\n'])
        with self.assertRaises(RuntimeError):
            _read_cgroup_procs(path)
        self.assertEqual(path.read_count, 2)


if __name__=='__main__':
    tests=['StorageContentionTests.'+name for name in StorageContentionTests.__dict__ if name.startswith('test_')]
    tests += ['CgroupCpuQuotaValidationTests.'+name for name in CgroupCpuQuotaValidationTests.__dict__ if name.startswith('test_')]
    tests += ['CgroupProcessSamplingTests.'+name for name in CgroupProcessSamplingTests.__dict__ if name.startswith('test_')]
    tests += ['ProcessCommReadingTests.'+name for name in ProcessCommReadingTests.__dict__ if name.startswith('test_')]
    tests += ['AckTimelineRecorderTests.'+name for name in AckTimelineRecorderTests.__dict__ if name.startswith('test_')]
    unittest.main(verbosity=2,defaultTest=tests)
