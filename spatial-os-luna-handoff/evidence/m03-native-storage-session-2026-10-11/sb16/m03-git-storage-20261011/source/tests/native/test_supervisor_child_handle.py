"""Independent descriptor witness against the real supervisor and broker."""
import array
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import unittest

import test_supervisor_broker_handoff_regressions as base
import launch_pb2 as pb


class ChildHandleTests(base.BrokerHandoffRegressionTests):
    def start_with_handles(self, invalid_mapping=False):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        parent.settimeout(5)
        self.addCleanup(parent.close)
        inode = os.fstat(child.fileno()).st_ino
        try:
            # Keep target 38 free so the negative case reaches the inode alias check.
            config = self.broker_config(target=39 if invalid_mapping else 38)
            proc, channel = self.supervisor_process(config, handle_fd=child.fileno(),
                invalid_mapping=invalid_mapping, supervisor_binary=os.environ.get('SPATIAL_HANDLE_SUPERVISOR'))
        finally:
            child.close()
        if invalid_mapping:
            return proc, channel
        initial = self.message(channel, pb.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.view.record.stage, pb.START_ATTEMPTED)
        self.assertEqual(initial.view.record, self.read_record())
        self.record_snapshot = initial.view.record
        self.handles = parent
        self.handle_inode = inode
        self.supervisor_pid = proc.pid
        return proc, channel

    def request(self, expected=None, version=1):
        return pb.AuthorityChildHandleRequest(protocol_version=version, request_id=73,
            expected=self.record_snapshot if expected is None else expected)

    def exchange(self, request, suffix=b''):
        self.handles.send(request.SerializeToString() + suffix)
        data, control, flags, _ = self.handles.recvmsg(65536,
            socket.CMSG_SPACE(16 * array.array('i').itemsize) + socket.CMSG_SPACE(struct.calcsize('3i')),
            socket.MSG_CMSG_CLOEXEC)
        descriptors, credentials = [], []
        for level, kind, body in control:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array('i'); values.frombytes(body)
                descriptors.extend(values)
                for fd in values:
                    self.addCleanup(os.close, fd)
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                credentials.append(struct.unpack('3i', body))
            else:
                self.fail('Unexpected reply ancillary data')
        self.assertEqual(flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), 0)
        self.assertEqual(credentials, [(self.supervisor_pid, os.getuid(), os.getgid())])
        reply = pb.AuthorityChildHandleReply.FromString(data)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, request.request_id)
        return reply, descriptors

    def stopped(self, proc, channel):
        result = self.control(channel, stop=pb.AuthorityLaunchQuery())
        self.assertEqual(result.view.record.stage, pb.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()

    def test_transferred_pidfd_matches_real_broker_and_observes_cleanup(self):
        proc, channel = self.start_with_handles()
        reply, descriptors = self.exchange(self.request())
        self.assertTrue(reply.HasField('launch'), reply)
        self.assertEqual(reply.launch, self.record_snapshot)
        self.assertEqual(len(descriptors), 1)
        handle = descriptors[0]
        self.assertTrue(fcntl.fcntl(handle, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
        status = Path(f'/proc/self/fdinfo/{handle}').read_text()
        child_pid = int(next(line.split()[1] for line in status.splitlines() if line.startswith('Pid:')))
        members = (self.cgroup / self.record_snapshot.process.scope_name / 'cgroup.procs').read_text().split()
        self.assertEqual(members, [str(child_pid)])
        signal.pidfd_send_signal(handle, 0)
        self.assertEqual(select.select([handle], [], [], 0)[0], [])
        observer = subprocess.run(['sudo', '-n', 'python3', '-c',
            'import json,os,sys; p="/proc/"+str(int(sys.argv[1]))+"/fd"; '
            'print(json.dumps([os.readlink(p+"/"+n) for n in os.listdir(p)]))', str(child_pid)],
            capture_output=True, text=True, check=True, timeout=5)
        self.assertNotIn(f'socket:[{self.handle_inode}]', json.loads(observer.stdout))
        self.assertEqual(select.select([handle], [], [], 0)[0], [])
        self.stopped(proc, channel)
        self.assertEqual(select.select([handle], [], [], 1)[0], [handle])

    def test_other_launch_snapshot_is_rejected_without_a_descriptor(self):
        proc, channel = self.start_with_handles()
        wrong = pb.AuthorityLaunchRecord(); wrong.CopyFrom(self.record_snapshot)
        wrong.process.launch_id = os.urandom(32)
        reply, descriptors = self.exchange(self.request(wrong))
        self.assertTrue(reply.HasField('error'), reply); self.assertEqual(descriptors, [])
        good, descriptors = self.exchange(self.request())
        self.assertTrue(good.HasField('launch'), good); self.assertEqual(len(descriptors), 1)
        self.stopped(proc, channel)

    def test_unknown_field_and_version_are_rejected_without_a_descriptor(self):
        proc, channel = self.start_with_handles()
        for request, suffix in ((self.request(version=9), b''), (self.request(), b'\xa0\x06\x01')):
            reply, descriptors = self.exchange(request, suffix)
            self.assertTrue(reply.HasField('error'), reply); self.assertEqual(descriptors, [])
        self.stopped(proc, channel)

    def test_child_mapping_cannot_alias_private_handle_channel(self):
        proc, _ = self.start_with_handles(invalid_mapping='handle-alias')
        self.assertEqual(proc.wait(timeout=5), 2)
        logs = '\n'.join(p.read_text() for p in self.root.glob('supervisor-*.log'))
        self.assertIn('aliases privileged supervisor input', logs)
        self.assertEqual(self.activation_rows(), [])

    def test_non_parent_sender_is_rejected_and_owned_broker_is_cleaned(self):
        proc, _ = self.start_with_handles()
        child = os.fork()
        if child == 0:
            try:
                self.handles.send(self.request().SerializeToString())
                os._exit(0)
            except BaseException:
                os._exit(1)
        self.assertEqual(os.waitpid(child, 0)[1], 0)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.checked_cleanup()

    def test_request_descriptors_are_rejected_and_owned_broker_is_cleaned(self):
        proc, _ = self.start_with_handles()
        fd = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
        try:
            self.handles.sendmsg([self.request().SerializeToString()],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
        finally:
            os.close(fd)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.checked_cleanup()

    def test_truncated_request_is_rejected_and_owned_broker_is_cleaned(self):
        proc, _ = self.start_with_handles()
        self.handles.send(b'x' * 66000)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.checked_cleanup()

    def test_handle_channel_loss_records_checked_broker_cleanup(self):
        proc, _ = self.start_with_handles()
        self.handles.close()
        self.assertEqual(proc.wait(timeout=8), 2)
        self.checked_cleanup()


if __name__ == '__main__':
    names = [name for name in ChildHandleTests.__dict__ if name.startswith('test_')]
    selected = os.environ.get('SPATIAL_CHILD_HANDLE_CASE')
    if selected:
        assert selected in names, 'Unknown focused handle scenario'
        names = [selected]
    suite = unittest.TestSuite(ChildHandleTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
