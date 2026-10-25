#include "platform/render_worker.hpp"
#include "world/store.hpp"
#include <chrono>
#include <iostream>
#include <poll.h>
#include <stdexcept>

int main(int argc,char** argv) {
    try {
        if(argc!=3)throw std::invalid_argument("Expected compositor and state directory");
        std::string bytes;if(!spatial::read_frame(std::cin,bytes))throw std::runtime_error("Missing cancellation command");
        spatial::wire::RenderCommand request;
        if(!request.ParseFromString(bytes))throw std::runtime_error("Malformed cancellation command");
        spatial::validate_message(request);
        spatial::RenderWorker client(argv[1],argv[2],":protocol-fixture");
        const auto ticket=client.begin_cancel_input(request.input());
        if(!client.busy() || !client.cancellation_pending())throw std::runtime_error("Cancellation lost its stream owner");
        bool stale=false;try{(void)client.poll_cancel_input(ticket+1);}catch(const std::runtime_error&){stale=true;}
        if(!stale)throw std::runtime_error("Foreign cancellation ticket was accepted");
        bool competing=false;try{(void)client.begin_cancel_input(request.input());}catch(const std::runtime_error&){competing=true;}
        if(!competing)throw std::runtime_error("Competing cancellation acquired the stream");
        bool synchronous=false;try{(void)client.input(request.input());}catch(const std::runtime_error&){synchronous=true;}
        if(!synchronous)throw std::runtime_error("Ordinary input stole the pending stream");
        try {
            std::optional<spatial::wire::NativeActorInput> result;
            while(!result) {
                const auto started=std::chrono::steady_clock::now();result=client.poll_cancel_input(ticket);
                if(std::chrono::steady_clock::now()-started>std::chrono::milliseconds(250))
                    throw std::runtime_error("Native cancellation poll blocked");
                if(!result)::poll(nullptr,0,1);
            }
            if(client.busy() || client.cancellation_pending())throw std::runtime_error("Checked cancellation retained the stream");
            stale=false;try{(void)client.poll_cancel_input(ticket);}catch(const std::runtime_error&){stale=true;}
            if(!stale)throw std::runtime_error("Completed cancellation ticket was reusable");
            // Existing synchronous input still uses the same checked native protocol.
            const auto next=client.input(request.input());
            spatial::wire::RenderResult reply;reply.set_protocol_version(1);reply.set_request_id(request.request_id());
            *reply.mutable_input()=next;spatial::write_frame(std::cout,reply);
        }catch(const std::exception& error) {
            // Report detection before RenderWorker performs its bounded child cleanup.
            spatial::wire::RenderResult reply;reply.set_protocol_version(1);reply.set_request_id(request.request_id());
            const auto* world_error=dynamic_cast<const spatial::WorldError*>(&error);
            reply.mutable_error()->set_code(world_error?world_error->code:spatial::wire::UNAVAILABLE);
            reply.mutable_error()->set_message(error.what());spatial::write_frame(std::cout,reply);std::cout.flush();
            throw;
        }
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 2;}
}
