#pragma once

#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <string_view>

namespace spatial {
using Json = nlohmann::json;
inline constexpr std::size_t max_frame_bytes = 1024 * 1024;

class Error : public std::runtime_error {
public:
    Error(std::string code, std::string message)
        : std::runtime_error(std::move(message)), code(std::move(code)) {}
    std::string code;
};

void require_fields(const Json& value, std::initializer_list<std::string_view> fields);
Json error_response(const Json& id, std::string_view code, std::string_view message);
Json dispatch(std::string_view method, const Json& params);
Json handle_line(std::string_view line);
} // namespace spatial
