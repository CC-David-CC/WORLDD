#pragma once
#include <filesystem>
#include <string>
#include <vector>
namespace spatial {
struct GitResult { int status; std::string output; };
// Administrative Git only. Application launches use a separate confined path.
GitResult git_command(const std::filesystem::path& directory, const std::vector<std::string>& arguments);
}
