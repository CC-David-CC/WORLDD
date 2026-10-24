"""The production private owner rejects malformed local-view selection receipts."""
import unittest
from unittest.mock import patch
import test_presentation_owner as baseline

LOCAL_PEER = r'''if mode.startswith('local_'):
 state=p.LocalActorPresentationState(target=r.select_local_actor,selected=True)
 if mode=='local_identity':state.target.identity.view_id=b'x'*32
 if mode=='local_selected':state.selected=False
 if mode=='local_missing':
  send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True))
 else:send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,local_actor_state=state))
 if mode in ('local_missing','local_identity','local_selected'):receive();sys.exit(0)
 r=receive()
 if not r.HasField('retire_local_actor'):raise RuntimeError('Expected local view retirement')
 state.selected=mode=='local_retire_selected'
 if mode=='local_retire_identity':state.target.identity.actor_id='other'
 send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,local_actor_state=state))
 if mode!='local_success':receive();sys.exit(0)
 r=receive()
 if not r.HasField('shutdown'):raise RuntimeError('Expected checked shutdown')
 send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True))
 sys.exit(0)
'''


class LocalFrameOwnerTests(baseline.PresentationOwnerTests):
    def local_attempt(self, mode, diagnostic):
        marker='r=receive()\n'
        assert marker in baseline.PEER
        peer=baseline.PEER.replace(marker,marker+LOCAL_PEER,1)
        with patch.object(baseline,'PEER',peer):
            self.attempt(mode,diagnostic)

    def test_missing_local_selection_receipt_fences_owner(self):
        self.local_attempt('local_missing','Selected local actor view unconfirmed')

    def test_wrong_selected_identity_fences_owner(self):
        self.local_attempt('local_identity','Selected local actor view unconfirmed')

    def test_unselected_selection_receipt_fences_owner(self):
        self.local_attempt('local_selected','Selected local actor view unconfirmed')

    def test_still_selected_retirement_receipt_fences_owner(self):
        self.local_attempt('local_retire_selected','Retired local actor view unconfirmed')

    def test_wrong_retired_identity_fences_owner(self):
        self.local_attempt('local_retire_identity','Retired local actor view unconfirmed')

    def test_checked_local_selection_retirement_and_shutdown(self):
        self.local_attempt('local_success','local view sequence confirmed')


if __name__=='__main__':
    suite=unittest.TestSuite(LocalFrameOwnerTests(name) for name in LocalFrameOwnerTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
