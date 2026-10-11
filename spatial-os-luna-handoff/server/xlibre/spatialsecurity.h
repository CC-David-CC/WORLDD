/* SPDX-License-Identifier: MIT */
#ifndef SPATIAL_SECURITY_H
#define SPATIAL_SECURITY_H
#include "dix.h"
#include <stdint.h>

// Server-local private object label. Transport uses SelectionScope protobuf.
#define SPATIAL_APPLICATION_LABEL_BYTES 256
typedef struct {
    size_t workspace_size, application_size;
    uint8_t workspace[SPATIAL_APPLICATION_LABEL_BYTES];
    uint8_t application[SPATIAL_APPLICATION_LABEL_BYTES];
} SpatialSelectionNamespace;

void SpatialSecurityConfigure(const char *descriptor);
Bool SpatialSecurityEnabled(void);
const char *SpatialSecurityAuthorize(ClientPtr client, unsigned name_size,
                                     const char *name, unsigned cookie_size,
                                     const char *cookie);
void SpatialSecurityExtensionInit(void);
Bool SpatialSecurityPrivileged(ClientPtr client);
Bool SpatialSecuritySameApplication(ClientPtr client, ClientPtr owner);
Bool SpatialSecuritySelectionNamespace(ClientPtr client, SpatialSelectionNamespace *label);
Bool SpatialSecurityAtomVisible(ClientPtr client, Atom atom);
Atom SpatialSecurityInternAtom(ClientPtr client, const char *name,
                              unsigned length, Bool create);
Bool SpatialSecurityVisibleResource(ClientPtr client, XID id);
struct Spatial__Security__V1__ApplicationIdentity;
Bool SpatialSecurityApplicationIdentity(ClientPtr client, XID id,
                                        struct Spatial__Security__V1__ApplicationIdentity *identity);
void SpatialSecurityPolicyInit(void);
void SpatialSecurityPolicyReset(void);
Bool SpatialSecurityFilterEvent(ClientPtr client, const xEvent *event, xEvent *filtered);
#endif
