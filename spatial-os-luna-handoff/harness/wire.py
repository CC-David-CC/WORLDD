"""Protobuf transport and typed value conversion. No world behavior lives here."""
import math
import struct
import core_pb2 as pb

MAX_FRAME = 1024 * 1024


def finite(message):
    for field, value in message.ListFields():
        values = value if field.label == field.LABEL_REPEATED else [value]
        for item in values:
            if field.type == field.TYPE_MESSAGE:
                finite(item)
            elif field.type in (field.TYPE_DOUBLE, field.TYPE_FLOAT) and not math.isfinite(item):
                raise ValueError('Non-finite protobuf number')


def validate_reply(reply):
    clean = pb.Reply()
    clean.CopyFrom(reply)
    clean.DiscardUnknownFields()
    if clean.SerializeToString(deterministic=True) != reply.SerializeToString(deterministic=True):
        raise ValueError('Unknown protobuf reply field')
    if reply.protocol_version != 1:
        raise ValueError('Unsupported protobuf reply version')
    if reply.WhichOneof('outcome') is None:
        raise ValueError('Response needs exactly one outcome')
    finite(reply)


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            raise EOFError('SUT closed stdout during protobuf frame')
        data.extend(chunk)
    return bytes(data)


def read_message(stream, message_type):
    length, = struct.unpack('!I', read_exact(stream, 4))
    if length > MAX_FRAME:
        raise ValueError('Oversized protobuf frame')
    message = message_type()
    message.ParseFromString(read_exact(stream, length))
    return message


def frame(message):
    finite(message)
    payload = message.SerializeToString()
    if len(payload) > MAX_FRAME:
        raise ValueError('Oversized protobuf frame')
    return struct.pack('!I', len(payload)) + payload


def numbers(value, count):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError('Wrong vector shape')
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('Expected finite numbers')
    return value


def vec3(value):
    x, y, z = numbers(value, 3)
    return pb.Vec3(x=x, y=y, z=z)


def vec2(value):
    x, y = numbers(value, 2)
    return pb.Vec2(x=x, y=y)


def pose(value):
    result = pb.Pose()
    if set(value) - {'p', 'q'}:
        raise ValueError('Unknown pose fields')
    if 'p' in value:
        result.position.CopyFrom(vec3(value['p']))
    if 'q' in value:
        x, y, z, w = numbers(value['q'], 4)
        result.orientation.CopyFrom(pb.Quaternion(x=x, y=y, z=z, w=w))
    return result


