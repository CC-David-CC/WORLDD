#include "platform/render_worker.hpp"
#include "core/scene.hpp"
#include "platform/presentation_stream.hpp"
#include "platform/process_handle.hpp"
#include "presentation.pb.h"
#include "world/store.hpp"
#include <array>
#include <cerrno>
#include <chrono>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/random.h>
namespace spatial {
using Clock = std::chrono::steady_clock;
struct RenderWorker::Shared {
    std::string display;
    std::filesystem::path capture_path;
    PresentationStream::File captures;
    ProcessHandle process;
    std::unique_ptr<PresentationStream> stream;
    Shared(const std::filesystem::path& state,std::string display,int channel,int pidfd,pid_t pid,pid_t creator)
        :display(std::move(display)),process(ProcessHandle::duplicate(pidfd,pid)) {
        PresentationStream::File copy(fcntl(channel,F_DUPFD_CLOEXEC,3));
        if(copy.value<3)throw std::runtime_error("Cannot retain world presentation channel");
        stream.reset(new PresentationStream(copy.release(),creator,pid));
        if(!state.is_absolute())throw std::invalid_argument("Capture state must be absolute");
        PresentationStream::File directory(open(state.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        if(directory.value<3 || (mkdirat(directory.value,"captures",0700)!=0 && errno!=EEXIST))
            throw std::runtime_error("Cannot create world capture directory");
        captures=PresentationStream::File(openat(directory.value,"captures",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        struct stat info{};
        if(captures.value<3 || fstat(captures.value,&info)!=0 || info.st_uid!=geteuid() || (info.st_mode&077)!=0)
            throw std::runtime_error("Unsafe world capture directory");
        capture_path=state/"captures";
    }
};

RenderWorker::RenderWorker(const std::filesystem::path& executable, const std::filesystem::path& state, std::string display, int native_fd)
    : process_(std::make_unique<RenderProcess>(executable, state, std::move(display), native_fd)),
      socket_(process_->socket()), pidfd_(process_->pidfd()) {
    try {
        const auto flags = fcntl(socket_, F_GETFL);
        if (flags < 0 || fcntl(socket_, F_SETFL, flags | O_NONBLOCK) < 0) throw std::runtime_error("Cannot configure renderer channel");
        deadline_ = Clock::now() + std::chrono::seconds(10);
        const auto ready = receive();
        if (ready.request_id() != 0 || !ready.has_ready()) throw std::runtime_error("Missing compositor readiness");
    } catch (...) {
        process_->terminate();
        throw;
    }
}
RenderWorker::RenderWorker(const std::filesystem::path& state,std::string display,int channel,int pidfd,pid_t pid,pid_t creator,int native_fd)
    :shared_(std::make_unique<Shared>(state,std::move(display),channel,pidfd,pid,creator)) {
    try {
        render::v1::PresentationWorldRequest hello;
        hello.set_protocol_version(1);hello.set_request_id(++sequence_);hello.mutable_hello();
        shared_->stream->send(hello.SerializeAsString(),{native_fd});
        deadline_=Clock::now()+std::chrono::seconds(10);
        const auto ready=receive();
        if(ready.request_id()!=sequence_ || !ready.has_ready())throw std::runtime_error("Missing shared compositor readiness");
    }catch(...){shared_->stream.reset();throw;}
}
RenderWorker::~RenderWorker() = default;
pid_t RenderWorker::pid() const {return shared_?shared_->process.pid():process_->pid();}
const std::string& RenderWorker::display() const {return shared_?shared_->display:process_->display();}
void RenderWorker::check_alive() const {
    if(shared_){
        if(!shared_->stream || !shared_->process.live())throw std::runtime_error("Shared compositor is unavailable");
        return;
    }
    pollfd fd{pidfd_, POLLIN, 0};
    const int result = poll(&fd, 1, 0);
    if (result < 0 && errno == EINTR) return;
    if (result < 0 || fd.revents) throw std::runtime_error("Compositor exited");
}
void RenderWorker::wait(short events) {
    while (true) {
        tick_();
        if (Clock::now() >= deadline_) {
            process_->terminate(); // A stalled compositor cannot resume an uncertain frame.
            throw std::runtime_error("Compositor I/O deadline exceeded");
        }
        pollfd fds[2]{{socket_, events, 0}, {pidfd_, POLLIN, 0}};
        const int ready = poll(fds, 2, 10);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0 || fds[1].revents) throw std::runtime_error("Compositor exited during request");
        if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) throw std::runtime_error("Compositor channel closed");
        if (fds[0].revents & events) return;
    }
}
void RenderWorker::read_all(char* out, std::size_t size) {
    std::size_t used = 0;
    while (used < size) {
        wait(POLLIN);
        const auto count = read(socket_, out + used, size - used);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count <= 0) throw std::runtime_error("Truncated compositor frame");
        used += static_cast<std::size_t>(count);
    }
}
wire::RenderResult RenderWorker::receive() {
    if(shared_){
        try {
            while(true){
                tick_();check_alive();
                if(Clock::now()>=deadline_)throw std::runtime_error("Shared compositor I/O deadline exceeded");
                shared_->stream->flush();
                if(auto frame=shared_->stream->receive()){
                    wire::RenderResult result;
                    if(!frame->descriptors.empty() || !result.ParseFromString(frame->bytes) || result.protocol_version()!=1)
                        throw std::runtime_error("Invalid shared compositor reply");
                    validate_message(result);return result;
                }
                pollfd descriptors[2]{{shared_->stream->descriptor(),static_cast<short>(POLLIN|(shared_->stream->pending_output()?POLLOUT:0)),0},
                    {shared_->process.descriptor(),POLLIN,0}};
                if(poll(descriptors,2,10)<0 && errno!=EINTR)throw std::runtime_error("Shared compositor poll failed");
            }
        }catch(...){shared_->stream.reset();throw;}
    }
    std::array<unsigned char, 4> header{};
    read_all(reinterpret_cast<char*>(header.data()), header.size());
    const std::uint32_t size = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                               (std::uint32_t(header[2]) << 8) | header[3];
    if (size > max_frame_bytes) throw std::runtime_error("Excessive compositor frame");
    std::string bytes(size, '\0'); read_all(bytes.data(), size);
    wire::RenderResult result;
    if (!result.ParseFromString(bytes) || result.protocol_version() != 1) throw std::runtime_error("Invalid compositor reply");
    validate_message(result); return result;
}
wire::FrameReply RenderWorker::render(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool capture) {
    validate_render_extent(width, height);
    wire::RenderCommand command;
    *command.mutable_scene() = scene;
    command.set_width(width); command.set_height(height); command.set_capture(capture);
    struct Capture {
        PresentationStream::File file;
        int directory=-1;std::string name;bool accepted=false;
        ~Capture(){if(directory>=0 && !accepted && !name.empty())unlinkat(directory,name.c_str(),0);}
    } output;
    if(shared_ && capture){
        std::array<unsigned char,16> random{};
        if(getrandom(random.data(),random.size(),0)!=static_cast<ssize_t>(random.size()))
            throw std::runtime_error("Cannot allocate capture identity");
        output.directory=shared_->captures.value;output.name="frame-";
        constexpr char hex[]="0123456789abcdef";
        for(auto byte:random){output.name+=hex[byte>>4];output.name+=hex[byte&15];}output.name+=".ppm";
        output.file=PresentationStream::File(openat(output.directory,output.name.c_str(),O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));
        if(output.file.value<3){output.name.clear();throw std::runtime_error("Cannot create world capture");}
    }
    auto reply = exchange(command,{},output.file.value);
    if (!reply.has_frame() || reply.frame().world_revision() != scene.world_revision() || reply.frame().frame_id() <= frame_id_)
        throw std::runtime_error("Compositor frame identity mismatch");
    if(shared_){
        if(!reply.frame().path().empty())throw std::runtime_error("Shared compositor supplied a capture path");
        if(capture){
            struct stat info{};
            const auto header="P6\n"+std::to_string(width)+" "+std::to_string(height)+"\n255\n";
            if(fstat(output.file.value,&info)!=0 || info.st_size!=static_cast<off_t>(header.size()+std::uint64_t(width)*height*3))
                throw std::runtime_error("Incomplete shared compositor capture");
            reply.mutable_frame()->set_path((shared_->capture_path/output.name).string());output.accepted=true;
        }
    }
    frame_id_ = reply.frame().frame_id();
    return reply.frame();
}
wire::NativeSnapshot RenderWorker::synchronize() {
    wire::RenderCommand command; command.mutable_sync();
    const auto reply = exchange(command);
    if (!reply.has_snapshot()) throw std::runtime_error("Missing native surface snapshot");
    return reply.snapshot();
}
wire::RenderVisibility RenderWorker::observe(const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height, bool include_pixels) {
    validate_observation_extent(width, height, include_pixels);
    wire::RenderCommand command;
    *command.mutable_scene() = scene;
    command.set_width(width); command.set_height(height); command.set_observe(true);
    command.set_include_pixels(include_pixels);
    const auto reply = exchange(command);
    if (!reply.has_visibility() || reply.visibility().world_revision() != scene.world_revision() ||
        reply.visibility().frame_id() <= frame_id_) throw std::runtime_error("Compositor observation identity mismatch");
    if (reply.visibility().has_image() != include_pixels)
        throw std::runtime_error("Compositor observation image presence mismatch");
    if (include_pixels) {
        const auto& image = reply.visibility().image();
        if (image.width() != width || image.height() != height || image.rgb8().size() != std::uint64_t(width) * height * 3)
            throw std::runtime_error("Invalid compositor observation image");
    }
    const auto count = scene_panels(scene).size();
    unsigned previous = 0;
    std::uint64_t total = 0;
    for (const auto& sample : reply.visibility().visible()) {
        if (sample.index() == 0 || sample.index() > count || sample.index() <= previous || !sample.has_pixel() ||
            sample.pixel().x() >= width || sample.pixel().y() >= height || sample.pixels() == 0 ||
            sample.pixels() > std::uint64_t(width) * height - total)
            throw std::runtime_error("Invalid compositor visibility sample");
        total += sample.pixels();
        previous = sample.index();
    }
    frame_id_ = reply.visibility().frame_id();
    return reply.visibility();
}
wire::NativeActorInput RenderWorker::input(const wire::RendererInputCommand& input, const std::function<void()>& before_send) {
    wire::RenderCommand command; *command.mutable_input() = input;
    const auto reply = exchange(command, before_send);
    if (!reply.has_input() || reply.input().actor_id() != input.actor_id())
        throw std::runtime_error("Invalid compositor actor input reply");
    return reply.input();
}
wire::RenderResult RenderWorker::exchange(wire::RenderCommand command, const std::function<void()>& before_send,int capture_fd) {
    if (busy_) throw std::runtime_error("Recursive compositor exchange");
    busy_ = true;
    struct Reset { bool& value; ~Reset() { value = false; } } reset{busy_};
    if(sequence_==UINT64_MAX)throw std::runtime_error("Compositor request sequence exhausted");
    command.set_protocol_version(1); command.set_request_id(sequence_ + 1);
    if(shared_){
        check_alive();
        render::v1::PresentationWorldRequest request;request.set_protocol_version(1);request.set_request_id(++sequence_);
        *request.mutable_command()=command;
        deadline_=Clock::now()+std::chrono::seconds(10);
        wire::RenderResult reply;
        try {
            tick_();if(before_send)before_send();
            shared_->stream->send(request.SerializeAsString(),capture_fd>=3?std::vector<int>{capture_fd}:std::vector<int>{});
            while(shared_->stream->pending_output()){
                if(before_send)before_send();
                shared_->stream->flush();
                if(shared_->stream->pending_output()){
                    tick_();check_alive();
                    pollfd output{shared_->stream->descriptor(),POLLOUT,0};
                    if(poll(&output,1,10)<0 && errno!=EINTR)throw std::runtime_error("Shared compositor send poll failed");
                }
            }
            reply=receive();
            if(reply.request_id()!=sequence_)throw std::runtime_error("Shared compositor response identity mismatch");
        }catch(...){shared_->stream.reset();throw;}
        if(reply.has_error())throw WorldError(reply.error().code(),reply.error().message().c_str());
        return reply;
    }
    const auto payload = command.SerializeAsString();
    if (payload.size() > max_frame_bytes) throw std::invalid_argument("Scene exceeds renderer channel limit");
    ++sequence_;
    const auto size = static_cast<std::uint32_t>(payload.size());
    const std::array<char, 4> header{static_cast<char>(size >> 24), static_cast<char>(size >> 16),
                                    static_cast<char>(size >> 8), static_cast<char>(size)};
    std::string bytes(header.data(), header.size()); bytes += payload;
    deadline_ = Clock::now() + std::chrono::seconds(10);
    std::size_t sent = 0;
    while (sent < bytes.size()) {
        wait(POLLOUT);
        // A scheduler callback may revoke input while the socket is blocked.
        // Once bytes have been sent, finish the frame and cancel after its reply.
        if (!sent && before_send) before_send();
        const auto count = send(socket_, bytes.data() + sent, bytes.size() - sent, MSG_NOSIGNAL);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count <= 0) throw std::runtime_error("Cannot submit compositor scene");
        sent += static_cast<std::size_t>(count);
    }
    const auto reply = receive();
    if (reply.request_id() != sequence_) throw std::runtime_error("Compositor response identity mismatch");
    if (reply.has_error()) throw WorldError(reply.error().code(), reply.error().message().c_str());
    return reply;
}
}
