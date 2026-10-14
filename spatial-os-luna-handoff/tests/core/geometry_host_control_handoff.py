"""Validate and adopt the first native host-control response."""
from dataclasses import dataclass


class HostControlHandoffError(ValueError):
    """The startup control reply cannot seed a trusted request cursor."""


@dataclass(frozen=True)
class HostControlCursor:
    sequence: int
    generation: int


def adopt_initial_host_control(reply, *, expected_request_id=1):
    """Validate the initial inspect reply and seed the next request from it."""
    if type(expected_request_id) is not int or expected_request_id <= 0:
        raise HostControlHandoffError('Invalid expected startup request ID')
    if (reply.protocol_version != 1 or reply.request_id != expected_request_id or
            not reply.accepted or not reply.HasField('inspection') or
            reply.HasField('transition') or reply.HasField('action') or
            reply.HasField('geometry_status') or reply.cleanup_complete):
        raise HostControlHandoffError('Invalid initial host-control reply context')
    inspection = reply.inspection
    if (inspection.protocol_version != 1 or inspection.request_id <= 0 or
            inspection.WhichOneof('outcome') != 'state' or not inspection.state.id):
        raise HostControlHandoffError('Initial host-control inspection is malformed')
    return HostControlCursor(sequence=reply.request_id, generation=reply.generation)
