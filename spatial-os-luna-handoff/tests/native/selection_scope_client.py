"""Independent protobuf framing for native extension operation 1."""
import struct
import selection_scope_pb2 as scope_pb


def exchange(client, request=None, payload=None):
    data = payload if payload is not None else request.SerializeToString()
    client.send(client.opcode, client.pack('I', len(data)) + data, minor=1)
    sequence = client.sequence % 65536
    header = client.exact(32)
    if header[0] == 0 and struct.unpack_from(client.endian + 'H', header, 2)[0] == sequence:
        return header[1]
    if header[0] != 1 or struct.unpack_from(client.endian + 'H', header, 2)[0] != sequence:
        raise AssertionError('Invalid scope reply header')
    size = struct.unpack_from(client.endian + 'I', header, 8)[0]
    padded = struct.unpack_from(client.endian + 'I', header, 4)[0] * 4
    if size > 4096 or padded != (size + 3) // 4 * 4:
        raise AssertionError('Invalid scope reply length')
    reply = scope_pb.SelectionScopeReply.FromString(client.exact(padded)[:size])
    if reply.protocol_version != 1 or (request is not None and reply.request_id != request.request_id):
        raise AssertionError('Invalid scope reply identity')
    return reply


def call(client, scope):
    client.operation_sequence = getattr(client, 'operation_sequence', 0) + 1
    return exchange(client, scope_pb.SelectionScopeRequest(protocol_version=1,
                                                          request_id=client.operation_sequence,
                                                          scope=scope))
