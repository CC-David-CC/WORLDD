#include "platform/render_worker.hpp"
#include "core/scene.hpp"
#include "platform/presentation_stream.hpp"
#include "platform/process_handle.hpp"
#include "presentation.pb.h"
#include "world/store.hpp"
#include "world/local_view_validation.hpp"
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
#include <exception>
#include <cstring>
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
struct RenderWorker::PendingCancellation {
    std::uint64_t token=0;
    wire::RendererInputCommand command;
    std::string output,input;
    std::size_t sent=0;
    std::optional<std::uint32_t> size;
    Clock::time_point deadline;
    std::exception_ptr failure;
};

RenderWorker::RenderWorker(const std::filesystem::path& executable, const std::filesystem::path& state, std::string display,
                           int native_fd, bool fixture_faults)
    : process_(std::make_unique<RenderProcess>(executable, state, std::move(display), native_fd,
                                               RenderProcess::Mode::OwnedWorld, fixture_faults)),
      socket_(process_->socket()), pidfd_(process_->pidfd()) {
    try {
        const auto flags = fcntl(socket_, F_GETFL);
        if (flags < 0 || fcntl(socket_, F_SETFL, flags | O_NONBLOCK) < 0) throw std::runtime_error("Cannot configure renderer channel");
        deadline_ = Clock::now() + std::chrono::seconds(10);
        const auto ready = receive();
        if (ready.request_id() != 0 || !ready.has_ready()) throw std::runtime_error("Missing compositor readiness");
        retain_readiness(ready.ready());
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
        retain_readiness(ready.ready());
    }catch(...){shared_->stream.reset();throw;}
}
RenderWorker::~RenderWorker() = default;
void RenderWorker::retain_readiness(const wire::RendererReady& ready) {
    if (!ready.has_viewport()) return;
    validate_render_extent(ready.viewport().width(),ready.viewport().height());
    native_viewport_=ready.viewport();
}
wire::PixelExtent RenderWorker::native_viewport() const {
    if (!native_viewport_) throw WorldError(wire::UNAVAILABLE,"Compositor did not report its native viewport");
    return *native_viewport_;
}
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
std::string RenderWorker::receive_bytes() {
    if(shared_){
        try {
            while(true){
                tick_();check_alive();
                if(Clock::now()>=deadline_)throw std::runtime_error("Shared compositor I/O deadline exceeded");
                shared_->stream->flush();
                if(auto frame=shared_->stream->receive()){
                    if(!frame->descriptors.empty()) throw std::runtime_error("Invalid shared compositor reply: unexpected descriptor");
                    return std::move(frame->bytes);
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
    return bytes;
}
wire::RenderResult RenderWorker::receive() {
    const auto bytes=receive_bytes();
    wire::RenderResult result;
    if (!result.ParseFromString(bytes) || result.protocol_version() != 1)
        throw std::runtime_error(shared_ ? "Invalid shared compositor reply: malformed protobuf or protocol version" :
                                          "Invalid compositor reply");
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
wire::FrameReply RenderWorker::prepare_local_frame(const wire::ActorLocalViewIdentity& identity,
        const wire::RenderScene& scene, std::uint32_t width, std::uint32_t height,
        const std::function<void()>& before_send) {
    if (!shared_) throw WorldError(wire::UNIMPLEMENTED, "Local frames require retained presentation ownership");
    prepared_identity_.reset(); prepared_frame_.Clear();
    validate_local_view_identity(identity); validate_render_extent(width, height);
    wire::RenderCommand command; *command.mutable_local_actor_view()=identity;
    *command.mutable_scene()=scene; command.set_width(width); command.set_height(height);
    command.set_prepare_local_frame(true);
    const auto reply=exchange(command,before_send);
    if (!reply.has_frame() || !reply.frame().path().empty() || reply.frame().world_revision()!=scene.world_revision() ||
        reply.frame().frame_id()<=frame_id_ || !reply.frame().xid() || !reply.frame().presentation_generation())
        throw std::runtime_error("Prepared compositor frame identity mismatch");
    frame_id_=reply.frame().frame_id(); prepared_frame_=reply.frame(); prepared_identity_=identity;
    return prepared_frame_;
}
wire::FrameReply RenderWorker::commit_local_frame(const wire::ActorLocalViewIdentity& identity,
        std::uint64_t frame_id, const std::function<void()>& before_send) {
    if (!shared_) throw WorldError(wire::UNIMPLEMENTED, "Local frames require retained presentation ownership");
    validate_local_view_identity(identity);
    if (!frame_id || !prepared_identity_ || prepared_identity_->SerializeAsString()!=identity.SerializeAsString() ||
        prepared_frame_.frame_id()!=frame_id)
        throw WorldError(wire::STALE_TARGET, "Actor frame is not prepared on this producer");
    const auto expected=prepared_frame_; prepared_identity_.reset(); prepared_frame_.Clear();
    wire::RenderCommand command; *command.mutable_local_actor_view()=identity;
    command.mutable_commit_local_frame()->set_frame_id(frame_id);
    const auto reply=exchange(command,before_send);
    if (!reply.has_frame() || reply.frame().SerializeAsString()!=expected.SerializeAsString())
        throw std::runtime_error("Committed compositor frame identity mismatch");
    return reply.frame();
}
void RenderWorker::clear_local_view(const wire::ActorLocalViewIdentity& identity) {
    if (!shared_) throw WorldError(wire::UNIMPLEMENTED, "Local frames require retained presentation ownership");
    validate_local_view_identity(identity);
    if (prepared_identity_ && prepared_identity_->SerializeAsString()==identity.SerializeAsString()) {
        prepared_identity_.reset(); prepared_frame_.Clear();
    }
    wire::RenderCommand command; *command.mutable_local_actor_view()=identity; command.mutable_clear_local_view();
    const auto reply=exchange(command);
    if (!reply.has_local_view_cleared() || !reply.local_view_cleared())
        throw std::runtime_error("Local actor view clearing unconfirmed");
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
render::v1::NativeActorSeatIdentity RenderWorker::native_actor_seat(const std::string& actor) {
    if (actor.empty() || actor.size()>256 || actor.find('\0')!=std::string::npos)
        throw std::invalid_argument("Invalid private actor seat selector");
    if (busy_) throw std::runtime_error("Recursive compositor exchange");
    if (sequence_==UINT64_MAX) throw std::runtime_error("Compositor request sequence exhausted");
    busy_=true;struct Reset {bool& value;~Reset(){value=false;}} reset{busy_};
    render::v1::NativeActorSeatQuery query;query.set_protocol_version(2);query.set_request_id(++sequence_);
    query.set_actor_entity_id(actor);deadline_=Clock::now()+std::chrono::seconds(10);
    render::v1::NativeActorSeatReply reply;
    try {
        check_alive();
        if(shared_){
            render::v1::PresentationWorldRequest request;request.set_protocol_version(1);request.set_request_id(sequence_);
            *request.mutable_seat_query()=query;
            shared_->stream->send(request.SerializeAsString());
        }else{
            const auto payload=query.SerializeAsString();const auto n=static_cast<std::uint32_t>(payload.size());
            std::string bytes(4,'\0');for(unsigned i=0;i<4;++i)bytes[i]=static_cast<char>(n>>(24-8*i));bytes+=payload;
            std::size_t sent=0;
            while(sent<bytes.size()){
                wait(POLLOUT);
                const auto count=send(socket_,bytes.data()+sent,bytes.size()-sent,MSG_NOSIGNAL);
                if(count<0 && (errno==EAGAIN || errno==EINTR))continue;
                if(count<=0)throw std::runtime_error("Cannot submit private seat query");
                sent+=static_cast<std::size_t>(count);
            }
        }
        const auto bytes=receive_bytes();
        if(bytes.size()>1024 || !reply.ParseFromString(bytes) || reply.protocol_version()!=2 || reply.request_id()!=sequence_)
            throw std::runtime_error("Invalid private seat query reply");
        validate_message(reply);
        if(reply.has_identity()){
            const auto& identity=reply.identity();
            if(identity.actor_entity_id()!=actor || !identity.native_seat_id() || identity.server_id().size()!=32 ||
                !identity.presentation_generation() || !identity.pointer_device() || !identity.keyboard_device() ||
                identity.pointer_device()==identity.keyboard_device())throw std::runtime_error("Private seat identity mismatch");
        }else if(!reply.has_error_code() || !reply.error_code() || reply.error_code()>INT32_MAX ||
                 !wire::ErrorCode_IsValid(static_cast<int>(reply.error_code())))
            throw std::runtime_error("Invalid private seat query outcome");
    }catch(...){if(shared_)shared_->stream.reset();else process_->terminate();throw;}
    if(reply.has_error_code())throw WorldError(static_cast<wire::ErrorCode>(reply.error_code()),"Private native seat query refused");
    return reply.identity();
}
wire::NativeActorInput RenderWorker::input(const wire::RendererInputCommand& input, const std::function<void()>& before_send) {
    wire::RenderCommand command; *command.mutable_input() = input;
    const auto reply = exchange(command, before_send);
    if (!reply.has_input() || reply.input().actor_id() != input.actor_id())
        throw std::runtime_error("Invalid compositor actor input reply");
    return reply.input();
}
bool RenderWorker::cancellation_pending() const {return bool(cancellation_);}
std::uint64_t RenderWorker::begin_cancel_input(const wire::RendererInputCommand& input) {
    validate_message(input);
    if(input.actor_id().empty() || (!input.has_cancel() && !input.has_close()))
        throw std::invalid_argument("Native continuation requires exact actor cancellation");
    if(busy_ || cancellation_)throw std::runtime_error("Compositor stream already has an owner");
    if(sequence_==UINT64_MAX)throw std::runtime_error("Compositor request sequence exhausted");
    check_alive();
    auto pending=std::make_unique<PendingCancellation>();
    pending->token=sequence_+1;pending->command=input;
    pending->deadline=Clock::now()+std::chrono::seconds(10);
    wire::RenderCommand command;command.set_protocol_version(1);command.set_request_id(pending->token);
    *command.mutable_input()=input;
    if(!shared_) {
        auto bytes=command.SerializeAsString();
        if(bytes.size()>max_frame_bytes)throw std::invalid_argument("Excessive native cancellation");
        const auto size=static_cast<std::uint32_t>(bytes.size());
        const std::array<char,4> header{char(size>>24),char(size>>16),char(size>>8),char(size)};
        pending->output.assign(header.data(),header.size());pending->output+=bytes;
    }
    sequence_=pending->token;cancellation_=std::move(pending);busy_=true;
    if(shared_)try {
        render::v1::PresentationWorldRequest request;request.set_protocol_version(1);request.set_request_id(sequence_);
        *request.mutable_command()=command;shared_->stream->send(request.SerializeAsString());
    }catch(...) {
        cancellation_->failure=std::current_exception();shared_->stream.reset();
    }
    return sequence_;
}
std::optional<wire::NativeActorInput> RenderWorker::poll_cancel_input(std::uint64_t token) {
    if(!cancellation_ || token!=cancellation_->token)throw std::runtime_error("Stale native cancellation ticket");
    auto& pending=*cancellation_;
    if(pending.failure)std::rethrow_exception(pending.failure);
    wire::RenderResult reply;
    try {
        check_alive();
        if(Clock::now()>=pending.deadline)throw std::runtime_error("Native cancellation deadline exceeded");
        std::optional<std::string> bytes;
        if(shared_) {
            shared_->stream->flush();auto frame=shared_->stream->receive();
            if(!frame)return {};
            if(!frame->descriptors.empty())throw std::runtime_error("Native cancellation returned descriptors");
            bytes=std::move(frame->bytes);
        }else {
            if(pending.sent<pending.output.size()) {
                const auto sent=send(socket_,pending.output.data()+pending.sent,pending.output.size()-pending.sent,MSG_DONTWAIT|MSG_NOSIGNAL);
                if(sent<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)
                    throw std::runtime_error("Cannot submit native cancellation");
                if(sent==0)throw std::runtime_error("Native cancellation channel closed");
                if(sent>0)pending.sent+=std::size_t(sent);
                if(pending.sent<pending.output.size())return {};
            }
            char data[65536];iovec vector{data,sizeof(data)};
            alignas(cmsghdr) char ancillary[CMSG_SPACE(sizeof(int)*16)]{};
            msghdr message{};message.msg_iov=&vector;message.msg_iovlen=1;
            message.msg_control=ancillary;message.msg_controllen=sizeof(ancillary);
            const auto received=recvmsg(socket_,&message,MSG_DONTWAIT|MSG_CMSG_CLOEXEC);
            if(received<0 && (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR))return {};
            if(received<=0)throw std::runtime_error("Native cancellation response was truncated");
            bool descriptors=false;
            for(auto* header=CMSG_FIRSTHDR(&message);header;header=CMSG_NXTHDR(&message,header)) {
                descriptors=true;
                if(header->cmsg_level==SOL_SOCKET && header->cmsg_type==SCM_RIGHTS && header->cmsg_len>=CMSG_LEN(0)) {
                    const auto count=(header->cmsg_len-CMSG_LEN(0))/sizeof(int);
                    for(std::size_t n=0;n<count;++n) {
                        int fd=-1;std::memcpy(&fd,CMSG_DATA(header)+n*sizeof(int),sizeof(fd));if(fd>=0)::close(fd);
                    }
                }
            }
            if(descriptors || (message.msg_flags&(MSG_CTRUNC|MSG_TRUNC)))
                throw std::runtime_error("Native cancellation returned ancillary data");
            pending.input.append(data,std::size_t(received));
            if(pending.input.size()>=4 && !pending.size) {
                const auto* header=reinterpret_cast<const unsigned char*>(pending.input.data());
                pending.size=(std::uint32_t(header[0])<<24)|(std::uint32_t(header[1])<<16)|(std::uint32_t(header[2])<<8)|header[3];
                if(!*pending.size || *pending.size>max_frame_bytes)throw std::runtime_error("Excessive native cancellation reply");
            }
            if(!pending.size || pending.input.size()<4+*pending.size)return {};
            if(pending.input.size()!=4+*pending.size)throw std::runtime_error("Unexpected extra native cancellation bytes");
            bytes=pending.input.substr(4);
        }
        if(!reply.ParseFromString(*bytes) || reply.protocol_version()!=1 || reply.request_id()!=token)
            throw std::runtime_error("Native cancellation response identity mismatch");
        validate_message(reply);
        if(!reply.has_error() && (!reply.has_input() || reply.input().actor_id()!=pending.command.actor_id() ||
                reply.input().has_focus() || reply.input().held_keys_size() || reply.input().held_buttons_size()))
            throw std::runtime_error("Native input cancellation was not confirmed");
    }catch(...) {
        pending.failure=std::current_exception();
        if(shared_)shared_->stream.reset();else (void)::shutdown(socket_,SHUT_RDWR);
        throw;
    }
    // A correlated denial consumes the exchange but confirms no cancellation.
    cancellation_.reset();busy_=false;
    if(reply.has_error())throw WorldError(reply.error().code(),reply.error().message().c_str());
    return reply.input();
}
void RenderWorker::drain_input_priority() {
    if(servicing_input_priority_)throw std::runtime_error("Recursive native cancellation scheduling");
    servicing_input_priority_=true;
    struct Reset {bool& value;~Reset(){value=false;}} reset{servicing_input_priority_};
    while(input_priority_()) {
        if(cancellation_ && cancellation_->failure)std::rethrow_exception(cancellation_->failure);
        tick_();::poll(nullptr,0,1);
    }
    if(cancellation_) {
        if(cancellation_->failure)std::rethrow_exception(cancellation_->failure);
        throw std::runtime_error("Native cancellation retains its stream owner");
    }
}
wire::RenderResult RenderWorker::exchange(wire::RenderCommand command, const std::function<void()>& before_send,int capture_fd) {
    if(!busy_ || cancellation_)drain_input_priority();
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
        bool submitted=false;
        try {
            tick_();if(before_send)before_send();
            submitted=true; // send may deliver bytes before throwing.
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
        }catch(...){
            // A definite pre-send authority denial affects only this actor operation.
            // Once submitted, uncertain delivery still fences the exact producer channel.
            if(submitted)shared_->stream.reset();
            throw;
        }
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
