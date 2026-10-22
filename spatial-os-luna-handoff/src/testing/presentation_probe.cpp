#include "render/renderer.hpp"
#include "platform/compositor_config.hpp"
#include "presentation_probe.pb.h"
#include "world/store.hpp"
#include <fcntl.h>
#include <iostream>
#include <unistd.h>
#include <vector>
int main(int argc, char** argv) {
    try {
        if (argc < 4) throw std::invalid_argument("Expected state, graphics descriptor, and input descriptors");
        spatial::CompositorConfig config(std::stoi(argv[2]));
        spatial::Renderer renderer(config.display(),argv[1],&config.value());
        config.consume();
        std::vector<int> descriptors;
        for (int i=3;i<argc;++i) descriptors.push_back(std::stoi(argv[i]));
        spatial::testing::PresentationProbeReply ready;
        ready.mutable_result()->set_protocol_version(1);
        *ready.mutable_result()->mutable_ready()=renderer.ready();
        spatial::write_frame(std::cout,ready);
        std::string bytes;
        while (spatial::read_frame(std::cin,bytes)) {
            spatial::testing::PresentationProbeRequest request;
            if (!request.ParseFromString(bytes)) throw std::invalid_argument("Malformed probe request");
            spatial::validate_message(request);
            spatial::testing::PresentationProbeReply reply;
            auto* result=reply.mutable_result();result->set_protocol_version(1);result->set_request_id(request.request_id());
            try {
                if (request.protocol_version()!=1) throw std::invalid_argument("Unsupported probe version");
                switch (request.operation_case()) {
                case spatial::testing::PresentationProbeRequest::kBindDescriptor: {
                    if (request.bind_descriptor()>=descriptors.size()) throw std::invalid_argument("Unknown fixture descriptor");
                    int fd=fcntl(descriptors[request.bind_descriptor()],F_DUPFD_CLOEXEC,3);
                    if(fd<0)throw std::runtime_error("Cannot duplicate sealed fixture");
                    renderer.bind_input(request.binding(),fd);result->mutable_ready();break;
                }
                case spatial::testing::PresentationProbeRequest::kRetire:
                    renderer.retire_input(request.binding());result->mutable_ready();break;
                case spatial::testing::PresentationProbeRequest::kSnapshot:
                    *result->mutable_snapshot()=request.has_binding()?renderer.synchronize(request.binding()):renderer.synchronize();break;
                case spatial::testing::PresentationProbeRequest::kInput:
                    *result->mutable_input()=renderer.input(request.binding(),request.input());break;
                case spatial::testing::PresentationProbeRequest::kUnboundInput:
                    *result->mutable_input()=renderer.input(request.unbound_input());break;
                case spatial::testing::PresentationProbeRequest::kDraw:
                    *result->mutable_frame()=renderer.draw(request.draw());break;
                case spatial::testing::PresentationProbeRequest::kSeatActor:
                    *reply.mutable_seat()=request.has_binding()?renderer.native_actor_seat(request.binding(),request.seat_actor()):
                        renderer.native_actor_seat(request.seat_actor());result->mutable_ready();break;
                case spatial::testing::PresentationProbeRequest::kDescriptors: {
                    std::uint64_t count=0;
                    for (const auto& file:std::filesystem::directory_iterator("/proc/self/fd")){(void)file;++count;}
                    reply.set_descriptors(count);result->mutable_ready();break;
                }
                default:throw std::invalid_argument("Missing probe operation");
                }
            } catch (const spatial::WorldError& error) {
                result->mutable_error()->set_code(error.code);result->mutable_error()->set_message(error.what());
            } catch (const std::invalid_argument& error) {
                result->mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);result->mutable_error()->set_message(error.what());
            } catch (const std::exception& error) {
                result->mutable_error()->set_code(spatial::wire::UNAVAILABLE);result->mutable_error()->set_message(error.what());
            }
            renderer.events(); // Match the production compositor event pump before waiting again.
            spatial::write_frame(std::cout,reply);
        }
        for (int fd:descriptors) close(fd);
        return 0;
    } catch (const std::exception& error) {std::cerr<<"presentation probe: "<<error.what()<<'\n';return 2;}
}
