#include "identity/tls_support.hpp"
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>

namespace spatial::identity::tls_detail {
namespace {
// Open relative to a checked private parent. OpenSSL reads this same descriptor.
int open_config_file(const std::filesystem::path& path, bool secret) {
    require(path.is_absolute() && !path.filename().empty() && path.string().find('\0') == std::string::npos,
            "TLS material requires an absolute file path");
    Fd parent(open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    struct stat status{};
    require(parent.value >= 0 && fstat(parent.value, &status) == 0 && status.st_uid == geteuid() &&
            (status.st_mode & 0077) == 0, "TLS material requires an owned private parent directory");
    Fd file(openat(parent.value, path.filename().c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    require(file.value >= 0 && fstat(file.value, &status) == 0 && S_ISREG(status.st_mode) &&
            status.st_uid == geteuid() && status.st_nlink == 1 && (status.st_mode & (secret ? 0077 : 0022)) == 0 &&
            status.st_size > 0 && status.st_size <= 65536, "Unsafe or missing TLS material");
    const int fd = file.value; file.value = -1; return fd;
}
std::string read_config(int fd) {
    std::string data;
    std::array<char, 4096> buffer{};
    for (;;) {
        const auto count = read(fd, buffer.data(), buffer.size());
        if (count < 0 && errno == EINTR) continue;
        require(count >= 0, "Cannot read TLS configuration");
        if (!count) return data;
        require(data.size()+static_cast<std::size_t>(count) <= 65536, "TLS configuration exceeds size limit");
        data.append(buffer.data(), count);
    }
}
std::string descriptor_path(int fd) { return "/proc/self/fd/"+std::to_string(fd); }
int no_password(char*, int, int, void*) { return 0; }
}
void nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL);
    require(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
            fcntl(fd, F_SETFD, FD_CLOEXEC) == 0, "Cannot configure issuer socket");
}

std::string load_config(const std::filesystem::path& path) {
    Fd file(open_config_file(path, false)); return read_config(file.value);
}
Context make_context(bool server, const std::filesystem::path& certificate_path,
                     const std::filesystem::path& key_path, const std::filesystem::path& trust_path) {
        Fd certificate(open_config_file(certificate_path, false));
        Fd key(open_config_file(key_path, true));
        Fd trust(open_config_file(trust_path, false));
        Context context(SSL_CTX_new(server ? TLS_server_method() : TLS_client_method()), SSL_CTX_free);
        require(context && SSL_CTX_set_min_proto_version(context.get(), TLS1_3_VERSION) == 1 &&
                SSL_CTX_set_max_proto_version(context.get(), TLS1_3_VERSION) == 1, "Cannot require TLS 1.3");
        SSL_CTX_set_default_passwd_cb(context.get(), no_password);
        require(SSL_CTX_use_certificate_chain_file(context.get(), descriptor_path(certificate.value).c_str()) == 1 &&
                SSL_CTX_use_PrivateKey_file(context.get(), descriptor_path(key.value).c_str(), SSL_FILETYPE_PEM) == 1 &&
                SSL_CTX_check_private_key(context.get()) == 1, "Invalid TLS certificate or private key");
        require(SSL_CTX_load_verify_locations(context.get(), descriptor_path(trust.value).c_str(), nullptr) == 1,
                "Cannot load explicit service certificate trust");
        SSL_CTX_set_verify(context.get(), SSL_VERIFY_PEER | (server ? SSL_VERIFY_FAIL_IF_NO_PEER_CERT : 0), nullptr);
        SSL_CTX_set_verify_depth(context.get(), 4);
        SSL_CTX_set_session_cache_mode(context.get(), SSL_SESS_CACHE_OFF);
        SSL_CTX_set_options(context.get(), SSL_OP_NO_TICKET | SSL_OP_NO_RENEGOTIATION);
        require((!server || SSL_CTX_set_num_tickets(context.get(), 0) == 1) && SSL_CTX_set_max_early_data(context.get(), 0) == 1,
                "Cannot disable TLS tickets and early data");
    return context;
}
}
