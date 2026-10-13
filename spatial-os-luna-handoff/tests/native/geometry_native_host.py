#!/usr/bin/env python3
"""Trusted peer-host fixture. Every actor operation reaches spatial-host-session.

The independent X supervisor witnesses compositor pixels. It supplies no actor API.
"""
import os
from pathlib import Path
import socket
import struct
import sys
import time

if len(sys.argv)!=8:
    raise SystemExit('Expected SOURCE PROBE SECURITY IDENTITY ISSUER HOST PROFILE_DIRECTORY')
profile_directory=Path(sys.argv.pop()).resolve()
if socket.gethostname().split('.')[0] not in ('sb16','sb16-2'):
    raise SystemExit('Native fixture runs only on qualification hosts')
import test_host_session_production as fixture
from google.protobuf import text_format
import actor_session_client_pb2 as actor
import geometry_native_pb2 as protocol
import host_session_pb2 as host
from harness.wire import frame
from geometry_pixel_witness import GeometryPixelWitness
import geometry_process_identity as process_identity
from geometry_host_control_handoff import adopt_initial_host_control


def read(fd,n):
    result=b''
    while len(result)<n:
        part=os.read(fd,n-len(result))
        if not part:raise EOFError('Witness input ended')
        result+=part
    return result


class NativeHost(fixture.ProductionHostSessionTests):
    def configure_remote(self):
        config,descriptors=self.prepare()
        profiles=[]
        for path in sorted(profile_directory.glob('actor-*.textproto')):
            profile=text_format.Parse(path.read_text(),actor.ActorSessionClientTlsConfig())
            if profile.address in ('127.0.0.1','localhost','::1'):
                raise RuntimeError('Loopback cannot qualify a cross-host native view')
            profiles.append((path,profile))
            config.remote_worlds.add(world_id=profile.expected_world_id,actor_tls_config=str(path))
        if len(profiles)!=3:raise RuntimeError('Expected A, B, and private-denied remote profiles')
        config.initial_world_id=profiles[0][1].expected_world_id
        config.initial_actor_tls_config=str(profiles[0][0])
        self.launch_host(config,descriptors)
        first=self.request()
        adopt_initial_host_control(first,expected_request_id=1)
        self.native_xid=None
        return first

    def witness_processes(self):
        pids=[self.server.pid]+self.live_compositor_pids()
        self.assertEqual(len(pids),2)
        expected=[(pid,int(self.process_start(pid))) for pid in pids]
        def note_privileged_witness():
            evidence=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if evidence:
                target=Path(evidence);target.mkdir(parents=True,exist_ok=True)
                (target/'geometry-process-identity-witness.txt').write_text(
                    'sudo-n process identity fallback used\n')
        identities=process_identity.collect(expected,on_privileged=note_privileged_witness,protocol=protocol)
        return [protocol.NativeGeometryProcess(pid=item.pid,start_ticks=item.start_ticks,
            executable_sha256=item.executable_sha256) for item in identities]

    def capture(self):
        if not hasattr(self,'pixel_witness'):
            self.pixel_witness=GeometryPixelWitness(self.display,self.cookie)
            self.addCleanup(self.pixel_witness.close)
        pixels=self.pixel_witness.pixels()
        self.assertEqual(len(pixels),512*384*4)
        return pixels


def main():
    test=NativeHost('test_initial_presentation_selection_and_authenticated_actor_readiness')
    output=os.fdopen(os.dup(1),'wb',buffering=0)
    # Keep incidental fixture diagnostics away from the strict framed channel.
    os.dup2(2,1)
    def send(reply):output.write(frame(reply));output.flush()
    try:
        test.setUp();initial_control=test.configure_remote()
        ready=protocol.NativeGeometryWitnessReply(request_id=0,processes=test.witness_processes())
        ready.control.CopyFrom(initial_control)
        send(ready)
        previous=0
        while True:
            header=read(0,4);size,=struct.unpack('!I',header)
            if not 0<size<=1048576:raise RuntimeError('Invalid witness frame size')
            request=protocol.NativeGeometryWitnessRequest.FromString(read(0,size))
            if request.request_id<=previous or request.WhichOneof('operation') is None:
                raise RuntimeError('Invalid witness request sequence')
            previous=request.request_id
            reply=protocol.NativeGeometryWitnessReply(request_id=previous)
            if request.HasField('control'):
                test.control.sendall(frame(request.control))
                size,=struct.unpack('!I',fixture.exact(test.control,4))
                if not 0<size<=1048576:raise RuntimeError('Invalid production host reply frame')
                reply.control.CopyFrom(host.HostControlReply.FromString(fixture.exact(test.control,size)))
            elif request.capture:reply.pixels=test.capture()
            elif request.identity:reply.processes.extend(test.witness_processes())
            elif request.shutdown:
                test.control.shutdown(socket.SHUT_WR)
                terminal=test.terminal_cleanup_reply()
                test.assertTrue(terminal.cleanup_complete)
                test.assertEqual(test.host_process.wait(timeout=25),0,test.host_log.read_text())
                test.assert_checked_cleanup();reply.cleanup_complete=True;send(reply);break
            else:raise RuntimeError('Invalid witness operation')
            send(reply)
    finally:
        evidence=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if evidence and hasattr(test,'host_log'):
            target=Path(evidence);target.mkdir(parents=True,exist_ok=True)
            (target/'geometry-host-session.log').write_bytes(test.host_log.read_bytes())
        if not test.doCleanups():raise RuntimeError('Native fixture cleanup failed')


if __name__=='__main__':main()
