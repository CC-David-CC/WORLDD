#include "core/presentation_identity.hpp"
#include "core/scene.hpp"
#include "world/geometry_view_validation.hpp"
#include <array>
#include <stdexcept>
#include <string>

namespace {
void check(bool good, const char* message) { if (!good) throw std::runtime_error(message); }
template<class F> void rejects(F&& operation, const char* message) {
    try { operation(); } catch (const std::invalid_argument&) { return; }
    throw std::runtime_error(message);
}
spatial::wire::Pose pose(double z) {
    spatial::wire::Pose value;
    value.mutable_position()->set_z(z);
    value.mutable_orientation()->set_w(1);
    return value;
}
spatial::wire::RenderConfig config() {
    spatial::wire::RenderConfig value;
    value.set_vertical_fov(1.0); value.set_near_clip(0.1); value.set_far_clip(100.0);
    value.mutable_background()->set_red(12); value.mutable_background()->set_green(14); value.mutable_background()->set_blue(18);
    value.set_object_size(0.4); value.mutable_object_color()->set_red(220);
    value.mutable_object_color()->set_green(170); value.mutable_object_color()->set_blue(70);
    return value;
}
spatial::wire::RenderScene scene(bool locked) {
    spatial::wire::RenderScene value;
    *value.mutable_camera() = pose(0); *value.mutable_config() = config();
    auto* object = value.add_objects();
    object->set_id(spatial::presentation_geometry_id(std::string(16, 'x')));
    object->set_kind(spatial::wire::OBJECT); object->set_room("room-a");
    object->set_revision(7); *object->mutable_pose() = pose(-3);
    auto* view = value.add_presentation_views();
    view->set_representation_id(std::string(16, 'x')); view->set_room_id("room-a");
    *view->mutable_transform() = pose(-3); view->set_generation(7); view->set_locked(locked);
    return value;
}
}
int main() {
    const std::string raw(16, '\x00');
    const auto encoded = spatial::presentation_geometry_id(raw);
    check(encoded == "workspace-presentation.v1/00000000000000000000000000000000", "canonical ID encoding changed");
    check(spatial::presentation_representation_id(encoded) == raw, "ID round trip failed");
    check(!spatial::presentation_representation_id("workspace-presentation.v1/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
          "uppercase ID encoding was accepted");
    check(spatial::is_reserved_presentation_geometry_id("workspace-presentation.v1/not-hex"),
          "malformed reserved prefix was not recognized");

    auto unlocked = scene(false), locked = scene(true);
    spatial::validate_presentation_views(unlocked, "room-a");
    spatial::validate_presentation_views(locked, "room-a");
    const auto ordinary = spatial::scene_panels(unlocked);
    const auto hidden = spatial::scene_panels(locked);
    check(ordinary.size() == 6 && hidden.size() == ordinary.size(), "presentation changed canonical object geometry");
    check(ordinary.front().color != hidden.front().color, "locked appearance did not change native object styling");
    check(ordinary.front().representation.generation() == 7 && hidden.front().representation.generation() == 7,
          "presentation generation was not preserved");

    auto neutral_locked = scene(true);
    neutral_locked.mutable_config()->mutable_object_color()->set_red(96);
    neutral_locked.mutable_config()->mutable_object_color()->set_green(104);
    neutral_locked.mutable_config()->mutable_object_color()->set_blue(120);
    auto neutral_unlocked = neutral_locked;
    neutral_unlocked.mutable_presentation_views(0)->set_locked(false);
    check(spatial::scene_panels(neutral_locked).front().color != spatial::scene_panels(neutral_unlocked).front().color,
          "locked styling collapsed to the existing neutral object color");

    auto wrong_room = scene(false);
    wrong_room.mutable_presentation_views(0)->set_room_id("room-b");
    rejects([&] { spatial::validate_presentation_views(wrong_room, "room-a"); }, "wrong-room view accepted");
    auto room_mismatch = scene(false);
    room_mismatch.mutable_objects(0)->set_room("room-b");
    rejects([&] { spatial::validate_presentation_views(room_mismatch, "room-a"); },
            "object room differing from its public view was accepted");
    auto roomless_object = scene(false);
    roomless_object.mutable_objects(0)->clear_room();
    spatial::validate_presentation_views(roomless_object, "room-a");
    auto wrong_pose = scene(false);
    wrong_pose.mutable_presentation_views(0)->mutable_transform()->mutable_position()->set_x(1);
    rejects([&] { spatial::validate_presentation_views(wrong_pose); }, "mismatched pose accepted");
    auto duplicate = scene(false);
    *duplicate.add_presentation_views() = duplicate.presentation_views(0);
    rejects([&] { spatial::validate_presentation_views(duplicate); }, "duplicate presentation identity accepted");
    auto missing = scene(false);
    missing.clear_presentation_views();
    rejects([&] { spatial::validate_presentation_views(missing); }, "projected object without status accepted");
    auto malformed = scene(false);
    malformed.mutable_objects(0)->set_id("workspace-presentation.v1/bad");
    rejects([&] { spatial::validate_presentation_views(malformed); }, "malformed reserved object accepted");
    auto non_object_identity = scene(false);
    non_object_identity.mutable_objects(0)->set_kind(spatial::wire::BARRIER);
    rejects([&] { spatial::validate_presentation_views(non_object_identity); },
            "reserved identity on non-object geometry was accepted");

    auto panel_collision = scene(false);
    panel_collision.add_panels()->set_id(encoded);
    rejects([&] { spatial::validate_presentation_views(panel_collision); },
            "reserved identity collision in panel category was accepted");
    auto surface_handle_collision = scene(false);
    surface_handle_collision.add_surfaces()->mutable_handle()->set_id(encoded);
    rejects([&] { spatial::validate_presentation_views(surface_handle_collision); },
            "reserved identity collision in surface handle was accepted");
    auto surface_panel_collision = scene(false);
    surface_panel_collision.add_surfaces()->mutable_panel()->set_id(encoded);
    rejects([&] { spatial::validate_presentation_views(surface_panel_collision); },
            "reserved identity collision in surface panel was accepted");
    auto barrier_collision = scene(false);
    barrier_collision.add_barriers()->set_id(encoded);
    rejects([&] { spatial::validate_presentation_views(barrier_collision); },
            "reserved identity collision in barrier category was accepted");
    auto portal_collision = scene(false);
    portal_collision.add_portals()->set_id(encoded);
    rejects([&] { spatial::validate_presentation_views(portal_collision); },
            "reserved identity collision in portal category was accepted");
    auto actor_collision = scene(false);
    actor_collision.add_actors()->set_id(encoded);
    rejects([&] { spatial::validate_presentation_views(actor_collision); },
            "reserved identity collision in actor category was accepted");

    spatial::identity::v1::ActorGeometryView geometry;
    geometry.set_world_id("world-a"); geometry.set_actor_id("actor-a"); geometry.set_view_id(std::string(32, 'v'));
    geometry.set_authority_service_incarnation(std::string(32, 's'));
    geometry.set_authority_generation(1); geometry.set_sequence(1); geometry.set_room("room-a");
    *geometry.mutable_camera() = pose(0); *geometry.mutable_config() = config();
    auto* drawable = geometry.add_objects(); drawable->set_id(locked.objects(0).id());
    drawable->set_kind(spatial::wire::OBJECT); *drawable->mutable_pose() = pose(-3);
    *geometry.add_presentation_views() = locked.presentation_views(0);
    spatial::validate_geometry_view(geometry);

    auto ordinary_categories = geometry;
    auto* ordinary_panel = ordinary_categories.add_panels();
    ordinary_panel->set_id("ordinary-panel"); ordinary_panel->set_kind(spatial::wire::PANEL);
    *ordinary_panel->mutable_pose() = pose(-2); ordinary_panel->mutable_size()->set_x(1);
    ordinary_panel->mutable_size()->set_y(1); ordinary_panel->mutable_color()->set_red(20);
    ordinary_panel->mutable_color()->set_green(30); ordinary_panel->mutable_color()->set_blue(40);
    auto* ordinary_barrier = ordinary_categories.add_barriers();
    ordinary_barrier->set_id("ordinary-barrier"); ordinary_barrier->set_kind(spatial::wire::BARRIER);
    *ordinary_barrier->mutable_pose() = pose(-2);
    ordinary_barrier->mutable_bounds()->mutable_min()->set_x(-1);
    ordinary_barrier->mutable_bounds()->mutable_min()->set_y(-1);
    ordinary_barrier->mutable_bounds()->mutable_min()->set_z(-1);
    ordinary_barrier->mutable_bounds()->mutable_max()->set_x(1);
    ordinary_barrier->mutable_bounds()->mutable_max()->set_y(1);
    ordinary_barrier->mutable_bounds()->mutable_max()->set_z(1);
    auto* ordinary_portal = ordinary_categories.add_portals();
    ordinary_portal->set_id("ordinary-portal"); ordinary_portal->set_kind(spatial::wire::PORTAL);
    *ordinary_portal->mutable_pose() = pose(-2); ordinary_portal->mutable_size()->set_x(1);
    ordinary_portal->mutable_size()->set_y(1);
    auto* ordinary_actor = ordinary_categories.add_actors();
    ordinary_actor->set_id("ordinary-actor"); ordinary_actor->set_kind(spatial::wire::ACTOR);
    *ordinary_actor->mutable_pose() = pose(-2);
    spatial::validate_geometry_view(ordinary_categories);

    const auto reserved_nonobject_id = spatial::presentation_geometry_id(std::string(16, 'y'));
    auto reserved_panel = ordinary_categories;
    reserved_panel.mutable_panels(0)->set_id(reserved_nonobject_id);
    rejects([&] { spatial::validate_geometry_view(reserved_panel); },
            "geometry-view panel used reserved presentation namespace");
    auto reserved_barrier = ordinary_categories;
    reserved_barrier.mutable_barriers(0)->set_id(reserved_nonobject_id);
    rejects([&] { spatial::validate_geometry_view(reserved_barrier); },
            "geometry-view barrier used reserved presentation namespace");
    auto reserved_portal = ordinary_categories;
    reserved_portal.mutable_portals(0)->set_id(reserved_nonobject_id);
    rejects([&] { spatial::validate_geometry_view(reserved_portal); },
            "geometry-view portal used reserved presentation namespace");
    auto reserved_actor = ordinary_categories;
    reserved_actor.mutable_actors(0)->set_id(reserved_nonobject_id);
    rejects([&] { spatial::validate_geometry_view(reserved_actor); },
            "geometry-view actor used reserved presentation namespace");

    geometry.mutable_presentation_views(0)->set_room_id("room-b");
    rejects([&] { spatial::validate_geometry_view(geometry); }, "geometry room mismatch accepted");

    spatial::wire::ActorObservation observation;
    observation.set_room("room-a");
    auto* visible = observation.add_visible();
    visible->set_id(locked.objects(0).id()); visible->set_generation(7);
    visible->set_kind(spatial::wire::OBJECT); *visible->mutable_pose() = pose(-3);
    *observation.add_presentation_views() = locked.presentation_views(0);
    spatial::validate_presentation_views(observation);
    auto orphan_visible = observation;
    orphan_visible.clear_presentation_views();
    rejects([&] { spatial::validate_presentation_views(orphan_visible); },
            "reserved visible object without public metadata was accepted");
    auto orphan_view = observation;
    orphan_view.clear_visible();
    rejects([&] { spatial::validate_presentation_views(orphan_view); },
            "public metadata without a visible object was accepted");
    auto malformed_visible = observation;
    malformed_visible.mutable_visible(0)->set_id("workspace-presentation.v1/bad");
    rejects([&] { spatial::validate_presentation_views(malformed_visible); },
            "malformed reserved visible identity was accepted");
    auto wrong_kind_visible = observation;
    wrong_kind_visible.mutable_visible(0)->set_kind(spatial::wire::ACTOR);
    rejects([&] { spatial::validate_presentation_views(wrong_kind_visible); },
            "reserved visible identity on non-object was accepted");
    auto duplicate_visible = observation;
    const auto first_visible = duplicate_visible.visible(0);
    *duplicate_visible.add_visible() = first_visible;
    rejects([&] { spatial::validate_presentation_views(duplicate_visible); },
            "duplicate visible presentation identity was accepted");
    observation.mutable_visible(0)->set_generation(6);
    rejects([&] { spatial::validate_presentation_views(observation); }, "stale visible generation accepted");

    const auto* render_scene = spatial::wire::RenderScene::descriptor();
    const auto* observation_descriptor = spatial::wire::ActorObservation::descriptor();
    const auto* geometry_view = spatial::identity::v1::ActorGeometryView::descriptor();
    check(render_scene->FindFieldByName("presentation_views")->number() == 10, "RenderScene field tag changed");
    check(observation_descriptor->FindFieldByName("presentation_views")->number() == 9, "ActorObservation field tag changed");
    check(geometry_view->FindFieldByName("presentation_views")->number() == 16, "ActorGeometryView field tag changed");
}
