#pragma once
#include "identity/issuer.hpp"
#include <string_view>

namespace spatial::identity {
v1::IssuerReply dispatch_issuer(const v1::IssuerRequest& request, Issuer& issuer);
v1::IssuerReply handle_issuer_frame(std::string_view bytes, Issuer& issuer);
}
