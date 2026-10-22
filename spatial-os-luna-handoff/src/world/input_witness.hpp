#pragma once
namespace spatial {
class Actors;
class WorldStore;
class RenderWorker;
// One launch-parent-owned qualification channel. No public actor entrypoint.
class InputWitness final {
public:
    explicit InputWitness(int owned_fd);
    ~InputWitness();
    InputWitness(const InputWitness&) = delete;
    InputWitness& operator=(const InputWitness&) = delete;
    void pump(WorldStore&, Actors&, RenderWorker*);
private:
    void close_channel();
    int fd_ = -1;
    int parent_ = -1;
    bool consumed_ = false;
};
}
