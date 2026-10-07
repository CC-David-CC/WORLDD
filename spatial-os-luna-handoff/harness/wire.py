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


def make_request(method, params):
    request = pb.Request(protocol_version=1)
    schemas = {
        'system.hello': ('hello', {}),
        'world.get': ('world_get', {'id': str}),
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
        else:
            getattr(body, name).CopyFrom(convert(value))
    return request


OUTCOMES = {'hello': 'hello', 'world_get': None, 'transform_point': 'point',
            'inverse_point': 'point', 'uv_pixel': 'pixel', 'ray_panel': 'ray', 'integrate': 'pose'}


def vector_value(value):
    return [value.x, value.y, value.z]


def result_value(reply):
    kind = reply.WhichOneof('outcome')
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
