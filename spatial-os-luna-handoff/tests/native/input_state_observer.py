"""Finite-artifact parser for the M09 qualification-only native input witness."""
from dataclasses import dataclass
import native_input_observer_pb2 as pb

PROTOCOL_VERSION=1
MAX_RECORD_BYTES=4096
MAX_RECORD_COUNT=3
MAX_RECEIPTS=32
MAX_ARTIFACT_BYTES=MAX_RECORD_COUNT*(MAX_RECORD_BYTES+2)
TOKEN_BYTES=16
ACTOR_BYTES=32
DOWN_BITS=256
DOWN_BYTES=32
XI_KEY_PRESS,XI_KEY_RELEASE,XI_BUTTON_PRESS,XI_BUTTON_RELEASE,XI_MOTION=2,3,4,5,6
INPUT_EVENT_TYPES={2,3,4,5,6}

class ObservationError(ValueError):
    """Finite artifact is incomplete, ambiguous, stale, or invalid."""

@dataclass(frozen=True)
class Limits:
    max_record_bytes:int=MAX_RECORD_BYTES
    max_record_count:int=MAX_RECORD_COUNT
    max_receipts:int=MAX_RECEIPTS
    max_artifact_bytes:int=MAX_ARTIFACT_BYTES

def _check_limits(limits):
    if type(limits) is not Limits: raise ObservationError("invalid limits type")
    values=(limits.max_record_bytes,limits.max_record_count,limits.max_receipts,limits.max_artifact_bytes)
    ceilings=(MAX_RECORD_BYTES,MAX_RECORD_COUNT,MAX_RECEIPTS,MAX_ARTIFACT_BYTES)
    if any(type(v) is not int or v<1 or v>cap for v,cap in zip(values,ceilings)):
        raise ObservationError("invalid or excessive limits")

def _varint(data,offset,bound):
    value=0; start=offset
    for shift in range(0,35,7):
        if offset>=len(data): raise ObservationError("truncated frame length")
        byte=data[offset]; offset+=1; value|=(byte&127)<<shift
        if byte<128:
            if value>bound: raise ObservationError("frame byte limit")
            if offset-start>1 and byte==0: raise ObservationError("noncanonical frame length")
            return value,offset
    raise ObservationError("frame length overflow")

def _encode_varint(value):
    out=bytearray()
    while value>127:
        out.append((value&127)|128); value>>=7
    out.append(value)
    return bytes(out)

def decode_records(artifact:bytes,limits:Limits=Limits()):
    """Decode only a complete immutable byte artifact. Never reads a descriptor."""
    _check_limits(limits)
    if type(artifact) is not bytes: raise ObservationError("finite bytes artifact required")
    if len(artifact)>limits.max_artifact_bytes: raise ObservationError("artifact byte limit")
    records=[]; offset=0
    while offset<len(artifact):
        if len(records)>=limits.max_record_count: raise ObservationError("record count limit")
        size,offset=_varint(artifact,offset,limits.max_record_bytes)
        if size==0: raise ObservationError("empty record")
        end=offset+size
        if end>len(artifact): raise ObservationError("truncated protobuf")
        raw=artifact[offset:end]; offset=end
        record=pb.NativeInputObservationRecord()
        try: record.ParseFromString(raw)
        except Exception as exc: raise ObservationError("invalid protobuf") from exc
        record.DiscardUnknownFields()
        if record.SerializeToString(deterministic=True)!=raw: raise ObservationError("unknown, duplicate, or noncanonical fields")
        records.append(record)
    if len(records)!=3: raise ObservationError("artifact needs exactly three records")
    return records

def _same(a,b):
    return a.SerializeToString(deterministic=True)==b.SerializeToString(deterministic=True)

def _token(value):
    return type(value) is bytes and len(value)==TOKEN_BYTES

def _epoch(value):
    return type(value) is int and 0<value<=0xffffffffffffffff

def _application(label):
    return (0<len(label.workspace)<=256 and b"\0" not in label.workspace
            and 0<len(label.application)<=256 and b"\0" not in label.application)

def _pair(identity):
    return identity.kind,identity.seat_role

