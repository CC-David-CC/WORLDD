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
Bool SpatialSecurityPrivileged(ClientPtr client);
Bool SpatialSecuritySameApplication(ClientPtr client, ClientPtr owner);
Bool SpatialSecurityAtomVisible(ClientPtr client, Atom atom);
Atom SpatialSecurityInternAtom(ClientPtr client, const char *name,
                              unsigned length, Bool create);
Bool SpatialSecurityVisibleResource(ClientPtr client, XID id);
struct Spatial__Security__V1__ApplicationIdentity;
Bool SpatialSecurityApplicationIdentity(ClientPtr client, XID id,
                                        struct Spatial__Security__V1__ApplicationIdentity *identity);
void SpatialSecurityPolicyInit(void);
void SpatialSecurityPolicyReset(void);
#endif
