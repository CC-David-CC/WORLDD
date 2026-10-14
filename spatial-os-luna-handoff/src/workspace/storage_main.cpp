#include "workspace/storage_owner.hpp"
#include "workspace/storage_channel.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include "platform/process_handle.hpp"
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
int descriptor(const char* input) {
    std::size_t used = 0; const std::string text(input); const auto value = std::stoi(text, &used);
    if (used != text.size() || value < 3) throw std::invalid_argument("Invalid private storage descriptor");
    return value;
}
}
int main(int argc, char** argv) {
    try {
        int channel = -1; std::string configuration;
        bool channel_stdin = false, peer_stdout = false, migrate_catalog = false;
        for (int i = 1; i < argc; ++i) {
            const std::string argument(argv[i]);
            if (argument == "--channel-fd" && channel < 0 && i+1 < argc) channel = descriptor(argv[++i]);
            else if (argument == "--config" && configuration.empty() && i+1 < argc) configuration = argv[++i];
            else if (argument == "--channel-stdin" && !channel_stdin && channel < 0) channel_stdin = true;
            else if (argument == "--broker-pidfd-stdout" && !peer_stdout) peer_stdout = true;
            else if (argument == "--migrate-copy-catalog" && !migrate_catalog) migrate_catalog = true;
            else throw std::invalid_argument("Invalid storage owner arguments");
        }
        if (channel_stdin) {
            if (channel >= 0) throw std::invalid_argument("Conflicting storage channel arguments");
            channel = fcntl(STDIN_FILENO, F_DUPFD_CLOEXEC, 3);
        }
        if (getuid() || geteuid() || (!migrate_catalog && channel < 3) || configuration.empty() || channel_stdin != peer_stdout ||
            (migrate_catalog && (channel >= 0 || channel_stdin || peer_stdout)))
            throw std::invalid_argument("Storage owner requires operator configuration and a private channel");
        umask(0077); signal(SIGPIPE, SIG_IGN);
        spatial::identity::tls_detail::Fd file(open(configuration.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC));
        struct stat status{};
        if (file.value < 0 || fstat(file.value, &status) < 0 || !S_ISREG(status.st_mode) || status.st_uid != 0 ||
            status.st_nlink != 1 || (status.st_mode & 0077) || status.st_size < 1 || status.st_size > static_cast<off_t>(spatial::max_frame_bytes))
            throw std::invalid_argument("Storage configuration must be a private operator-owned bounded file");
        std::string bytes(static_cast<std::size_t>(status.st_size), '\0'); std::size_t used = 0;
        while (used < bytes.size()) {
            const auto count = pread(file.value, bytes.data()+used, bytes.size()-used, static_cast<off_t>(used));
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) throw std::runtime_error("Incomplete storage configuration");
            used += static_cast<std::size_t>(count);
        }
        spatial::workspace::v1::StorageOwnerConfig config;
        if (!config.ParseFromString(bytes)) throw std::invalid_argument("Invalid storage configuration protobuf");
        if (migrate_catalog) {
            spatial::validate_storage_policy(config,false);
            spatial::StorageOwner owner(std::move(config),false,true);
            std::cerr << "Stopped copy catalog migration complete\n";
            return 0;
        }
        spatial::ProcessHandle peer;
        if (peer_stdout) {
            peer = spatial::ProcessHandle::take(fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 3));
            if (config.has_broker_pid() && config.broker_pid() != static_cast<unsigned>(peer.pid()))
                throw std::invalid_argument("Storage policy conflicts with retained broker identity");
            config.set_broker_pid(peer.pid());
            std::ifstream status("/proc/" + std::to_string(peer.pid()) + "/status");
            std::string line; bool uid_checked = false;
            while (std::getline(status, line)) if (line.starts_with("Uid:")) {
                std::istringstream ids(line.substr(4)); unsigned real = 0, effective = 0, saved = 0, filesystem = 0;
                uid_checked = static_cast<bool>(ids >> real >> effective >> saved >> filesystem) &&
                    config.has_broker_uid() && real == config.broker_uid() && effective == real && saved == real && filesystem == real;
            }
            if (!uid_checked || !peer.live()) throw std::invalid_argument("Retained storage broker UID rejected");
        }
        spatial::validate_storage_policy(config);
        spatial::StorageChannel transport(channel, config.broker_uid(), static_cast<pid_t>(config.broker_pid()), peer.descriptor());
        if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) throw std::runtime_error("Cannot protect storage owner privilege");
        spatial::StorageOwner owner(std::move(config), channel_stdin);
        while (const auto frame = transport.receive(spatial::StorageChannel::Clock::time_point::max(), true)) {
            spatial::workspace::v1::StorageOwnerRequest request;
            spatial::workspace::v1::StorageOwnerReply reply;
            if (request.ParseFromString(*frame)) reply = owner.handle(request);
            else {
                reply.set_protocol_version(1);
                reply.mutable_error()->set_code(spatial::workspace::v1::StorageOwnerError::INVALID_ARGUMENT);
                reply.mutable_error()->set_message("Invalid storage protobuf");
            }
            transport.send(reply.SerializeAsString(), spatial::StorageChannel::Clock::now()+std::chrono::seconds(5));
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "spatial-storage-owner: " << error.what() << '\n';
        return 1;
    }
}
