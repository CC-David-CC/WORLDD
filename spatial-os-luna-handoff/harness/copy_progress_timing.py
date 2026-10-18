"""Bound only fixture observation; production copy and control deadlines stay separate."""
import time

COPY_JOB_OBSERVATION_SECONDS = 300


def poll_copy_job_before_deadline(poll, deadline, *, now=time.monotonic):
    """Reject an overdue observation before returning any receipt to the caller."""
    if now() >= deadline:
        raise AssertionError('copy job exceeded its absolute observation deadline')
    reply = poll()
    if now() >= deadline:
        raise AssertionError('copy job exceeded its absolute observation deadline')
    return reply
