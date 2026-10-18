#pragma once
#include "world/store.hpp"
#include <functional>
#include <google/protobuf/message_lite.h>
#include <vector>

namespace spatial {
struct PresentationGeometry {
    std::string representation_id;
    std::string room_id;
    wire::Pose pose;
    std::uint64_t generation = 0;
    bool locked = true;
    bool usable = false;
    PresentationKind kind = PresentationKind::generic;
    bool cabinet_locked = false;
};
using WorkspaceReadable = std::function<bool(const std::string&)>;
using PresentationUsable = std::function<bool(const std::string&, const std::string&)>;
using PresentationVisible = std::function<bool(const std::string& room, const std::string& representation)>;
// World observation and representation view are evaluated per representation. Workspace data never enters this view.
std::string request_digest(const google::protobuf::MessageLite&);
std::vector<PresentationGeometry> visible_presentations(WorldStore&, const std::string& room,
    const PresentationVisible& authorized_view,
    const WorkspaceReadable& fresh_workspace_read, const PresentationUsable& fresh_presentation_use);
}
