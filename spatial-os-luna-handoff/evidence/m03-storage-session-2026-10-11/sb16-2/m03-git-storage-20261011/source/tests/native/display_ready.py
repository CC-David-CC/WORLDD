"""Read the native server's display-number notice without owning its pipe."""
import os
import select
import time


def read_display_notice(descriptor, timeout=5):
    deadline = time.monotonic()+timeout
    data = b''
    while not data.endswith(b'\n') and len(data) < 64:
        remaining = deadline-time.monotonic()
        if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]: break
        part = os.read(descriptor, 64-len(data))
        if not part: break
        data += part
    return data
