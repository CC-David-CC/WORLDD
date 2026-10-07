#pragma once
#include "core.pb.h"
#include <cstddef>
#include <iosfwd>
#include <string>
#include <string_view>

namespace spatial {
namespace wire = core::v1;
inline constexpr std::size_t max_frame_bytes = 1024 * 1024;
class WorldStore;
class Actors;
class RenderWorker;
class Surfaces;
class BrokerClient;
void validate_message(const google::protobuf::Message& message, int depth = 0);
wire::Reply dispatch(const wire::Request& request, WorldStore* world = nullptr, Actors* actors = nullptr, RenderWorker* renderer = nullptr, Surfaces* surfaces = nullptr, BrokerClient* broker = nullptr);
wire::Reply handle_frame(std::string_view frame, WorldStore* world = nullptr, Actors* actors = nullptr, RenderWorker* renderer = nullptr, Surfaces* surfaces = nullptr, BrokerClient* broker = nullptr);
bool read_frame(std::istream& input, std::string& frame);
void write_frame(std::ostream& output, const google::protobuf::Message& reply);
} // namespace spatial
