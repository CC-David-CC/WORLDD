#!/usr/bin/env python3
"""Send one semantic request on an inherited host-session AF_UNIX stream."""
import argparse
import socket
import struct
import sys
import select
import time
from google.protobuf import text_format
import host_session_pb2 as host

MAX_FRAME = 1024 * 1024


def ready(sock, writing, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('host-session exchange deadline expired')
    readable, writable, _ = select.select([] if writing else [sock], [sock] if writing else [], [], remaining)
    if not (writable if writing else readable):
        raise TimeoutError('host-session exchange deadline expired')


def exact(sock, size, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        ready(sock, False, deadline)
        try:
            part = sock.recv(size - len(chunks))
        except (InterruptedError, BlockingIOError):
            continue
        if not part:
            raise EOFError('host session closed the control channel')
        chunks.extend(part)
    return bytes(chunks)


def send_all(sock, data, deadline):
    sent = 0
    while sent < len(data):
        ready(sock, True, deadline)
        try:
            count = sock.send(data[sent:])
        except (InterruptedError, BlockingIOError):
            continue
        if count <= 0:
            raise EOFError('host session closed during request')
        sent += count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fd', type=int, required=True, help='inherited connected control descriptor')
    parser.add_argument('--generation', type=int, required=True)
    parser.add_argument('--request-id', type=int, required=True)
    parser.add_argument('operation', choices=('inspect', 'action', 'transition'))
    parser.add_argument('textproto', nargs='?', help='ActorActionRequest or HostActorTransitionRequest textproto')
    args = parser.parse_args()
    if args.operation != 'inspect' and not args.textproto:
        parser.error('action and transition require a semantic textproto file')
    request = host.HostControlRequest(protocol_version=1, request_id=args.request_id,
        generation=args.generation)
    if args.operation == 'inspect':
        request.inspect = True
    elif args.operation == 'action':
        text_format.Parse(open(args.textproto, encoding='utf-8').read(), request.action)
    else:
        text_format.Parse(open(args.textproto, encoding='utf-8').read(), request.transition)
    sock = socket.socket(fileno=args.fd)
    sock.setblocking(False)
    deadline = time.monotonic() + 10
    payload = request.SerializeToString()
    if not payload or len(payload) > MAX_FRAME:
        raise ValueError('request exceeds the host-session frame bound')
    send_all(sock, struct.pack('!I', len(payload)) + payload, deadline)
    size, = struct.unpack('!I', exact(sock, 4, deadline))
    if not size or size > MAX_FRAME:
        raise ValueError('invalid host-session response frame')
    reply = host.HostControlReply.FromString(exact(sock, size, deadline))
    if reply.protocol_version != 1 or reply.request_id != args.request_id:
        raise ValueError('host-session response correlation failed')
    sys.stdout.write(text_format.MessageToString(reply))
    return 0 if reply.accepted else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'host-session request failed: {error}', file=sys.stderr)
        raise SystemExit(2)
