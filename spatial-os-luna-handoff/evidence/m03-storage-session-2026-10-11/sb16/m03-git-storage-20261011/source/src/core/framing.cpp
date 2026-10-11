#include "core/protocol.hpp"
#include <array>
#include <cmath>
#include <istream>
#include <ostream>
#include <stdexcept>
#include <vector>
namespace spatial {
namespace {
void require(bool value, const char* message) {
    if (!value) throw std::invalid_argument(message);
}
}
void validate_message(const google::protobuf::Message& message, int depth) {
    require(depth <= 64, "Message nesting exceeds limit");
    const auto* reflection = message.GetReflection();
    require(reflection->GetUnknownFields(message).empty(), "Unknown protobuf fields");
    std::vector<const google::protobuf::FieldDescriptor*> fields;
    reflection->ListFields(message, &fields);
    for (const auto* field : fields) {
        const int count = field->is_repeated() ? reflection->FieldSize(message, field) : 1;
        for (int i = 0; i < count; ++i) {
            if (field->cpp_type() == google::protobuf::FieldDescriptor::CPPTYPE_MESSAGE) {
                const auto& child = field->is_repeated()
                    ? reflection->GetRepeatedMessage(message, field, i) : reflection->GetMessage(message, field);
                validate_message(child, depth + 1);
            } else if (field->cpp_type() == google::protobuf::FieldDescriptor::CPPTYPE_DOUBLE) {
                const double value = field->is_repeated()
                    ? reflection->GetRepeatedDouble(message, field, i) : reflection->GetDouble(message, field);
                require(std::isfinite(value), "Non-finite protobuf number");
            } else if (field->cpp_type() == google::protobuf::FieldDescriptor::CPPTYPE_ENUM) {
                const int value = field->is_repeated() ? reflection->GetRepeatedEnumValue(message, field, i)
                                                       : reflection->GetEnumValue(message, field);
                require(field->enum_type()->FindValueByNumber(value) != nullptr, "Unknown protobuf enum value");
            }
        }
    }
}
bool read_frame(std::istream& input, std::string& frame) {
    std::array<unsigned char, 4> header{};
    input.read(reinterpret_cast<char*>(header.data()), header.size());
    if (input.gcount() == 0 && input.eof()) return false;
    require(input.gcount() == 4, "Truncated frame header");
    const auto length = (std::uint32_t(header[0]) << 24) | (std::uint32_t(header[1]) << 16) |
                        (std::uint32_t(header[2]) << 8) | std::uint32_t(header[3]);
    require(length <= max_frame_bytes, "Frame exceeds size limit");
    frame.resize(length);
    input.read(frame.data(), length);
    require(input.gcount() == length, "Truncated frame payload");
    return true;
}

void write_frame(std::ostream& output, const google::protobuf::Message& reply) {
    const auto payload = reply.SerializeAsString();
    require(payload.size() <= max_frame_bytes, "Reply exceeds size limit");
    const auto length = static_cast<std::uint32_t>(payload.size());
    const std::array<unsigned char, 4> header{static_cast<unsigned char>(length >> 24),
        static_cast<unsigned char>(length >> 16), static_cast<unsigned char>(length >> 8),
        static_cast<unsigned char>(length)};
    output.write(reinterpret_cast<const char*>(header.data()), header.size());
    output.write(payload.data(), payload.size());
    output.flush();
    if (!output) throw std::runtime_error("Cannot write protobuf reply");
}
} // namespace spatial
