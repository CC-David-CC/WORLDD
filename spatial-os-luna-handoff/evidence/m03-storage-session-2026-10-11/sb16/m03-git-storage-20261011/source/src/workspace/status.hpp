#pragma once
#include "core/protocol.hpp"
#include <string>
namespace spatial {
wire::GitStatusReply parse_git_status(const std::string& porcelain);
}
