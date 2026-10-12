#pragma once
#include "issuer.pb.h"

namespace spatial::identity {
void validate_bootstrap_profile(const v1::BootstrapProfile&);
void validate_bootstrap_read(const v1::BootstrapProfileRead&, bool exact);
bool bootstrap_has_administration(const v1::BootstrapProfile&);
}
