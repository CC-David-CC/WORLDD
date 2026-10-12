#include "render/presentation_inputs.hpp"
#include "x11/input.hpp"
#include "x11/input_router.hpp"
#include "world/store.hpp"
#include <map>
#include <unistd.h>
namespace spatial {
using Binding = render::v1::WorldPresentationBinding;
void PresentationInputs::validate_binding(const Binding& binding) {
    validate_message(binding);
    if (binding.protocol_version() != 1 || binding.world_id().empty() || binding.world_id().size() > 256 ||
        binding.world_id().find('\0') != std::string::npos || binding.service_incarnation().size() != 32 ||
        !binding.authority_generation() || !binding.presentation_generation())
        throw std::invalid_argument("Invalid world presentation binding");
}
namespace {
struct Descriptor {
    int value;
    ~Descriptor() { if (value >= 0) close(value); }
    int release() { int result=value; value=-1; return result; }
};
}
struct PresentationInputs::Impl {
    struct Entry {
        Binding binding;
        std::unique_ptr<NativeInput> native;
        std::unique_ptr<XInputRouter> router;
    };
    Display* display;
    XSurfaces& surfaces;
    std::string server_id;
    std::uint64_t high_water = 0;
    std::map<std::uint64_t, Entry> entries;
    Impl(Display* display, XSurfaces& surfaces, std::string server_id)
        : display(display), surfaces(surfaces), server_id(std::move(server_id)) {}
    Entry& find(const Binding& binding) {
        PresentationInputs::validate_binding(binding);
        const auto found = entries.find(binding.presentation_generation());
        if (found == entries.end() || found->second.binding.SerializeAsString() != binding.SerializeAsString())
            throw WorldError(wire::STALE_TARGET, "World presentation binding is retired or mismatched");
        return found->second;
    }
};
PresentationInputs::PresentationInputs(Display* display, XSurfaces& surfaces, std::string server_id)
    : impl_(std::make_unique<Impl>(display,surfaces,std::move(server_id))) {}
PresentationInputs::~PresentationInputs() = default;
void PresentationInputs::bind(const Binding& binding, int descriptor) {
    if (descriptor <= STDERR_FILENO) throw std::invalid_argument("Expected private scoped input descriptor");
    Descriptor owned{descriptor};
    validate_binding(binding);
    auto& state=*impl_;
    if (state.server_id.size()!=32) throw WorldError(wire::FORBIDDEN, "Scoped input requires authenticated graphics");
    if (binding.presentation_generation() <= state.high_water)
        throw WorldError(wire::STALE_TARGET, "Presentation generation cannot be reused");
    if (state.entries.size() >= 256) throw WorldError(wire::IN_USE, "Too many world presentation bindings");
    Impl::Entry entry; entry.binding=binding;
    entry.native.reset(new NativeInput(owned.release()));
    if (entry.native->server_id()!=state.server_id)
        throw WorldError(wire::FORBIDDEN, "Input and graphics belong to different native servers");
    entry.router=std::make_unique<XInputRouter>(state.display,*entry.native,state.surfaces);
    state.entries.emplace(binding.presentation_generation(),std::move(entry));
    state.high_water=binding.presentation_generation();
}
void PresentationInputs::reserve(const Binding& binding) {
    validate_binding(binding);
    auto& state=*impl_;
    if (state.server_id.size()!=32) throw WorldError(wire::FORBIDDEN, "Scoped input requires authenticated graphics");
    if (binding.presentation_generation()<=state.high_water)
        throw WorldError(wire::STALE_TARGET, "Presentation generation cannot be reused");
    if (state.entries.size()>=256) throw WorldError(wire::IN_USE, "Too many world presentation bindings");
    Impl::Entry entry;entry.binding=binding;
    state.entries.emplace(binding.presentation_generation(),std::move(entry));
    state.high_water=binding.presentation_generation();
}
void PresentationInputs::initialize(const Binding& binding,int descriptor) {
    if (descriptor<=STDERR_FILENO) throw std::invalid_argument("Expected private scoped input descriptor");
    Descriptor owned{descriptor};auto& state=*impl_;auto& entry=state.find(binding);
    if (entry.native) throw WorldError(wire::IN_USE, "World presentation input is already initialized");
    std::unique_ptr<NativeInput> native(new NativeInput(owned.release()));
    if (native->server_id()!=state.server_id)
        throw WorldError(wire::FORBIDDEN, "Input and graphics belong to different native servers");
    auto router=std::make_unique<XInputRouter>(state.display,*native,state.surfaces);
    entry.native=std::move(native);entry.router=std::move(router);
}
void PresentationInputs::retire(const Binding& binding) {
    auto& entry=impl_->find(binding);
    if (entry.router) entry.router->close_all();
    impl_->entries.erase(binding.presentation_generation());
}
wire::NativeActorInput PresentationInputs::input(const Binding& binding, const wire::RendererInputCommand& command) {
    auto& entry=impl_->find(binding);
    if (!entry.router) throw WorldError(wire::UNAVAILABLE, "World presentation input is not initialized");
    return entry.router->execute(command);
}
void PresentationInputs::snapshot(const Binding& binding, wire::NativeSnapshot& result) {
    auto& entry=impl_->find(binding);
    if (!entry.router) throw WorldError(wire::UNAVAILABLE, "World presentation input is not initialized");
    entry.router->snapshot(result);
}
}
