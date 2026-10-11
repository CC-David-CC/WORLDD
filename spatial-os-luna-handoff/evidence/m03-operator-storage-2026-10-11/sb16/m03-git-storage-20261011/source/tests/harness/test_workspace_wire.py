"""Strict transport checks for workspace authority and process outcomes."""
import unittest
from harness.wire import make_request, result_value, pb


class WorkspaceWireTests(unittest.TestCase):
    def test_false_availability_is_present_and_integers_are_not_booleans(self):
        request=make_request('workspace.available',dict(id='w',available=False))
        self.assertTrue(request.workspace_available.HasField('available'))
        self.assertFalse(request.workspace_available.available)
        for invalid in [0,1,'false',None]:
            with self.assertRaises(ValueError): make_request('workspace.available',dict(id='w',available=invalid))

    def test_unknown_or_nonstructural_rights_are_rejected(self):
        for invalid in [['files.*'],['files.read','administrator'],[True],[2],'files.read']:
            with self.assertRaises(ValueError): make_request('workspace.member.set',dict(id='w',actor='a',rights=invalid))
        request=make_request('workspace.member.set',dict(id='w',actor='a',rights=[]))
        self.assertEqual(request.WhichOneof('operation'),'workspace_member_set')
        self.assertEqual(list(request.workspace_member_set.rights),[])

    def test_launch_arguments_preserve_boundaries_and_reject_extra_fields(self):
        args=dict(argv=['/usr/bin/python3','a b',"$(touch should-not-run)"],operation_id='op',profile='development')
        request=make_request('actor.act',dict(token='t',seq=1,op='process.launch',args=args))
        self.assertEqual(list(request.actor_act.process_launch.argv),args['argv'])
        for invalid in [dict(args,shell=True),dict(args,argv='sh -c x'),dict(args,argv=['x',1])]:
            with self.assertRaises(ValueError): make_request('actor.act',dict(token='t',seq=1,op='process.launch',args=invalid))

    def test_missing_exit_status_cannot_be_decoded_as_success(self):
        reply=pb.Reply(protocol_version=1);reply.process_wait.SetInParent()
        with self.assertRaises(ValueError): result_value(reply)
        for code in [0,17,-9]:
            reply.process_wait.exit_code=code
            self.assertEqual(result_value(reply),dict(exit_code=code))
