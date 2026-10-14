#include "identity/keys.hpp"
#include "world/store.hpp"
#include <csignal>
#include <iostream>
#include <sys/stat.h>

namespace {
template<class Message> Message input() {
    std::string bytes;
    if (!spatial::read_frame(std::cin, bytes) || std::cin.peek() != std::char_traits<char>::eof())
        throw std::invalid_argument("Expected exactly one complete protobuf frame and EOF");
    Message request;
    if (!request.ParseFromString(bytes)) throw std::invalid_argument("Malformed identity key protobuf");
    spatial::validate_message(request);
    return request;
}
}

int main(int argc, char** argv) {
    namespace key = spatial::identity;
    key::v1::KeyReply reply; reply.set_protocol_version(1);
    umask(0077); signal(SIGPIPE, SIG_IGN);
    int status = 0;
    try {
        if (argc == 2 && std::string_view(argv[1]) == "verify") {
            reply.mutable_verification()->set_valid(key::verify(input<key::v1::VerifyRequest>()));
        } else if (argc == 4 && std::string_view(argv[2]) == "--key") {
            const std::string_view command(argv[1]);
            if (command == "generate") {
                auto pair = key::KeyPair::generate(); pair.save_new(argv[3]);
                reply.mutable_public_key()->set_ed25519(pair.public_key());
            } else if (command == "public") {
                reply.mutable_public_key()->set_ed25519(key::KeyPair::load(argv[3]).public_key());
            } else if (command == "sign") {
                const auto request = input<key::v1::SignRequest>();
                *reply.mutable_proof() = key::KeyPair::load(argv[3]).sign(request);
            } else throw std::invalid_argument("Unknown identity key command");
        } else throw std::invalid_argument("Usage: spatial-key generate|public|sign --key ABSOLUTE_FILE, or spatial-key verify");
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what()); status = 1;
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what()); status = 1;
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(spatial::wire::INTERNAL_ERROR);
        reply.mutable_error()->set_message("Identity key operation failed"); status = 1;
    }
    try { spatial::write_frame(std::cout, reply); }
    catch (const std::exception&) { std::cerr << "spatial-key: output transport failed\n"; return 2; }
    return status;
}
