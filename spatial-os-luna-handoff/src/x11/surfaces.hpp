#pragma once
#include "core/protocol.hpp"
#include <GL/glew.h>
#include <X11/Xlib.h>
#include <memory>
#include <optional>
namespace spatial {
class NativeInput;
class XSurfaces {
public:
    XSurfaces(Display* display, Window output, Window overlay, NativeInput* input = nullptr);
    ~XSurfaces();
    void event(const XEvent& event);
    void synchronize();
    wire::NativeSnapshot snapshot() const;
    struct Texture { GLuint name; bool inverted; };
    std::optional<Texture> texture(const wire::SurfaceHandle& handle) const;
    std::optional<wire::NativeSurface> native(const wire::SurfaceHandle& handle) const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
