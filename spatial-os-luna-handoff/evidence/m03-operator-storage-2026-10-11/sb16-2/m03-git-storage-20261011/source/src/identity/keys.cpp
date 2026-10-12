#include "identity/keys.hpp"
#include "world/store.hpp"
#include <openssl/crypto.h>
#include <openssl/pem.h>
#include <openssl/rand.h>
#include <array>
#include <cerrno>
#include <cctype>
#include <fcntl.h>
#include <linux/fs.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace spatial::identity {
namespace {
using Bio = std::unique_ptr<BIO, decltype(&BIO_free)>;
using KeyContext = std::unique_ptr<EVP_PKEY_CTX, decltype(&EVP_PKEY_CTX_free)>;
using DigestContext = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>;
constexpr std::size_t max_key_file = 16384;

void require(bool condition, const char* message) {
    if (!condition) throw std::invalid_argument(message);
}
void crypto(bool condition) {
    if (!condition) throw std::runtime_error("OpenSSL identity operation failed");
}
class Fd {
public:
    explicit Fd(int fd) : fd_(fd) {}
    ~Fd() { if (fd_ >= 0) close(fd_); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    int get() const { return fd_; }
private:
    int fd_;
};
struct SecretBuffer {
    std::array<char, max_key_file + 1> bytes{};
    ~SecretBuffer() { OPENSSL_cleanse(bytes.data(), bytes.size()); }
};
Fd key_directory(const std::filesystem::path& path) {
    require(path.is_absolute() && path.has_filename() && path.filename() != "." && path.filename() != "..",
            "Key path must name an absolute file");
    const int fd = open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) throw WorldError(wire::FORBIDDEN, "Key directory cannot be opened safely");
    struct stat info{};
    if (fstat(fd, &info) || info.st_uid != geteuid() || (info.st_mode & 0077)) {
        close(fd); throw WorldError(wire::FORBIDDEN, "Key directory must be private and owned by this user");
    }
    return Fd(fd);
}
void append_u32(std::string& out, std::uint32_t value) {
    for (int shift : {24, 16, 8, 0}) out.push_back(static_cast<char>(value >> shift));
}
std::string transcript(const v1::SignRequest& request) {
    validate_message(request);
    require(request.purpose() != v1::SIGNATURE_PURPOSE_UNSPECIFIED, "Signature purpose is required");
    require(request.has_payload() && request.payload().size() <= max_signed_payload, "Missing or excessive signing payload");
    std::string result = "spatial.identity.key-proof/v1";
    result.push_back('\0');
    append_u32(result, static_cast<std::uint32_t>(request.purpose()));
    append_u32(result, static_cast<std::uint32_t>(request.payload().size()));
    result += request.payload();
    return result;
}
std::string temporary_name() {
    std::array<unsigned char, 16> random{};
    crypto(RAND_bytes(random.data(), random.size()) == 1);
    constexpr char digits[] = "0123456789abcdef";
    std::string name = ".spatial-key-";
    for (const auto byte : random) { name += digits[byte >> 4]; name += digits[byte & 15]; }
    return name;
}
int no_password(char*, int, int, void*) { return 0; }
} // namespace

KeyPair KeyPair::generate() {
    KeyContext context(EVP_PKEY_CTX_new_from_name(nullptr, "ED25519", nullptr), EVP_PKEY_CTX_free);
    crypto(context && EVP_PKEY_keygen_init(context.get()) == 1);
    EVP_PKEY* key = nullptr;
    crypto(EVP_PKEY_generate(context.get(), &key) == 1);
    return KeyPair(key);
}

