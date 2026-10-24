#include "control/scripted_actor.hpp"
#include "core/protocol.hpp"
#include <google/protobuf/text_format.h>
#include "identity/tls_support.hpp"
#include <csignal>
#include <iostream>
#include <stdexcept>

int main(int argc,char** argv) {
    namespace pb=spatial::control::v1;
    namespace core=spatial::core::v1;
    try {
        if(argc!=3 || std::string(argv[1])!="--config")
            throw std::invalid_argument("Usage: spatial-scripted-actor --config PRIVATE_TEXTPROTO");
        if(signal(SIGPIPE,SIG_IGN)==SIG_ERR)throw std::runtime_error("Cannot configure actor transport signals");
        pb::ScriptedActorConfig config;
        if(!google::protobuf::TextFormat::ParseFromString(spatial::identity::tls_detail::load_config(argv[2]),&config))
            throw std::invalid_argument("Malformed scripted actor configuration");
        spatial::control::ScriptedActor actor(config);
        std::uint64_t last_id=0;std::string frame;
        while(spatial::read_frame(std::cin,frame)) {
            pb::ScriptedActorRequest request;pb::ScriptedActorReply reply;reply.set_protocol_version(1);
            const bool decoded=request.ParseFromString(frame);
            if(decoded)reply.set_request_id(request.request_id());
            try {
                if(!decoded)throw std::invalid_argument("Malformed scripted actor request");
                spatial::validate_message(request);
                if(request.protocol_version()!=1 || request.request_id()==0 || request.request_id()<=last_id)
                    throw std::invalid_argument("Invalid scripted actor version or request sequence");
                last_id=request.request_id();
                if(request.has_inspect() && request.inspect())
                    *reply.mutable_state()=actor.inspect(request.generation());
                else if(request.has_propose())
                    *reply.mutable_action()=actor.propose(request.generation(),request.propose());
                else if(request.has_transition())
                    *reply.mutable_transition()=actor.transition(request.generation(),request.transition());
                else throw std::invalid_argument("Missing scripted actor operation");
            } catch(const spatial::ActorInputCancelled& error) {
                *reply.mutable_error()=error.error();reply.set_cancellation_confirmed(true);
            } catch(const spatial::ActorSessionRejected& error) {
                *reply.mutable_error()=error.error();
            } catch(const std::invalid_argument& error) {
                reply.mutable_error()->set_code(core::INVALID_ARGUMENT);reply.mutable_error()->set_message(error.what());
            } catch(const std::exception&) {
                reply.mutable_error()->set_code(core::INTERNAL_ERROR);reply.mutable_error()->set_message("Scripted actor operation failed; inspect context availability");
            }
            *reply.mutable_context()=actor.context();
            spatial::write_frame(std::cout,reply);
        }
        return 0;
    } catch(const std::exception& error) {std::cerr<<"Scripted actor stopped: "<<error.what()<<'\n';return 1;}
}
