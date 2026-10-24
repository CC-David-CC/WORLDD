#pragma once
#include "control.pb.h"
#include <memory>

namespace spatial {
// Draws host preferences to the native back buffer only. Never a scene target.
class ControlsOverlay final {
public:
    explicit ControlsOverlay(void* display);
    ~ControlsOverlay();
    ControlsOverlay(const ControlsOverlay&) = delete;
    ControlsOverlay& operator=(const ControlsOverlay&) = delete;
    static void validate(const control::v1::ControlMenuView&);
    void set(const control::v1::ControlMenuView&);
    bool visible() const;
    void draw(unsigned width, unsigned height);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
