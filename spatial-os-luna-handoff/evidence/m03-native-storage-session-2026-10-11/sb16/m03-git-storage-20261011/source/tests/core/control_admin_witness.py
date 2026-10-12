"""Independent packet sender. It implements no world or authorization service."""
import array
import os
import socket
import sys
import time
import broker_pb2 as pb
import core_pb2 as core

channel = socket.socket(fileno=int(sys.argv[1]))
mode = sys.argv[2]
packet = channel.recv(65536)
request = pb.ControlSupervisorRequest.FromString(packet)
reply = pb.ControlSupervisorReply(protocol_version=1, request_id=request.request_id)
reply.state.owner.CopyFrom(request.inspect)
reply.state.channel_fenced = False
if mode == 'error':
    reply.error.code = core.NOT_FOUND
    reply.error.message = 'Independent owner absence'
elif mode == 'correlation':
    reply.request_id += 1
elif mode == 'owner':
    reply.state.owner.issuer_id = b'x' * 32
elif mode == 'version':
    reply.protocol_version = 2
elif mode == 'timeout':
    time.sleep(20)
    sys.exit(0)
elif mode == 'exit':
    sys.exit(0)

packet = reply.SerializeToString()
if mode == 'oversize':
    packet = b'x' * 65537
elif mode == 'malformed':
    packet = b'\xff'
elif mode == 'wrong-sender':
    sender = os.fork()
    if sender:
        os.waitpid(sender, 0)
        time.sleep(20)
        sys.exit(0)
elif mode == 'rights':
    fd = os.open('/dev/null', os.O_RDONLY)
    channel.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
    os.close(fd)
    time.sleep(20)
    sys.exit(0)
channel.send(packet)
# Keep the credential owner alive until the receiver closes its transport.
if mode == 'wrong-sender':
    os._exit(0)
channel.recv(1)
