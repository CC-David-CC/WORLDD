#pragma once
#include "control/adapters.hpp"
#include <functional>
#include <optional>
#include <map>
#include <set>
#include <vector>

namespace spatial::control {
// Host preferences only. This object never receives credentials or native input.
class ControlsMenu final {
public:
    using Persist = std::function<void(const v1::ControlProfiles&)>;
    ControlsMenu(DeviceAdapters& adapters, Persist persist);
    // Call after DeviceAdapters::event. An opening menu key cannot edit a row.
    void event(const v1::DeviceEvent&, DeviceAdapters::Clock::time_point now, bool previously_open, bool fresh = true);
    void event(const v1::DeviceEvent&, DeviceAdapters::Clock::time_point captured,
               DeviceAdapters::Clock::time_point now, bool previously_open, bool fresh);
    v1::ControlMenuView view() const;
private:
    void key(const std::string&, DeviceAdapters::Clock::time_point);
    void apply(v1::ControlProfiles);
    v1::ControlProfile* selected(v1::ControlProfiles&);
    void calibrate(DeviceAdapters::Clock::time_point);
    DeviceAdapters& adapters_;
    Persist persist_;
    unsigned row_ = 0;
    std::optional<unsigned> rebinding_;
    std::optional<unsigned> calibration_axis_;
    bool measuring_ = false;
    std::array<double,6> raw_{}, center_{}, peak_{};
    std::vector<v1::AxisCalibration> calibration_;
    DeviceAdapters::Clock::time_point sample_time_{};
    bool sampled_ = false;
    std::string message_;
    std::map<std::string,std::set<std::string>> physical_keys_;
};
}
