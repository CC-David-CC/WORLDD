#pragma once
#include "key.pb.h"
#include <openssl/evp.h>
#include <filesystem>
#include <memory>
#include <string>

namespace spatial::identity {
inline constexpr std::size_t max_signed_payload = 65536;

// This component provides cryptographic material, not actor authorization.
class KeyPair {
public:
    static KeyPair generate();
    static KeyPair load(const std::filesystem::path& path);
    void save_new(const std::filesystem::path& path) const;
    std::string public_key() const;
    v1::KeyProof sign(const v1::SignRequest& request) const;
private:
    explicit KeyPair(EVP_PKEY* key) : key_(key, EVP_PKEY_free) {}
    std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)> key_;
};

bool verify(const v1::VerifyRequest& request);
} // namespace spatial::identity
