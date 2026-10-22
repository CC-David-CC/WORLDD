#pragma once
#include "compositor_native.pb.h"
#include <X11/Xlib.h>
#include "security.pb.h"
namespace spatial {
// The caller owns the returned display and serializes startup on its X thread.
security::v1::Identity inspect_compositor_identity(Display*, std::uint64_t sequence);
Display* open_compositor_display(const std::string& display, const render::v1::NativeCompositorConfig& config);
}
