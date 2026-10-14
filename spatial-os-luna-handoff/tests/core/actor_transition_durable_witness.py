"""Read one live world entity through SQLite's read-only interface using protobuf frames."""
import sqlite3
import struct
import sys
from contextlib import closing
from pathlib import Path

import actor_transition_crosshost_pb2 as schema

LIMIT = 1024 * 1024


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            raise EOFError("truncated protobuf request")
        data.extend(part)
    return bytes(data)


def main():
    header = exact(sys.stdin.buffer, 4)
    size = struct.unpack("!I", header)[0]
    if not 0 < size <= LIMIT:
        raise ValueError("invalid protobuf request length")
    request = schema.ActorTransitionDurableReadRequest.FromString(exact(sys.stdin.buffer, size))
    reply = schema.ActorTransitionDurableReadReply(protocol_version=1)
    try:
        path = Path(request.database_path)
        if not path.is_absolute() or path.name != "world.db" or not request.actor_id or not request.expected_world_id:
            raise ValueError("invalid read-only world database request")
        uri = path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as database:
            database.execute("PRAGMA query_only=ON")
            world = database.execute("SELECT world_id,world_revision FROM metadata WHERE singleton=1").fetchone()
            if world is None or world[0] != request.expected_world_id or world[1] < 1:
                raise ValueError("durable database world identity mismatch")
            row = database.execute("SELECT data FROM entities WHERE id=?", (request.actor_id,)).fetchone()
        reply.world_id, reply.world_revision = world
        if row is not None:
            entity = bytes(row[0])
            if not 0 < len(entity) <= LIMIT:
                raise ValueError("durable actor entity exceeds the protobuf frame limit")
            reply.entity = entity
            reply.actor_present = True
    except Exception as error:
        reply.error = str(error)
    payload = reply.SerializeToString()
    if len(payload) > LIMIT:
        raise ValueError("durable witness reply exceeds the protobuf frame limit")
    sys.stdout.buffer.write(struct.pack("!I", len(payload)) + payload)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
