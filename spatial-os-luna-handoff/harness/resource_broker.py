"""Connect direct broker fixtures to the existing production process-scope owner."""
import fcntl
import os
from pathlib import Path
import subprocess
import select
import signal
import time
from google.protobuf import text_format
import broker_pb2 as broker
import process_scope_pb2 as scope
from harness.wire import frame, read_message


_RESOURCE_ENVIRONMENT = (
    'ASAN_OPTIONS', 'UBSAN_OPTIONS',
    'SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL',
    'SPATIAL_TEST_COPY_PREPARED_NOTIFY',
    'SPATIAL_TEST_FILE_JOURNAL_FAIL_FINISH_ONCE',
    'SPATIAL_TEST_FILE_JOURNAL_FINISH_NOTIFY',
    'SPATIAL_TEST_FILE_JOURNAL_FINISH_DELAY_MS',
)


def resource_environment(environ):
    return {name: environ[name] for name in _RESOURCE_ENVIRONMENT if name in environ}


class RetainedBrokerChild:
    """Actual pidfd and retained production exit status. The scope owner stays separate."""
    def __init__(self, resources):
        self.resources=resources;self.pid=resources.pid;self.returncode=None
        self.pidfd=os.pidfd_open(self.pid)
    def poll(self):
        if self.returncode is None and select.select([self.pidfd],[],[],0)[0]:
            self.returncode=self.resources.call(exit_status=scope.ProcessScopeQuery()).exit_status.code
        return self.returncode
    def wait(self, timeout=None):
        deadline=None if timeout is None else time.monotonic()+timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic()>=deadline:
                raise subprocess.TimeoutExpired(['workspaced'],timeout)
            select.select([self.pidfd],[],[],.01)
        return self.returncode
    def send_signal(self, value):
        if self.poll() is None:signal.pidfd_send_signal(self.pidfd,value)
    def kill(self):self.send_signal(signal.SIGKILL)
    def terminate(self):self.send_signal(signal.SIGTERM)


class ResourceBroker:
    def __init__(self, executable, command, channel, log, owner_log, configuration, extra_fds=None):
        self.sequence = 0
        self.closed = False
        config = broker.BrokerProcessConfig()
        text_format.Parse(Path(configuration).read_text(), config)
        if config.protocol_version != 1 or not config.HasField('resources') or config.resources.delegated_cgroup:
            raise ValueError('Expected explicit unbound resource template')
        policy = os.memfd_create('fixture-resource-policy', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        descriptors = [channel.fileno(), log.fileno(), policy]
        arguments=list(command)
        extra_fds=extra_fds or {}
        try:
            if set(extra_fds)-{'--native-security-fd','--control-supervisor-fd','--storage-fd'}:
                raise ValueError('Unexpected broker descriptor role')
            for flag,descriptor in extra_fds.items():
                if arguments.count(flag)!=1:raise ValueError('Expected one explicit broker descriptor binding')
                index=arguments.index(flag)+1
                if descriptor<3 or descriptor in descriptors or int(arguments[index])!=descriptor:
                    raise ValueError('Broker descriptor binding differs or aliases another role')
                arguments[index]=str(3+len(descriptors));descriptors.append(descriptor)
        except BaseException:
            os.close(policy);raise
        try:
            probe_mode = ('test-security-evidence' if Path(executable).name == 'workspaced' and
                          os.environ.get('SPATIAL_SECURITY_EVIDENCE') else 'normal')
            self.owner = subprocess.Popen([str(Path(executable).parent/'spatial-process-scope-probe'),
                config.delegated_cgroup, probe_mode, *map(str, descriptors)], pass_fds=descriptors,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=owner_log, bufsize=0)
        except BaseException:
            os.close(policy)
            raise
        try:
            prepared = self.call(prepare=scope.ProcessScopeQuery())
            self.root = Path(config.delegated_cgroup)/prepared.state.identity.scope_name
            config.resources.delegated_cgroup = str(self.root)
            payload = config.resources.SerializeToString()
            offset = 0
            while offset < len(payload):
                written = os.write(policy, payload[offset:])
                if written <= 0: raise OSError('Incomplete policy write')
                offset += written
            fcntl.fcntl(policy, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            environment = resource_environment(os.environ)
            arguments += ['--resource-config-fd', '5']
            code = ('import os;os.dup2(3,0);os.dup2(3,1);os.dup2(4,2);os.close(3);os.close(4);'
                f'os.environ.update({environment!r});os.execv({str(executable)!r},{arguments!r})')
            self.call(start=scope.AuthorityProcessLaunch(executable='/usr/bin/python3',
                arguments=['-c',code], inherited_fds=list(range(3,3+len(descriptors)))))
            children = Path(f'/proc/{self.owner.pid}/task/{self.owner.pid}/children').read_text().split()
            if len(children) != 1: raise RuntimeError('Expected exactly one retained broker child')
            self.pid = int(children[0])
            self.process=RetainedBrokerChild(self)
        except BaseException:
            self.owner.stdin.close()
            try: self.owner.wait(timeout=5)
            except subprocess.TimeoutExpired: self.owner.kill(); self.owner.wait(timeout=3)
            self.owner.stdout.close()
            raise
        finally:
            os.close(policy)

    def call(self, **operation):
        self.sequence += 1
        self.owner.stdin.write(frame(scope.ProcessScopeRequest(protocol_version=1,
            request_id=self.sequence, **operation)))
        self.owner.stdin.flush()
        deadline = time.monotonic()+5
        descriptor = self.owner.stdout.fileno()
        class BoundedStream:
            def read(self, size):
                if not select.select([descriptor], [], [], max(0,deadline-time.monotonic()))[0]:
                    raise TimeoutError('Process-scope reply deadline expired')
                return os.read(descriptor, size)
        reply = read_message(BoundedStream(), scope.ProcessScopeReply)
        if reply.protocol_version != 1 or reply.request_id != self.sequence or reply.HasField('error'):
            raise AssertionError(reply)
        expected = 'stopped' if 'stop' in operation else 'exit_status' if 'exit_status' in operation else 'state'
        if reply.WhichOneof('outcome') != expected: raise AssertionError(reply)
        return reply

    def close(self):
        if self.closed: return
        self.closed = True
        try:
            deadline = time.monotonic()+5
            while self.process.poll() is None:
                if time.monotonic() >= deadline: raise TimeoutError('Broker did not exit after channel closure')
                time.sleep(.01)
        finally:
            try:
                self.call(stop=scope.ProcessScopeQuery())
                if self.root.exists(): raise AssertionError('Owned broker scope survived checked stop')
            finally:
                self.owner.stdin.close()
                try:
                    if self.owner.wait(timeout=5) != 0: raise AssertionError('Process-scope owner failed')
                finally:
                    if self.owner.poll() is None: self.owner.kill(); self.owner.wait(timeout=3)
                    self.owner.stdout.close()
                    os.close(self.process.pidfd)
