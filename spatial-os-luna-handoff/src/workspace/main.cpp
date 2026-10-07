#include "workspace/git.hpp"
#include "world/store.hpp"
#include "build_id.hpp"
#include <csignal>
#include <iostream>
#include <sys/stat.h>
#include <unistd.h>

namespace {
spatial::wire::Reply dispatch(const spatial::wire::Request& request, spatial::WorkspaceGit& git) {
    namespace wire = spatial::wire;
    wire::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        spatial::validate_message(request);
        if (request.protocol_version() != 1) throw spatial::WorldError(wire::UNSUPPORTED_VERSION, "Unsupported broker protocol version");
        switch (request.operation_case()) {
        case wire::Request::kHello: {
            auto* hello = reply.mutable_hello(); hello->set_protocol("spatial-protobuf/1");
            hello->set_implementation("production"); hello->set_build_id(SPATIAL_BUILD_ID);
            hello->add_capabilities("workspace.git-create/v1"); break;
        }
        case wire::Request::kRuntimeIdentities: reply.mutable_identities()->set_workspaced_pid(getpid()); break;
        case wire::Request::kGitRepoImport: *reply.mutable_repository() = git.import_repository(request.git_repo_import()); break;
        case wire::Request::kGitWorktreeCreate: *reply.mutable_worktree() = git.create(request.git_worktree_create()); break;
        case wire::Request::kGitOperation: *reply.mutable_git_operation() = git.operation(request.git_operation().operation_id()); break;
        case wire::Request::kGitStatus: *reply.mutable_git_status() = git.status(request.git_status().worktree()); break;
        case wire::Request::kGitWorktreeRemove: *reply.mutable_worktree_removed() = git.remove(request.git_worktree_remove()); break;
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
}
int main(int argc, char** argv) {
    const bool migration = argc == 4 && std::string_view(argv[3]) == "--migrate-v1";
    if ((argc != 3 && !migration) || std::string_view(argv[1]) != "--state-dir") {
        std::cerr << "Usage: workspaced --state-dir ABSOLUTE_EXECUTION_DIRECTORY [--migrate-v1]\n"; return 2;
    }
    try {
        umask(0077); signal(SIGPIPE, SIG_IGN);
        spatial::WorkspaceGit git(argv[2], migration);
        if (migration) { std::cerr << "Execution schema migration complete\n"; return 0; }
        // The supervisor supplies a private trusted management channel. No actor listener.
        std::string bytes;
        while (spatial::read_frame(std::cin, bytes)) {
            spatial::wire::Request request;
            spatial::wire::Reply reply;
            if (!request.ParseFromString(bytes)) {
                reply.set_protocol_version(1); reply.mutable_error()->set_code(spatial::wire::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Malformed broker protobuf payload");
            } else reply = dispatch(request, git);
            spatial::write_frame(std::cout, reply);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "workspaced: " << error.what() << '\n'; return 2;
    }
}
