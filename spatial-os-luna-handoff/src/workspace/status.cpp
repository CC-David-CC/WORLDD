#include "workspace/status.hpp"
#include "world/store.hpp"
#include <set>
#include <string_view>
#include <vector>

namespace spatial {
namespace {
void require(bool condition) {
    if (!condition) throw WorldError(wire::STORAGE_ERROR, "Invalid Git porcelain status output");
}
bool utf8(std::string_view value) {
    std::size_t index = 0;
    while (index < value.size()) {
        const auto first = static_cast<unsigned char>(value[index++]);
        if (first < 0x80) continue;
        unsigned count = 0, code = 0, minimum = 0;
        if (first >= 0xc2 && first <= 0xdf) { count = 1; code = first & 0x1f; minimum = 0x80; }
        else if (first >= 0xe0 && first <= 0xef) { count = 2; code = first & 0x0f; minimum = 0x800; }
        else if (first >= 0xf0 && first <= 0xf4) { count = 3; code = first & 0x07; minimum = 0x10000; }
        else return false;
        if (index + count > value.size()) return false;
        while (count--) {
            const auto next = static_cast<unsigned char>(value[index++]);
            if ((next & 0xc0) != 0x80) return false;
            code = (code << 6) | (next & 0x3f);
        }
        if (code < minimum || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return false;
    }
    return true;
}
std::string text(std::string_view value) {
    if (!utf8(value)) throw WorldError(wire::INVALID_ARGUMENT, "Git metadata contains a path or branch unsupported by the UTF-8 status interface");
    return std::string(value);
}
std::string path(std::string_view row, unsigned spaces) {
    std::size_t start = 0;
    while (spaces--) {
        const auto end = row.find(' ', start); require(end != std::string_view::npos && end > start);
        start = end + 1;
    }
    require(start < row.size());
    return text(row.substr(start));
}
}
wire::GitStatusReply parse_git_status(const std::string& porcelain) {
    if (porcelain.size() > max_frame_bytes)
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Git status exceeds the bounded reply size");
    std::vector<std::string_view> rows;
    std::size_t start = 0;
    while (start < porcelain.size()) {
        const auto end = porcelain.find('\0', start);
        if (end == std::string::npos)
            throw WorldError(wire::RESOURCE_EXHAUSTED, "Git status output exceeds its bounded reply size");
        rows.emplace_back(porcelain.data() + start, end - start); start = end + 1;
    }
    wire::GitStatusReply out;
    bool oid = false, branch = false;
    std::set<std::string> modified, untracked;
    for (std::size_t i = 0; i < rows.size(); ++i) {
        const auto row = rows[i]; require(!row.empty());
        if (row.starts_with("# branch.oid ")) {
            require(!oid); oid = true; const auto value = row.substr(13);
            if (value != "(initial)") {
                require((value.size() == 40 || value.size() == 64) && value.find_first_not_of("0123456789abcdef") == std::string_view::npos);
                out.set_head(std::string(value));
            }
        } else if (row.starts_with("# branch.head ")) {
            require(!branch); branch = true; const auto value = row.substr(14); require(!value.empty());
            if (value != "(detached)") out.set_branch(text(value));
        } else if (row.starts_with("# ")) continue;
        else if (row.starts_with("? ") || row.starts_with("! ")) untracked.insert(path(row, 1));
        else if (row.starts_with("1 ")) modified.insert(path(row, 8));
        else if (row.starts_with("2 ")) {
            modified.insert(path(row, 9)); require(++i < rows.size() && !rows[i].empty());
            modified.insert(text(rows[i]));
        } else if (row.starts_with("u ")) modified.insert(path(row, 10));
        else require(false);
    }
    require(oid && branch);
    for (const auto& value : modified) out.add_modified(value);
    for (const auto& value : untracked) out.add_untracked(value);
    out.set_clean(modified.empty() && untracked.empty());
    if (out.ByteSizeLong() > max_frame_bytes - 128) throw WorldError(wire::RESOURCE_EXHAUSTED, "Git status exceeds the bounded reply size");
    return out;
}
}
