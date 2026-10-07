#include "identity/issuer.hpp"
#include "world/store.hpp"
#include "build_id.hpp"
#include <charconv>
#include <csignal>
#include <fstream>
#include <iostream>
#include <sys/stat.h>

namespace {
namespace identity = spatial::identity::v1;
identity::IssuerReply dispatch(const identity::IssuerRequest& request, spatial::identity::Issuer& issuer) {
    identity::IssuerReply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(spatial::wire::UNSUPPORTED_VERSION, "Unsupported issuer protocol version");
        if (request.has_hello()) {
            auto* result = reply.mutable_hello(); result->set_protocol("spatial-identity-protobuf/1");
            result->set_implementation("production"); result->set_build_id(SPATIAL_BUILD_ID);
            result->add_capabilities("identity.issuer-control/v1");
            result->add_capabilities("identity.enrollment/v1");
            result->add_capabilities("identity.revocation/v1");
            result->add_capabilities("identity.authentication/v1");
        } else if (request.has_info()) *reply.mutable_info() = issuer.info();
        else if (request.has_challenge()) *reply.mutable_challenge() = issuer.challenge(request.challenge());
        else if (request.has_execute()) {
            const auto result = issuer.execute(request.execute());
            if (result.has_policy()) *reply.mutable_policy() = result.policy();
            else if (result.has_enrollment()) *reply.mutable_enrollment() = result.enrollment();
            else *reply.mutable_identity_status() = result.identity_status();
        } else if (request.has_enrollment_begin()) *reply.mutable_challenge() = issuer.enrollment_begin(request.enrollment_begin());
        else if (request.has_enrollment_complete()) *reply.mutable_enrollment() = issuer.enrollment_complete(request.enrollment_complete());
        else if (request.has_credential_check()) *reply.mutable_identity_status() = issuer.credential_check(request.credential_check());
        else if (request.has_authentication_begin()) *reply.mutable_challenge() = issuer.authentication_begin(request.authentication_begin());
        else if (request.has_authentication_complete()) *reply.mutable_authentication() = issuer.authentication_complete(request.authentication_complete());
        else throw spatial::WorldError(spatial::wire::UNKNOWN_METHOD, "No recognized issuer operation");
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(spatial::wire::INTERNAL_ERROR); reply.mutable_error()->set_message("Issuer operation failed internally");
    }
    return reply;
}
std::string read_owner(const std::filesystem::path& path) {
    if (!path.is_absolute()) throw std::invalid_argument("Owner public-key path must be absolute");
    std::ifstream input(path, std::ios::binary); std::string bytes;
    if (!input || !spatial::read_frame(input, bytes) || input.peek() != std::char_traits<char>::eof())
        throw std::invalid_argument("Owner public key requires one framed KeyReply");
    identity::KeyReply reply;
    if (!reply.ParseFromString(bytes)) throw std::invalid_argument("Malformed owner public-key protobuf");
    spatial::validate_message(reply);
    if (reply.protocol_version() != 1 || !reply.has_public_key() || reply.public_key().ed25519().size() != 32)
        throw std::invalid_argument("Owner public key requires one Ed25519 public key");
    return reply.public_key().ed25519();
}
}

int main(int argc, char** argv) {
    try {
        umask(0077); signal(SIGPIPE, SIG_IGN);
        std::filesystem::path state, owner_path;
        bool initialize = false, lifetime_set = false, migrate = false;
        unsigned lifetime = 30000;
        for (int i = 1; i < argc; ++i) {
            const std::string_view option(argv[i]);
            if (option == "--init" && !initialize) initialize = true;
            else if (option == "--migrate" && !migrate) migrate = true;
            else if (option == "--state-dir" && state.empty() && i+1 < argc) state = argv[++i];
            else if (option == "--owner-public" && owner_path.empty() && i+1 < argc) owner_path = argv[++i];
            else if (option == "--challenge-ttl-ms" && !lifetime_set && i+1 < argc) {
                lifetime_set = true; const std::string_view number(argv[++i]);
                const auto result = std::from_chars(number.data(), number.data()+number.size(), lifetime);
                if (result.ec != std::errc{} || result.ptr != number.data()+number.size())
                    throw std::invalid_argument("Invalid issuer challenge lifetime");
            } else throw std::invalid_argument("Unknown or duplicate identityd argument");
        }
        if (initialize != !owner_path.empty() || (initialize && lifetime_set) || (migrate && (initialize || lifetime_set)))
            throw std::invalid_argument("Only explicit initialization accepts --owner-public; initialization cannot change challenge lifetime");
        std::optional<std::string> owner;
        if (initialize) owner = read_owner(owner_path);
        spatial::identity::Issuer issuer(state, owner, std::chrono::milliseconds(lifetime), migrate);
        if (initialize || migrate) {
            identity::IssuerReply reply; reply.set_protocol_version(1); *reply.mutable_info() = issuer.info();
            spatial::write_frame(std::cout, reply); return 0;
        }
        // The supervisor owns this inherited channel. No untrusted network listener.
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            identity::IssuerRequest request; identity::IssuerReply reply;
            if (request.ParseFromString(bytes)) reply = dispatch(request, issuer);
            else {
                reply.set_protocol_version(1); reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Malformed issuer protobuf payload");
            }
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& error) { std::cerr << "identityd: " << error.what() << '\n'; return 2; }
}
