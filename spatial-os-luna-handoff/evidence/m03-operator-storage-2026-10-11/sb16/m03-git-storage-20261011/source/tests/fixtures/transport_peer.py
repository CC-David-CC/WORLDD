"""Protobuf transport fixture only. Never certifies the production runtime."""
import argparse
from pathlib import Path
import struct
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harness.wire import pb, frame, read_message

p = argparse.ArgumentParser()
p.add_argument('--mode', default='echo')
p.add_argument('--state-dir')
args = p.parse_args()
pending = []

def emit(reply):
    sys.stdout.buffer.write(frame(reply))
    sys.stdout.buffer.flush()

while True:
    try:
        request = read_message(sys.stdin.buffer, pb.Request)
    except EOFError:
        break
    reply = pb.Reply(protocol_version=1, request_id=request.request_id)
    if request.HasField('transform_point'):
        reply.point.point.CopyFrom(request.transform_point.point)
    else:
        reply.hello.protocol = 'spatial-protobuf/1'
        reply.hello.implementation = 'harness-fixture'
        reply.hello.build_id = 'fixture-学校'
    if args.mode == 'reverse-eight':
        pending.append(reply)
        if len(pending) == 8:
            for item in reversed(pending):
                emit(item)
            pending.clear()
        continue
    if args.mode == 'timeout':
        time.sleep(5)
        continue
    if args.mode == 'eof':
        break
    if args.mode == 'garbage':
        sys.stdout.buffer.write(struct.pack('!I', 1) + b'\xff')
        sys.stdout.buffer.flush()
        continue
    if args.mode == 'wrong-id':
        reply.request_id += 100
    if args.mode == 'no-outcome':
        reply.ClearField('outcome')
    if args.mode == 'nonfinite':
        reply.point.point.x = float('nan')
        payload = reply.SerializeToString()
        sys.stdout.buffer.write(struct.pack('!I', len(payload)) + payload)
        sys.stdout.buffer.flush()
        continue
    if args.mode == 'error':
        reply.error.code = pb.INVALID_ARGUMENT
        reply.error.message = 'expected fixture error'
    emit(reply)
