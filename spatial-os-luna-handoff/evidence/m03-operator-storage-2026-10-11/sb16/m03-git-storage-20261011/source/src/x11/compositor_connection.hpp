#pragma once
#include "compositor_native.pb.h"
#include <X11/Xlib.h>
namespace spatial {
// The caller owns the returned display and serializes startup on its X thread.
Display* open_compositor_display(const std::string& display, const render::v1::NativeCompositorConfig& config);
}