def entity(value):
    result = pb.Entity()
    allowed = {'id', 'kind', 'room', 'label', 'pose', 'size', 'color', 'bounds',
               'destination', 'revision', 'world_revision'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unknown entity fields')
    for name, item in value.items():
        if name in ('id', 'room', 'label'):
            if type(item) is not str:
                raise ValueError('Expected entity string')
            setattr(result, name, item)
        elif name == 'kind':
            result.kind = pb.EntityKind.Value(item.upper())
        elif name in ('revision', 'world_revision'):
            if type(item) is not int or not 0 <= item < 2**64:
                raise ValueError('Expected uint64 revision')
            setattr(result, name, item)
        elif name == 'pose':
            result.pose.CopyFrom(pose(item))
        elif name == 'size':
            result.size.CopyFrom(vec2(item))
        elif name == 'color':
            red, green, blue = numbers(item, 3)
            if any(type(v) is not int for v in item):
                raise ValueError('Expected integer color')
            result.color.CopyFrom(pb.Color(red=red, green=green, blue=blue))
        elif name == 'bounds':
            if set(item) - {'min', 'max'}:
                raise ValueError('Unknown bounds fields')
            result.bounds.SetInParent()
            for field, point in item.items():
                getattr(result.bounds, field).CopyFrom(vec3(point))
        elif name == 'destination':
            if set(item) - {'room', 'pose'}:
                raise ValueError('Unknown destination fields')
            result.destination.SetInParent()
            if 'room' in item:
                result.destination.room = item['room']
            if 'pose' in item:
                result.destination.pose.CopyFrom(pose(item['pose']))
    return result


def pose_value(value):
    if not value.HasField('position') or not value.HasField('orientation'):
        raise ValueError('Missing pose fields')
    q = value.orientation
    return {'p': vector_value(value.position), 'q': [q.x, q.y, q.z, q.w]}


def entity_value(value):
    result = {}
    for field, item in value.ListFields():
        name = field.name
        if name == 'kind':
            result[name] = pb.EntityKind.Name(item).lower()
        elif name == 'pose':
            result[name] = pose_value(item)
        elif name == 'size':
            result[name] = [item.x, item.y]
        elif name == 'color':
            result[name] = [item.red, item.green, item.blue]
        elif name == 'bounds':
            result[name] = {'min': vector_value(item.min), 'max': vector_value(item.max)}
        elif name == 'destination':
            result[name] = {'room': item.room, 'pose': pose_value(item.pose)}
        else:
            result[name] = item
    return result


def make_request(method, params):
    request = pb.Request(protocol_version=1)
    if method == 'actor.act':
        if not isinstance(params, dict) or set(params) - {'token', 'seq', 'op', 'args'}:
            raise ValueError('Unknown actor action parameters')
        body = request.actor_act
        body.SetInParent()
        if 'token' in params:
            body.token = params['token']
        if 'seq' in params:
            if type(params['seq']) is not int:
                raise ValueError('Expected integer sequence')
            body.seq = params['seq']
        args = params.get('args', {})
        if params.get('op') == 'input':
            if not isinstance(args, dict) or set(args) - {'axes'}:
                raise ValueError('Unknown input fields')
            body.input.SetInParent()
            if 'axes' in args:
                body.input.axes.extend(numbers(args['axes'], len(args['axes'])))
        elif params.get('op') in ('stop', 'unfocus'):
            if not isinstance(args, dict) or args:
                raise ValueError('Unknown cancellation fields')
            getattr(body, params['op']).SetInParent()
        elif params.get('op') == 'aim':
            if not isinstance(args, dict) or set(args) - {'direction'}:
                raise ValueError('Unknown aim fields')
            body.aim.SetInParent()
            if 'direction' in args:
                body.aim.direction.CopyFrom(vec3(args['direction']))
        elif params.get('op') in ('pointer', 'key', 'scroll'):
            fields = {'pointer': {'button': int, 'pressed': bool},
                      'key': {'keysym': str, 'pressed': bool},
                      'scroll': {'steps_y': int}}[params['op']]
            if not isinstance(args, dict) or set(args) - set(fields):
                raise ValueError('Unknown actor input fields')
            operation = getattr(body, params['op'])
            operation.SetInParent()
            for name, value in args.items():
                if type(value) is not fields[name]:
                    raise ValueError('Invalid actor input field type')
                setattr(operation, name, value)
        return request
    schemas = {
        'system.hello': ('hello', {}),
        'world.get': ('world_get', {'id': str}),
        'world.create': ('world_create', {'entity': entity}),
        'world.update': ('world_update', {'id': str, 'expected_revision': int, 'patch': entity}),
        'world.delete': ('world_delete', {'id': str, 'expected_revision': int}),
        'world.list': ('world_list', {}),
        'world.snapshot': ('world_snapshot', {}),
        'world.info': ('world_info', {}),
        'runtime.identities': ('runtime_identities', {}),
        'render.capture': ('render_capture', {'token': str, 'width': int, 'height': int}),
        'render.present': ('render_present', {'token': str, 'width': int, 'height': int}),
        'surface.find': ('surface_find', {'probe_id': str}),
        'surface.get': ('surface_get', {'id': str}),
        'surface.place': ('surface_place', {'id': str, 'room': str, 'pose': pose, 'size': vec2}),
        'surface.children': ('surface_children', {'id': str}),
        'runtime.fence': ('runtime_fence', {}),
        'runtime.resources': ('runtime_resources', {}),
        'actor.open': ('actor_open', {'id': str, 'room': str, 'pose': pose, 'grants': list}),
        'actor.inspect': ('actor_inspect', {'token': str}),
        'actor.close': ('actor_close', {'token': str}),
        'world.checkpoint': ('world_checkpoint', {}),
        'clock.advance': ('clock_advance', {'seconds': float}),
        'geometry.transform_point': ('transform_point', {'pose': pose, 'point': vec3}),
        'geometry.inverse_point': ('inverse_point', {'pose': pose, 'point': vec3}),
        'geometry.uv_pixel': ('uv_pixel', {'uv': vec2, 'pixels': None}),
        'geometry.ray_panel': ('ray_panel', {'origin': vec3, 'direction': vec3, 'pose': pose,
                                          'size': vec2, 'pixels': None}),
        'motion.integrate': ('integrate', {'pose': pose, 'linear': vec3, 'angular': vec3, 'seconds': float}),
    }
    if method not in schemas:
        raise NotImplementedError(f'No protobuf schema for {method}; the phase remains unimplemented')
    operation, fields = schemas[method]
    if not isinstance(params, dict) or set(params) - set(fields):
        raise ValueError('Unknown parameters or invalid parameter object')
    body = getattr(request, operation)
    body.SetInParent()
    for name, value in params.items():
        convert = fields[name]
        if name == 'pixels':
            width, height = numbers(value, 2)
            if type(width) is not int or type(height) is not int:
                raise ValueError('Pixel dimensions must be integers')
            body.pixels.CopyFrom(pb.PixelExtent(width=width, height=height))
        elif convert is str:
            if type(value) is not str:
                raise ValueError('Expected string')
            setattr(body, name, value)
        elif convert is float:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Expected finite number')
            setattr(body, name, value)
        elif convert is int:
            if type(value) is not int or not 0 <= value < 2**64:
                raise ValueError('Expected uint64 integer')
            setattr(body, name, value)
        elif convert is list:
            if not isinstance(value, (tuple, list)) or any(type(v) is not str for v in value):
                raise ValueError('Expected string list')
            getattr(body, name).extend(value)
        else:
            getattr(body, name).CopyFrom(convert(value))
    return request


OUTCOMES = {'hello': 'hello', 'world_get': 'entity', 'world_create': 'entity',
            'world_update': 'entity', 'world_delete': 'deleted', 'world_list': 'snapshot',
            'world_snapshot': 'snapshot', 'world_info': 'world_info', 'runtime_identities': 'identities',
            'actor_open': 'actor_opened', 'actor_inspect': 'actor_state', 'actor_close': 'actor_closed',
            'actor_act': 'actor_action', 'world_checkpoint': 'checkpoint', 'clock_advance': 'clock',
            'render_capture': 'frame', 'render_present': 'frame',
            'surface_find': 'surface', 'surface_get': 'surface_record', 'surface_place': 'surface_record',
            'surface_children': 'surfaces', 'runtime_fence': 'fence', 'runtime_resources': 'resources',
            'transform_point': 'point',
            'inverse_point': 'point', 'uv_pixel': 'pixel', 'ray_panel': 'ray', 'integrate': 'pose'}


def vector_value(value):
    return [value.x, value.y, value.z]


def result_value(reply):
    kind = reply.WhichOneof('outcome')
    if kind == 'surface':
        return {'surface': surface_value(reply.surface.surface) if reply.surface.HasField('surface') else None}
    if kind == 'surface_record':
        return surface_value(reply.surface_record)
    if kind == 'surfaces':
        return {'surfaces': [surface_value(value) for value in reply.surfaces.surfaces]}
    if kind == 'fence':
        return {'complete': reply.fence.complete}
    if kind == 'resources':
        return {name: getattr(reply.resources, name) for name in
                ('retired_pixmaps_pending', 'invalid_texture_bindings', 'live_pixmaps')}
    if kind == 'actor_opened':
        return {'id': reply.actor_opened.id, 'token': reply.actor_opened.token}
    if kind == 'actor_state':
        value = reply.actor_state
        return {'id': value.id, 'room': value.room, 'pose': pose_value(value.pose),
                'axes': list(value.axes), 'held_keys': list(value.held_keys), 'held_buttons': list(value.held_buttons),
                'focus': {'id': value.focus, 'generation': value.focus_generation} if value.HasField('focus') else None}
    if kind == 'actor_closed':
        return {'closed': reply.actor_closed.closed}
    if kind == 'actor_action':
        if reply.actor_action.HasField('aim'):
            if not reply.actor_action.aim.HasField('hit'):
                return {'hit': None}
            hit = reply.actor_action.aim.hit
            return {'hit': {'id': hit.id, 'generation': hit.generation, 'uv': [hit.uv.x, hit.uv.y],
                            'pixel': [hit.pixel.x, hit.pixel.y] if hit.HasField('pixel') else None,
                            'distance': hit.distance}}
        return {'accepted': reply.actor_action.accepted}
    if kind == 'frame':
        value = reply.frame
        return {'path': value.path, 'frame_id': value.frame_id, 'world_revision': value.world_revision, 'xid': value.xid}
    if kind == 'checkpoint':
        return {'world_revision': reply.checkpoint.world_revision}
    if kind == 'clock':
        return {'seconds': reply.clock.seconds}
    if kind == 'entity':
        if not reply.entity.HasField('entity'):
            raise ValueError('Missing result entity')
        return entity_value(reply.entity.entity)
    if kind == 'snapshot':
        value = reply.snapshot
        return {'world_id': value.world_id, 'world_revision': value.world_revision,
                'entry_room': value.entry_room, 'entities': [entity_value(e) for e in value.entities]}
    if kind == 'deleted':
        return {'deleted': reply.deleted.deleted, 'world_revision': reply.deleted.world_revision}
    if kind == 'world_info':
        value = reply.world_info
        return {'world_id': value.world_id, 'entry_room': value.entry_room, 'world_revision': value.world_revision}
    if kind == 'identities':
        value = reply.identities
        result = {'worldd_pid': value.worldd_pid, 'world_id': value.world_id}
        if value.spatialwm_pid:
            result.update(spatialwm_pid=value.spatialwm_pid, display=value.display)
        return result
    if kind == 'hello':
        value = reply.hello
        return {'protocol': value.protocol, 'implementation': value.implementation,
                'build_id': value.build_id, 'capabilities': list(value.capabilities)}
    if kind == 'point':
        if not reply.point.HasField('point'):
            raise ValueError('Missing result point')
        return {'point': vector_value(reply.point.point)}
    if kind == 'pixel':
        return {'pixel': [reply.pixel.pixel.x, reply.pixel.pixel.y] if reply.pixel.HasField('pixel') else None}
    if kind == 'ray':
        if not reply.ray.HasField('hit'):
            return {'hit': None}
        hit = reply.ray.hit
        if not hit.HasField('uv') or not hit.HasField('pixel') or hit.distance < 0:
            raise ValueError('Invalid ray hit')
        return {'hit': {'uv': [hit.uv.x, hit.uv.y], 'pixel': [hit.pixel.x, hit.pixel.y], 'distance': hit.distance}}
    if kind == 'pose':
        value = reply.pose.pose
        if not reply.pose.HasField('pose') or not value.HasField('position') or not value.HasField('orientation'):
            raise ValueError('Missing result pose')
        q = value.orientation
        return {'pose': {'p': vector_value(value.position), 'q': [q.x, q.y, q.z, q.w]}}
    raise ValueError('Unexpected reply outcome')


def surface_value(value):
    return {'id': value.id, 'xid': value.xid, 'generation': value.generation, 'room': value.room,
            'pose': pose_value(value.pose), 'size': [value.size.x, value.size.y],
            'pixels': [value.pixels.width, value.pixels.height], 'mapped': value.mapped,
            'parent_id': value.parent_id if value.HasField('parent_id') else None,
            'slot': value.slot if value.HasField('slot') else None}
