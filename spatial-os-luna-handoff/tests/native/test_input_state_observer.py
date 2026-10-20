"""Behavioral regressions for the M09 finite-artifact witness validator."""
import unittest
import native_input_observer_pb2 as pb
from input_state_observer import (
    DOWN_BYTES, DOWN_BITS, MAX_ARTIFACT_BYTES, MAX_RECEIPTS, MAX_RECORD_BYTES, MAX_RECORD_COUNT, Limits, ObservationError, decode_and_validate,
)

def varint(value):
    out=bytearray()
    while value>127:
        out.append((value&127)|128); value >>= 7
    out.append(value)
    return bytes(out)

def framed(records):
    out=bytearray()
    for record in records:
        raw=record.SerializeToString(deterministic=True)
        out.extend(varint(len(raw))); out.extend(raw)
    return bytes(out)

class ObserverArtifactTests(unittest.TestCase):
    def setUp(self):
        self.session=b"S"*16
        self.arm_id=b"A"*16
        self.arm=pb.Arm(actor_id=b"I"*32,issuer_id=b"J"*32,peer_actor_id=b"Q"*32,peer_issuer_id=b"J"*32,arm_monotonic_ns=100,deadline_monotonic_ns=500,
                        expected_xi2_event_type=4,expected_detail=1,prearm_receipt_watermark=40)
        self.old=[]
        entries=((pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.MASTER,10,1),
                 (pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.SLAVE,11,2),
                 (pb.ActorDeviceIdentity.KEYBOARD,pb.ActorDeviceIdentity.MASTER,12,3),
                 (pb.ActorDeviceIdentity.KEYBOARD,pb.ActorDeviceIdentity.SLAVE,13,4))
        for kind,role,did,token in entries:
            item=self.arm.old_actor_devices.add(kind=kind,seat_role=role,device_id=did,object_epoch=token)
            self.old.append(item)
        self.arm.expected_recipient.CopyFrom(pb.ClientIdentity(client_id=20,object_epoch=5))
        self.arm.expected_window.CopyFrom(pb.WindowIdentity(window_id=30,object_epoch=6))
        self.arm.expected_peer_device.CopyFrom(pb.ActorDeviceIdentity(kind=pb.ActorDeviceIdentity.POINTER,seat_role=pb.ActorDeviceIdentity.MASTER,device_id=14,object_epoch=7))
        self.arm.expected_peer_source.CopyFrom(pb.ActorDeviceIdentity(kind=pb.ActorDeviceIdentity.POINTER,seat_role=pb.ActorDeviceIdentity.SLAVE,device_id=15,object_epoch=8))
        label=pb.ApplicationLabel(workspace=b"workspace",application=b"application")
        self.arm.expected_recipient.application_label.CopyFrom(label)
        self.arm.expected_window.application_label.CopyFrom(label)
        self.arm.expected_window.spatial_generation=1
        self.arm.peer_actor_devices.add().CopyFrom(self.arm.expected_peer_device)
        self.arm.peer_actor_devices.add().CopyFrom(self.arm.expected_peer_source)
        self.arm.peer_actor_devices.add(kind=pb.ActorDeviceIdentity.KEYBOARD,seat_role=pb.ActorDeviceIdentity.MASTER,device_id=16,object_epoch=9)
        self.arm.peer_actor_devices.add(kind=pb.ActorDeviceIdentity.KEYBOARD,seat_role=pb.ActorDeviceIdentity.SLAVE,device_id=17,object_epoch=10)
        for devices in (self.arm.old_actor_devices,self.arm.peer_actor_devices):
            pm,ps,km,ks=devices
            pm.paired_or_attached=km.device_id
            ps.paired_or_attached=pm.device_id
            km.paired_or_attached=pm.device_id
            ks.paired_or_attached=km.device_id
        self.arm.expected_peer_device.CopyFrom(self.arm.peer_actor_devices[0])
        self.arm.expected_peer_source.CopyFrom(self.arm.peer_actor_devices[1])
        self.records=self.make_records()

    def make_records(self):
        out=[
            pb.NativeInputObservationRecord(protocol_version=1,native_session=7,session_id=self.session,arm_id=self.arm_id,stream_sequence=1),
            pb.NativeInputObservationRecord(protocol_version=1,native_session=7,session_id=self.session,arm_id=self.arm_id,stream_sequence=2),
            pb.NativeInputObservationRecord(protocol_version=1,native_session=7,session_id=self.session,arm_id=self.arm_id,stream_sequence=3),
        ]
        out[0].arm.CopyFrom(self.arm)
        obs=out[1].observer_press; obs.observer_ordinal=1; obs.snapshot_complete=True; obs.callback_monotonic_ns=200
        ev=obs.event; ev.recipient.CopyFrom(self.arm.expected_recipient); ev.window.CopyFrom(self.arm.expected_window)
        ev.device.CopyFrom(self.arm.expected_peer_device); ev.source.CopyFrom(self.arm.expected_peer_source)
        ev.xi2_event_type=4; ev.detail=1; ev.event_time_ms=700
        for identity in self.arm.old_actor_devices:
            obs.old_actor_state.add(identity=identity,bitmap_bits=DOWN_BITS,down_bitmap=bytes(DOWN_BYTES),state_complete=True,bitmap_kind=(pb.NativeDeviceState.BUTTON_CLASS_DOWN if identity.kind==pb.ActorDeviceIdentity.POINTER else pb.NativeDeviceState.KEY_CLASS_DOWN))
        batch=out[2].probe_receipts
        c=batch.context
        c.recipient_object_epoch=self.arm.expected_recipient.object_epoch
        c.window_object_epoch=self.arm.expected_window.object_epoch
        c.device_object_epoch=self.arm.expected_peer_device.object_epoch
        c.source_object_epoch=self.arm.expected_peer_source.object_epoch
        # Preserve intervening key, motion, and release receipts. Ordinals are contiguous.
        batch.receipts.add(receipt_ordinal=41,event_type=3,deviceid=12,sourceid=13,xid=30,detail=42,time=698)
        batch.receipts.add(receipt_ordinal=42,event_type=6,deviceid=14,sourceid=15,xid=30,detail=0,time=699)
        batch.receipts.add(receipt_ordinal=43,event_type=4,deviceid=14,sourceid=15,xid=30,detail=1,time=700)
        return out

    def parse(self, records=None, limits=Limits(), expected_arm=None):
        return decode_and_validate(framed(self.records if records is None else records),
            expected_native_session=7,expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=(self.arm if expected_arm is None else expected_arm),limits=limits)

    def test_positive_framed_artifact_correlates_first_press_after_intervening_inputs(self):
        self.assertEqual(self.parse().observer_ordinal,1)

    def test_finite_artifact_rejects_truncation_missing_frames_and_nonbytes_without_reading(self):
        artifact=framed(self.records)
        class BlockingSource:
            def read(self,*args):
                raise AssertionError("parser must not read an unbounded stream")
        with self.assertRaises(ObservationError):
            decode_and_validate(BlockingSource(),expected_native_session=7,expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm)
        for broken in (artifact[:-1],b"\x80",framed(self.records[:2])):
            with self.subTest(length=len(broken)),self.assertRaises(ObservationError):
                decode_and_validate(broken,expected_native_session=7,expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm)

    def test_hard_byte_record_receipt_and_artifact_limits_reject_invalid_values(self):
        artifact=framed(self.records)
        for limits in (Limits(max_record_bytes=MAX_RECORD_BYTES+1),Limits(max_record_count=True),
                       Limits(max_receipts=1.5),Limits(max_receipts=-1),
                       Limits(max_artifact_bytes=MAX_ARTIFACT_BYTES+1),
                       Limits(max_artifact_bytes=len(artifact)-1)):
            with self.subTest(limits=limits),self.assertRaises(ObservationError):
                decode_and_validate(artifact,expected_native_session=7,expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm,limits=limits)
        with self.assertRaises(ObservationError):
            decode_and_validate(artifact+b"X",expected_native_session=7,expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm)

    def test_each_limit_field_rejects_invalid_types_zero_negative_and_excess(self):
        caps={"max_record_bytes":MAX_RECORD_BYTES,"max_record_count":MAX_RECORD_COUNT,
              "max_receipts":MAX_RECEIPTS,"max_artifact_bytes":MAX_ARTIFACT_BYTES}
        for field,cap in caps.items():
            for value in (True,1.5,0,-1,cap+1):
                with self.subTest(field=field,value=value),self.assertRaisesRegex(ObservationError,"invalid or excessive limits"):
                    self.parse(limits=Limits(**{field:value}))
        with self.assertRaisesRegex(ObservationError,"invalid limits type"):
            self.parse(limits=None)

    def test_envelope_version_session_arm_order_and_record_count_are_exact(self):
        for mutation in ("version","session","arm","sequence","payload","native-session"):
            records=self.make_records()
            if mutation=="version": records[1].protocol_version=2
            elif mutation=="session": records[1].session_id=b"X"*16
            elif mutation=="arm": records[1].arm_id=b"X"*16
            elif mutation=="sequence": records[1].stream_sequence=1
            elif mutation=="native-session": records[1].native_session=8
            else: records[1].arm.CopyFrom(self.arm)
            with self.subTest(mutation=mutation),self.assertRaises(ObservationError):
                self.parse(records)
        extra=self.make_records()+[self.make_records()[2]]
        with self.assertRaises(ObservationError): self.parse(extra)

    def test_old_actor_requires_four_unique_typed_live_device_identities(self):
        for mutation in ("missing","duplicate-pair","zero-id","peer-overlap","token-overlap"):
            records=self.make_records(); arm=records[0].arm
            if mutation=="missing": del arm.old_actor_devices[3]
            elif mutation=="duplicate-pair": arm.old_actor_devices[3].CopyFrom(arm.old_actor_devices[0])
            elif mutation=="zero-id": arm.old_actor_devices[0].device_id=0
            elif mutation=="peer-overlap":
                arm.old_actor_devices[0].device_id=arm.expected_peer_device.device_id
                # Keep the old hierarchy internally valid to reach the disjoint-set guard.
                arm.old_actor_devices[1].paired_or_attached=arm.old_actor_devices[0].device_id
                arm.old_actor_devices[2].paired_or_attached=arm.old_actor_devices[0].device_id
            else: arm.old_actor_devices[0].object_epoch=arm.old_actor_devices[1].object_epoch
            reason={"missing":"four distinct old", "duplicate-pair":"four distinct old",
                    "zero-id":"native device ID invalid", "peer-overlap":"device IDs overlap",
                    "token-overlap":"epochs overlap"}[mutation]
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ObservationError,reason):
                self.parse(records,expected_arm=arm)

    def test_all_four_native_classes_require_full_256_bit_clear_snapshots(self):
        for index in range(4):
            for mutation in ("short","wrong-bits","held","incomplete"):
                records=self.make_records(); state=records[1].observer_press.old_actor_state[index]
                if mutation=="short": state.down_bitmap=b"\0"
                elif mutation=="wrong-bits": state.bitmap_bits=8
                elif mutation=="held": state.down_bitmap=bytes(DOWN_BYTES-1)+bytes([128])
                else: state.state_complete=False
                with self.subTest(index=index,mutation=mutation),self.assertRaises(ObservationError): self.parse(records)

    def test_old_state_duplicate_missing_and_lifetime_reuse_fail(self):
        for mutation in ("missing","duplicate","reused"):
            records=self.make_records(); states=records[1].observer_press.old_actor_state
            if mutation=="missing": del states[3]
            elif mutation=="duplicate": states[3].CopyFrom(states[0])
            else: states[0].identity.object_epoch=99
            with self.subTest(mutation=mutation),self.assertRaises(ObservationError): self.parse(records)

    def test_receipt_gap_duplicate_overflow_disconnect_and_count_fail(self):
        for mutation in ("gap","duplicate","overflow","disconnect","too-many","missing"):
            records=self.make_records(); batch=records[2].probe_receipts
            if mutation=="gap": batch.receipts[1].receipt_ordinal+=1
            elif mutation=="duplicate": batch.receipts[1].receipt_ordinal=batch.receipts[0].receipt_ordinal
            elif mutation=="overflow": batch.overflow=True
            elif mutation=="disconnect": batch.disconnected=True
            elif mutation=="too-many":
                while len(batch.receipts)<=MAX_RECEIPTS: batch.receipts.add().CopyFrom(batch.receipts[0])
            else: del batch.receipts[:]
            with self.subTest(mutation=mutation),self.assertRaises(ObservationError): self.parse(records)

    def test_first_peer_press_cannot_be_skipped_for_a_later_success(self):
        records=self.make_records(); batch=records[2].probe_receipts
        first=batch.receipts[2]; first.detail=2
        later=batch.receipts.add(); later.CopyFrom(batch.receipts[2]); later.receipt_ordinal=44; later.detail=1; later.time=700
        with self.assertRaises(ObservationError): self.parse(records)
        records=self.make_records(); records[2].probe_receipts.receipts[2].synthetic=True
        with self.assertRaises(ObservationError): self.parse(records)

    def test_probe_tuple_time_context_and_observer_identity_must_match(self):
        for mutation in ("device","source","window","detail","time","type","synthetic","context","stale-window"):
            records=self.make_records(); batch=records[2].probe_receipts; receipt=batch.receipts[-1]
            if mutation=="device": receipt.deviceid=99
            elif mutation=="source": receipt.sourceid=99
            elif mutation=="window": receipt.xid=99
            elif mutation=="detail": receipt.detail=2
            elif mutation=="time": receipt.time=701
            elif mutation=="type": receipt.event_type=2
            elif mutation=="synthetic": receipt.synthetic=True
            elif mutation=="context": batch.context.window_object_epoch=99
            else: records[1].observer_press.event.window.object_epoch=99
            with self.subTest(mutation=mutation),self.assertRaises(ObservationError): self.parse(records)

    def test_no_peer_press_wrong_arm_interval_or_transport_loss_fails(self):
        for mutation in ("no-peer","late-observer","observer-overflow","observer-disconnect","probe-overflow","probe-disconnect","bad-ordinal"):
            records=self.make_records()
            if mutation=="no-peer": del records[2].probe_receipts.receipts[2]
            elif mutation=="late-observer": records[1].observer_press.callback_monotonic_ns=501
            elif mutation=="observer-overflow": records[1].observer_press.overflow=True
            elif mutation=="observer-disconnect": records[1].observer_press.disconnected=True
            elif mutation=="probe-overflow": records[2].probe_receipts.overflow=True
            elif mutation=="probe-disconnect": records[2].probe_receipts.disconnected=True
            else: records[2].probe_receipts.receipts[-1].receipt_ordinal=40
            with self.subTest(mutation=mutation),self.assertRaises(ObservationError): self.parse(records)

    def test_each_native_device_uses_its_actual_class_bitmap(self):
        for index in range(4):
            records=self.make_records()
            state=records[1].observer_press.old_actor_state[index]
            if state.identity.kind==pb.ActorDeviceIdentity.POINTER:
                state.bitmap_kind=pb.NativeDeviceState.KEY_CLASS_DOWN
            else:
                state.bitmap_kind=pb.NativeDeviceState.BUTTON_CLASS_DOWN
            with self.assertRaises(ObservationError):
                self.parse(records)


    def test_actor_and_issuer_ids_require_production_32_byte_identities(self):
        for field in ("actor_id","issuer_id","peer_actor_id","peer_issuer_id"):
            for size in (0,16,31,33):
                records=self.make_records()
                arm=records[0].arm
                setattr(arm,field,b"X"*size)
                with self.subTest(field=field,size=size),self.assertRaisesRegex(ObservationError,"requires 32 bytes"):
                    self.parse(records,expected_arm=arm)
        records=self.make_records()
        arm=records[0].arm
        arm.peer_actor_id=arm.actor_id
        with self.assertRaisesRegex(ObservationError,"actors must differ"):
            self.parse(records,expected_arm=arm)

    def test_peer_requires_four_disjoint_paired_device_lifetimes(self):
        cases=("missing","pair","zero-epoch","duplicate-epoch","old-overlap","zero-id","pointer-pin")
        for case in cases:
            records=self.make_records()
            arm=records[0].arm
            if case=="missing": del arm.peer_actor_devices[3]
            elif case=="pair": arm.peer_actor_devices[3].paired_or_attached=99
            elif case=="zero-epoch": arm.peer_actor_devices[3].object_epoch=0
            elif case=="duplicate-epoch": arm.peer_actor_devices[3].object_epoch=arm.old_actor_devices[0].object_epoch
            elif case=="old-overlap": arm.peer_actor_devices[3].device_id=arm.old_actor_devices[3].device_id
            elif case=="zero-id": arm.peer_actor_devices[3].device_id=0
            else: arm.expected_peer_device.object_epoch=99
            reason={"missing":"four distinct peer","pair":"pairing", "zero-epoch":"device lifetime", "duplicate-epoch":"epochs overlap", "old-overlap":"device IDs overlap", "zero-id":"native device ID", "pointer-pin":"pointer master mismatch"}[case]
            with self.subTest(case=case),self.assertRaisesRegex(ObservationError,reason):
                self.parse(records,expected_arm=arm)

    def test_application_labels_follow_production_issuance_byte_constraints(self):
        # Production labels are bytes, not UTF-8 text. Non-NUL high bytes are valid.
        for label in (self.arm.expected_recipient.application_label,self.arm.expected_window.application_label):
            label.workspace=b"\xff\xfe"
            label.application=b"\x80\xff"
        self.assertEqual(self.parse(self.make_records()).observer_ordinal,1)
        for field in ("workspace","application"):
            for invalid in (b"",b"a\0b",b"x"*257):
                records=self.make_records()
                arm=records[0].arm
                for label in (arm.expected_recipient.application_label,arm.expected_window.application_label):
                    setattr(label,field,invalid)
                with self.subTest(field=field,invalid=invalid),self.assertRaisesRegex(ObservationError,"application label mismatch or invalid"):
                    self.parse(records,expected_arm=arm)

    def test_client_window_labels_epochs_generation_and_arm_interval_are_required(self):
        for case,reason in (("label","application label"),("epoch","lifetime invalid"),("generation","generation missing"),("interval","invalid arm interval"),("detail","valid XI_ButtonPress")):
            records=self.make_records()
            arm=records[0].arm
            if case=="label": arm.expected_window.application_label.application=b"other"
            elif case=="epoch": arm.expected_recipient.object_epoch=0
            elif case=="generation": arm.expected_window.spatial_generation=0
            elif case=="interval": arm.deadline_monotonic_ns=arm.arm_monotonic_ns
            else: arm.expected_detail=0
            with self.subTest(case=case),self.assertRaisesRegex(ObservationError,reason):
                self.parse(records,expected_arm=arm)

    def test_first_wrong_peer_target_or_source_cannot_be_replaced(self):
        for field,value in (("sourceid",99),("deviceid",99),("xid",99),("time",701),("detail",2)):
            records=self.make_records()
            receipts=records[2].probe_receipts.receipts
            first=receipts[-1]
            later=receipts.add()
            later.CopyFrom(first)
            later.receipt_ordinal=44
            setattr(first,field,value)
            with self.subTest(field=field),self.assertRaisesRegex(ObservationError,"past first peer ButtonPress"):
                self.parse(records)

    def test_unknown_duplicate_noncanonical_payload_and_frame_fields_fail(self):
        raw=self.records[0].SerializeToString(deterministic=True)
        tail=framed(self.records[1:])
        # Unknown field, duplicate version, noncanonical version encoding, noncanonical frame prefix.
        for bad in (raw+b"\xf8\x07\x01",raw+b"\x08\x01",b"\x08\x81\x00"+raw[2:]):
            with self.assertRaisesRegex(ObservationError,"noncanonical fields"):
                decode_and_validate(varint(len(bad))+bad+tail,expected_native_session=7,
                    expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm)
        size=varint(len(raw))
        bad_prefix=size[:-1]+bytes([size[-1]|128,0])
        with self.assertRaisesRegex(ObservationError,"noncanonical frame"):
            decode_and_validate(bad_prefix+raw+tail,expected_native_session=7,
                expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm)

    def test_tightened_valid_limits_exercise_record_receipt_and_record_count_branches(self):
        for limits,reason in ((Limits(max_record_bytes=1),"frame byte limit"),
                              (Limits(max_record_count=2),"record count limit"),
                              (Limits(max_receipts=2),"receipt count invalid")):
            with self.subTest(limits=limits),self.assertRaisesRegex(ObservationError,reason):
                self.parse(limits=limits)

    def test_invalid_native_session_and_watermark_overflow_fail_closed(self):
        artifact=framed(self.records)
        for value in (0,True,-1,1.5,2**64):
            with self.subTest(value=value),self.assertRaisesRegex(ObservationError,"invalid native session"):
                decode_and_validate(artifact,expected_native_session=value,
                    expected_session_id=self.session,expected_arm_id=self.arm_id,expected_arm=self.arm)
        records=self.make_records()
        arm=records[0].arm
        arm.prearm_receipt_watermark=2**64-1
        with self.assertRaisesRegex(ObservationError,"ordinals not contiguous"):
            self.parse(records,expected_arm=arm)

if __name__=="__main__": unittest.main()
