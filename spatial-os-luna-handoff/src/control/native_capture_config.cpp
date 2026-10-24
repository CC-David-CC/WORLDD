#include "control/native_capture_config.hpp"
#include "control/physical_source.hpp"
#include "core/protocol.hpp"
#include <set>
#include <stdexcept>
namespace spatial::control {
void validate_native_capture(const v1::NativeCaptureConfig& config) {
    validate_message(config);
    if (config.physical_sources_size()>32 || (!config.spacemouse() && config.physical_sources().empty()))
        throw std::invalid_argument("Capture requires explicit bounded native sources");
    std::set<dev_t> devices;bool keyboard=false,mouse=false;
    for(const auto& source:config.physical_sources()) {
        if(source.device_node().empty() || source.device_node().size()>4096 || source.device_node().find('\0')!=std::string::npos)
            throw std::invalid_argument("Invalid physical source path");
        const auto identity=physical_detail::kernel_identity(source.device_node(),source.kind());
        if(!identity || !devices.insert(identity->device).second)
            throw std::invalid_argument("Physical source must identify a distinct matching kernel device");
        keyboard=keyboard || source.kind()==v1::KEYBOARD;mouse=mouse || source.kind()==v1::MOUSE;
    }
    if(!config.physical_sources().empty() && (!keyboard || !mouse))
        throw std::invalid_argument("Native capture requires both physical keyboard and mouse");
}
}
