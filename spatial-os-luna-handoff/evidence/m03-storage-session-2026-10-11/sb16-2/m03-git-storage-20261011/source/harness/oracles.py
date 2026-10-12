"""Small independent oracles. Production code MUST NOT import this module."""
from __future__ import annotations
import math
from pathlib import Path

IDENTITY = {"p": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]}

def pose(p=(0, 0, 0), q=(0, 0, 0, 1)):
    return {"p": list(p), "q": list(q)}

def axis_angle(axis, angle):
    length = math.sqrt(sum(a*a for a in axis))
    if length == 0:
        raise ValueError("zero axis")
    return [a / length * math.sin(angle/2) for a in axis] + [math.cos(angle/2)]

def rotate(q, v):
    """Rodrigues/quaternion vector identity, independent of any matrix library."""
    x, y, z, w = q
    def cross(a, b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    t = [2*a for a in cross([x, y, z], v)]
    c = cross([x, y, z], t)
    return [v[i] + w*t[i] + c[i] for i in range(3)]

def transform(pose_, point):
    r = rotate(pose_["q"], point)
    return [r[i] + pose_["p"][i] for i in range(3)]

def close_vec(actual, expected, tol=1e-6):
    if len(actual) != len(expected) or not all(math.isfinite(float(x)) for x in actual):
        return False
    return all(abs(a-b) <= tol for a,b in zip(actual, expected))

def same_rotation(a, b, tol=1e-6):
    return close_vec(a,b,tol) or close_vec(a,[-v for v in b],tol)

def uv_pixel(uv, size):
    u,v = uv
    w,h = size
    if not (0 <= u < 1 and 0 <= v < 1 and w > 0 and h > 0):
        return None
    return [math.floor(u*w), math.floor(v*h)]

class PPM:
    """Read tightly packed binary RGB P6, including comments in the header."""
    def __init__(self, path):
        data = Path(path).read_bytes()
        offset = 0
        def token():
            nonlocal offset
            while offset < len(data):
                if data[offset:offset+1] == b"#":
                    end = data.find(b"\n", offset)
                    if end < 0:
                        raise ValueError("unterminated PPM comment")
                    offset = end+1
                elif data[offset:offset+1].isspace():
                    offset += 1
                else:
                    break
            start = offset
            while offset < len(data) and not data[offset:offset+1].isspace():
                offset += 1
            return data[start:offset]
        if token() != b"P6":
            raise ValueError("capture must be binary RGB P6")
        self.width, self.height = int(token()), int(token())
        if int(token()) != 255 or self.width <= 0 or self.height <= 0:
            raise ValueError("invalid PPM geometry/range")
        if offset >= len(data) or not data[offset:offset+1].isspace():
            raise ValueError("missing PPM separator")
        # Exactly one whitespace separator; pixel data can itself start with whitespace.
        offset += 1
        self.data = data[offset:]
        if len(self.data) != self.width*self.height*3:
            raise ValueError("truncated/overlong PPM payload")
    def pixel(self, x, y):
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError((x,y))
        start = 3*(y*self.width+x)
        return tuple(self.data[start:start+3])
    def mean_patch(self, x, y, radius=1):
        samples = [self.pixel(i,j) for j in range(y-radius,y+radius+1)
                   for i in range(x-radius,x+radius+1)]
        return tuple(sum(s[c] for s in samples)/len(samples) for c in range(3))

def write_ppm(path, width, height, color):
    Path(path).write_bytes(f"P6\n{width} {height}\n255\n".encode() + bytes(color)*width*height)
