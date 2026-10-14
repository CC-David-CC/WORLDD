#!/usr/bin/env python3
"""Focused regressions for protobuf geometry process identity witnessing."""
import errno
import hashlib
import os
from pathlib import Path
import struct
import select
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import geometry_native_pb2 as protocol
from geometry_process_identity import (
    ProcessIdentity, ProcessIdentityError, _no_unknown_or_noncanonical,
    _parse_cli, start_ticks, collect, finish_observer, parse_witness_request, privileged_read, read_identity, validate_expected,
)

class GeometryProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.expected=[(2345,123456)]
        self.digest=b'c'*32
        self.bindings=Path(protocol.__file__).resolve().parent

    def test_permission_error_uses_privileged_reader_and_checks_identity(self):
        def denied(pid,ticks):
            raise PermissionError(errno.EACCES,'proc exe denied')
        def privileged(expected,**kwargs):
            self.assertEqual(expected,self.expected)
            return [ProcessIdentity(2345,123456,self.digest)]
        self.assertEqual(collect(self.expected,direct=denied,privileged=privileged),
            [ProcessIdentity(2345,123456,self.digest)])

    def test_direct_success_never_invokes_sudo(self):
        self.assertEqual(collect(self.expected,
            direct=lambda pid,ticks:ProcessIdentity(pid,ticks,self.digest),
            privileged=lambda *_args,**_kwargs:self.fail('unexpected sudo fallback')),
            [ProcessIdentity(2345,123456,self.digest)])

    def test_only_permission_error_selects_privileged_reader(self):
        def failed(*_):
            raise FileNotFoundError(errno.ENOENT,'process disappeared')
        with self.assertRaises(FileNotFoundError):
            collect(self.expected,direct=failed,privileged=lambda *_a,**_k:
                self.fail('unexpected sudo fallback'))

    def test_privileged_command_has_fixed_helper_and_numeric_identity_arguments(self):
        helper=Path(__file__).with_name('geometry_process_identity.py')
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs))
            request=self.parse_frame(kwargs['input'],protocol.NativeGeometryWitnessRequest)
            self.assertEqual((request.request_id,request.WhichOneof('operation'),request.identity),(1,'identity',True))
            reply=protocol.NativeGeometryWitnessReply(request_id=1)
            reply.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest)
            return SimpleNamespace(returncode=0,stderr=b'',stdout=self.frame(reply.SerializeToString(deterministic=True)))
        self.assertEqual(privileged_read(self.expected,protocol=protocol,runner=runner,helper=helper),
            [ProcessIdentity(2345,123456,self.digest)])
        argv,kwargs=calls[0]
        self.assertEqual(argv,['sudo','-n',os.sys.executable,'-I',str(helper),'--root-witness',
            '--bindings-dir',str(self.bindings),'2345','123456'])
        self.assertEqual(kwargs['timeout'],10);self.assertTrue(kwargs['capture_output'])
        self.assertIsInstance(kwargs['input'],bytes)

    def test_root_cli_rejects_arbitrary_path_and_noncanonical_numbers(self):
        with self.assertRaises(SystemExit):
            _parse_cli(['--root-witness','--bindings-dir',str(self.bindings),'2345','123456','/tmp/x','4'])
        with self.assertRaises(SystemExit):
            _parse_cli(['--root-witness','--bindings-dir',str(self.bindings),'02345','123456'])

    def test_total_process_record_count_is_bounded(self):
        with self.assertRaises(ValueError):
            validate_expected([(pid,123456) for pid in range(100,117)])

    def test_root_request_requires_correlated_identity_operation(self):
        requests=(
            protocol.NativeGeometryWitnessRequest(request_id=2,identity=True),
            protocol.NativeGeometryWitnessRequest(request_id=1,capture=True),
            protocol.NativeGeometryWitnessRequest(request_id=1,identity=False),
        )
        for request in requests:
            with self.assertRaises(ProcessIdentityError):
                parse_witness_request(request.SerializeToString(deterministic=True),protocol)

    def test_root_request_rejects_unknown_and_noncanonical_fields(self):
        request=protocol.NativeGeometryWitnessRequest(request_id=1,identity=True)
        body=request.SerializeToString(deterministic=True)
        for hostile in (body+b'\xa0\x06\x01',body+b'\x08\x01'):
            parsed=protocol.NativeGeometryWitnessRequest();parsed.ParseFromString(hostile)
            with self.assertRaises(ProcessIdentityError):
                _no_unknown_or_noncanonical(parsed,hostile)

    def test_reply_rejects_context_count_pid_start_hash_and_unknown(self):
        replies=[]
        wrong=protocol.NativeGeometryWitnessReply(request_id=2)
        wrong.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest);replies.append(wrong)
        replies.append(protocol.NativeGeometryWitnessReply(request_id=1))
        pid=protocol.NativeGeometryWitnessReply(request_id=1)
        pid.processes.add(pid=9999,start_ticks=123456,executable_sha256=self.digest);replies.append(pid)
        ticks=protocol.NativeGeometryWitnessReply(request_id=1)
        ticks.processes.add(pid=2345,start_ticks=123457,executable_sha256=self.digest);replies.append(ticks)
        pixels=protocol.NativeGeometryWitnessReply(request_id=1,pixels=b'x')
        pixels.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest);replies.append(pixels)
        error=protocol.NativeGeometryWitnessReply(request_id=1,error='unexpected')
        error.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest);replies.append(error)
        cleanup=protocol.NativeGeometryWitnessReply(request_id=1,cleanup_complete=True)
        cleanup.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest);replies.append(cleanup)
        badhash=protocol.NativeGeometryWitnessReply(request_id=1)
        badhash.processes.add(pid=2345,start_ticks=123456,executable_sha256=b'x');replies.append(badhash)
        unknown=protocol.NativeGeometryWitnessReply(request_id=1)
        unknown.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest)
        replies.append(unknown.SerializeToString(deterministic=True)+b'\xa0\x06\x01')
        nested=protocol.NativeGeometryWitnessReply(request_id=1)
        entry=nested.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest)
        entry.ParseFromString(entry.SerializeToString(deterministic=True)+b'\xa0\x06\x01')
        replies.append(nested)
        for reply in replies:
            body=reply if isinstance(reply,bytes) else reply.SerializeToString(deterministic=True)
            with self.subTest(reply=body),self.assertRaises(ProcessIdentityError):
                self.invoke_privileged(body)

    def test_reply_rejects_duplicate_fields_trailing_bytes_and_stderr(self):
        reply=protocol.NativeGeometryWitnessReply(request_id=1)
        reply.processes.add(pid=2345,start_ticks=123456,executable_sha256=self.digest)
        body=reply.SerializeToString(deterministic=True)
        outputs=((self.frame(body+b'\x08\x01'),b''),(self.frame(body)+b'x',b''),
                 (self.frame(body),b'warning'))
        for stdout,stderr in outputs:
            with self.subTest(stderr=stderr),self.assertRaises(ProcessIdentityError):
                self.invoke_privileged(stdout=stdout,stderr=stderr)

    def test_reply_rejects_truncated_and_oversized_frames(self):
        self.assert_raises_reply(b'\x00\x00')
        self.assert_raises_reply(struct.pack('!I',1024*1024+1))

    def test_pidfd_ready_rejects_dead_process_before_proc_access(self):
        def unexpected(*_):self.fail('proc read after pidfd exit')
        with self.assertRaisesRegex(ProcessIdentityError,'already exited'):
            read_identity(2345,123456,pin=lambda *_:os.open('/dev/null',os.O_RDONLY),
                ready=lambda _:True,read_start=unexpected,stat=unexpected,open_file=unexpected)

    def test_wrong_start_before_read_and_changed_start_during_read_reject_pid_reuse(self):
        def unexpected(*_):self.fail('executable read for stale start identity')
        with self.assertRaisesRegex(ProcessIdentityError,'changed before'):
            read_identity(2345,123456,pin=lambda *_:os.open('/dev/null',os.O_RDONLY),
                ready=lambda _:False,read_start=lambda *_:123457,stat=unexpected,open_file=unexpected)
        ticks=iter((123456,123457))
        with tempfile.TemporaryDirectory() as directory:
            proc=Path(directory)/'2345';proc.mkdir()
            image=proc/'image';image.write_bytes(b'process image');(proc/'exe').symlink_to(image)
            with self.assertRaisesRegex(ProcessIdentityError,'changed during'):
                read_identity(2345,123456,proc=directory,pin=lambda *_:os.open('/dev/null',os.O_RDONLY),
                    ready=lambda _:False,read_start=lambda *_:next(ticks))

    def test_death_after_hash_and_executable_inode_change_are_rejected(self):
        states=iter((False,True))
        with tempfile.TemporaryDirectory() as directory:
            proc=Path(directory)/'2345';proc.mkdir()
            image=proc/'image';image.write_bytes(b'process image');(proc/'exe').symlink_to(image)
            with self.assertRaisesRegex(ProcessIdentityError,'exited during'):
                read_identity(2345,123456,proc=directory,pin=lambda *_:os.open('/dev/null',os.O_RDONLY),
                    ready=lambda _:next(states),read_start=lambda *_:123456)
        with tempfile.TemporaryDirectory() as directory:
            proc=Path(directory)/'2345';proc.mkdir()
            first=proc/'first';first.write_bytes(b'first image')
            second=proc/'second';second.write_bytes(b'second image')
            link=proc/'exe';link.symlink_to(first)
            def switch(path,mode):
                stream=open(path,mode)
                if Path(path)==link:
                    link.unlink();link.symlink_to(second)
                return stream
            with self.assertRaisesRegex(ProcessIdentityError,'Executable changed'):
                read_identity(2345,123456,proc=directory,pin=lambda *_:os.open('/dev/null',os.O_RDONLY),
                    ready=lambda _:False,read_start=lambda *_:123456,open_file=switch)

    def test_pre_ready_failure_recovers_exact_unit_without_shutdown(self):
        calls=[]
        finish_observer(False,exchange=lambda **_:self.fail('unexpected shutdown frame'),
            recover=lambda:calls.append('exact-owned-unit'))
        self.assertEqual(calls,['exact-owned-unit'])

    def test_ready_observer_uses_protocol_shutdown(self):
        calls=[]
        class Reply:cleanup_complete=True
        finish_observer(True,exchange=lambda **value:calls.append(value) or Reply(),
            recover=lambda:self.fail('ready observer must use protocol shutdown'))
        self.assertEqual(calls,[dict(shutdown=True)])

    def test_real_isolated_import_roundtrip_excludes_environment_and_cwd_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.hostile_import_environment(directory)
            code = """import sys
sys.path.insert(0, sys.argv[1])
import geometry_native_pb2 as protocol
request = protocol.NativeGeometryWitnessRequest(request_id=1, identity=True)
parsed = protocol.NativeGeometryWitnessRequest.FromString(request.SerializeToString())
assert parsed.request_id == 1 and parsed.identity
reply = protocol.NativeGeometryWitnessReply(request_id=parsed.request_id)
assert protocol.NativeGeometryWitnessReply.FromString(reply.SerializeToString()).request_id == 1
print('isolated-protobuf-roundtrip')
"""
            result = subprocess.run(['/usr/bin/python3', '-I', '-c', code, str(self.bindings)],
                env=env, cwd=directory, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b'')
            self.assertEqual(result.stdout, b'isolated-protobuf-roundtrip\n')

    def test_real_root_isolated_roundtrip_reads_nondumpable_child_and_rejects_injection(self):
        self.assertNotEqual(os.geteuid(), 0, 'Run this regression as the qualification user')
        code = """import ctypes, sys, time
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(4, 0, 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), 'PR_SET_DUMPABLE')
print('nondumpable-ready', flush=True)
time.sleep(60)
"""
        child = subprocess.Popen(['/usr/bin/python3', '-I', '-c', code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'Child readiness timeout')
            self.assertEqual(child.stdout.readline(), b'nondumpable-ready\n')
            expected = [(child.pid, start_ticks(child.pid))]
            with self.assertRaises(PermissionError):
                read_identity(*expected[0])
            digest = hashlib.sha256(Path('/usr/bin/python3').resolve().read_bytes()).digest()
            with tempfile.TemporaryDirectory() as directory:
                env = self.hostile_import_environment(directory)
                def runner(argv, **kwargs):
                    # Pin the same distro interpreter used by the qualification bundle.
                    argv = list(argv)
                    argv[2] = '/usr/bin/python3'
                    return subprocess.run(argv, env=env, cwd=directory, **kwargs)
                calls = []
                result = collect(expected, protocol=protocol, bindings_dir=self.bindings,
                    privileged=lambda identities, **kwargs: privileged_read(
                        identities, runner=runner, **kwargs),
                    on_privileged=lambda: calls.append('privileged'))
                self.assertEqual(calls, ['privileged'])
                self.assertEqual(result, [ProcessIdentity(child.pid, expected[0][1], digest)])
                # An otherwise valid request reaches the root helper's context guard.
                request = protocol.NativeGeometryWitnessRequest(request_id=2, identity=True)
                helper = Path(__file__).with_name('geometry_process_identity.py')
                argv = ['sudo', '-n', '/usr/bin/python3', '-I', str(helper), '--root-witness',
                    '--bindings-dir', str(self.bindings), str(child.pid), str(expected[0][1])]
                rejected = subprocess.run(argv, input=self.frame(request.SerializeToString(
                    deterministic=True)), env=env, cwd=directory, capture_output=True, timeout=10)
                self.assertEqual(rejected.returncode, 1)
                self.assertEqual(rejected.stdout, b'')
                self.assertEqual(rejected.stderr, b'ProcessIdentityError\n')
                nonisolated = subprocess.run([value for value in argv if value != '-I'],
                    input=b'', capture_output=True, timeout=10)
                self.assertNotEqual(nonisolated.returncode, 0)
                self.assertEqual(nonisolated.stdout, b'')
                self.assertEqual(nonisolated.stderr, b'Root witness requires isolated Python\n')
        finally:
            # Only this Popen child is terminated. No process-name cleanup is used.
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            child.stdout.close()
            child.stderr.close()

    @staticmethod
    def hostile_import_environment(directory):
        root = Path(directory)
        for name in ('sitecustomize.py', 'geometry_native_pb2.py', 'google.py'):
            (root / name).write_text("raise RuntimeError('untrusted import executed')\n")
        env = os.environ.copy()
        env.update(PYTHONPATH=str(root), PYTHONUSERBASE=str(root))
        return env

    def invoke_privileged(self,body=None,stdout=None,stderr=b''):
        if stdout is None:stdout=self.frame(body)
        return privileged_read(self.expected,protocol=protocol,
            runner=lambda *_a,**_k:SimpleNamespace(returncode=0,stdout=stdout,stderr=stderr),
            bindings_dir=self.bindings)

    def assert_raises_reply(self,stdout):
        with self.assertRaises(ProcessIdentityError):
            self.invoke_privileged(stdout=stdout)

    @staticmethod
    def frame(body):
        return struct.pack('!I',len(body))+body

    def parse_frame(self,value,message_type):
        size,=struct.unpack('!I',value[:4])
        self.assertEqual(len(value),size+4)
        message=message_type();message.ParseFromString(value[4:])
        return message

if __name__=='__main__':
    unittest.main()
