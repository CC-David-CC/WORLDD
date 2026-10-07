#pragma once
#include "core/protocol.hpp"
#include <GL/glew.h>
#include <X11/Xlib.h>
#include <memory>
#include <optional>
namespace spatial {
class XSurfaces {
public:
    XSurfaces(Display* display, Window output, Window overlay);
    ~XSurfaces();
    void event(const XEvent& event);
    void synchronize();
    wire::NativeSnapshot snapshot() const;
    struct Texture { GLuint name; bool inverted; };
    std::optional<Texture> texture(const wire::SurfaceHandle& handle) const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
