#include "core/protocol.hpp"
#include "build_id.hpp"

#include <algorithm>
#include <set>
#include <vector>

namespace spatial {
void require_fields(const Json& value, std::initializer_list<std::string_view> fields) {
    if (!value.is_object() || value.size() != fields.size()) {
        throw Error("INVALID_ARGUMENT", "Missing or unknown object fields");
    }
    for (const auto field : fields) {
        if (!value.contains(field)) throw Error("INVALID_ARGUMENT", "Missing required field");
    }
}

Json error_response(const Json& id, std::string_view code, std::string_view message) {
    return {{"id", id}, {"error", {{"code", code}, {"message", message}, {"details", nullptr}}}};
}

Json dispatch(std::string_view method, const Json& params) {
    if (method == "system.hello") {
        require_fields(params, {});
        return {{"protocol", "spatial-conformance/1"}, {"implementation", "production"},
                {"build_id", SPATIAL_BUILD_ID}, {"capabilities", {"protocol.strict-json-lines"}}};
    }
    if (method == "world.get") {
        require_fields(params, {"id"});
        if (!params.at("id").is_string() || params.at("id").get_ref<const std::string&>().empty()) {
            throw Error("INVALID_ARGUMENT", "Expected a nonempty world entity ID");
        }
        throw Error("UNIMPLEMENTED", "The world daemon is not implemented yet");
    }
    throw Error("UNKNOWN_METHOD", "Unknown method");
}

Json handle_line(std::string_view line) {
    Json id = nullptr;
    try {
        if (line.size() > max_frame_bytes) throw Error("INVALID_ARGUMENT", "Frame exceeds size limit");
        std::vector<std::set<std::string>> object_keys;
        auto callback = [&object_keys](int depth, Json::parse_event_t event, Json& value) {
            if (depth > 64) throw Error("INVALID_ARGUMENT", "JSON nesting exceeds limit");
            if (event == Json::parse_event_t::object_start) object_keys.emplace_back();
            if (event == Json::parse_event_t::object_end) object_keys.pop_back();
            if (event == Json::parse_event_t::key &&
                !object_keys.back().insert(value.get<std::string>()).second) {
                throw Error("INVALID_ARGUMENT", "Duplicate JSON key");
            }
            return true;
        };
        const auto request = Json::parse(line, callback);
        if (request.is_object() && request.contains("id") && request.at("id").is_number_integer()) {
            id = request.at("id");
        }
        require_fields(request, {"id", "method", "params"});
        if (id.is_null() || !request.at("method").is_string() || !request.at("params").is_object()) {
            throw Error("INVALID_ARGUMENT", "Expected integer id, string method, and object params");
        }
        const auto result = dispatch(request.at("method").get_ref<const std::string&>(), request.at("params"));
        return {{"id", id}, {"result", result}};
    } catch (const Error& error) {
        return error_response(id, error.code, error.what());
    } catch (const Json::exception&) {
        return error_response(id, "INVALID_ARGUMENT", "Invalid JSON or value type");
    } catch (const std::exception&) {
        return error_response(id, "INTERNAL_ERROR", "Request failed internally");
    }
}
} // namespace spatial
