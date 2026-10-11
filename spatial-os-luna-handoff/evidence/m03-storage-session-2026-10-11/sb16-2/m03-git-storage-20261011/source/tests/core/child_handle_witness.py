"""Independent untrusted reply producer. No authority or world implementation."""
import array
import os
import socket
import subprocess
import sys
import time
import launch_pb2 as pb

channel = socket.socket(fileno=int(sys.argv[1])); mode = sys.argv[2]
request = pb.AuthorityChildHandleRequest.FromString(channel.recv(65536))
child = subprocess.Popen(['/usr/bin/sleep', '30'])
fd = os.pidfd_open(child.pid)
try:
    reply = pb.AuthorityChildHandleReply(protocol_version=1, request_id=request.request_id, launch=request.expected)
    if mode == 'wrong-request': reply.request_id += 1
    elif mode == 'wrong-launch': reply.launch.process.launch_id = b'x' * 32
    elif mode == 'version': reply.protocol_version = 9
    elif mode == 'error-rights': reply.error.message = 'Independent denial'
    elif mode == 'plain-file': os.close(fd); fd = os.open('/dev/null', os.O_RDONLY)
    elif mode == 'wrong-parent': os.close(fd); fd = os.pidfd_open(os.getpid())
    elif mode == 'dead': child.kill(); child.wait(timeout=3)
    elif mode == 'timeout': time.sleep(15)
    packet = reply.SerializeToString()
    if mode == 'unknown': packet += b'\xa0\x06\x01'
    elif mode == 'malformed': packet = b'\xff'
    elif mode == 'oversize': packet = b'x' * 66000
    rights = [fd, fd] if mode == 'duplicate' else [] if mode == 'missing' else [fd]
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', rights))] if rights else []
    if mode == 'wrong-sender':
        sender = os.fork()
        if sender == 0:
            channel.sendmsg([packet], ancillary); os._exit(0)
        os.waitpid(sender, 0)
    else: channel.sendmsg([packet], ancillary)
    channel.recv(1)
finally:
    os.close(fd)
    if child.poll() is None: child.kill()
    child.wait(timeout=3)