void KeyPair::save_new(const std::filesystem::path& path) const {
    auto directory = key_directory(path);
    const auto name = temporary_name();
    Fd file(openat(directory.get(), name.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
    if (file.get() < 0) throw WorldError(wire::STORAGE_ERROR, "Cannot create private key temporary file");
    bool published = false;
    try {
        if (fchmod(file.get(), 0600)) throw WorldError(wire::STORAGE_ERROR, "Cannot protect private key file");
        Bio output(BIO_new_fd(file.get(), BIO_NOCLOSE), BIO_free);
        if (!output || PEM_write_bio_PKCS8PrivateKey(output.get(), key_.get(), nullptr, nullptr, 0, nullptr, nullptr) != 1 ||
            BIO_flush(output.get()) != 1 || fsync(file.get()))
            throw WorldError(wire::STORAGE_ERROR, "Cannot persist private key material");
        // Publish one complete file. Never replace an existing actor identity.
        if (syscall(SYS_renameat2, directory.get(), name.c_str(), directory.get(), path.filename().c_str(), RENAME_NOREPLACE)) {
            if (errno == EEXIST) throw WorldError(wire::ALREADY_EXISTS, "Key path already exists");
            throw WorldError(wire::STORAGE_ERROR, "Cannot publish private key without replacement");
        }
        published = true;
        if (fsync(directory.get()))
            throw WorldError(wire::STORAGE_ERROR, "Key was published but directory durability is uncertain; preserve the key");
    } catch (...) {
        if (!published) unlinkat(directory.get(), name.c_str(), 0);
        throw;
    }
}

KeyPair KeyPair::load(const std::filesystem::path& path) {
    auto directory = key_directory(path);
    Fd file(openat(directory.get(), path.filename().c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
    if (file.get() < 0) {
        if (errno == ENOENT) throw WorldError(wire::NOT_FOUND, "Private key is absent");
        throw WorldError(wire::FORBIDDEN, "Private key cannot be opened safely");
    }
    struct stat info{};
    if (fstat(file.get(), &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
        info.st_nlink != 1 || (info.st_mode & 0077))
        throw WorldError(wire::FORBIDDEN, "Private key requires an owned, private, unlinked regular file");
    require(info.st_size > 0 && info.st_size <= static_cast<off_t>(max_key_file), "Invalid private key file size");
    SecretBuffer secret;
    std::size_t size = 0;
    while (size < secret.bytes.size()) {
        const auto count = read(file.get(), secret.bytes.data() + size, secret.bytes.size() - size);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) throw WorldError(wire::STORAGE_ERROR, "Cannot read private key");
        if (count == 0) break;
        size += static_cast<std::size_t>(count);
    }
    require(size > 0 && size <= max_key_file, "Invalid private key file size");
    require(std::string_view(secret.bytes.data(), size).starts_with("-----BEGIN PRIVATE KEY-----"), "Expected unencrypted PKCS8 private key");
    Bio input(BIO_new_mem_buf(secret.bytes.data(), static_cast<int>(size)), BIO_free);
    crypto(bool(input));
    KeyPair result(PEM_read_bio_PrivateKey(input.get(), nullptr, no_password, nullptr));
    require(result.key_ && EVP_PKEY_is_a(result.key_.get(), "ED25519") == 1, "Expected Ed25519 private key");
    char character = 0;
    while (BIO_read(input.get(), &character, 1) == 1)
        require(std::isspace(static_cast<unsigned char>(character)), "Unexpected trailing private key content");
    return result;
}

std::string KeyPair::public_key() const {
    std::string result(32, '\0'); std::size_t size = result.size();
    crypto(EVP_PKEY_get_raw_public_key(key_.get(), reinterpret_cast<unsigned char*>(result.data()), &size) == 1 && size == 32);
    return result;
}

v1::KeyProof KeyPair::sign(const v1::SignRequest& request) const {
    const auto payload = transcript(request);
    DigestContext context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    crypto(context && EVP_DigestSignInit(context.get(), nullptr, nullptr, nullptr, key_.get()) == 1);
    std::string signature(64, '\0'); std::size_t size = signature.size();
    crypto(EVP_DigestSign(context.get(), reinterpret_cast<unsigned char*>(signature.data()), &size,
                         reinterpret_cast<const unsigned char*>(payload.data()), payload.size()) == 1 && size == 64);
    v1::KeyProof result; result.set_purpose(request.purpose()); result.set_public_key(public_key()); result.set_signature(signature);
    return result;
}

bool verify(const v1::VerifyRequest& request) {
    validate_message(request);
    require(request.has_content() && request.has_proof(), "Verification content and proof are required");
    const auto payload = transcript(request.content());
    const auto& proof = request.proof();
    require(request.expected_public_key().size() == 32 && proof.public_key().size() == 32 && proof.signature().size() == 64,
            "Invalid Ed25519 key or signature length");
    require(proof.purpose() != v1::SIGNATURE_PURPOSE_UNSPECIFIED, "Signature purpose is required");
    if (proof.purpose() != request.content().purpose() ||
        CRYPTO_memcmp(proof.public_key().data(), request.expected_public_key().data(), 32)) return false;
    std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)> key(
        EVP_PKEY_new_raw_public_key_ex(nullptr, "ED25519", nullptr,
            reinterpret_cast<const unsigned char*>(request.expected_public_key().data()), 32), EVP_PKEY_free);
    DigestContext context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    crypto(key && context && EVP_DigestVerifyInit(context.get(), nullptr, nullptr, nullptr, key.get()) == 1);
    const auto result = EVP_DigestVerify(context.get(), reinterpret_cast<const unsigned char*>(proof.signature().data()), 64,
                                        reinterpret_cast<const unsigned char*>(payload.data()), payload.size());
    crypto(result >= 0);
    return result == 1;
}
} // namespace spatial::identity
