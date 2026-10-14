#pragma once
#include "world/workspace_presentations.hpp"
#include "core/scene.hpp"
#include <string>
#include <vector>

namespace spatial {
void append_presentation_geometry(wire::RenderScene&, const std::string& room,
                                  const std::vector<PresentationGeometry>& authorized_records);
}
