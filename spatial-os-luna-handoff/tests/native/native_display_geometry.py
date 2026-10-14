"""Validated Xvfb geometry selection for native fixtures."""
import re

_DEFAULT_SCREEN = "128x128x24"
_MAX_WIDTH = 512
_MAX_HEIGHT = 384


def screen_argument(fixture):
    """Return a canonical bounded WxHx24 argument, with a borrowed-start fallback."""
    value = getattr(fixture, "native_screen_geometry", _DEFAULT_SCREEN)
    if not isinstance(value, str):
        raise ValueError("Native fixture screen geometry must be text")
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)x24", value)
    if match is None:
        raise ValueError("Native fixture screen geometry must be canonical WxHx24")
    width, height = map(int, match.groups())
    if width > _MAX_WIDTH or height > _MAX_HEIGHT:
        raise ValueError("Native fixture screen geometry exceeds witness bounds")
    return value
