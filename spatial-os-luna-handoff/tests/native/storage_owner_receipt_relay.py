"""Authenticated test peer. Forward production protobuf through a real broker UID."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import struct
import subprocess
import sys
import traceback
import time


def receive(channel):
    prefix = bytearray()
    while len(prefix) < 4:
        part = channel.recv(4-len(prefix))
        if not part and not prefix:
            return None
        if not part:
            raise RuntimeError('truncated private protobuf prefix')
        prefix.extend(part)
    size = struct.unpack('!I', prefix)[0]
    if not 0 < size <= 1024*1024:
        raise RuntimeError('invalid private protobuf frame length')
    payload = bytearray()
    while len(payload) < size:
        part = channel.recv(size-len(payload))
        if not part:
            raise RuntimeError('truncated private protobuf payload')
        payload.extend(part)
    return bytes(payload)


def send(channel, payload):
    channel.sendall(struct.pack('!I', len(payload))+payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel-fd', type=int, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--mutate', action='store_true')
    mode.add_argument('--delay-copy-begin', action='store_true')
    args = parser.parse_args()
    if os.getuid() != 0 or socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise RuntimeError('receipt relay requires an approved root test host')
    sys.path.insert(0, str(args.build/'python'))
    import storage_pb2 as storage
    operator = pwd.getpwnam('dflanag3')
    client = socket.socket(fileno=args.channel_fd)
    client.settimeout(190)
    relay, broker = socket.socketpair()
    upstream, owner_channel = socket.socketpair()
    for channel in (relay, broker, upstream, owner_channel):
        channel.settimeout(190)
    # Match StorageChannel's early credential setup before any queued first frame.
    upstream.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    owner_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    child = os.fork()
    if child == 0:
        # The owner sees this process's actual nonroot UID and PID on every frame.
        client.close(); relay.close(); owner_channel.close()
        try:
            os.setgroups([]); os.setgid(operator.pw_gid); os.setuid(operator.pw_uid)
            while True:
                payload = receive(broker)
                if payload is None:
                    break
                send(upstream, payload)
                reply = receive(upstream)
                if reply is None:
                    raise RuntimeError('production owner closed before reply')
                send(broker, reply)
            upstream.close(); broker.close()
            os._exit(0)
        except BaseException:
            traceback.print_exc(); sys.stderr.flush()
            os._exit(2)
    broker.close(); upstream.close()
    owner = None
    audit = {'peer_uid':os.getuid(), 'peer_pid':os.getpid(), 'broker_uid':operator.pw_uid,
             'broker_pid':child, 'upstream_passcred':True, 'owner_channel_passcred':True, 'requests':[], 'mutations':[], 'delays':[], 'client_eof':False,
             'coverage':'authenticated root-negative private protocol' if args.mutate else 'authorized relay production control'}
    waited = False
    try:
        config = storage.StorageOwnerConfig.FromString(args.config.read_bytes())
        config.broker_uid = operator.pw_uid; config.broker_pid = child
        args.config.write_bytes(config.SerializeToString(deterministic=True))
        args.config.chmod(0o600)
        owner = subprocess.Popen([str(args.build/'spatial-storage-owner'), '--channel-fd',
                                  str(owner_channel.fileno()), '--config', str(args.config)],
                                 pass_fds=[owner_channel.fileno()], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True)
        audit['owner_pid'] = owner.pid
        owner_channel.close()
        while True:
            payload = receive(client)
            if payload is None:
                audit['client_eof'] = True
                break
            request = storage.StorageOwnerRequest.FromString(payload)
            method = request.WhichOneof('operation')
            audit['requests'].append(method)
            send(relay, payload)
            original = receive(relay)
            if original is None:
                raise RuntimeError('broker closed before owner reply')
            reply = storage.StorageOwnerReply.FromString(original)
            if reply.HasField('identity'):
                if reply.identity.process_id != owner.pid:
                    raise RuntimeError('unexpected production owner identity')
                reply.identity.process_id = os.getpid()
            if args.mutate and request.HasField('copy_stage_begin'):
                if not reply.HasField('copy_stage') or audit['mutations']:
                    raise RuntimeError('expected exactly one valid BEGIN receipt')
                before = reply.copy_stage.destination_subvolume_id
                after = before+1
                if after == reply.copy_stage.source_subvolume_id:
                    after += 1
                if not 0 < after < 2**64:
                    raise RuntimeError('cannot select otherwise valid mismatched subvolume')
                unchanged = storage.StorageOwnerReply(); unchanged.CopyFrom(reply)
                reply.copy_stage.destination_subvolume_id = after
                restored = storage.StorageOwnerReply(); restored.CopyFrom(reply)
                restored.copy_stage.destination_subvolume_id = before
                if restored != unchanged:
                    raise RuntimeError('mutation changed another receipt field')
                audit['mutations'].append({'field':'destination_subvolume_id','before':before,'after':after})
            if args.delay_copy_begin and request.HasField('copy_stage_begin') and not audit['delays']:
                if not reply.HasField('copy_stage'):
                    raise RuntimeError('delay witness requires a valid production BEGIN receipt')
                started = time.monotonic()
                delayed = dict(method=method, start_monotonic=started,
                    reply_sha256=hashlib.sha256(reply.SerializeToString(deterministic=True)).hexdigest())
                audit['delays'].append(delayed)
                while time.monotonic()-started < 4.1:
                    time.sleep(min(.02, max(0, 4.1-(time.monotonic()-started))))
                delayed['end_monotonic'] = time.monotonic()
                delayed['elapsed_seconds'] = delayed['end_monotonic']-started
            send(client, reply.SerializeToString(deterministic=True))
        relay.close()
        _, status = os.waitpid(child, 0); waited = True
        audit['broker_exit'] = os.waitstatus_to_exitcode(status)
        audit['owner_stderr'] = owner.communicate(timeout=10)[1]
        audit['owner_exit'] = owner.returncode
        if audit['broker_exit'] or owner.returncode:
            raise RuntimeError('authenticated forwarding peer or owner failed')
    except BaseException as error:
        audit['relay_failure'] = repr(error)
        raise
    finally:
        cleanup_errors = []
        for channel in (client, relay, owner_channel):
            try: channel.close()
            except BaseException as error: cleanup_errors.append(repr(error))
        if not waited:
            try:
                found, status = os.waitpid(child, os.WNOHANG)
                if found == 0:
                    os.kill(child, signal.SIGKILL)
                    _, status = os.waitpid(child, 0)
                    audit['broker_cleanup_killed'] = True
                audit['broker_exit'] = os.waitstatus_to_exitcode(status)
            except BaseException as error:
                cleanup_errors.append('forwarder cleanup: '+repr(error))
        if owner:
            try:
                audit['owner_pid'] = owner.pid
                if owner.poll() is None:
                    owner.kill(); audit['owner_cleanup_killed'] = True
                audit['owner_stderr'] = owner.communicate(timeout=10)[1]
                audit['owner_exit'] = owner.returncode
            except BaseException as error:
                cleanup_errors.append('owner cleanup: '+repr(error))
        if cleanup_errors:
            audit['cleanup_errors'] = cleanup_errors
        args.audit.write_text(json.dumps(audit, indent=2, sort_keys=True)+'\n')
    if audit.get('cleanup_errors'):
        raise RuntimeError('private relay cleanup failed: '+repr(audit['cleanup_errors']))



if __name__ == '__main__':
    main()
