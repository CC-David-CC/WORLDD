"""Native protocol regressions for production geometry presentation targets.

These are boundary checks. The two-host actor suite supplies semantic oracles.
"""
import os
import unittest
import core_pb2 as core
import presentation_pb2 as protocol
import geometry_view_pb2 as geometry
import test_presentation_server as baseline


class GeometryPresentationTests(baseline.PresentationServerTests):
    def ready(self):
        self.launch()
        binding, stream = self.attach(1)
        self.hello(stream)
        self.control(select=binding)
        result = self.draw(stream, 2, (17, 34, 51))
        self.assertTrue(result.HasField('frame'), result)
        self.xid = result.frame.xid
        self.local_binding, self.local_stream = binding, stream
        self.targets = []
        self.addCleanup(self.retire_targets)

    def retire_targets(self):
        if self.proc.poll() is None:
            for target in self.targets:
                reply = self.control(retire_geometry=target)
                self.assertTrue(reply.HasField('geometry_state'), reply)

    def target(self, generation=2, world='remote-world', actor='observer'):
        value = protocol.GeometryPresentationTarget(protocol_version=1,
            world_id=world, actor_id=actor, geometry_view_id=b'v'*32,
            authority_service_incarnation=b'i'*32, authority_generation=7,
            presentation_generation=generation)
        reply = self.control(attach_geometry=protocol.GeometryPresentationAttach(target=value))
        self.assertEqual(reply.geometry_state.stage, protocol.ATTACHED, reply)
        self.targets.append(value)
        return value

    def view(self, target, sequence=1, color=(51, 34, 17)):
        result = geometry.ActorGeometryView(world_id=target.world_id,
            actor_id=target.actor_id, view_id=target.geometry_view_id,
            authority_generation=target.authority_generation,
            authority_service_incarnation=target.authority_service_incarnation,
            sequence=sequence, room='entry', world_revision=1)
        result.camera.position.SetInParent(); result.camera.orientation.w = 1
        result.config.CopyFrom(core.RenderConfig(vertical_fov=1.57079632679,
            near_clip=.01, far_clip=1000,
            background=core.Color(red=color[0],green=color[1],blue=color[2]),
            barrier_color=core.Color(red=1,green=2,blue=3)))
        return result

    def publish(self, target, view):
        return self.control(publish_geometry=protocol.GeometryPresentationPublish(target=target,view=view))

    def selected(self, target):
        reply = self.control(select_geometry=target)
        self.assertTrue(reply.geometry_state.selected, reply)

    def test_geometry_draw_changes_independent_native_pixels(self):
        self.ready(); target=self.target(); self.selected(target)
        self.assertEqual(self.pixel(self.xid), 0)
        reply=self.publish(target,self.view(target))
        self.assertEqual(reply.geometry_state.last_view_sequence,1,reply)
        self.assertEqual(self.pixel(self.xid),0x332211)
        self.assertFalse(self.control(inspect=self.local_binding).state.selected)

    def test_delayed_source_cannot_repaint_destination(self):
        self.ready(); a=self.target(); b=self.target(3,world='destination')
        self.selected(a); self.publish(a,self.view(a)); self.selected(b)
        self.publish(b,self.view(b,color=(17,34,51)))
        reply=self.publish(a,self.view(a,2,color=(255,0,0)))
        self.assertEqual(reply.error.code,core.FORBIDDEN,reply)
        self.assertEqual(self.pixel(self.xid),0x112233)

    def test_wrong_actor_world_session_and_epoch_reject_before_pixels(self):
        self.ready(); target=self.target(); self.selected(target)
        self.publish(target,self.view(target))
        for name,value in (('actor_id','other'),('world_id','other'),('view_id',b'x'*32),
                           ('authority_generation',8),('authority_service_incarnation',b'x'*32)):
            view=self.view(target,2,color=(255,0,0)); setattr(view,name,value)
            reply=self.publish(target,view)
            self.assertEqual(reply.error.code,core.INVALID_ARGUMENT,(name,reply))
            self.assertEqual(self.pixel(self.xid),0x332211)
        self.assertEqual(self.publish(target,self.view(target,2)).geometry_state.last_view_sequence,2)

    def test_stale_sequence_rejected_and_selection_preserves_sequence(self):
        self.ready(); a=self.target(); b=self.target(3)
        self.selected(a); self.publish(a,self.view(a,5)); self.selected(b); self.selected(a)
        reply=self.publish(a,self.view(a,5,color=(255,0,0)))
        self.assertEqual(reply.error.code,core.INVALID_ARGUMENT,reply)
        self.assertEqual(self.pixel(self.xid),0)
        self.assertEqual(self.publish(a,self.view(a,6)).geometry_state.last_view_sequence,6)

    def test_retirement_clears_pixels_and_fences_delayed_frame(self):
        self.ready(); target=self.target(); self.selected(target); self.publish(target,self.view(target))
        reply=self.control(retire_geometry=target); self.targets.remove(target)
        self.assertEqual(reply.geometry_state.stage,protocol.RETIRED,reply)
        self.assertEqual(reply.geometry_state.last_view_sequence,1,reply)
        self.assertEqual(self.pixel(self.xid),0)
        self.assertEqual(self.publish(target,self.view(target,2)).error.code,core.STALE_TARGET)
        self.assertIsNone(self.proc.poll())

    def test_retiring_unselected_target_preserves_other_world(self):
        self.ready(); a=self.target(); b=self.target(3)
        self.selected(a); self.publish(a,self.view(a)); self.selected(b); self.publish(b,self.view(b,color=(17,34,51)))
        self.control(retire_geometry=a); self.targets.remove(a)
        self.assertEqual(self.pixel(self.xid),0x112233)
        self.assertEqual(self.publish(b,self.view(b,2)).geometry_state.last_view_sequence,2)

    def test_clear_selection_blanks_local_producer_without_retiring_channel(self):
        self.ready()
        reply=self.control(clear_selection=protocol.PresentationEmpty())
        self.assertTrue(reply.selection_cleared,reply);self.assertEqual(self.pixel(self.xid),0)
        state=self.control(inspect=self.local_binding).state
        self.assertEqual(state.stage,protocol.READY);self.assertFalse(state.selected)
        rejected=self.draw(self.local_stream,3,(255,0,0))
        self.assertEqual(rejected.error.code,core.FORBIDDEN,rejected);self.assertEqual(self.pixel(self.xid),0)
        self.assertTrue(self.control(select=self.local_binding).state.selected)
        self.assertTrue(self.draw(self.local_stream,4,(17,34,51)).HasField('frame'))
        self.assertEqual(self.pixel(self.xid),0x112233);self.assertIsNone(self.proc.poll())

    def test_shutdown_requires_checked_geometry_retirement(self):
        self.ready(); target=self.target()
        self.control(detach=self.local_binding); self.worlds.clear(); self.local_stream.close()
        self.assertEqual(self.control(shutdown=protocol.PresentationEmpty()).error.code,core.IN_USE)
        self.control(retire_geometry=target); self.targets.remove(target)
        self.assertIsNone(self.proc.poll())

    def test_local_duplicate_generation_keeps_in_use_contract(self):
        self.ready(); target=self.target()
        reply=self.control(attach_geometry=protocol.GeometryPresentationAttach(target=target))
        self.assertEqual(reply.error.code,core.IN_USE,reply)
        wrong=protocol.GeometryPresentationTarget();wrong.CopyFrom(target);wrong.presentation_generation=1
        self.assertEqual(self.control(attach_geometry=protocol.GeometryPresentationAttach(target=wrong)).error.code,core.IN_USE)
        stream,child=__import__('socket').socketpair();pidfd=os.pidfd_open(os.getpid())
        try:
            reply=self.control((child.fileno(),pidfd),attach=protocol.PresentationAttach(
                binding=self.local_binding,world_pid=os.getpid()))
        finally:stream.close();child.close();os.close(pidfd)
        self.assertEqual(reply.error.code,core.IN_USE,reply)
        self.assertTrue(self.control(select=self.local_binding).state.selected)

    def test_geometry_schema_excludes_application_and_native_authority(self):
        forbidden={'surfaces','xid','pixels','native_input','input','credential','process','pidfd','capture','sync'}
        for message in (geometry.ActorGeometryView,geometry.DrawableGeometry,protocol.GeometryPresentationTarget,protocol.GeometryPresentationPublish):
            self.assertFalse(forbidden & set(message.DESCRIPTOR.fields_by_name))
        self.ready(); target=self.target(); self.selected(target)
        malformed=self.view(target)
        malformed.objects.add(id='app',kind=core.PANEL,pose=core.Pose(orientation=core.Quaternion(w=1)))
        self.assertEqual(self.publish(target,malformed).error.code,core.INVALID_ARGUMENT)
        self.assertEqual(self.pixel(self.xid),0)
        self.assertEqual(self.publish(target,self.view(target)).geometry_state.last_view_sequence,1)
        # Inject excluded wire fields into otherwise valid geometry. Unknown fields
        # must reject at production validation, before native draw or input routing.
        self.owner.operation(2,self.owner.pack('III',self.window,1<<11,15))
        self.owner.operation(43,reply=True)
        before=[event for event in self.owner.events if event[0]&127 in (2,3,4,5)]
        forged=self.view(target,2,color=(255,0,0))
        native_command=core.RenderCommand(protocol_version=1,request_id=2,
            input=core.RendererInputCommand(actor_id='forged',
                key=core.ActorKeyRequest(keysym='a',pressed=True))).SerializeToString()
        self.assertLess(len(native_command),128)
        forged.ParseFromString(forged.SerializeToString()+b'\xa2\x06'+bytes([len(native_command)])+native_command)
        reply=self.publish(target,forged)
        self.assertEqual(reply.error.code,core.INVALID_ARGUMENT,reply)
        self.assertIn('Unknown',reply.error.message)
        self.assertEqual(self.pixel(self.xid),0x332211)
        fd=os.memfd_create('forged-native-descriptor',os.MFD_CLOEXEC)
        try:
            reply=self.control((fd,),publish_geometry=protocol.GeometryPresentationPublish(
                target=target,view=self.view(target,2,color=(255,0,0))))
        finally:os.close(fd)
        self.assertEqual(reply.error.code,core.INVALID_ARGUMENT,reply)
        self.assertIn('descriptor count',reply.error.message)
        self.assertEqual(self.pixel(self.xid),0x332211)
        self.owner.operation(43,reply=True)
        self.assertEqual([event for event in self.owner.events if event[0]&127 in (2,3,4,5)],before)
        self.assertEqual(self.publish(target,self.view(target,2)).geometry_state.last_view_sequence,2)


if __name__=='__main__':
    names=[name for name in GeometryPresentationTests.__dict__ if name.startswith('test_')]
    selected=os.environ.get('SPATIAL_GEOMETRY_BOUNDARY_CASES')
    if selected:
        wanted=selected.split(',')
        if any(name not in names for name in wanted):raise SystemExit('Unknown geometry boundary case')
        names=wanted
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(GeometryPresentationTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
