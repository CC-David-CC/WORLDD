#pragma once
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>
#include <sys/types.h>
namespace spatial {
// Private local protobuf framing with authenticated senders and bounded descriptor handoff.
class PresentationStream final {
public:
    struct File {
        int value = -1;
        explicit File(int value = -1) : value(value) {}
        ~File();
        File(const File&) = delete;
        File& operator=(const File&) = delete;
        File(File&&) noexcept;
        File& operator=(File&&) noexcept;
        int release();
    };
    struct Frame { std::string bytes; std::vector<File> descriptors; };
    // Takes ownership. The socket creator and actual message sender are checked separately.
    PresentationStream(int fd, pid_t creator, pid_t sender);
    ~PresentationStream();
    PresentationStream(const PresentationStream&) = delete;
    PresentationStream& operator=(const PresentationStream&) = delete;
    int descriptor() const;
    bool pending_output() const;
    void send(std::string bytes, const std::vector<int>& borrowed_descriptors = {});
    void flush();
    std::optional<Frame> receive();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
