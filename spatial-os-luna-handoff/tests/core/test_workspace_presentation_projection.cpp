#include "world/presentation_geometry.hpp"
#include "core/presentation_identity.hpp"
#include "core/scene.hpp"
#include <stdexcept>
#include <string>

using namespace spatial;
namespace {
void expect(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
wire::Pose pose() {
    wire::Pose value; value.mutable_position()->set_z(-2); value.mutable_orientation()->set_w(1); return value;
}
wire::RenderConfig config() {
    wire::RenderConfig value; value.set_vertical_fov(1.0); value.set_near_clip(.01); value.set_far_clip(100);
    value.mutable_background()->set_red(0); value.mutable_background()->set_green(0); value.mutable_background()->set_blue(0);
    return value;
}
}
int main() {
    std::string id(16, '\0'); id[0]='A'; id[3]='\0'; id[15]=static_cast<char>(0xff);
    const auto geometry_id=presentation_geometry_id(id);
    expect(geometry_id.find('\0')==std::string::npos, "geometry namespace is safe UTF-8 text");
    expect(presentation_representation_id(geometry_id)==id, "geometry ID round trips all binary identity bytes");
    expect(is_reserved_presentation_geometry_id(geometry_id), "projected entity ID uses reserved namespace");

    wire::RenderScene scene; *scene.mutable_camera()=pose(); *scene.mutable_config()=config();
    PresentationGeometry allowed; allowed.representation_id=id; allowed.room_id="room"; allowed.pose=pose(); allowed.generation=7;
    allowed.locked=true;
    append_presentation_geometry(scene, "room", {allowed});
    expect(scene.objects_size()==1 && scene.objects(0).kind()==wire::OBJECT, "presentation uses existing object primitive");
    expect(scene.objects(0).revision()==7 && scene.objects(0).id()==geometry_id, "world generation is retained in projected object");
    expect(scene.presentation_views_size()==1 &&
           scene.presentation_views(0).representation_id()==id &&
           scene.presentation_views(0).generation()==7 &&
           scene.presentation_views(0).locked(),
           "canonical public metadata retains opaque identity, generation, and locked state");
    validate_presentation_views(scene,"room");
    const auto panels=scene_panels(scene);
    expect(panels.size()==6, "existing native object renderer produces canonical six faces");
    for (const auto& face : panels) expect(face.representation.id()==geometry_id && face.representation.generation()==7,
        "native geometry retains representation identity and generation");

    bool collision=false;
    try { append_presentation_geometry(scene, "room", {allowed}); }
    catch (const WorldError& error) { collision=error.code==wire::ALREADY_EXISTS; }
    expect(collision, "deterministic identity collision fails closed without replacing existing geometry");
}
