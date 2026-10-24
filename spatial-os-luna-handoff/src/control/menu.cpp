#include "control/menu.hpp"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace spatial::control {
namespace {
constexpr unsigned first_binding = 4;
constexpr unsigned calibration_row = first_binding + 11;
constexpr unsigned copy_row = calibration_row + 1;
constexpr unsigned close_row = copy_row + 1;
const char* const names[]{"Forward", "Backward", "Left", "Right", "Up", "Down", "Roll left", "Roll right", "Switch mode", "Controls menu", "Cancel input"};
const char* const directions[]{"Move right", "Move up", "Move backward", "Look up", "Turn left", "Roll left"};
void need(bool b,const char* s){if(!b)throw std::invalid_argument(s);}
}
ControlsMenu::ControlsMenu(DeviceAdapters& adapters, Persist persist) : adapters_(adapters), persist_(std::move(persist)) {
    need(bool(persist_),"Controls menu requires preference persistence");
}
v1::ControlProfile* ControlsMenu::selected(v1::ControlProfiles& p) {
    for(auto& profile:*p.mutable_profiles())if(profile.id()==p.active_profile())return &profile;
    throw std::invalid_argument("Active control profile is missing");
}
void ControlsMenu::apply(v1::ControlProfiles p) {
    validate_profiles(p);
    adapters_.set_profiles(p);
    // A failed save leaves the visible session usable. State is reported honestly.
    try { persist_(p);message_="Saved"; }
    catch(const std::exception&) {message_="Applied for this session. Saving failed.";}
}
void ControlsMenu::event(const v1::DeviceEvent& e,DeviceAdapters::Clock::time_point now,bool previously_open,bool fresh) {
    if(e.has_connected())physical_keys_[e.source_id()].clear();
    if(e.has_disconnected()){
        physical_keys_.erase(e.source_id());sampled_=false;calibration_axis_.reset();measuring_=false;
    }
    bool fresh_key=false;
    if(e.has_key()){
        auto& keys=physical_keys_[e.source_id()];
        if(e.key().pressed())fresh_key=!e.key().repeat()&&keys.insert(e.key().keysym()).second;
        else keys.erase(e.key().keysym());
    }
    const bool open=adapters_.status().menu_open();
    if(!open){rebinding_.reset();calibration_axis_.reset();measuring_=false;return;}
    if(!fresh)return;
    if(e.has_axes()) {
        need(e.axes().raw_size()==6,"Calibration requires six raw axes");
        for(unsigned i=0;i<6;++i){
            const auto value=e.axes().raw(static_cast<int>(i));
            need(std::isfinite(value)&&std::abs(value)<=1e9,"Invalid calibration sample");
            raw_[i]=value;
            if(measuring_&&std::abs(value-center_[i])>std::abs(peak_[i]))peak_[i]=value-center_[i];
        }
        sampled_=true;sample_time_=now;
    }
    if(previously_open&&fresh_key){
        try{key(e.key().keysym(),now);}catch(const std::invalid_argument& error){message_=error.what();}
    }
}
void ControlsMenu::calibrate(DeviceAdapters::Clock::time_point now) {
    need(sampled_&&now>=sample_time_&&now-sample_time_<std::chrono::milliseconds(250),"SpaceMouse sample is missing or stale");
    if(!measuring_){center_=raw_;peak_.fill(0);measuring_=true;message_.clear();return;}
    unsigned largest=0;for(unsigned i=1;i<6;++i)if(std::abs(peak_[i])>std::abs(peak_[largest]))largest=i;
    const double scale=std::abs(peak_[largest]);
    need(scale>=1,"Move one axis through its intended full range");
    for(unsigned i=0;i<6;++i)if(i!=largest)need(std::abs(peak_[i])<=scale*.5,"Several axes moved. Retry one axis.");
    for(const auto& axis:calibration_)need(static_cast<unsigned>(std::abs(axis.raw_axis())-1)!=largest,"That raw axis is already assigned. Retry.");
    v1::AxisCalibration axis;axis.set_raw_axis(static_cast<int>(largest+1)*(peak_[largest]<0?-1:1));axis.set_center(center_[largest]);axis.set_full_scale(scale);
    calibration_.push_back(axis);measuring_=false;
    if(*calibration_axis_<5){++*calibration_axis_;message_.clear();return;}
    auto profiles=adapters_.profiles();auto* profile=selected(profiles);profile->clear_spacemouse_axes();
    for(const auto& value:calibration_)*profile->add_spacemouse_axes()=value;
    profile->set_spacemouse_calibrated(true);apply(std::move(profiles));calibration_axis_.reset();
}
void ControlsMenu::key(const std::string& key,DeviceAdapters::Clock::time_point now) {
    if(key=="Escape"){
        if(rebinding_||calibration_axis_){rebinding_.reset();calibration_axis_.reset();measuring_=false;message_="Edit cancelled";}
        else adapters_.set_menu(false);
        return;
    }
    if(rebinding_){
        auto p=adapters_.profiles();auto* profile=selected(p);const auto action=static_cast<v1::BindingAction>(*rebinding_+1);
        for(auto& binding:*profile->mutable_bindings())if(binding.action()==action)binding.set_keysym(key);
        if(std::none_of(profile->bindings().begin(),profile->bindings().end(),[&](const auto& b){return b.action()==action;})){
            auto* b=profile->add_bindings();b->set_action(action);b->set_keysym(key);
        }
        apply(std::move(p));rebinding_.reset();return;
    }
    if(calibration_axis_){
        if(key=="Return"){
            try{calibrate(now);}
            catch(const std::invalid_argument&){measuring_=false;peak_.fill(0);throw;}
        }
        return;
    }
    if(key=="Up"){row_=row_==0?close_row:row_-1;return;}
    if(key=="Down"){row_=(row_+1)%(close_row+1);return;}
    if(key!="Left"&&key!="Right"&&key!="Return")return;
    auto p=adapters_.profiles();auto* profile=selected(p);const int direction=key=="Left"?-1:1;
    if(row_==0){
        int i=0;while(p.profiles(i).id()!=p.active_profile())++i;
        i=(i+direction+p.profiles_size())%p.profiles_size();p.set_active_profile(p.profiles(i).id());apply(std::move(p));
    }else if(row_==1){profile->set_mouse_radians_per_count(std::clamp(profile->mouse_radians_per_count()*(direction<0?1/1.2:1.2),.00001,.1));apply(std::move(p));
    }else if(row_==2){profile->set_invert_mouse_y(!profile->invert_mouse_y());apply(std::move(p));
    }else if(row_==3){profile->set_movement(profile->movement()==v1::LEVEL_MOVEMENT?v1::FREE_FLIGHT:v1::LEVEL_MOVEMENT);apply(std::move(p));
    }else if(row_>=first_binding&&row_<calibration_row&&key=="Return"){rebinding_=row_-first_binding;message_.clear();
    }else if(row_==calibration_row&&key=="Return"){calibration_axis_=0;calibration_.clear();measuring_=false;sampled_=false;message_.clear();
    }else if(row_==copy_row&&key=="Return"){
        need(p.profiles_size()<32,"The 32-profile limit is reached");
        const auto original=*profile;unsigned suffix=1;std::string id;
        do{id="variation-"+std::to_string(suffix++);}while(std::any_of(p.profiles().begin(),p.profiles().end(),[&](const auto& v){return v.id()==id;}));
        auto* copy=p.add_profiles();*copy=original;copy->set_id(id);copy->set_label("Variation "+std::to_string(suffix-1));p.set_active_profile(id);apply(std::move(p));
    }else if(row_==close_row&&key=="Return")adapters_.set_menu(false);
}
v1::ControlMenuView ControlsMenu::view() const {
    v1::ControlMenuView result;result.set_protocol_version(1);result.set_visible(adapters_.status().menu_open());
    if(!result.visible())return result;
    result.set_message(message_);result.set_prompt("Up/Down: select   Left/Right: change   Enter: edit   Esc: back");
    if(rebinding_)result.set_prompt(std::string("Press a new key for ")+names[*rebinding_]+". Esc cancels.");
    if(calibration_axis_)result.set_prompt(std::string(measuring_?directions[*calibration_axis_]:"Release the SpaceMouse cap")+". Then press Enter. Esc cancels.");
    const auto& p=adapters_.profiles();const auto& profile=active_profile(p);
    auto add=[&](std::string label,std::string value){auto* row=result.add_entries();row->set_selected(static_cast<unsigned>(result.entries_size()-1)==row_);row->set_label(std::move(label));row->set_value(std::move(value));};
    add("Preset",profile.label());std::ostringstream sensitivity;sensitivity<<std::setprecision(3)<<profile.mouse_radians_per_count()/.0025<<"x";add("Mouse sensitivity",sensitivity.str());
    add("Invert mouse Y",profile.invert_mouse_y()?"Yes":"No");add("Keyboard movement",profile.movement()==v1::LEVEL_MOVEMENT?"Level":"Flight");
    for(unsigned action=1;action<=11;++action){std::string key="Unbound";for(const auto& b:profile.bindings())if(static_cast<unsigned>(b.action())==action)key=b.keysym();add(names[action-1],key);}
    add("SpaceMouse calibration",profile.spacemouse_calibrated()?"Calibrated: Enter to replace":"Uncalibrated: Enter to measure");add("Copy to editable variation","Enter");add("Close controls","Enter");
    for(const auto value:raw_)result.add_raw_axes(value);
    return result;
}
}
