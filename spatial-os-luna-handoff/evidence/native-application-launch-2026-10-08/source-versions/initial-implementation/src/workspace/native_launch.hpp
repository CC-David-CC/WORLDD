#pragma once
#include <functional>
#include <memory>
#include <string>

namespace spatial {
class NativeLaunch;
class NativeCredential {
public:
    ~NativeCredential();
    NativeCredential(const NativeCredential&) = delete;
    NativeCredential& operator=(const NativeCredential&) = delete;
    int authority_fd() const { return authority_fd_; }
    int socket_fd() const;
    const std::string& display_number() const;
private:
    friend class NativeLaunch;
    NativeCredential(NativeLaunch& owner, std::string id, int authority_fd);
    NativeLaunch& owner_;
    std::string id_;
    int authority_fd_;
};

// The owner outlives every application credential. A broken channel fails closed.
class NativeLaunch {
public:
    explicit NativeLaunch(int sealed_config_fd);
    ~NativeLaunch();
    NativeLaunch(const NativeLaunch&) = delete;
    NativeLaunch& operator=(const NativeLaunch&) = delete;
    std::unique_ptr<NativeCredential> issue(const std::string& workspace, const std::string& application,
                                          const std::function<void()>& checkpoint);
    void check();
private:
    friend class NativeCredential;
    void revoke(const std::string& id) noexcept;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
