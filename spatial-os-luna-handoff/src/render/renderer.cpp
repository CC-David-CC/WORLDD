#include "render/renderer.hpp"
#include "control/device_stream.hpp"
#include "control/physical_capture.hpp"
#include "control/spacemouse.hpp"
#include "control/native_capture_config.hpp"
#include "render/presentation_inputs.hpp"
#include "render/controls_overlay.hpp"
#include "x11/compositor_connection.hpp"
#include "core/scene.hpp"
#include "x11/surfaces.hpp"
#include "x11/input_router.hpp"
#include "world/store.hpp"
#include "world/geometry_view_validation.hpp"
#include "world/local_view_validation.hpp"
#include <GL/glew.h>
#include <GL/glx.h>
#include <GL/glxext.h>
#include <X11/Xlib.h>
#include <X11/extensions/Xcomposite.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <optional>
#include <poll.h>
#include <stdexcept>
#include <unistd.h>
#include <sys/stat.h>
#include <vector>
namespace spatial {
namespace {
int x_error = 0;
int record_x_error(Display*, XErrorEvent* event) { x_error = event->error_code; return 0; }
void check_x(Display* display) {
    XSync(display, False);
    if (x_error) {
        const auto code = x_error; x_error = 0;
        throw std::runtime_error("X request failed: " + std::to_string(code));
    }
}
void check_gl() {
    if (glGetError() != GL_NO_ERROR) throw std::runtime_error("OpenGL command failed");
}
GLuint shader(GLenum type, const char* source) {
    const GLuint result = glCreateShader(type);
    glShaderSource(result, 1, &source, nullptr); glCompileShader(result);
    GLint status = 0; glGetShaderiv(result, GL_COMPILE_STATUS, &status);
    if (!status) {
        std::array<char, 4096> log{}; glGetShaderInfoLog(result, log.size(), nullptr, log.data());
        glDeleteShader(result); throw std::runtime_error(std::string("Shader compilation: ") + log.data());
    }
    return result;
}
void write_all(int fd, const char* data, std::size_t size) {
    std::size_t used = 0;
    while (used < size) {
        const auto count = write(fd, data + used, size - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw std::runtime_error("Cannot write frame capture");
        used += static_cast<std::size_t>(count);
    }
}
}
struct Renderer::Impl {
    Display* display = nullptr;
    Window window = 0;
    Window overlay = 0;
    Colormap colormap = 0;
    GLXContext context = nullptr;
    GLuint program = 0, vao = 0, vbo = 0;
    struct Target {
        GLuint fbo = 0, color = 0, depth = 0, identifiers = 0;
        unsigned width = 0, height = 0;
        void release() {
            if (fbo) glDeleteFramebuffers(1, &fbo);
            if (color) glDeleteRenderbuffers(1, &color);
            if (depth) glDeleteRenderbuffers(1, &depth);
            if (identifiers) glDeleteRenderbuffers(1, &identifiers);
        }
    } presented, observed, prepared;
    std::optional<wire::ActorLocalViewIdentity> prepared_identity;
    wire::FrameReply prepared_frame;
    std::vector<wire::SurfaceHandle> prepared_surfaces;
    std::uint64_t frame_id = 0, security_sequence = 0;
    std::filesystem::path captures;
    wire::RendererReady info;
    std::unique_ptr<NativeInput> native_input;
    std::unique_ptr<XSurfaces> surfaces;
    std::unique_ptr<XInputRouter> input;
    std::unique_ptr<PresentationInputs> bindings;
    std::unique_ptr<ControlsOverlay> controls;
    std::unique_ptr<control::DeviceStream> capture_stream;
    std::unique_ptr<control::PhysicalCapture> physical_capture;
    std::unique_ptr<control::SpaceMouseSource> spacemouse;
    ~Impl() {
        physical_capture.reset();spacemouse.reset();capture_stream.reset();
        controls.reset(); // Release GL resources while this context is current.
        bindings.reset(); input.reset(); surfaces.reset(); native_input.reset();
        if (context) {
            if (program) glDeleteProgram(program);
            if (vao) glDeleteVertexArrays(1, &vao);
            if (vbo) glDeleteBuffers(1, &vbo);
            presented.release(); observed.release(); prepared.release();
            glXMakeCurrent(display, None, nullptr);
            glXDestroyContext(display, context);
        }
        if (window) XDestroyWindow(display, window);
        if (overlay) XCompositeReleaseOverlayWindow(display, DefaultRootWindow(display));
        if (colormap) XFreeColormap(display, colormap);
        if (display) XCloseDisplay(display);
    }
    void output() {
        if (!presented.width) return;
        glBindFramebuffer(GL_READ_FRAMEBUFFER, presented.fbo);
        glReadBuffer(GL_COLOR_ATTACHMENT0);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, 0);
        glDrawBuffer(GL_BACK);
        glBlitFramebuffer(0, 0, presented.width, presented.height, 0, 0, presented.width, presented.height, GL_COLOR_BUFFER_BIT, GL_NEAREST);
        if (controls) controls->draw(presented.width, presented.height);
        glXSwapBuffers(display, window);
        glFinish(); check_gl(); check_x(display);
    }
};
Renderer::Renderer(const std::string& name, const std::filesystem::path& state, const render::v1::NativeCompositorConfig* native) : impl_(std::make_unique<Impl>()) {
    auto& r = *impl_;
    if (name.empty()) throw std::invalid_argument("An explicit X display is required");
    r.captures = state / "captures";
    const auto status = std::filesystem::symlink_status(r.captures);
    if (std::filesystem::is_symlink(status) || (std::filesystem::exists(status) && !std::filesystem::is_directory(status)))
        throw std::invalid_argument("Unsafe capture directory");
    std::filesystem::create_directory(r.captures);
    r.display = native ? open_compositor_display(name, *native) : XOpenDisplay(name.c_str());
    r.security_sequence = native ? 1 : 0;
    if (!r.display) throw std::runtime_error("Cannot open explicit X display");
    XSetErrorHandler(record_x_error);
    const auto root = DefaultRootWindow(r.display);
    // SubstructureRedirect is exclusive. BadAccess preserves the existing WM.
    XSelectInput(r.display, root, SubstructureRedirectMask | SubstructureNotifyMask);
    check_x(r.display);
    int major=0,minor=0;
    if (!XCompositeQueryVersion(r.display,&major,&minor) || (major==0 && minor<3))
        throw std::runtime_error("Composite 0.3 is required");
    r.overlay=XCompositeGetOverlayWindow(r.display,root);
    check_x(r.display);
    const int attributes[]{GLX_X_RENDERABLE, True, GLX_DRAWABLE_TYPE, GLX_WINDOW_BIT,
        GLX_RENDER_TYPE, GLX_RGBA_BIT, GLX_X_VISUAL_TYPE, GLX_TRUE_COLOR,
        GLX_RED_SIZE, 8, GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8,
        GLX_DOUBLEBUFFER, True, None};
    int count = 0;
    GLXFBConfig* configs = glXChooseFBConfig(r.display, DefaultScreen(r.display), attributes, &count);
    if (!configs || count == 0) { if (configs) XFree(configs); throw std::runtime_error("No GLX window configuration"); }
    const auto config = configs[0];
    XVisualInfo* visual = glXGetVisualFromFBConfig(r.display, config);
    XFree(configs);
    if (!visual) throw std::runtime_error("No GLX visual");
    r.colormap = XCreateColormap(r.display, root, visual->visual, AllocNone);
    XSetWindowAttributes values{};
    values.colormap = r.colormap; values.override_redirect = True;
    values.event_mask = ExposureMask | StructureNotifyMask;
    r.window = XCreateWindow(r.display, r.overlay, 0, 0, 128, 128, 0, visual->depth, InputOutput,
                              visual->visual, CWColormap | CWOverrideRedirect | CWEventMask, &values);
    XFree(visual); check_x(r.display);
    XStoreName(r.display, r.window, "Spatial runtime");
    const auto create = reinterpret_cast<PFNGLXCREATECONTEXTATTRIBSARBPROC>(
        glXGetProcAddressARB(reinterpret_cast<const GLubyte*>("glXCreateContextAttribsARB")));
    if (!create) throw std::runtime_error("GLX context creation unavailable");
    const int context_attributes[]{GLX_CONTEXT_MAJOR_VERSION_ARB, 3, GLX_CONTEXT_MINOR_VERSION_ARB, 3,
                                  GLX_CONTEXT_PROFILE_MASK_ARB, GLX_CONTEXT_CORE_PROFILE_BIT_ARB, None};
    r.context = create(r.display, config, nullptr, True, context_attributes);
    check_x(r.display);
    if (!r.context || !glXMakeCurrent(r.display, r.window, r.context)) throw std::runtime_error("Cannot activate GLX context");
    glewExperimental = GL_TRUE;
    const auto glew_status = glewInit();
    // GLEW 2.2 probes legacy extension strings in a core context.
    while (glGetError() != GL_NO_ERROR) {}
    if (glew_status != GLEW_OK || !GLEW_VERSION_3_3 || !GLEW_ARB_gpu_shader_fp64)
        throw std::runtime_error("OpenGL 3.3 with ARB_gpu_shader_fp64 is required");
    r.info.set_gl_version(reinterpret_cast<const char*>(glGetString(GL_VERSION)));
    r.info.set_gl_renderer(reinterpret_cast<const char*>(glGetString(GL_RENDERER)));
    r.info.mutable_viewport()->set_width(DisplayWidth(r.display, DefaultScreen(r.display)));
    r.info.mutable_viewport()->set_height(DisplayHeight(r.display, DefaultScreen(r.display)));
    std::cerr << "spatialwm: " << r.info.gl_version() << "; " << r.info.gl_renderer() << '\n';
    const char* vertex = R"(#version 330 core
layout(location=0) in vec2 position;
void main() { gl_Position = vec4(position, 0.0, 1.0); }
)";
    const char* fragment = R"(#version 330 core
#extension GL_ARB_gpu_shader_fp64 : require
uniform dvec3 origin, ray_x, ray_y, ray_z;
uniform dvec2 extent, viewport, clip_planes;
uniform double focal;
uniform vec3 rgb;
uniform sampler2D application;
uniform bool textured, inverted;
uniform uint representation;
layout(location=0) out vec4 pixel;
layout(location=1) out uint visible_representation;
void main() {
    dvec3 ray = dvec3((double(gl_FragCoord.x)*2.0-viewport.x)/(focal*viewport.y),
                     (double(gl_FragCoord.y)*2.0-viewport.y)/(focal*viewport.y), -1.0);
    // Parameterize by camera depth. Normalization is unnecessary for intersection
    // and introduces another rounding step at half-open panel boundaries.
    dvec3 local_ray = ray_x*ray.x + ray_y*ray.y + ray_z*ray.z;
    if (local_ray.z >= 0.0 || origin.z < 0.0) discard;
    double distance = -origin.z/local_ray.z;
    double depth = -ray.z*distance;
    if (depth < clip_planes.x || depth > clip_planes.y) discard;
    dvec3 contact = origin + local_ray*distance;
    dvec2 uv = dvec2(contact.x/extent.x+0.5, 0.5-contact.y/extent.y);
    if (uv.x < 0.0 || uv.x >= 1.0 || uv.y < 0.0 || uv.y >= 1.0) discard;
    gl_FragDepth = float(clip_planes.x/depth);
    vec2 sample_uv=vec2(uv);
    // Panel UV starts at the top. GLX Y-inverted pixmaps already use top=0.
    if (!inverted) sample_uv.y=1.0-sample_uv.y;
    pixel = textured ? vec4(texture(application,sample_uv).rgb,1.0) : vec4(rgb, 1.0);
    visible_representation = representation;
}
)";
    const auto vs = shader(GL_VERTEX_SHADER, vertex);
    GLuint fs = 0;
    try { fs = shader(GL_FRAGMENT_SHADER, fragment); } catch (...) { glDeleteShader(vs); throw; }
    r.program = glCreateProgram();
    glAttachShader(r.program, vs); glAttachShader(r.program, fs); glLinkProgram(r.program);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint linked = 0; glGetProgramiv(r.program, GL_LINK_STATUS, &linked);
    if (!linked) throw std::runtime_error("Cannot link scene shader");
    glGenVertexArrays(1, &r.vao); glBindVertexArray(r.vao);
    glGenBuffers(1, &r.vbo); glBindBuffer(GL_ARRAY_BUFFER, r.vbo);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * sizeof(float), nullptr);
    glEnableVertexAttribArray(0);
    check_gl();
    int opcode=0, event=0, error=0;
    if (XQueryExtension(r.display, "SPATIAL-INPUT", &opcode, &event, &error))
        r.native_input=std::make_unique<NativeInput>(r.display);
    r.surfaces=std::make_unique<XSurfaces>(r.display,r.window,r.overlay,r.native_input.get());
    if (r.native_input) r.input=std::make_unique<XInputRouter>(r.display,*r.native_input,*r.surfaces);
    r.bindings=std::make_unique<PresentationInputs>(r.display,*r.surfaces,native ? native->server_id() : std::string{});
    r.controls=std::make_unique<ControlsOverlay>(r.display);
}
Renderer::~Renderer() = default;
wire::RendererReady Renderer::ready() const { return impl_->info; }
int Renderer::connection() const { return ConnectionNumber(impl_->display); }
void Renderer::events() {
    auto& r = *impl_;
    for (unsigned drained=0; drained<256 && XPending(r.display); ++drained) {
        XEvent event{}; XNextEvent(r.display, &event);
        if(r.physical_capture) {
            try {if(r.physical_capture->handle(event))continue;}
            catch(...) {stop_capture();}
        }
        if (event.type == Expose && event.xexpose.window == r.window && event.xexpose.count == 0) r.output();
        if (r.surfaces) r.surfaces->event(event);
        if (event.type == DestroyNotify && event.xdestroywindow.window == r.window)
            throw std::runtime_error("Compositor output was destroyed");
    }
    if(r.capture_stream) {
        try {
            const auto now=control::DeviceAdapters::Clock::now();
            if(r.physical_capture)r.physical_capture->pump(now);
            if(r.spacemouse)r.spacemouse->pump(now);
            pollfd peer{r.capture_stream->descriptor(),static_cast<short>(POLLIN),0};
            if(poll(&peer,1,0)<0 || (peer.revents&(POLLIN|POLLHUP|POLLERR|POLLNVAL)))stop_capture();
        } catch(...) {stop_capture();}
    }
    check_x(r.display);
}
wire::NativeSnapshot Renderer::synchronize() {
    auto& r=*impl_;
    // First barrier covers prior client requests; second covers WM map/configure replies.
    for (int i=0;i<2;++i) { check_x(r.display); events(); }
    r.surfaces->synchronize(); check_x(r.display);
    auto result = r.surfaces->snapshot();
    if (r.input) r.input->snapshot(result);
    return result;
}
render::v1::NativeActorSeatIdentity Renderer::native_actor_seat(const std::string& actor) {
    auto& r=*impl_;
    if (!r.input) throw WorldError(wire::UNAVAILABLE, "Unbound compositor input is unavailable");
    if (r.security_sequence == UINT64_MAX) throw std::runtime_error("Native identity sequence exhausted");
    const auto owner=inspect_compositor_identity(r.display,++r.security_sequence);
    render::v1::NativeActorSeatIdentity result;
    const auto seat=r.input->native_seat(actor);
    result.set_actor_entity_id(actor); result.set_native_seat_id(seat.seat());
    result.set_pointer_device(seat.pointer_device());result.set_keyboard_device(seat.keyboard_device());
    result.set_server_id(owner.server_id()); result.set_presentation_generation(1);
    return result;
}
render::v1::NativeActorSeatIdentity Renderer::native_actor_seat(const render::v1::WorldPresentationBinding& binding, const std::string& actor) {
    return impl_->bindings->native_actor_seat(binding,actor);
}
wire::NativeActorInput Renderer::input(const wire::RendererInputCommand& command) {
    if (!impl_->input) throw WorldError(wire::UNIMPLEMENTED, "Native actor input requires the project XLibre server");
    synchronize();
    return impl_->input->execute(command);
}
void Renderer::reserve_input(const render::v1::WorldPresentationBinding& binding,const supervisor::v1::NativeScopeIdentity* scope) {
    impl_->bindings->reserve(binding,scope?scope->server_id():std::string{},scope?scope->credential_id():std::string{});
}
void Renderer::initialize_input(const render::v1::WorldPresentationBinding& binding,int descriptor) {
    auto& r=*impl_;
    r.bindings->initialize(binding,descriptor);
    try { if(r.input)r.input->close_all(); }
    catch (...) { r.bindings->retire(binding);throw; }
    r.input.reset();
}
void Renderer::clear_presentation() {
    auto& r=*impl_;
    discard_prepared_local_frame();
    if(!r.presented.width)return;
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER,r.presented.fbo);
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    const float black[4]={0,0,0,1};glClearBufferfv(GL_COLOR,0,black);
    check_gl();r.output();
}
void Renderer::present_controls(const control::v1::ControlMenuView& view) {
    ControlsOverlay::validate(view);
    auto& r=*impl_;
    if (view.visible() && !r.presented.width) {
        // Host UI remains usable before any actor has observation authority.
        // This blank scene contains no world data and never becomes an observation.
        wire::RenderCommand blank;
        blank.set_width(r.info.viewport().width());blank.set_height(r.info.viewport().height());
        auto* scene=blank.mutable_scene();scene->mutable_camera()->mutable_position();
        scene->mutable_camera()->mutable_orientation()->set_w(1);
        auto* config=scene->mutable_config();config->set_vertical_fov(std::acos(-1.0)/2);
        config->set_near_clip(.01);config->set_far_clip(1000);
        config->mutable_background();
        (void)draw(blank);
    }
    r.controls->set(view);
    r.output(); // Receipt follows swap, GL completion, and the X error barrier.
}
void Renderer::start_capture(int fd,pid_t creator,std::uint64_t generation,
        const control::v1::NativeCaptureConfig& config) {
    auto& r=*impl_;
    // DeviceStream owns the transferred descriptor even if subsequent validation fails.
    auto stream=std::make_unique<control::DeviceStream>(fd,creator,generation,control::DeviceStream::Role::Capture);
    control::validate_native_capture(config);
    if(r.capture_stream)throw std::invalid_argument("Native capture is already active");
    if(!r.presented.width) {
        wire::RenderCommand blank;blank.set_width(r.info.viewport().width());blank.set_height(r.info.viewport().height());
        auto* scene=blank.mutable_scene();scene->mutable_camera()->mutable_position();scene->mutable_camera()->mutable_orientation()->set_w(1);
        auto* projection=scene->mutable_config();projection->set_vertical_fov(std::acos(-1.0)/2);
        projection->set_near_clip(.01);projection->set_far_clip(1000);projection->mutable_background();
        (void)draw(blank);
    }
    XResizeWindow(r.display,r.window,r.info.viewport().width(),r.info.viewport().height());
    XMapRaised(r.display,r.window);check_x(r.display);
    r.capture_stream=std::move(stream);
    const auto emit=[&r](const control::v1::DeviceEvent& event) {
        r.capture_stream->emit(event,control::DeviceAdapters::Clock::now());
    };
    try {
        if(!config.physical_sources().empty()) {
            std::vector<control::PhysicalSource> sources;
            for(const auto& source:config.physical_sources())sources.push_back({source.device_node(),source.kind()});
            r.physical_capture=std::make_unique<control::PhysicalCapture>(r.display,r.window,std::move(sources),emit);
        }
        if(config.spacemouse()) {r.spacemouse=std::make_unique<control::SpaceMouseSource>(emit);(void)r.spacemouse->open();}
        check_x(r.display);
    } catch(...) {stop_capture();throw;}
}
void Renderer::stop_capture() {
    auto& r=*impl_;r.physical_capture.reset();r.spacemouse.reset();r.capture_stream.reset();check_x(r.display);
}
bool Renderer::capture_active() const {return bool(impl_->capture_stream);}
void Renderer::bind_input(const render::v1::WorldPresentationBinding& binding, int descriptor) {
    auto& r=*impl_;
    r.bindings->bind(binding,descriptor);
    try { if (r.input) r.input->close_all(); }
    catch (...) { r.bindings->retire(binding); throw; }
    r.input.reset(); // Scoped mode never returns to the unbound input path.
}
void Renderer::confirm_input_retired(const render::v1::WorldPresentationBinding& binding) {impl_->bindings->confirm_retired(binding);}
void Renderer::retire_input(const render::v1::WorldPresentationBinding& binding) {
    impl_->bindings->retire(binding);
}
wire::NativeActorInput Renderer::input(const render::v1::WorldPresentationBinding& binding,
                                     const wire::RendererInputCommand& command) {
    synchronize();
    return impl_->bindings->input(binding,command);
}
wire::NativeSnapshot Renderer::synchronize(const render::v1::WorldPresentationBinding& binding) {
    auto result=synchronize();
    impl_->bindings->snapshot(binding,result);
    return result;
}
wire::FrameReply Renderer::draw(const wire::RenderCommand& command,int capture_fd) {
    return draw_scene(command, nullptr,capture_fd);
}
void Renderer::discard_prepared_local_frame() {
    impl_->prepared_identity.reset(); impl_->prepared_frame.Clear();
    impl_->prepared_surfaces.clear();
}
wire::FrameReply Renderer::prepare_local_frame(const wire::RenderCommand& command) {
    discard_prepared_local_frame();
    validate_message(command); validate_local_view_identity(command.local_actor_view());
    if (!command.prepare_local_frame() || !command.has_scene() || command.observe() || command.capture() ||
        command.include_pixels() || command.has_sync() || command.has_input() ||
        command.has_commit_local_frame() || command.has_clear_local_view())
        throw std::invalid_argument("Invalid local frame preparation");
    const auto result = draw_scene(command, nullptr);
    for (const auto& surface : command.scene().surfaces())
        impl_->prepared_surfaces.push_back(surface.handle());
    impl_->prepared_frame = result; impl_->prepared_identity = command.local_actor_view();
    return result;
}
wire::FrameReply Renderer::commit_local_frame(const wire::ActorLocalViewIdentity& identity, std::uint64_t frame_id) {
    validate_local_view_identity(identity);
    auto& r = *impl_;
    if (!frame_id || !r.prepared_identity || r.prepared_frame.frame_id() != frame_id ||
        r.prepared_identity->SerializeAsString() != identity.SerializeAsString())
        throw WorldError(wire::STALE_TARGET, "Local frame is not prepared for this actor view");
    const auto result = r.prepared_frame;
    const auto surfaces = r.prepared_surfaces;
    discard_prepared_local_frame(); // Output failure cannot make this frame replayable.
    synchronize();
    for (const auto& handle : surfaces)
        if (!r.surfaces->native(handle))
            throw WorldError(wire::STALE_TARGET, "Prepared application surface changed before commitment");
    XResizeWindow(r.display, r.window, r.prepared.width, r.prepared.height);
    XMapRaised(r.display, r.window); check_x(r.display);
    std::swap(r.prepared, r.presented);
    r.output();
    return result;
}
wire::FrameReply Renderer::draw_geometry(const identity::v1::ActorGeometryView& view) {
    validate_geometry_view(view);
    wire::RenderScene scene;
    *scene.mutable_camera() = view.camera();
    *scene.mutable_config() = view.config();
    scene.set_world_revision(view.world_revision());
    const auto copy = [](const auto& input, auto* output) {
        for (const auto& source : input) {
            auto* entity = output->Add();
            entity->set_id(source.id()); entity->set_kind(source.kind());
            *entity->mutable_pose() = source.pose();
            if (source.has_size()) *entity->mutable_size() = source.size();
            if (source.has_color()) *entity->mutable_color() = source.color();
            if (source.has_bounds()) *entity->mutable_bounds() = source.bounds();
        }
    };
    copy(view.panels(), scene.mutable_panels());
    copy(view.barriers(), scene.mutable_barriers());
    copy(view.portals(), scene.mutable_portals());
    copy(view.objects(), scene.mutable_objects());
    copy(view.actors(), scene.mutable_actors());
    for (const auto& metadata : view.presentation_views())
        *scene.add_presentation_views() = metadata;
    const auto& display = impl_->display;
    const int screen = DefaultScreen(display);
    const int width = DisplayWidth(display, screen), height = DisplayHeight(display, screen);
    validate_render_extent(static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height));
    wire::RenderCommand command;
    command.set_protocol_version(1); command.set_request_id(1);
    *command.mutable_scene() = std::move(scene);
    command.set_width(static_cast<std::uint32_t>(width));
    command.set_height(static_cast<std::uint32_t>(height));
    return draw(command);
}
wire::RenderVisibility Renderer::observe(const wire::RenderCommand& command) {
    wire::RenderVisibility result;
    const auto frame = draw_scene(command, &result);
    result.set_frame_id(frame.frame_id()); result.set_world_revision(frame.world_revision());
    if (result.ByteSizeLong() > max_frame_bytes - 128)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Compositor observation exceeds message limit");
    return result;
}
wire::FrameReply Renderer::draw_scene(const wire::RenderCommand& command, wire::RenderVisibility* visibility,int capture_fd) {
    if(capture_fd>=0){
        struct stat info{};const int flags=fcntl(capture_fd,F_GETFL);
        if(capture_fd<3 || !command.capture() || visibility || fstat(capture_fd,&info)!=0 || !S_ISREG(info.st_mode) ||
            info.st_uid!=geteuid() || (info.st_mode&0777)!=0600 || info.st_nlink!=1 || info.st_size!=0 ||
            flags<0 || (flags&O_ACCMODE)==O_RDONLY || (flags&O_APPEND) || lseek(capture_fd,0,SEEK_SET)!=0)
            throw std::invalid_argument("Capture requires a private empty writable regular file");
    }
    if (visibility) validate_observation_extent(command.width(), command.height(), command.include_pixels());
    else validate_render_extent(command.width(), command.height());
    if (!command.has_scene()) throw std::invalid_argument("Missing render scene");
    const auto panels = scene_panels(command.scene());
    auto& r = *impl_;
    synchronize();
    const bool preparing = command.prepare_local_frame();
    const bool presenting = !visibility && !preparing;
    auto& target = visibility ? r.observed : preparing ? r.prepared : r.presented;
    if (!target.fbo) {
        glGenFramebuffers(1, &target.fbo); glGenRenderbuffers(1, &target.color);
        glGenRenderbuffers(1, &target.depth); glGenRenderbuffers(1, &target.identifiers);
    }
    if (target.width != command.width() || target.height != command.height()) {
        target.width = command.width(); target.height = command.height();
        glBindRenderbuffer(GL_RENDERBUFFER, target.color);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, target.width, target.height);
        glBindRenderbuffer(GL_RENDERBUFFER, target.depth);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT32F, target.width, target.height);
        glBindFramebuffer(GL_FRAMEBUFFER, target.fbo);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, target.color);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, target.depth);
        glBindRenderbuffer(GL_RENDERBUFFER, target.identifiers);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_R32UI, target.width, target.height);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT1, GL_RENDERBUFFER, target.identifiers);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) throw std::runtime_error("Incomplete scene framebuffer");
        if (presenting) XResizeWindow(r.display, r.window, target.width, target.height);
    }
    if (presenting) { XMapRaised(r.display, r.window); check_x(r.display); }
    glBindFramebuffer(GL_FRAMEBUFFER, target.fbo);
    const GLenum attachments[]{GL_COLOR_ATTACHMENT0, GL_COLOR_ATTACHMENT1};
    glDrawBuffers(2, attachments); glReadBuffer(GL_COLOR_ATTACHMENT0);
    glViewport(0, 0, target.width, target.height);
    glDisable(GL_BLEND); glDisable(GL_DITHER); glDisable(GL_MULTISAMPLE); glDisable(GL_FRAMEBUFFER_SRGB);
    glEnable(GL_DEPTH_TEST); glDepthFunc(GL_GREATER); glDepthMask(GL_TRUE);
    glEnable(GL_CULL_FACE); glCullFace(GL_BACK); glFrontFace(GL_CCW);
    const auto& config = command.scene().config();
    const auto& bg = config.background();
    const GLfloat background[]{bg.red() / 255.0f, bg.green() / 255.0f, bg.blue() / 255.0f, 1};
    const GLuint zero[]{0, 0, 0, 0};
    const GLfloat depth = 0;
    glClearBufferfv(GL_COLOR, 0, background); glClearBufferuiv(GL_COLOR, 1, zero);
    glClearBufferfv(GL_DEPTH, 0, &depth);
    // The projection retains the precision of the OpenGL float vertex pipeline.
    const double f = static_cast<float>(1 / std::tan(config.vertical_fov() / 2));
    glUseProgram(r.program);
    glActiveTexture(GL_TEXTURE0);
    glUniform1i(glGetUniformLocation(r.program,"application"),0);
    glUniform1d(glGetUniformLocation(r.program, "focal"), f);
    glUniform2d(glGetUniformLocation(r.program, "viewport"), target.width, target.height);
    glUniform2d(glGetUniformLocation(r.program, "clip_planes"), config.near_clip(), config.far_clip());
    glBindVertexArray(r.vao); glBindBuffer(GL_ARRAY_BUFFER, r.vbo);
    unsigned representation = 0;
    for (const auto& panel : panels) {
        glUniform1ui(glGetUniformLocation(r.program, "representation"), ++representation);
        const bool textured=!panel.surface.id().empty();
        if (textured) {
            const auto texture=r.surfaces->texture(panel.surface);
            if (!texture && preparing)
                throw WorldError(wire::STALE_TARGET, "Application surface changed before frame preparation");
            if (!texture) continue; // A later native generation cannot inherit an earlier scene handle.
            glBindTexture(GL_TEXTURE_2D,texture->name);
            glUniform1i(glGetUniformLocation(r.program,"inverted"),texture->inverted);
        }
        glUniform1i(glGetUniformLocation(r.program,"textured"),textured);
        const auto bounds = panel_bounds(panel, config, target.width, target.height);
        if (!bounds) continue;
        const auto& b = *bounds;
        const std::array<float, 12> vertices{b[0],b[1], b[2],b[1], b[2],b[3], b[0],b[1], b[2],b[3], b[0],b[3]};
        glUniform3dv(glGetUniformLocation(r.program, "origin"), 1, panel.origin.data());
        glUniform3dv(glGetUniformLocation(r.program, "ray_x"), 1, panel.ray_x.data());
        glUniform3dv(glGetUniformLocation(r.program, "ray_y"), 1, panel.ray_y.data());
        glUniform3dv(glGetUniformLocation(r.program, "ray_z"), 1, panel.ray_z.data());
        glUniform2dv(glGetUniformLocation(r.program, "extent"), 1, panel.size.data());
        glUniform3fv(glGetUniformLocation(r.program, "rgb"), 1, panel.color.data());
        glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices.data(), GL_STREAM_DRAW);
        glDrawArrays(GL_TRIANGLES, 0, 6);
    }
    if (presenting) r.output();
    else if (preparing) { glFinish(); check_gl(); }
    else {
        glFinish(); check_gl();
        std::vector<GLuint> indices(std::size_t(target.width) * target.height);
        glReadBuffer(GL_COLOR_ATTACHMENT1);
        glPixelStorei(GL_PACK_ALIGNMENT, 4);
        glReadPixels(0, 0, target.width, target.height, GL_RED_INTEGER, GL_UNSIGNED_INT, indices.data()); check_gl();
        std::vector<wire::VisibilitySample> samples(panels.size() + 1);
        for (unsigned y = 0; y < target.height; ++y) for (unsigned x = 0; x < target.width; ++x) {
            const auto index = indices[std::size_t(target.height - 1 - y) * target.width + x];
            if (index == 0) continue;
            if (index >= samples.size()) throw std::runtime_error("Invalid rendered representation index");
            auto& sample = samples[index];
            if (!sample.pixels()) {
                sample.set_index(index); sample.mutable_pixel()->set_x(x); sample.mutable_pixel()->set_y(y);
            }
            sample.set_pixels(sample.pixels() + 1);
        }
        for (const auto& sample : samples) if (sample.pixels()) *visibility->add_visible() = sample;
        if (command.include_pixels()) {
            glReadBuffer(GL_COLOR_ATTACHMENT0);
            glPixelStorei(GL_PACK_ALIGNMENT, 1);
            const std::size_t stride = std::size_t(target.width) * 3;
            std::vector<char> pixels(stride * target.height);
            glReadPixels(0, 0, target.width, target.height, GL_RGB, GL_UNSIGNED_BYTE, pixels.data()); check_gl();
            auto* image = visibility->mutable_image();
            image->set_width(target.width); image->set_height(target.height);
            auto* rgb = image->mutable_rgb8(); rgb->resize(pixels.size());
            for (unsigned y = 0; y < target.height; ++y)
                std::copy_n(pixels.data() + (target.height - 1 - y) * stride, stride, rgb->data() + y * stride);
        }
    }
    wire::FrameReply result;
    result.set_frame_id(++r.frame_id); result.set_world_revision(command.scene().world_revision());
    result.set_xid(r.window);
    if (command.capture()) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, target.fbo); glReadBuffer(GL_COLOR_ATTACHMENT0);
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        const std::size_t stride = std::size_t(target.width) * 3;
        std::vector<char> pixels(stride * target.height);
        glReadPixels(0, 0, target.width, target.height, GL_RGB, GL_UNSIGNED_BYTE, pixels.data()); check_gl();
        std::string name = (r.captures / "frame-XXXXXX.ppm").string();
        const int fd = capture_fd>=0?capture_fd:mkstemps(name.data(), 4);
        if (fd < 0) throw std::runtime_error("Cannot create frame capture");
        try {
            const auto header = "P6\n" + std::to_string(target.width) + " " + std::to_string(target.height) + "\n255\n";
            write_all(fd, header.data(), header.size());
            for (unsigned row = target.height; row > 0; --row) write_all(fd, pixels.data() + (row - 1) * stride, stride);
        } catch (...) { if(capture_fd<0){close(fd);unlink(name.c_str());} throw; }
        if(capture_fd<0){
            if (close(fd) != 0) { unlink(name.c_str()); throw std::runtime_error("Cannot close frame capture"); }
            result.set_path(name);
        }
    }
    return result;
}
}
