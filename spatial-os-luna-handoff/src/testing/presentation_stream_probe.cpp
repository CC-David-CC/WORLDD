#include "platform/presentation_stream.hpp"
#include "core/protocol.hpp"
#include <filesystem>
#include <iostream>
#include <poll.h>
#include <unistd.h>
static std::size_t descriptors() {
    std::size_t count=0;for(const auto& file:std::filesystem::directory_iterator("/proc/self/fd")){(void)file;++count;}return count;
}
int main(int argc,char** argv) {
    const auto before=descriptors();
    try {
        if(argc!=4)throw std::invalid_argument("Expected channel, creator, and sender");
        spatial::PresentationStream stream(std::stoi(argv[1]),std::stoi(argv[2]),std::stoi(argv[3]));
        spatial::wire::RenderResult ready;ready.set_protocol_version(1);ready.mutable_ready();spatial::write_frame(std::cout,ready);
        while(true){
            stream.flush();
            if(auto frame=stream.receive()){
                spatial::wire::Request request;
                if(!request.ParseFromString(frame->bytes))throw std::invalid_argument("Malformed test request");
                spatial::validate_message(request);
                std::vector<int> files;for(const auto& file:frame->descriptors)files.push_back(file.value);
                stream.send(std::move(frame->bytes),files);
            }
            pollfd event{stream.descriptor(),static_cast<short>(POLLIN|(stream.pending_output()?POLLOUT:0)),0};
            if(poll(&event,1,10)<0 && errno!=EINTR)throw std::runtime_error("Probe poll failed");
        }
    }catch(const std::exception& error){
        std::cerr<<error.what()<<" descriptors_before="<<before<<" descriptors_after="<<descriptors()<<'\n';return 2;
    }
}
