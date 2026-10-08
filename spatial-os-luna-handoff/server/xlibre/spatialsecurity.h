/* SPDX-License-Identifier: MIT */
#ifndef SPATIAL_SECURITY_H
#define SPATIAL_SECURITY_H
#include "dix.h"

void SpatialSecurityConfigure(const char *descriptor);
Bool SpatialSecurityEnabled(void);
const char *SpatialSecurityAuthorize(ClientPtr client, unsigned name_size,
                                     const char *name, unsigned cookie_size,
                                     const char *cookie);
void SpatialSecurityExtensionInit(void);
#endif
