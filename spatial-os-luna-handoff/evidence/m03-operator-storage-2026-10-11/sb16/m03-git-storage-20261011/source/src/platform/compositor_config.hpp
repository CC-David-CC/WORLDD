#pragma once
#include "compositor_native.pb.h"
#include <string>

namespace spatial {
// This owner keeps the sealed descriptor until the compositor fork completes.
// It disables unprivileged inspection before reading any credential bytes.
class CompositorConfig {
public:
    explicit CompositorConfig(int descriptor);
    ~CompositorConfig();
    CompositorConfig(const CompositorConfig&) = delete;
    CompositorConfig& operator=(const CompositorConfig&) = delete;
    int descriptor() const { return descriptor_; }
    std::string display() const;
    const render::v1::NativeCompositorConfig& value() const { return value_; }
    void consume();
private:
    int descriptor_ = -1;
    render::v1::NativeCompositorConfig value_;
};
}