def validate_records(records,*,expected_session_id:bytes,expected_arm_id:bytes,
                     expected_arm:pb.Arm,expected_native_session:int,limits:Limits=Limits()):
    _check_limits(limits)
    if type(records) is not list or len(records)!=3 or len(records)>limits.max_record_count:
        raise ObservationError("missing or extra records")
    if type(expected_arm) is not pb.Arm:
        raise ObservationError("invalid expected arm type")
    if any(type(record) is not pb.NativeInputObservationRecord for record in records):
        raise ObservationError("invalid record object")
    raw_records=[record.SerializeToString(deterministic=True) for record in records]
    for record,raw in zip(records,raw_records):
        clean=pb.NativeInputObservationRecord()
        clean.CopyFrom(record)
        clean.DiscardUnknownFields()
        if clean.SerializeToString(deterministic=True)!=raw:
            raise ObservationError("unknown protobuf fields")
    if any(len(raw)>limits.max_record_bytes for raw in raw_records):
        raise ObservationError("record byte limit")
    if sum(len(_encode_varint(len(raw)))+len(raw) for raw in raw_records)>limits.max_artifact_bytes:
        raise ObservationError("artifact byte limit")
    if not _epoch(expected_native_session):
        raise ObservationError("invalid native session")
    if not _token(expected_session_id) or not _token(expected_arm_id):
        raise ObservationError("invalid session/arm identity")
    for seq,record in enumerate(records,1):
        if record.native_session!=expected_native_session: raise ObservationError("native session mismatch")
        if record.protocol_version!=PROTOCOL_VERSION: raise ObservationError("version mismatch")
        if record.session_id!=expected_session_id or record.arm_id!=expected_arm_id: raise ObservationError("session/arm mismatch")
        if record.stream_sequence!=seq: raise ObservationError("missing, duplicate, replayed, or reordered record")
    ar,orr,pr=records
    if ar.WhichOneof("payload")!="arm" or not _same(ar.arm,expected_arm): raise ObservationError("arm mismatch")
    if orr.WhichOneof("payload")!="observer_press": raise ObservationError("observer press missing")
    if pr.WhichOneof("payload")!="probe_receipts": raise ObservationError("probe receipt batch missing")
    arm=expected_arm; observed=orr.observer_press; batch=pr.probe_receipts
    if observed.observer_ordinal!=1: raise ObservationError("not first observer press")
    if observed.overflow or observed.disconnected or batch.overflow or batch.disconnected: raise ObservationError("observer/probe overflow or disconnect")
    if not observed.snapshot_complete: raise ObservationError("native snapshot incomplete")
    if not 0<arm.arm_monotonic_ns<arm.deadline_monotonic_ns:
        raise ObservationError("invalid arm interval")
    if not arm.arm_monotonic_ns<=observed.callback_monotonic_ns<=arm.deadline_monotonic_ns:
        raise ObservationError("observer outside arm interval")
    if any(len(x)!=ACTOR_BYTES for x in (arm.actor_id,arm.issuer_id,arm.peer_actor_id,arm.peer_issuer_id)):
        raise ObservationError("actor/issuer identity requires 32 bytes")
    if (arm.actor_id,arm.issuer_id)==(arm.peer_actor_id,arm.peer_issuer_id):
        raise ObservationError("old and peer actors must differ")
    if not _epoch(arm.expected_recipient.object_epoch) or not _epoch(arm.expected_window.object_epoch):
        raise ObservationError("client/window lifetime invalid")
    if arm.expected_recipient.client_id==0 or arm.expected_window.window_id==0:
        raise ObservationError("zero client/window ID")
    if not _application(arm.expected_recipient.application_label) or not _same(arm.expected_recipient.application_label,arm.expected_window.application_label):
        raise ObservationError("client/window application label mismatch or invalid")
    if not arm.expected_window.spatial_generation:
        raise ObservationError("window spatial generation missing")
    if arm.expected_xi2_event_type!=XI_BUTTON_PRESS or not 1<=arm.expected_detail<=255:
        raise ObservationError("arm requires a valid XI_ButtonPress detail")
    required={
        (pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.MASTER),
        (pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.SLAVE),
        (pb.ActorDeviceIdentity.KEYBOARD,pb.ActorDeviceIdentity.MASTER),
        (pb.ActorDeviceIdentity.KEYBOARD,pb.ActorDeviceIdentity.SLAVE),
    }
    old=list(arm.old_actor_devices)
    peers=list(arm.peer_actor_devices)
    for name,devices in (("old",old),("peer",peers)):
        pairs=[_pair(x) for x in devices]
        if len(devices)!=4 or set(pairs)!=required:
            raise ObservationError("four distinct "+name+" actor devices required")
        if any(not 0<x.device_id<=0xffff for x in devices):
            raise ObservationError("native device ID invalid")
        if any(not _epoch(x.object_epoch) for x in devices):
            raise ObservationError("device lifetime invalid")
        by_pair={_pair(x):x for x in devices}
        pm=by_pair[(pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.MASTER)]
        ps=by_pair[(pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.SLAVE)]
        km=by_pair[(pb.ActorDeviceIdentity.KEYBOARD,pb.ActorDeviceIdentity.MASTER)]
        ks=by_pair[(pb.ActorDeviceIdentity.KEYBOARD,pb.ActorDeviceIdentity.SLAVE)]
        if (pm.paired_or_attached,ps.paired_or_attached,km.paired_or_attached,ks.paired_or_attached)!=(km.device_id,pm.device_id,pm.device_id,km.device_id):
            raise ObservationError("actor device pairing or attachment mismatch")
    all_devices=old+peers
    if len({x.device_id for x in all_devices})!=8:
        raise ObservationError("actor device IDs overlap")
    epochs=[x.object_epoch for x in all_devices]
    epochs.extend((arm.expected_recipient.object_epoch,arm.expected_window.object_epoch))
    if len(set(epochs))!=len(epochs):
        raise ObservationError("object lifetime epochs overlap")
    peer,source=arm.expected_peer_device,arm.expected_peer_source
    peer_by_pair={_pair(x):x for x in peers}
    if not _same(peer,peer_by_pair[(pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.MASTER)]):
        raise ObservationError("peer pointer master mismatch")
    if not _same(source,peer_by_pair[(pb.ActorDeviceIdentity.POINTER,pb.ActorDeviceIdentity.SLAVE)]):
        raise ObservationError("peer pointer slave mismatch")
    states={}
    for state in observed.old_actor_state:
        key=_pair(state.identity)
        if key in states: raise ObservationError("duplicate native state pair")
        states[key]=state
    if set(states)!=required or len(observed.old_actor_state)!=4: raise ObservationError("all four native states required")
    old_by_pair={_pair(x):x for x in old}
    for key,state in states.items():
        if not _same(state.identity,old_by_pair[key]): raise ObservationError("retired/reused device identity")
        bitmap_kind=pb.NativeDeviceState.BUTTON_CLASS_DOWN if state.identity.kind==pb.ActorDeviceIdentity.POINTER else pb.NativeDeviceState.KEY_CLASS_DOWN
        if state.bitmap_kind!=bitmap_kind: raise ObservationError("native bitmap class mismatch")
        if state.bitmap_bits!=DOWN_BITS or len(state.down_bitmap)!=DOWN_BYTES or not state.state_complete: raise ObservationError("native class bitmap incomplete or wrong width")
        if any(state.down_bitmap): raise ObservationError("old native key/button remains held")
    event=observed.event
    if event.synthetic or event.xi2_event_type!=XI_BUTTON_PRESS or event.detail!=arm.expected_detail: raise ObservationError("wrong or synthetic observer press")
    if not _same(event.recipient,arm.expected_recipient) or not _same(event.window,arm.expected_window): raise ObservationError("observer client/window lifetime mismatch")
    if not _same(event.device,peer) or not _same(event.source,source): raise ObservationError("observer device/source lifetime mismatch")
    context=batch.context
    context_tokens=(context.recipient_object_epoch,context.window_object_epoch,context.device_object_epoch,context.source_object_epoch)
    expected_context=(arm.expected_recipient.object_epoch,arm.expected_window.object_epoch,peer.object_epoch,source.object_epoch)
    if context_tokens!=expected_context: raise ObservationError("supervisor-pinned probe context mismatch")
    receipts=list(batch.receipts)
    if not receipts or len(receipts)>min(limits.max_receipts,MAX_RECEIPTS): raise ObservationError("probe receipt count invalid")
    match=None
    for index,receipt in enumerate(receipts):
        ordinal=arm.prearm_receipt_watermark+index+1
        if ordinal>0xffffffffffffffff or receipt.receipt_ordinal!=ordinal: raise ObservationError("probe ordinals not contiguous from watermark")
        if receipt.synthetic: raise ObservationError("synthetic probe input")
        if not 0<receipt.deviceid<=0xffff or not 0<receipt.sourceid<=0xffff or not receipt.xid:
            raise ObservationError("probe native identity invalid")
        if receipt.event_type not in INPUT_EVENT_TYPES: raise ObservationError("unfiltered XI2 input record invalid")
        if receipt.event_type==XI_BUTTON_PRESS and (receipt.deviceid==peer.device_id or receipt.sourceid==source.device_id):
            match=(index,receipt); break
    if match is None: raise ObservationError("first peer ButtonPress missing")
    index,receipt=match
    if index!=len(receipts)-1: raise ObservationError("receipts continue past first peer ButtonPress")
    if (receipt.deviceid,receipt.sourceid,receipt.xid,receipt.event_type,receipt.detail,receipt.time)!=(event.device.device_id,event.source.device_id,event.window.window_id,event.xi2_event_type,event.detail,event.event_time_ms):
        raise ObservationError("first peer ButtonPress does not match observer")
    return observed

def decode_and_validate(artifact:bytes,*,expected_session_id:bytes,expected_arm_id:bytes,
                       expected_arm:pb.Arm,expected_native_session:int,limits:Limits=Limits()):
    return validate_records(decode_records(artifact,limits),expected_session_id=expected_session_id,
                            expected_arm_id=expected_arm_id,expected_arm=expected_arm,expected_native_session=expected_native_session,limits=limits)
