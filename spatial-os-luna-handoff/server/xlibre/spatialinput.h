#ifndef SPATIAL_INPUT_H
#define SPATIAL_INPUT_H
#include "inputstr.h"
#include "eventstr.h"
#include "windowstr.h"
void SpatialInputExtensionInit(void);
/* NULL means an ordinary device. An owned device always has an explicit route. */
WindowPtr SpatialInputRoute(DeviceIntPtr device, DeviceEvent *event);
#endif
