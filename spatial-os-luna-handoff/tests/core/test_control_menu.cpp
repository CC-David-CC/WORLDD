#include "control/menu.hpp"
#include <iostream>
#include <stdexcept>
namespace c=spatial::control;
namespace p=spatial::control::v1;
namespace wire=spatial::core::v1;
namespace {
void check(bool value,const char* detail){if(!value)throw std::runtime_error(detail);}
struct Fixture {
    unsigned stops=0,saves=0;bool save_fails=false;
    c::ActorControl actor{{"world","actor",1},[this](const auto& request){
        check(request.has_stop(),"Menu sent application or movement input");++stops;
        spatial::identity::v1::WorldSessionReply reply;reply.set_protocol_version(1);reply.mutable_action()->set_accepted(true);return reply;
    },[]{return spatial::identity::v1::WorldSessionReply{};},[]{}};
    c::DeviceAdapters::Clock::time_point now{std::chrono::seconds(10)};
    c::DeviceAdapters adapter{actor,c::default_profiles(),[this]{return now;}};
    c::ControlsMenu menu{adapter,[this](const auto& profiles){c::validate_profiles(profiles);++saves;if(save_fails)throw std::runtime_error("save failure");}};
    std::map<std::string,std::uint64_t> sequences;
    p::DeviceEvent event(const std::string& source){p::DeviceEvent e;e.set_protocol_version(1);e.set_source_id(source);e.set_source_generation(1);e.set_event_sequence(++sequences[source]);return e;}
    void send(const p::DeviceEvent& e){now+=std::chrono::milliseconds(1);const bool open=adapter.status().menu_open();adapter.event(e,now);menu.event(e,now,open);}
    Fixture(){auto e=event("keyboard");e.mutable_connected()->set_kind(p::KEYBOARD);send(e);e=event("spacemouse");e.mutable_connected()->set_kind(p::SPACEMOUSE);send(e);press("F10");}
    void edge(const std::string& key,bool down){auto e=event("keyboard");e.mutable_key()->set_keysym(key);e.mutable_key()->set_pressed(down);send(e);}
    void press(const std::string& key){edge(key,true);edge(key,false);}
    void down(unsigned count){for(unsigned i=0;i<count;++i)press("Down");}
    void axes(const std::array<double,6>& raw){auto e=event("spacemouse");for(double x:raw)e.mutable_axes()->add_raw(x);send(e);}
};
void switching_and_variations(){
    Fixture f;check(f.menu.view().visible()&&f.menu.view().entries_size()==19,"Menu rows missing");
    const auto before=f.adapter.profiles();f.press("Right");check(f.adapter.status().menu_open()&&f.saves==1,"Preset selection did not remain in session");
    f.press("Left");check(f.adapter.profiles().active_profile()==before.active_profile(),"Preset cycle did not restore initial selection");
    f.press("Up");f.press("Up");f.press("Return");
    check(f.adapter.profiles().profiles_size()==7&&f.adapter.profiles().active_profile()=="variation-1","Editable variation was not created");
    for(int i=0;i<before.profiles_size();++i)check(f.adapter.profiles().profiles(i).SerializeAsString()==before.profiles(i).SerializeAsString(),"Variation changed a default preset");
}
void application_key_delivery_is_an_explicit_persisted_choice(){
    Fixture f;f.down(4);check(f.menu.view().entries(4).selected(),"Application key mode row moved");
    f.press("Right");check(c::active_profile(f.adapter.profiles()).key_delivery()==p::KEY_HOLD&&f.saves==1,
          "Game key hold mode was not applied and saved");
    f.press("Left");check(c::active_profile(f.adapter.profiles()).key_delivery()==p::KEY_TAP&&f.saves==2,
          "Typing tap mode did not restore");
}
void binding_conflict_and_fresh_keys(){
    Fixture f;f.down(6);f.press("Return");const auto before=f.adapter.profiles().SerializeAsString();f.press("w");
    check(f.adapter.profiles().SerializeAsString()==before&&f.saves==0,"Conflicting binding replaced usable controls");
    f.press("x");check(f.saves==1&&c::active_profile(f.adapter.profiles()).bindings(1).keysym()=="x","New binding was not applied");
    f.press("Escape");check(!f.adapter.status().menu_open(),"Menu did not close");
    f.edge("F10",true);check(f.adapter.status().menu_open(),"Menu did not reopen");
    for(unsigned i=0;i<5;++i)f.press("Up");
    const auto saves=f.saves;f.edge("Right",true);f.edge("Right",true);check(f.saves==saves+1,"Repeated physical press edited twice");
    f.edge("Right",false);f.edge("F10",false);
}
void save_failure_keeps_visible_state(){
    Fixture f;f.save_fails=true;f.press("Right");check(f.actor.available()&&f.adapter.status().menu_open(),"Preference failure killed the actor");
    check(f.menu.view().message()=="Applied for this session. Saving failed.","Preference failure was hidden");
}
void six_axis_measurement_and_stale_refusal(){
    Fixture f;f.down(16);f.press("Return");f.press("Return");
    check(!c::active_profile(f.adapter.profiles()).spacemouse_calibrated(),"Missing samples invented a calibration");
    f.axes({});f.now+=std::chrono::milliseconds(251);f.press("Return");
    check(f.menu.view().message()=="SpaceMouse sample is missing or stale","Stale calibration sample was accepted");
    const std::array<unsigned,6> order{2,0,1,5,3,4};
    for(unsigned axis=0;axis<6;++axis){
        f.axes({});f.press("Return");std::array<double,6> sample{};sample[order[axis]]=axis%2?-200:100;
        f.axes(sample);f.press("Return");
    }
    const auto& profile=c::active_profile(f.adapter.profiles());check(profile.spacemouse_calibrated()&&profile.spacemouse_axes_size()==6,"Measured calibration was not saved");
    for(unsigned axis=0;axis<6;++axis){const auto& measured=profile.spacemouse_axes(static_cast<int>(axis));
        check(measured.raw_axis()==static_cast<int>(order[axis]+1)*(axis%2?-1:1),"Measured signed axis changed");
        check(measured.center()==0&&measured.full_scale()==(axis%2?200:100),"Measured range changed");
    }
    check(f.saves==1,"Partial calibration was saved");
}
void ambiguous_measurement_can_retry_without_discarding_completed_axes(){
    Fixture f;f.down(16);f.press("Return");f.axes({});f.press("Return");
    f.axes({100,0,0,0,0,0});f.press("Return");
    f.axes({});f.press("Return");f.axes({0,100,100,0,0,0});f.press("Return");
    check(f.menu.view().prompt().find("Release the SpaceMouse cap")!=std::string::npos,
          "Ambiguous calibration did not return to a fresh neutral measurement");
    for(unsigned axis=1;axis<6;++axis){
        f.axes({});f.press("Return");std::array<double,6> sample{};sample[axis]=100;f.axes(sample);f.press("Return");
    }
    check(c::active_profile(f.adapter.profiles()).spacemouse_calibrated(),"Retry retained ambiguous peaks or lost progress");
}
void stale_gestures_keep_release_bookkeeping_without_editing_preferences(){
    Fixture f;auto queued=f.event("keyboard");queued.mutable_key()->set_keysym("Right");queued.mutable_key()->set_pressed(true);
    const auto captured=f.now+std::chrono::milliseconds(1);f.now+=std::chrono::milliseconds(10);
    f.adapter.set_profiles(f.adapter.profiles());
    const bool fresh=f.adapter.event(queued,captured,f.now);check(!fresh,"Stale menu fixture did not cross a barrier");
    f.menu.event(queued,f.now,true,fresh);f.edge("Right",true);
    check(f.saves==0,"Stale menu press or its duplicate edited preferences");
    f.edge("Right",false);f.press("Right");check(f.saves==1,"Fresh menu gesture did not recover after release");
    Fixture axes;axes.down(16);axes.press("Return");auto old=axes.event("spacemouse");
    for(double value:{100.,0.,0.,0.,0.,0.})old.mutable_axes()->add_raw(value);
    const auto axis_capture=axes.now+std::chrono::milliseconds(1);axes.now+=std::chrono::milliseconds(10);
    axes.adapter.set_profiles(axes.adapter.profiles());const bool axis_fresh=axes.adapter.event(old,axis_capture,axes.now);
    check(!axis_fresh,"Stale calibration fixture did not cross a barrier");axes.menu.event(old,axes.now,true,axis_fresh);
    axes.press("Return");check(axes.menu.view().message()=="SpaceMouse sample is missing or stale","Queued axes became neutral calibration input");
}
void calibration_uses_capture_age_after_queued_delivery(){
    Fixture f;f.down(16);f.press("Return");auto axes=f.event("spacemouse");
    for(double value:{0.,0.,0.,0.,0.,0.})axes.mutable_axes()->add_raw(value);
    const auto captured=f.now+std::chrono::milliseconds(1);f.now+=std::chrono::milliseconds(200);
    const bool fresh=f.adapter.event(axes,captured,f.now);check(fresh,"Fresh delayed axis fixture was rejected before menu delivery");
    f.menu.event(axes,captured,f.now,true,fresh);f.now+=std::chrono::milliseconds(100);f.press("Return");
    check(f.menu.view().message()=="SpaceMouse sample is missing or stale","Menu renewed sample age after delayed delivery");
}
void ambiguous_calibration_does_not_change_profile(){
    Fixture f;f.down(16);f.press("Return");f.axes({});f.press("Return");f.axes({100,100,0,0,0,0});f.press("Return");
    check(!c::active_profile(f.adapter.profiles()).spacemouse_calibrated()&&f.saves==0,"Ambiguous axes invented a mapping");
    f.press("Escape");check(f.menu.view().prompt().find("Up/Down")!=std::string::npos,"Calibration cancellation left the wizard active");
}
}
int main(){try{
    switching_and_variations();std::cout<<"PASS menu-presets-editable-variations\n";
    application_key_delivery_is_an_explicit_persisted_choice();std::cout<<"PASS menu-application-key-delivery-choice\n";
    binding_conflict_and_fresh_keys();std::cout<<"PASS menu-binding-conflicts-fresh-keys\n";
    save_failure_keeps_visible_state();std::cout<<"PASS menu-preference-failure-status\n";
    six_axis_measurement_and_stale_refusal();std::cout<<"PASS menu-measured-signed-six-axis-calibration\n";
    ambiguous_measurement_can_retry_without_discarding_completed_axes();std::cout<<"PASS menu-calibration-retry-fresh-neutral\n";
    stale_gestures_keep_release_bookkeeping_without_editing_preferences();std::cout<<"PASS menu-stale-gesture-release-bookkeeping\n";
    calibration_uses_capture_age_after_queued_delivery();std::cout<<"PASS menu-calibration-capture-age\n";
    ambiguous_calibration_does_not_change_profile();std::cout<<"PASS menu-ambiguous-calibration-cancellation\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
