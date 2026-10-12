"""Forward bounded protobuf actor frames through TLS from a second test host."""
import argparse
import socket
import ssl
import struct
import sys

import world_session_pb2 as session


def exact(read, size, allow_eof=False):
    data = bytearray()
    while len(data) < size:
        part = read(size - len(data))
        if not part:
            if allow_eof and not data:
                return None
            raise EOFError('Truncated actor frame')
        data.extend(part)
    return bytes(data)


def packet(read, message, allow_eof=False):
    header = exact(read, 4, allow_eof)
    if header is None:
        return None
    size = struct.unpack('!I', header)[0]
    if not 0 < size <= 1048576:
        raise ValueError('Invalid actor frame length')
    payload = exact(read, size)
    decoded = message.FromString(payload)
    if decoded.protocol_version != 1:
        raise ValueError('Invalid actor protocol version')
    return header + payload, decoded.request_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('address')
    parser.add_argument('port', type=int)
    parser.add_argument('credentials')
    args = parser.parse_args()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(args.credentials + '/server.crt')
    context.load_cert_chain(args.credentials + '/client.crt', args.credentials + '/client.key')
    with socket.create_connection((args.address, args.port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname='world.test') as stream:
            print('TLS witness connected: ' + repr(stream.getsockname()) + ' -> ' +
                  repr(stream.getpeername()) + ' ' + stream.version(), file=sys.stderr, flush=True)
            while True:
                request = packet(sys.stdin.buffer.read, session.WorldSessionRequest, allow_eof=True)
                if request is None:
                    return
                stream.sendall(request[0])
                reply = packet(stream.recv, session.WorldSessionReply)
                if reply[1] != request[1]:
                    raise ValueError('Mismatched actor reply')
                sys.stdout.buffer.write(reply[0])
                sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
