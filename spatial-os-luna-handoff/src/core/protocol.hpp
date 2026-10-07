#pragma once
#include "core.pb.h"
#include <cstddef>
#include <iosfwd>
#include <string>
#include <string_view>

namespace spatial {
namespace wire = core::v1;
inline constexpr std::size_t max_frame_bytes = 1024 * 1024;
wire::Reply dispatch(const wire::Request& request);
wire::Reply handle_frame(std::string_view frame);
bool read_frame(std::istream& input, std::string& frame);
void write_frame(std::ostream& output, const wire::Reply& reply);
} // namespace spatial
