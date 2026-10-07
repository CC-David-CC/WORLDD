#include "workspace/store.hpp"
#include "world/store.hpp"
#include "build_id.hpp"
#include <csignal>
#include <iostream>
#include <sys/stat.h>
#include <unistd.h>

namespace {
spatial::wire::Reply dispatch(const spatial::wire::Request& request, spatial::ExecutionStore& git) {
    namespace wire = spatial::wire;
    wire::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported broker protocol version");
        switch (request.operation_case()) {
        case wire::Request::kHello: {
            auto* hello = reply.mutable_hello(); hello->set_protocol("spatial-protobuf/1");
            hello->set_implementation("production"); hello->set_build_id(SPATIAL_BUILD_ID);
            hello->add_capabilities("workspace.git-create/v1");
            if (git.fixture_faults()) hello->add_capabilities("fixture.faults/v1");
            break;
        }
        case wire::Request::kRuntimeIdentities: reply.mutable_identities()->set_workspaced_pid(getpid()); break;
        case wire::Request::kFaultArm: *reply.mutable_fault_armed() = git.arm_fault(request.fault_arm()); break;
        case wire::Request::kGitRepoImport: *reply.mutable_repository() = git.import_repository(request.git_repo_import()); break;
        case wire::Request::kGitWorktreeCreate: *reply.mutable_worktree() = git.create(request.git_worktree_create()); break;
        case wire::Request::kGitOperation: *reply.mutable_git_operation() = git.operation(request.git_operation().operation_id()); break;
        case wire::Request::kGitStatus: *reply.mutable_git_status() = git.status(request.git_status().worktree()); break;
        case wire::Request::kGitWorktreeRemove: *reply.mutable_worktree_removed() = git.remove(request.git_worktree_remove()); break;
        case wire::Request::kWorkspaceCreate: *reply.mutable_workspace() = git.workspace_create(request.workspace_create()); break;
        case wire::Request::kWorkspaceGet: *reply.mutable_workspace() = git.workspace_get(request.workspace_get().id()); break;
        case wire::Request::kWorkspaceMemberSet: *reply.mutable_workspace_ack() = git.workspace_member(request.workspace_member_set()); break;
        case wire::Request::kProcessWait: *reply.mutable_process_wait() = git.wait(request.process_wait()); break;
        case wire::Request::OPERATION_NOT_SET: throw spatial::WorldError(wire::UNKNOWN_METHOD, "No recognized broker operation");
        default: throw spatial::WorldError(wire::UNIMPLEMENTED, "Broker operation is not implemented");
        }
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
    } catch (const std::filesystem::filesystem_error&) {
        reply.mutable_error()->set_code(wire::STORAGE_ERROR);
        reply.mutable_error()->set_message("Workspace filesystem effect failed; inspect operation state and retained data");
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(wire::INTERNAL_ERROR); reply.mutable_error()->set_message("Broker operation failed internally");
    }
    return reply;
}
spatial::workspace::v1::Reply dispatch_broker(const spatial::workspace::v1::Request& request, spatial::ExecutionStore& store) {
    namespace broker = spatial::workspace::v1;
    namespace wire = spatial::wire;
    broker::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported private broker protocol version");
        if (request.has_management()) {
            if (request.management().request_id() != request.request_id()) throw std::invalid_argument("Broker management identity mismatch");
            *reply.mutable_management() = dispatch(request.management(), store);
        } else if (request.has_access()) *reply.mutable_access() = store.access(request.access());
        else if (request.has_launch()) *reply.mutable_launched() = store.launch(request.launch());
        else throw spatial::WorldError(wire::UNKNOWN_METHOD, "No recognized private broker operation");
    } catch (const spatial::WorldError& error) {
        reply.mutable_error()->set_code(error.code); reply.mutable_error()->set_message(error.what());
    } catch (const std::invalid_argument& error) {
        reply.mutable_error()->set_code(wire::INVALID_ARGUMENT); reply.mutable_error()->set_message(error.what());
    } catch (const std::exception&) {
        reply.mutable_error()->set_code(wire::STORAGE_ERROR); reply.mutable_error()->set_message("Broker authority lookup failed");
    }
    return reply;
}
}
int main(int argc, char** argv) {
    const bool fixture_faults = argc == 4 && std::string_view(argv[3]) == "--fixture-faults";
    const unsigned migration = argc == 4 ? (std::string_view(argv[3]) == "--migrate-v1" ? 1 : std::string_view(argv[3]) == "--migrate-v2" ? 2 : std::string_view(argv[3]) == "--migrate-v3" ? 3 : 0) : 0;
    if ((argc != 3 && !migration && !fixture_faults) || std::string_view(argv[1]) != "--state-dir") {
        std::cerr << "Usage: workspaced --state-dir ABSOLUTE_EXECUTION_DIRECTORY [--migrate-v1|--migrate-v2|--migrate-v3|--fixture-faults]\n"; return 2;
    }
    try {
        umask(0077); signal(SIGPIPE, SIG_IGN);
        spatial::ExecutionStore git(argv[2], migration, fixture_faults);
        if (migration) { std::cerr << "Execution schema migration complete\n"; return 0; }
        // The supervisor supplies a private trusted management channel. No actor listener.
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            spatial::workspace::v1::Request request;
            spatial::workspace::v1::Reply reply;
            if (!request.ParseFromString(bytes)) {
                reply.set_protocol_version(1); reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Malformed broker protobuf payload");
            } else reply = dispatch_broker(request, git);
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "workspaced: " << error.what() << '\n'; return 2;
    }
}
