#include "control/physical_query.hpp"
#include <X11/Xatom.h>
#include <X11/extensions/XI.h>
#include <X11/extensions/XInput2.h>
#include <X11/extensions/XI2proto.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>

namespace spatial::control::physical_detail {
namespace {
class PropertyErrors {
public:
    PropertyErrors(Display* display, int opcode, int error_base)
        : display_(display), opcode_(opcode), error_(error_base + XI_BadDevice) {
        if (current_) throw std::logic_error("Input property error guards cannot nest");
        XSync(display_, False);
        first_ = NextRequest(display_);
        current_ = this;
        previous_ = XSetErrorHandler(handle);
    }
    ~PropertyErrors() { restore(); }
    bool finish() {
        restore();
        if (unexpected_) throw std::runtime_error("Unexpected X error while inspecting an input device");
        return removed_;
    }
private:
    void restore() noexcept {
        if (current_ != this) return;
        XSync(display_, False);
        XSetErrorHandler(previous_);
        current_ = nullptr;
    }
    static int handle(Display* display, XErrorEvent* event) {
        auto& guard = *current_;
        if (display == guard.display_ && event->serial >= guard.first_ && event->serial < NextRequest(display) &&
            event->request_code == guard.opcode_ && event->minor_code == X_XIGetProperty && event->error_code == guard.error_) {
            guard.removed_ = true;
            return 0;
        }
        guard.unexpected_ = true;
        return guard.previous_ ? guard.previous_(display, event) : 0;
    }
    inline static PropertyErrors* current_ = nullptr;
    Display* display_;
    int opcode_, error_;
    unsigned long first_ = 0;
    XErrorHandler previous_ = nullptr;
    bool removed_ = false, unexpected_ = false;
};
}
std::filesystem::path device_node(Display* display, int device, Atom property, int opcode, int error_base) {
    if (!display || device < 2 || opcode <= 0 || error_base <= 0)
        throw std::invalid_argument("Invalid input property query context");
    if (property == None) return {};
    PropertyErrors errors(display, opcode, error_base);
    Atom type = None;
    int format = 0;
    unsigned long length = 0, remaining = 0;
    unsigned char* data = nullptr;
    const auto status = XIGetProperty(display, device, property, 0, 256, False, XA_STRING,
                                      &type, &format, &length, &remaining, &data);
    struct Bytes { unsigned char* value; ~Bytes() { if (value) XFree(value); } } bytes{data};
    if (errors.finish()) return {};
    std::string value;
    if (status == Success && type == XA_STRING && format == 8 && length > 0 && length <= 1024 && remaining == 0 && data)
        value.assign(reinterpret_cast<const char*>(data), length);
    if (!value.empty() && value.back() == '\0') value.pop_back();
    if (value.empty() || value.find('\0') != std::string::npos || value.front() != '/') return {};
    std::error_code error;
    const auto canonical = std::filesystem::canonical(value, error);
    struct stat node{};
    if (error || stat(canonical.c_str(), &node) != 0 || !S_ISCHR(node.st_mode)) return {};
    return canonical;
}
}
