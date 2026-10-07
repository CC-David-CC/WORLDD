#pragma once
#include "world/actors.hpp"

namespace spatial {
class IdentitySessions;
// Bounded management I/O does not suspend the production monotonic scheduler.
void serve(WorldStore& world, Actors& actors, RenderWorker* renderer = nullptr, Surfaces* surfaces = nullptr, BrokerClient* broker = nullptr,
           IdentitySessions* sessions = nullptr);
}
