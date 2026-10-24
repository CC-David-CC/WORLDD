#pragma once
#include <X11/Xlib.h>
#include <filesystem>

namespace spatial::control::physical_detail {
// Single compositor X thread. Only this query's XI BadDevice is an unplug race.
// Other X errors reach the previous handler and fail the operation.
std::filesystem::path device_node(Display*, int device, Atom property, int opcode, int error_base);
}
