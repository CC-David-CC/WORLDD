#include "render/controls_overlay.hpp"
#include "core/protocol.hpp"
#include <GL/glew.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace spatial {
namespace {
void need(bool valid, const char* message) { if (!valid) throw std::invalid_argument(message); }
// The native core font covers ASCII. Replace each unsupported UTF-8 character
// once. Never interpret profile labels as markup or X control sequences.
std::string text(const std::string& input) {
    std::string result;
    for (unsigned char c : input) {
        if (c >= 32 && c < 127) result += static_cast<char>(c);
        else if ((c & 0xc0) != 0x80) result += c < 128 ? ' ' : '?';
    }
    return result;
}
struct Mask {
    Display* display;
    Pixmap pixmap = 0;
    GC gc = nullptr;
    XImage* image = nullptr;
    ~Mask() {
        if (image) XDestroyImage(image);
        if (gc) XFreeGC(display, gc);
        if (pixmap) XFreePixmap(display, pixmap);
    }
};
}
struct ControlsOverlay::Impl {
    Display* display;
    XFontStruct* font = nullptr;
    GLuint texture = 0, fbo = 0;
    control::v1::ControlMenuView view;
    unsigned output_width = 0, output_height = 0, width = 0, height = 0, scale = 1;
    bool dirty = true;
    explicit Impl(void* value) : display(static_cast<Display*>(value)) {}
    ~Impl() {
        if (fbo) glDeleteFramebuffers(1, &fbo);
        if (texture) glDeleteTextures(1, &texture);
        if (font) XFreeFont(display, font);
    }
    void rasterize(unsigned w, unsigned h) {
        if (!font) font = XLoadQueryFont(display, "fixed");
        if (!font) throw std::runtime_error("Native controls font unavailable");
        output_width = w; output_height = h; scale = w >= 1280 && h >= 720 ? 2 : 1;
        const unsigned margin = 4, padding = 4;
        width = std::min(720U / scale, (w - 2 * margin) / scale);
        height = std::min(680U / scale, (h - 2 * margin) / scale);
        const unsigned row_height = static_cast<unsigned>(font->ascent + font->descent + 3);
        const unsigned glyph_width = static_cast<unsigned>(std::max(1, XTextWidth(font, "M", 1)));
        const unsigned columns = std::max(1U, (width - 2 * padding) / glyph_width);
        const unsigned rows = height / row_height;
        need(rows >= 3, "Native output is too small for controls");
        struct Line { std::string value; bool selected = false; };
        std::vector<Line> footer;
        const auto wrap = [&](const std::string& input) {
            const auto value = text(input);
            for (std::size_t i = 0; i < value.size(); i += columns)
                footer.push_back({value.substr(i, columns), false});
        };
        wrap(view.message()); wrap(view.prompt());
        if (view.raw_axes_size()) {
            std::ostringstream axes; axes << "Axes:" << std::setprecision(4);
            for (double axis : view.raw_axes()) axes << ' ' << axis;
            wrap(axes.str());
        }
        // Keep at least one selectable row on small native diagnostic displays.
        if (footer.size() > rows - 2) footer.resize(rows - 2);
        const unsigned visible_rows = rows - 1 - static_cast<unsigned>(footer.size());
        unsigned selected = 0;
        for (int i = 0; i < view.entries_size(); ++i)
            if (view.entries(i).selected()) selected = static_cast<unsigned>(i);
        const unsigned count = static_cast<unsigned>(view.entries_size());
        const unsigned first = selected < visible_rows ? 0 : selected - visible_rows + 1;
        std::vector<Line> lines{{"Controls", false}};
        for (unsigned i = first; i < count && i < first + visible_rows; ++i) {
            const auto& entry = view.entries(static_cast<int>(i));
            lines.push_back({text(entry.label() + ": " + entry.value()).substr(0, columns), entry.selected()});
        }
        lines.insert(lines.end(), footer.begin(), footer.end());
        height = std::min(height, static_cast<unsigned>(lines.size()) * row_height + padding);
        Mask mask{display};
        mask.pixmap = XCreatePixmap(display, DefaultRootWindow(display), width, height, 1);
        if (!mask.pixmap) throw std::runtime_error("Cannot allocate native controls glyph mask");
        mask.gc = XCreateGC(display, mask.pixmap, 0, nullptr);
        if (!mask.gc) throw std::runtime_error("Cannot allocate native controls text context");
        XSetForeground(display, mask.gc, 0); XFillRectangle(display, mask.pixmap, mask.gc, 0, 0, width, height);
        XSetForeground(display, mask.gc, 1); XSetFont(display, mask.gc, font->fid);
        for (unsigned i = 0; i < lines.size(); ++i) {
            const auto& value = lines[i].value;
            XDrawString(display, mask.pixmap, mask.gc, padding, i * row_height + font->ascent + 1,
                value.data(), static_cast<int>(value.size()));
        }
        mask.image = XGetImage(display, mask.pixmap, 0, 0, width, height, 1, ZPixmap);
        if (!mask.image) throw std::runtime_error("Cannot read native controls glyph mask");
        std::vector<unsigned char> pixels(static_cast<std::size_t>(width) * height * 4);
        for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x) {
            const unsigned row = y / row_height;
            const bool highlighted = row < lines.size() && lines[row].selected;
            const bool glyph = XGetPixel(mask.image, x, y) != 0;
            const std::size_t offset = (static_cast<std::size_t>(y) * width + x) * 4;
            pixels[offset] = glyph ? 240 : highlighted ? 49 : 24;
            pixels[offset + 1] = glyph ? 244 : highlighted ? 90 : 32;
            pixels[offset + 2] = glyph ? 250 : highlighted ? 128 : 44;
            pixels[offset + 3] = 255;
        }
        if (!texture) glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
        if (!fbo) glGenFramebuffers(1, &fbo);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_READ_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
        if (glCheckFramebufferStatus(GL_READ_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
            throw std::runtime_error("Native controls framebuffer is incomplete");
        dirty = false;
    }
};
ControlsOverlay::ControlsOverlay(void* display) : impl_(std::make_unique<Impl>(display)) {}
ControlsOverlay::~ControlsOverlay() = default;
void ControlsOverlay::validate(const control::v1::ControlMenuView& view) {
    validate_message(view);
    need(view.protocol_version() == 1 && view.entries_size() <= 64 && view.prompt().size() <= 512 &&
        view.message().size() <= 512 && view.raw_axes_size() <= 6, "Invalid host controls view bounds");
    unsigned selected = 0;
    for (const auto& entry : view.entries()) {
        need(entry.label().size() <= 256 && entry.value().size() <= 256, "Invalid host controls entry bounds");
        selected += entry.selected();
    }
    need(selected <= 1, "Host controls view has several selected rows");
    for (double axis : view.raw_axes()) need(std::isfinite(axis) && std::abs(axis) <= 1e9, "Invalid host controls axes");
}
void ControlsOverlay::set(const control::v1::ControlMenuView& view) {
    validate(view);
    if (impl_->view.SerializeAsString() != view.SerializeAsString()) { impl_->view = view; impl_->dirty = true; }
}
bool ControlsOverlay::visible() const { return impl_->view.visible(); }
void ControlsOverlay::draw(unsigned width, unsigned height) {
    auto& r = *impl_;
    if (!visible()) return;
    need(width >= 64 && height >= 64 && width <= 16384 && height <= 16384, "Invalid native controls output extent");
    if (r.dirty || r.output_width != width || r.output_height != height) r.rasterize(width, height);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, r.fbo); glReadBuffer(GL_COLOR_ATTACHMENT0);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0); glDrawBuffer(GL_BACK);
    // CPU text rows start at the top. Flip the source into native screen coordinates.
    glBlitFramebuffer(0, 0, r.width, r.height, 4, height - 4,
        4 + r.width * r.scale, height - 4 - r.height * r.scale, GL_COLOR_BUFFER_BIT, GL_NEAREST);
}
}
