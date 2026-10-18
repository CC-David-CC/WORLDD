#pragma once

#include <openssl/evp.h>
#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl_lite.h>
#include <google/protobuf/message.h>

#include <array>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace spatial::file_policy {
inline constexpr char kNamespacePolicyDomainBytes[] = "spatial-file-namespace/v2";
inline constexpr std::string_view kNamespacePolicyDomain(
    kNamespacePolicyDomainBytes, sizeof(kNamespacePolicyDomainBytes));

inline bool is_registered_root_admin_path(const std::vector<std::string>& components) {
  return !components.empty() && components.front() == ".git";
}

inline std::string request_digest(const google::protobuf::Message& request) {
  std::string bytes;
  bytes.reserve(kNamespacePolicyDomain.size() + request.ByteSizeLong());
  bytes.append(kNamespacePolicyDomain.data(), kNamespacePolicyDomain.size());
  {
    google::protobuf::io::StringOutputStream stream(&bytes);
    google::protobuf::io::CodedOutputStream coded(&stream);
    coded.SetSerializationDeterministic(true);
    if (!request.SerializeToCodedStream(&coded) || coded.HadError())
      throw std::runtime_error("cannot serialize file operation identity");
    coded.Trim();
  }
  std::array<unsigned char, 32> digest{};
  unsigned length = 0;
  if (EVP_Digest(bytes.data(), bytes.size(), digest.data(), &length, EVP_sha256(), nullptr) != 1 ||
      length != digest.size())
    throw std::runtime_error("cannot digest file operation identity");
  return {reinterpret_cast<const char*>(digest.data()), digest.size()};
}
}  // namespace spatial::file_policy
