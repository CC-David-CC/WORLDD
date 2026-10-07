#include "x11/surfaces.hpp"
#include "x11/input.hpp"
#include <GL/glx.h>
#include <GL/glxext.h>
#include <X11/Xatom.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xcomposite.h>
#include <X11/extensions/Xdamage.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <map>
#include <stdexcept>
#include <sys/random.h>
#include <vector>
namespace spatial {
namespace {
// Single X thread. A trap drains earlier requests before inspecting its own errors.
class Trap {
public:
    explicit Trap(Display* display) : display_(display) {
        XSync(display_, False); previous_ = XSetErrorHandler(handle); current_ = this;
    }
    ~Trap() { if (current_ == this) finish(); }
    int finish() {
        XSync(display_, False); XSetErrorHandler(previous_); current_ = nullptr; return error_;
    }
private:
    static int handle(Display*, XErrorEvent* event) { current_->error_ = event->error_code; return 0; }
    inline static Trap* current_ = nullptr;
    Display* display_;
    XErrorHandler previous_;
    int error_ = 0;
};
std::string incarnation() {
    std::array<unsigned char,16> bytes{};
    std::size_t done = 0;
    while (done < bytes.size()) {
        const auto count = getrandom(bytes.data()+done,bytes.size()-done,0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("Cannot generate surface incarnation");
        done += static_cast<std::size_t>(count);
    }
    constexpr char hex[]="0123456789abcdef";
    std::string value="surface-";
    for (auto byte : bytes) { value+=hex[byte>>4]; value+=hex[byte&15]; }
    return value;
}
void require(bool value, const char* reason) { if (!value) throw std::runtime_error(reason); }
}
struct XSurfaces::Impl {
    struct Resource { Pixmap pixmap=0; GLXPixmap glx=0; GLuint imported=0, texture=0; bool inverted=false; };
    struct Surface {
        wire::NativeSurface native;
        Resource resource;
        Damage damage=0;
        bool dirty=true;
        VisualID visual=0;
        int depth=0;
    };
    Display* display;
    NativeInput* input = nullptr;
    Window output, overlay, root;
    int damage_event=0, damage_error=0;
    std::string session=incarnation();
    std::uint64_t next_id=0, invalid_bindings=0;
    std::map<Window,Surface> windows;
    std::vector<Resource> retired;
    PFNGLXBINDTEXIMAGEEXTPROC bind=nullptr;
    PFNGLXRELEASETEXIMAGEEXTPROC release=nullptr;
    GLuint copy_fbo=0;
    Impl(Display* d,Window w,Window o):display(d),output(w),overlay(o),root(DefaultRootWindow(d)) {}
    ~Impl() {
        glFinish();
        for (auto& [xid,surface] : windows) {
            if (surface.damage) { Trap trap(display); XDamageDestroy(display,surface.damage); trap.finish(); }
            retire(surface);
            (void)xid;
        }
        drain();
        if (copy_fbo) glDeleteFramebuffers(1,&copy_fbo);
        Trap trap(display); XCompositeUnredirectSubwindows(display,root,CompositeRedirectManual); trap.finish();
    }
    void retire(Surface& surface) {
        if (surface.resource.pixmap || surface.resource.glx || surface.resource.imported || surface.resource.texture)
            retired.push_back(surface.resource);
        surface.resource={};
    }
    void drain() {
        if (retired.empty()) return;
        glFinish();
        for (const auto& item:retired) {
            if (item.imported) glDeleteTextures(1,&item.imported);
            if (item.texture) glDeleteTextures(1,&item.texture);
            if (item.glx) glXDestroyPixmap(display,item.glx);
            if (item.pixmap) XFreePixmap(display,item.pixmap);
        }
        retired.clear();
        XSync(display,False);
    }
    void property(Window xid,Surface& surface) {
        Atom type=None; int format=0; unsigned long count=0,remaining=0; unsigned char* bytes=nullptr;
        const auto atom=XInternAtom(display,"_SPATIAL_PROBE_ID",False);
        if (XGetWindowProperty(display,xid,atom,0,1024,False,AnyPropertyType,&type,&format,&count,&remaining,&bytes)==Success &&
            bytes && format==8 && remaining==0) surface.native.set_probe_id(std::string(reinterpret_cast<char*>(bytes),count));
        else surface.native.clear_probe_id();
        if (bytes) XFree(bytes);
        Window parent=0;
        if (XGetTransientForHint(display,xid,&parent) && parent!=xid && parent!=root) surface.native.set_parent_xid(parent);
        else surface.native.set_parent_xid(0);
    }
    void update(Window xid, bool replaced=false) {
        if (xid==output || xid==overlay || xid==root) return;
        XWindowAttributes attr{};
        {
            Trap trap(display);
            const bool exists=XGetWindowAttributes(display,xid,&attr);
            const auto error=trap.finish();
            if (!exists || error==BadWindow) { remove(xid); return; }
            require(error==0,"Cannot inspect native window");
        }
        if (attr.c_class!=InputOutput) return;
        auto found=windows.find(xid);
        if (found==windows.end()) {
            // Keep pre-map registration private until a real mapped resource exists.
            auto [it,inserted]=windows.try_emplace(xid); (void)inserted;
            found=it;
            auto* handle=found->second.native.mutable_handle();
            handle->set_id(session+"-"+std::to_string(++next_id));
            found->second.native.set_xid(xid);
            Trap trap(display);
            XSelectInput(display,xid,StructureNotifyMask|PropertyChangeMask);
            const auto error=trap.finish();
            if (error==BadWindow) { windows.erase(found); return; }
            require(error==0,"Cannot watch application window");
            Trap damage_trap(display);
            found->second.damage=XDamageCreate(display,xid,XDamageReportNonEmpty);
            const auto damage_result=damage_trap.finish();
            if (damage_result==BadWindow || damage_result==BadDrawable) { remove(xid); return; }
            require(damage_result==0,"Cannot watch application damage");
        }
        auto& surface=found->second;
        const bool mapped=attr.map_state==IsViewable;
        std::uint64_t server_generation=0;
        if (mapped && input) {
            native_input::Request request; request.mutable_inspect()->set_xid(xid);
            const auto reply=input->call(request);
            if (reply.code()==native_input::STALE_TARGET) { remove(xid); return; }
            require(reply.code()==native_input::OK && reply.has_target(),"Cannot inspect native input generation");
            server_generation=reply.target().generation();
            // Dimensions and input generation must come from the same server reply.
            attr.width=reply.width(); attr.height=reply.height();
        }
        const bool changed=replaced || surface.native.mapped()!=mapped || surface.native.pixels().width()!=unsigned(attr.width) ||
                            surface.native.pixels().height()!=unsigned(attr.height) || surface.native.server_generation()!=server_generation;
        if (changed) {
            retire(surface);
            surface.native.mutable_handle()->set_generation(surface.native.handle().generation()+1);
            surface.native.set_mapped(mapped); surface.dirty=true;
        }
        surface.native.mutable_pixels()->set_width(attr.width); surface.native.mutable_pixels()->set_height(attr.height);
        surface.native.set_server_generation(server_generation);
        surface.visual=XVisualIDFromVisual(attr.visual); surface.depth=attr.depth;
        {
            Trap trap(display);
            Window child=0; int x=0,y=0;
            XTranslateCoordinates(display,xid,root,0,0,&x,&y,&child);
            surface.native.set_root_x(x); surface.native.set_root_y(y);
            property(xid,surface);
            const auto error=trap.finish();
            if (error==BadWindow) { remove(xid); return; }
            require(error==0,"Cannot inspect application relationship");
        }
    }
    void remove(Window xid) {
        const auto found=windows.find(xid); if (found==windows.end()) return;
        if (found->second.damage) {
            Trap trap(display); XDamageDestroy(display,found->second.damage);
            const auto error=trap.finish();
            require(error==0 || error==damage_error+BadDamage,"Cannot retire application damage");
        }
        retire(found->second); windows.erase(found);
    }
    GLXFBConfig config(const Surface& surface,bool& inverted,bool& rgba) {
        int count=0;
        auto* configs=glXGetFBConfigs(display,DefaultScreen(display),&count);
        GLXFBConfig chosen=nullptr;
        for (int i=0;i<count;++i) {
            int drawable=0,targets=0,rgb=0,alpha=0,visual=0,flip=0;
            glXGetFBConfigAttrib(display,configs[i],GLX_DRAWABLE_TYPE,&drawable);
            glXGetFBConfigAttrib(display,configs[i],GLX_BIND_TO_TEXTURE_TARGETS_EXT,&targets);
            glXGetFBConfigAttrib(display,configs[i],GLX_BIND_TO_TEXTURE_RGB_EXT,&rgb);
            glXGetFBConfigAttrib(display,configs[i],GLX_BIND_TO_TEXTURE_RGBA_EXT,&alpha);
            glXGetFBConfigAttrib(display,configs[i],GLX_VISUAL_ID,&visual);
            if (!(drawable&GLX_PIXMAP_BIT) || !(targets&GLX_TEXTURE_2D_BIT_EXT) || (!rgb&&!alpha) ||
                static_cast<VisualID>(visual)!=surface.visual) continue;
            glXGetFBConfigAttrib(display,configs[i],GLX_Y_INVERTED_EXT,&flip);
            chosen=configs[i]; inverted=flip; rgba=surface.depth==32 && alpha; break;
        }
        if (configs) XFree(configs);
        require(chosen!=nullptr,"No texture-from-pixmap configuration for application visual");
        return chosen;
    }
    bool acquire(Window xid,Surface& surface) {
        bool inverted=false,rgba=false;
        const auto fb=config(surface,inverted,rgba);
        Resource resource; resource.inverted=inverted;
        {
            Trap trap(display);
            resource.pixmap=XCompositeNameWindowPixmap(display,xid);
            const auto error=trap.finish();
            if (error==BadWindow || error==BadMatch) return false;
            require(error==0 && resource.pixmap,"Cannot name application backing pixmap");
        }
        const int attrs[]{GLX_TEXTURE_TARGET_EXT,GLX_TEXTURE_2D_EXT,GLX_TEXTURE_FORMAT_EXT,
                          rgba?GLX_TEXTURE_FORMAT_RGBA_EXT:GLX_TEXTURE_FORMAT_RGB_EXT,None};
        {
            Trap trap(display);
            resource.glx=glXCreatePixmap(display,fb,resource.pixmap,attrs);
            const auto error=trap.finish();
            if (error || !resource.glx) { retired.push_back(resource); throw std::runtime_error("Cannot create application GLX pixmap"); }
        }
        glGenTextures(1,&resource.imported); glGenTextures(1,&resource.texture);
        glBindTexture(GL_TEXTURE_2D,resource.texture);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,surface.native.pixels().width(),surface.native.pixels().height(),0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        surface.resource=resource;
        require(glGetError()==GL_NO_ERROR,"Cannot allocate application texture");
        return true;
    }
    void copy(Surface& surface) {
        GLint prior_fbo=0,prior_texture=0;
        glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING,&prior_fbo);
        glGetIntegerv(GL_TEXTURE_BINDING_2D,&prior_texture);
        auto& resource=surface.resource;
        // Keep X drawing outside the GLX binding interval. The owned texture survives release.
        XGrabServer(display);
        bool bound=false;
        try {
            Trap trap(display);
            glBindTexture(GL_TEXTURE_2D,resource.imported);
            bind(display,resource.glx,GLX_FRONT_LEFT_EXT,nullptr); bound=true;
            if (trap.finish()) { ++invalid_bindings; throw std::runtime_error("Cannot bind application pixmap texture"); }
            glBindFramebuffer(GL_READ_FRAMEBUFFER,copy_fbo);
            glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,resource.imported,0);
            glReadBuffer(GL_COLOR_ATTACHMENT0);
            require(glCheckFramebufferStatus(GL_READ_FRAMEBUFFER)==GL_FRAMEBUFFER_COMPLETE,"Application import framebuffer is incomplete");
            glBindTexture(GL_TEXTURE_2D,resource.texture);
            glCopyTexSubImage2D(GL_TEXTURE_2D,0,0,0,0,0,surface.native.pixels().width(),surface.native.pixels().height());
            glFinish();
            require(glGetError()==GL_NO_ERROR,"Application texture copy failed");
            // An unbound FBO would otherwise retain a later-deleted imported texture.
            glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,0,0);
            release(display,resource.glx,GLX_FRONT_LEFT_EXT); bound=false;
            XUngrabServer(display); XSync(display,False);
        } catch (...) {
            glBindFramebuffer(GL_READ_FRAMEBUFFER,copy_fbo);
            glFramebufferTexture2D(GL_READ_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,0,0);
            if (bound) release(display,resource.glx,GLX_FRONT_LEFT_EXT);
            XUngrabServer(display); XSync(display,False);
            glBindFramebuffer(GL_READ_FRAMEBUFFER,prior_fbo); glBindTexture(GL_TEXTURE_2D,prior_texture);
            throw;
        }
        glBindFramebuffer(GL_READ_FRAMEBUFFER,prior_fbo); glBindTexture(GL_TEXTURE_2D,prior_texture);
        surface.dirty=false;
    }
};
XSurfaces::XSurfaces(Display* display,Window output,Window overlay,NativeInput* input):impl_(std::make_unique<Impl>(display,output,overlay)) {
    auto& r=*impl_;
    r.input=input;
    int event=0,error=0,major=0,minor=0;
    require(XCompositeQueryExtension(display,&event,&error) && XCompositeQueryVersion(display,&major,&minor) &&
            (major>0 || minor>=2),"Composite 0.2 is required");
    require(XDamageQueryExtension(display,&r.damage_event,&r.damage_error),"X Damage is required");
    const char* extensions=glXQueryExtensionsString(display,DefaultScreen(display));
    require(extensions && std::strstr(extensions,"GLX_EXT_texture_from_pixmap"),"GLX texture-from-pixmap is required");
    r.bind=reinterpret_cast<PFNGLXBINDTEXIMAGEEXTPROC>(glXGetProcAddressARB(reinterpret_cast<const GLubyte*>("glXBindTexImageEXT")));
    r.release=reinterpret_cast<PFNGLXRELEASETEXIMAGEEXTPROC>(glXGetProcAddressARB(reinterpret_cast<const GLubyte*>("glXReleaseTexImageEXT")));
    require(r.bind && r.release,"GLX pixmap entry points unavailable");
    {
        Trap trap(display); XCompositeRedirectSubwindows(display,r.root,CompositeRedirectManual);
        require(trap.finish()==0,"Another composite manager owns the display");
    }
    glGenFramebuffers(1,&r.copy_fbo);
    Window root=0,parent=0,*children=nullptr; unsigned count=0;
    require(XQueryTree(display,r.root,&root,&parent,&children,&count),"Cannot enumerate initial X windows");
    try { for (unsigned i=0;i<count;++i) r.update(children[i]); }
    catch (...) { if (children) XFree(children); throw; }
    if (children) XFree(children);
}
XSurfaces::~XSurfaces()=default;
void XSurfaces::event(const XEvent& event) {
    auto& r=*impl_;
    if (event.type==MapRequest) {
        r.update(event.xmaprequest.window);
        Trap trap(r.display); XMapWindow(r.display,event.xmaprequest.window);
        const auto error=trap.finish(); require(error==0 || error==BadWindow,"Cannot map application window"); return;
    }
    if (event.type==ConfigureRequest) {
        const auto& e=event.xconfigurerequest;
        XWindowChanges change{}; change.x=e.x;change.y=e.y;change.width=e.width;change.height=e.height;
        change.border_width=e.border_width;change.sibling=e.above;change.stack_mode=e.detail;
        Trap trap(r.display); XConfigureWindow(r.display,e.window,e.value_mask,&change);
        const auto error=trap.finish(); require(error==0 || error==BadWindow,"Cannot configure native window"); return;
    }
    if (event.type==DestroyNotify) { r.remove(event.xdestroywindow.window); return; }
    // A later XGetWindowAttributes can already describe a subsequent resize/remap.
    // Retire from the queued lifecycle evidence even if the final attributes match.
    if (event.type==MapNotify) r.update(event.xmap.window,true);
    if (event.type==ConfigureNotify) {
        const auto found=r.windows.find(event.xconfigure.window);
        const bool resized=found!=r.windows.end() &&
            (found->second.native.pixels().width()!=unsigned(event.xconfigure.width) ||
             found->second.native.pixels().height()!=unsigned(event.xconfigure.height));
        r.update(event.xconfigure.window,resized);
    }
    if (event.type==UnmapNotify) r.update(event.xunmap.window,true);
    if (event.type==PropertyNotify) r.update(event.xproperty.window);
    if (event.type==r.damage_event+XDamageNotify) {
        const auto& damage=reinterpret_cast<const XDamageNotifyEvent&>(event);
        const auto found=r.windows.find(damage.drawable);
        if (found!=r.windows.end() && found->second.damage==damage.damage) {
            found->second.dirty=true;
            Trap trap(r.display); XDamageSubtract(r.display,damage.damage,None,None);
            const auto error=trap.finish(); require(error==0 || error==r.damage_error+BadDamage,"Cannot acknowledge native damage");
        }
    }
}
void XSurfaces::synchronize() {
    auto& r=*impl_;
    for (auto& [xid,surface]:r.windows) if (surface.native.mapped()) {
        if (!surface.resource.pixmap && !r.acquire(xid,surface)) { surface.native.set_mapped(false); continue; }
        if (surface.dirty) r.copy(surface);
    }
    r.drain(); glFinish();
}
wire::NativeSnapshot XSurfaces::snapshot() const {
    wire::NativeSnapshot result;
    for (const auto& [xid,surface]:impl_->windows) {
        (void)xid; *result.add_surfaces()=surface.native;
        if (surface.resource.pixmap) result.mutable_resources()->set_live_pixmaps(result.resources().live_pixmaps()+1);
    }
    result.mutable_resources()->set_retired_pixmaps_pending(impl_->retired.size());
    result.mutable_resources()->set_invalid_texture_bindings(impl_->invalid_bindings);
    return result;
}
std::optional<XSurfaces::Texture> XSurfaces::texture(const wire::SurfaceHandle& handle) const {
    for (const auto& [xid,surface]:impl_->windows) {
        (void)xid;
        if (surface.native.handle().id()==handle.id() && surface.native.handle().generation()==handle.generation() &&
            surface.native.mapped() && surface.resource.texture) return Texture{surface.resource.texture,surface.resource.inverted};
    }
    return std::nullopt;
}
std::optional<wire::NativeSurface> XSurfaces::native(const wire::SurfaceHandle& handle) const {
    for (const auto& [xid,surface]:impl_->windows) {
        (void)xid;
        if (surface.native.handle().id()==handle.id() && surface.native.handle().generation()==handle.generation() &&
            surface.native.mapped()) return surface.native;
    }
    return std::nullopt;
}
}
