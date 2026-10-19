"""Resource-preserving device binding rewrite for the native storage fixture."""


def add_loop_binding(message, budget_type, backing_device, loop_device):
    """Add the fixture loop binding while preserving all inherited ceilings."""
    if message.DESCRIPTOR.full_name == budget_type:
        pairs = [(io.major, io.minor) for io in message.io]
        assert len(pairs) == len(set(pairs)), "I/O device bindings must be unique"
        assert loop_device not in pairs, "fixture loop device is already bound"
        matches = [io for io in message.io if (io.major, io.minor) == backing_device]
        assert len(matches) == 1, "backing device must have exactly one binding"
        io = message.io.add()
        io.CopyFrom(matches[0])
        io.major, io.minor = loop_device
        return
    for field, value in message.ListFields():
        if field.message_type is not None:
            if field.label == field.LABEL_REPEATED:
                for child in value:
                    add_loop_binding(child, budget_type, backing_device, loop_device)
            else:
                add_loop_binding(value, budget_type, backing_device, loop_device)
