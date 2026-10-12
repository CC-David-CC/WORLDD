#pragma once
namespace spatial {
// Shared application protocol and child lifecycle. The caller supplies the actual boundary.
int supervise_application(int state_directory);
}
