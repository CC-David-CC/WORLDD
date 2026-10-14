#pragma once
#include <sys/types.h>
namespace spatial {
class Renderer;
// Takes the inherited private control descriptor. Worlds never receive it.
void serve_presentations(Renderer&,int control_fd,pid_t owner);
}
