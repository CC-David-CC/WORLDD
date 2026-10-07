# P04 graphics preparation

## Current evidence

Both authorized hosts have the graphics build dependencies installed.
tools/bootstrap_graphics.py requires an explicit --install-deps flag.
It records prior package versions, the apt plan, the command, and recovery commands before installation.
It rejects a plan that removes packages.
It disables automatic service restarts during installation.

The package plan installed X11/OpenGL development libraries, GLEW, Mesa utilities, and Xvfb.
It also upgraded libpciaccess0 and the existing X server package family.
Full host-local records are under local/graphics-bootstrap-*.
Retained preparation evidence is under evidence/graphics-preparation-2026-10-07.
Recovery may require retained packages or an apt source that still contains the prior versions.
Review dependency removals before using the recorded commands.

No desktop selection, login path, firewall, or SSH configuration was changed.
The normal user session remains separate from the disposable test display.

## Dedicated test display

Run a command with:

```sh
python3 tools/with_test_display.py -- glxinfo -B
```

The wrapper uses xvfb-run to create an authenticated display.
Xvfb disables TCP listening.
The wrapper sets SPATIAL_TEST_DISPLAY only for its child command.
It does not adopt the operator's DISPLAY.
xvfb-run removes its authority file and terminates its own server after the command.

This wrapper forces software OpenGL.
Both hosts reported Mesa llvmpipe with OpenGL 4.5.
This is diagnostic rendering evidence.
It does not qualify the GTX 1060, visible scanout, timing, or physical devices.

The first P04 diagnostic reached the existing runtime on both hosts.
It failed because render.capture has no protobuf operation yet.
The failing logs are retained.
P04 remains in progress.

## Next implementation

Add a real spatialwm process with an OpenGL renderer.
Send typed protobuf scene and frame messages between production components.
Keep authoritative world and actor state in worldd.
Use one renderer for capture and visible output.
Use the same world geometry for rendering and selection.
Verify camera transforms, projection, depth, backfaces, room filtering, and scene revisions.

Later native qualification must use the selected XLibre build and real GPU/display.
Do not substitute the disposable software display for that gate.
