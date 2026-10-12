#include "identity/issuer.hpp"
#include "world/store.hpp"
#include "identity/protocol.hpp"
#include "identity/tls_server.hpp"
#include <charconv>
#include <csignal>
#include <fstream>
#include <iostream>
#include <sys/stat.h>

namespace {
namespace identity = spatial::identity::v1;
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
        std::filesystem::path state, owner_path, tls_config;
        int listener = -1; bool listener_set = false;
        bool initialize = false, lifetime_set = false, migrate = false;
        unsigned lifetime = 30000;
        for (int i = 1; i < argc; ++i) {
            const std::string_view option(argv[i]);
            if (option == "--init" && !initialize) initialize = true;
            else if (option == "--migrate" && !migrate) migrate = true;
            else if (option == "--state-dir" && state.empty() && i+1 < argc) state = argv[++i];
            else if (option == "--owner-public" && owner_path.empty() && i+1 < argc) owner_path = argv[++i];
            else if (option == "--tls-config" && tls_config.empty() && i+1 < argc) tls_config = argv[++i];
            else if (option == "--listen-fd" && !listener_set && i+1 < argc) {
                listener_set = true; const std::string_view number(argv[++i]);
                const auto result = std::from_chars(number.data(), number.data()+number.size(), listener);
                if (result.ec != std::errc{} || result.ptr != number.data()+number.size() || listener <= 2)
                    throw std::invalid_argument("Invalid issuer listener descriptor");
            }
            else if (option == "--challenge-ttl-ms" && !lifetime_set && i+1 < argc) {
                lifetime_set = true; const std::string_view number(argv[++i]);
                const auto result = std::from_chars(number.data(), number.data()+number.size(), lifetime);
                if (result.ec != std::errc{} || result.ptr != number.data()+number.size())
                    throw std::invalid_argument("Invalid issuer challenge lifetime");
            } else throw std::invalid_argument("Unknown or duplicate identityd argument");
        }
        if (initialize != !owner_path.empty() || (initialize && lifetime_set) || (migrate && (initialize || lifetime_set)))
            throw std::invalid_argument("Only explicit initialization accepts --owner-public; initialization cannot change challenge lifetime");
        if (listener_set != !tls_config.empty() || (listener_set && (initialize || migrate)))
            throw std::invalid_argument("TLS serving requires both --listen-fd and --tls-config, without initialization or migration");
        std::unique_ptr<spatial::identity::IssuerTlsServer> tls;
        if (listener_set) tls = std::make_unique<spatial::identity::IssuerTlsServer>(listener, tls_config);
        std::optional<std::string> owner;
        if (initialize) owner = read_owner(owner_path);
        spatial::identity::Issuer issuer(state, owner, std::chrono::milliseconds(lifetime), migrate);
        if (initialize || migrate) {
            identity::IssuerReply reply; reply.set_protocol_version(1); *reply.mutable_info() = issuer.info();
            spatial::write_frame(std::cout, reply); return 0;
        }
        if (tls) { tls->serve(issuer); return 0; }
        // The supervisor owns this inherited management channel.
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes))
            spatial::write_frame(std::cout, spatial::identity::handle_issuer_frame(bytes, issuer));
        return 0;
    } catch (const std::exception& error) { std::cerr << "identityd: " << error.what() << '\n'; return 2; }
}
