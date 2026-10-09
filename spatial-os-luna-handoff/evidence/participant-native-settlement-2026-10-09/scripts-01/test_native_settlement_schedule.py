"""Pause only the broker authority thread after its real settlement boundary."""
import os
import json
from pathlib import Path
import signal
import subprocess
import threading
import time
import unittest
import broker_pb2 as broker
import test_native_settlement_witness as baseline
from test_world_bootstrap_races import BootstrapRelay


class NativeSettlementSchedule(baseline.NativeSettlementWitness):
    def start_broker(self, *args, **kwargs):
        kwargs.update(relay=self.relay, query_relay=self.relay)
        return super().start_broker(*args, **kwargs)

    def test_stopped_server_cannot_confirm_native_credential_cleanup(self):
        self.relay = BootstrapRelay(self, self.revoke_now, 'info')
        item, lease, poll, count = self.native_broker()
        released = item.request(control_release=broker.ApplicationControlRelease(id=lease.id, principal=self.principal()))
        self.assertTrue(released.HasField('control_released'), released)
        source = Path(os.environ['SPATIAL_SCHEDULE_SOURCE'])
        native_source = source / 'src/workspace/native_launch.cpp'
        main_source = source / 'src/workspace/main.cpp'
        native_lines = native_source.read_text().splitlines()
        native_line = next(i+1 for i,line in enumerate(native_lines) if line.strip() == '} catch (...) { impl_->channel.reset(); }')
        main_lines = main_source.read_text().splitlines()
        after_settle = next(i+2 for i,line in enumerate(main_lines) if 'authority->settle(output.empty()' in line)
        stopped = self.root / 'authority-thread-stopped'
        commands = self.root / 'schedule.gdb'
        commands.write_text(f'''set $cleanup_failed = 0
break {native_source}:{native_line}
commands
silent
set $cleanup_failed = 1
continue
end
break {main_source}:{after_settle} if $cleanup_failed
commands
silent
python open({str(stopped)!r}, 'w').write('stopped after settlement')
end
''')
        debuglog = (self.root / 'schedule-debugger.log').open('wb')
        debugger = subprocess.Popen(['sudo','-n','gdb','-q','-nx','--interpreter=mi2'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        condition = threading.Condition(); replies = {}; sequence = 0
        def read_debugger():
            for raw in debugger.stdout:
                debuglog.write(raw); debuglog.flush()
                line = raw.decode(errors='replace').strip()
                with condition:
                    if '^' in line and line.split('^',1)[0].isdigit():
                        token, value = line.split('^',1); replies[int(token)] = value
                    condition.notify_all()
        reader = threading.Thread(target=read_debugger, daemon=True); reader.start()
        def exchange(command):
            nonlocal sequence
            sequence += 1; token = sequence
            debugger.stdin.write(f'{token}-{command}\n'.encode()); debugger.stdin.flush()
            deadline = time.monotonic()+5
            with condition:
                while token not in replies:
                    remaining = deadline-time.monotonic()
                    self.assertGreater(remaining,0,'Debugger command did not complete: '+command)
                    condition.wait(remaining)
                result = replies.pop(token)
            self.assertFalse(result.startswith('error'),result)
            return result
        def cleanup_debugger():
            if debugger.poll() is None:
                # Detach only this owned inferior; other threads may still be running.
                try: exchange('target-detach')
                except (AssertionError, BrokenPipeError): pass
                try: exchange('gdb-exit')
                except (AssertionError, BrokenPipeError): pass
            try: debugger.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # The PID belongs to this exact Popen. Never stop another debugger.
                subprocess.run(['sudo','-n','kill','-TERM',str(debugger.pid)],check=True,timeout=3)
                debugger.wait(timeout=3)
            reader.join(timeout=3)
            debugger.stdin.close(); debugger.stdout.close()
            debuglog.close()
        self.addCleanup(cleanup_debugger)
        exchange('gdb-set pagination off'); exchange('gdb-set confirm off'); exchange('gdb-set non-stop on')
        exchange('target-attach '+str(item.proc.pid))
        self.until(lambda:exchange('thread-info'),lambda value:value.count('state="stopped"')==2)
        exchange('interpreter-exec console '+json.dumps('source '+str(commands)))
        exchange('exec-continue --all')
        server = self.native.server
        try:
            server.send_signal(signal.SIGSTOP)
            self.until(lambda: Path(f'/proc/{server.pid}/status').read_text().split('State:', 1)[1].splitlines()[0],
                       lambda value: value.strip().startswith('T'))
            self.relay.armed.set()
            self.assertTrue(self.relay.delivered.wait(2), 'Idle keepalive did not receive the real revocation')
            self.until(lambda: len(poll.poll(0)), lambda value: value == count)
            deadline = time.monotonic() + 6
            observations = []
            while time.monotonic() < deadline:
                status = self.barrier()
                observations.append((status.pending_count, status.complete))
                if status.complete or item.proc.poll() is not None: break
                time.sleep(.01)
            print('post_settlement_pause=', stopped.exists(), 'barrier=', observations[-1], flush=True)
            self.assertFalse(status.complete,
                'Issuer completed revocation while native cleanup was unconfirmed and its X server was stopped')
            self.assertEqual(item.proc.wait(timeout=3), 2)
        finally:
            if server.poll() is None: server.send_signal(signal.SIGCONT)


if __name__ == '__main__': unittest.main(verbosity=2)
