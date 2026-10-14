#include "world/workspace_presentations.hpp"
#include <utility>
#include <array>
#include <openssl/evp.h>
#include <stdexcept>

namespace spatial {
std::string request_digest(const google::protobuf::MessageLite& request) {
    const auto bytes=request.SerializeAsString();
    std::array<unsigned char,EVP_MAX_MD_SIZE> digest{}; unsigned int length=0;
    if(EVP_Digest(bytes.data(),bytes.size(),digest.data(),&length,EVP_sha256(),nullptr)!=1 || length!=32)
        throw std::runtime_error("Cannot fingerprint workspace presentation request");
    return std::string(reinterpret_cast<const char*>(digest.data()), length);
}

std::vector<PresentationGeometry> visible_presentations(WorldStore& store, const std::string& room,
    const PresentationVisible& authorized_view,
    const WorkspaceReadable& fresh_workspace_read, const PresentationUsable& fresh_presentation_use) {
    if (!authorized_view) return {};
    std::vector<PresentationGeometry> result;
    for (const auto& record : store.presentations()) {
        if (record.room_id != room || !authorized_view(record.room_id, record.id)) continue;
        PresentationGeometry geometry;
        geometry.representation_id = record.id;
        geometry.room_id = record.room_id;
        geometry.pose = record.pose;
        geometry.generation = record.generation;
        geometry.locked = !fresh_workspace_read || !fresh_workspace_read(record.workspace_id);
        geometry.usable = fresh_presentation_use && fresh_presentation_use(record.id, record.workspace_id);
        result.push_back(std::move(geometry));
    }
    return result;
}
}
