#include "authority/store.hpp"
#include "world/store.hpp"
#include "grants_probe.pb.h"
#include <csignal>
#include <iostream>
#include <sys/stat.h>

// Fixture-only driver. This is deliberately not an authenticated actor endpoint.
int main(int argc, char** argv) {
    namespace pb = spatial::authority::v1;
    namespace test = spatial::authority::testing;
    namespace wire = spatial::core::v1;
    try {
        signal(SIGPIPE, SIG_IGN); umask(0077);
        if (argc != 5 || (std::string(argv[2]) != "1" && std::string(argv[2]) != "2") ||
                (std::string(argv[4]) != "open" && std::string(argv[4]) != "initialize" && std::string(argv[4]) != "migrate"))
            throw std::invalid_argument("Expected private directory, domain kind, identity, and open, initialize, or migrate");
        pb::Domain domain; domain.set_kind(std::string(argv[2]) == "1" ? pb::WORLD : pb::WORKSPACE); domain.set_id(argv[3]);
        spatial::authority::Store store(argv[1], domain, std::string(argv[4]) == "initialize", std::string(argv[4]) == "migrate");
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            test::ProbeRequest request; test::ProbeReply reply; reply.set_protocol_version(1);
            try {
                if (!request.ParseFromString(bytes)) throw std::invalid_argument("Malformed probe request");
                reply.set_request_id(request.request_id()); spatial::validate_message(request);
                if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported probe version");
                const auto identity = [&](const pb::ActorRef& actor) {
                    for (const auto& inactive : request.inactive_actors())
                        if (inactive.issuer_id() == actor.issuer_id() && inactive.actor_id() == actor.actor_id()) return false;
                    return true;
                };
                const auto expected = [](bool present) { if (!present) throw std::invalid_argument("Expected revision is required"); };
                if (request.has_bootstrap_bind()) {
                    const auto& q = request.bootstrap_bind(); expected(q.has_expected_revision());
                    *reply.mutable_binding() = store.bind_bootstrap(q.caller(), q.selector(), q.parent_id(), q.terms(), q.expected_revision(), identity);
                } else if (request.has_bootstrap_apply()) {
                    const auto& q = request.bootstrap_apply();
                    *reply.mutable_record() = store.materialize_bootstrap(q.binding_id(), q.recipient(), q.selector(), q.terms(), identity);
                } else if (request.has_bootstrap_revoke()) {
                    const auto& q = request.bootstrap_revoke(); expected(q.has_expected_revision());
                    *reply.mutable_binding() = store.revoke_bootstrap(q.caller(), q.binding_id(), q.expected_revision(), identity);
                } else if (request.has_bootstrap_get()) {
                    *reply.mutable_binding() = store.bootstrap_binding(request.bootstrap_get());
                } else if (request.has_bootstrap_subjects()) {
                    const auto& q = request.bootstrap_subjects();
                    for (const auto& subject : store.bootstrap_subjects({q.binding_ids().begin(), q.binding_ids().end()}, q.recipient()))
                        *reply.mutable_subjects()->add_actors() = subject;
                } else if (request.has_install()) {
                    expected(request.install().has_expected_revision());
                    *reply.mutable_record() = store.install_root(request.install().terms(), request.install().expected_revision());
                } else if (request.has_delegate()) {
                    const auto& q = request.delegate(); expected(q.has_expected_revision());
                    *reply.mutable_record() = store.delegate(q.caller(), q.parent_id(), q.terms(), q.expected_revision(), identity);
                } else if (request.has_revoke()) {
                    const auto& q = request.revoke(); expected(q.has_expected_revision());
                    *reply.mutable_record() = store.revoke(q.caller(), q.id(), q.expected_revision(), identity);
                } else if (request.has_check()) {
                    const auto& q = request.check(); *reply.mutable_decision() = store.check(q.caller(), q.scope(), q.right(), identity);
                } else if (request.has_get()) *reply.mutable_record() = store.get(request.get());
                else throw spatial::WorldError(wire::UNKNOWN_METHOD, "Missing probe operation");
            } catch (const spatial::WorldError& e) { reply.mutable_error()->set_code(e.code); reply.mutable_error()->set_message(e.what());
            } catch (const std::invalid_argument& e) { reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(e.what()); }
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 2; }
}
