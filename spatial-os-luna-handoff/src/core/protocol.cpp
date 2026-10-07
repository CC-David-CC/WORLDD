#include "core/protocol.hpp"
#include "core/geometry.hpp"
#include "build_id.hpp"

#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl_lite.h>
#include <array>
#include <cmath>
#include <istream>
#include <ostream>
#include <stdexcept>
#include <vector>

namespace spatial {
namespace {
void require(bool valid, const char* message) {
    if (!valid) throw std::invalid_argument(message);
}
void validate_message(const google::protobuf::Message& message, int depth = 0) {
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
            }
        }
    }
}
Vec3 vector(const wire::Vec3& value) { return {value.x(), value.y(), value.z()}; }
Vec2 vector(const wire::Vec2& value) { return {value.x(), value.y()}; }
Pixels pixels(const wire::PixelExtent& value) { return {value.width(), value.height()}; }
Pose pose(const wire::Pose& value) {
    require(value.has_position() && value.has_orientation(), "Incomplete pose");
    const auto& q = value.orientation();
    Pose result{vector(value.position()), {q.x(), q.y(), q.z(), q.w()}};
    validate_pose(result);
    return result;
}
void assign(wire::Vec3* out, const Vec3& value) {
    out->set_x(value[0]); out->set_y(value[1]); out->set_z(value[2]);
}
void assign(wire::Pixel* out, const Pixels& value) { out->set_x(value[0]); out->set_y(value[1]); }
void assign(wire::Pose* out, const Pose& value) {
    assign(out->mutable_position(), value.p);
    auto* q = out->mutable_orientation();
    q->set_x(value.q[0]); q->set_y(value.q[1]); q->set_z(value.q[2]); q->set_w(value.q[3]);
}
void error(wire::Reply& reply, wire::ErrorCode code, const char* message) {
    auto* result = reply.mutable_error();
    result->set_code(code); result->set_message(message);
}
}

wire::Reply dispatch(const wire::Request& request) {
    wire::Reply reply;
    reply.set_protocol_version(1); reply.set_request_id(request.request_id());
    try {
        validate_message(request);
        if (request.protocol_version() != 1) {
            error(reply, wire::UNSUPPORTED_VERSION, "Unsupported protocol version");
            return reply;
        }
        switch (request.operation_case()) {
        case wire::Request::kHello: {
            auto* hello = reply.mutable_hello();
            hello->set_protocol("spatial-protobuf/1");
            hello->set_implementation("production");
            hello->set_build_id(SPATIAL_BUILD_ID);
            hello->add_capabilities("protocol.protobuf/v1");
            hello->add_capabilities("geometry.rigid-pose");
            hello->add_capabilities("geometry.ray-panel");
            hello->add_capabilities("motion.body-twist");
            break;
        }
        case wire::Request::kWorldGet:
            require(!request.world_get().id().empty(), "Expected a nonempty entity ID");
            error(reply, wire::UNIMPLEMENTED, "The world daemon is not implemented yet");
            break;
        case wire::Request::kTransformPoint:
        case wire::Request::kInversePoint: {
            const bool inverse = request.has_inverse_point();
            const auto& operation = inverse ? request.inverse_point() : request.transform_point();
            require(operation.has_pose() && operation.has_point(), "Missing point or pose");
            const auto result = inverse ? inverse_point(pose(operation.pose()), vector(operation.point()))
                                        : transform_point(pose(operation.pose()), vector(operation.point()));
            assign(reply.mutable_point()->mutable_point(), result);
            break;
        }
        case wire::Request::kUvPixel: {
            const auto& operation = request.uv_pixel();
            require(operation.has_uv() && operation.has_pixels(), "Missing UV or pixel extents");
            const auto result = uv_pixel(vector(operation.uv()), pixels(operation.pixels()));
            auto* output = reply.mutable_pixel();
            if (result) assign(output->mutable_pixel(), *result);
            break;
        }
        case wire::Request::kRayPanel: {
            const auto& operation = request.ray_panel();
            require(operation.has_origin() && operation.has_direction() && operation.has_pose() &&
                    operation.has_size() && operation.has_pixels(), "Missing ray-panel parameters");
            const auto result = ray_panel(vector(operation.origin()), vector(operation.direction()),
                                          pose(operation.pose()), vector(operation.size()), pixels(operation.pixels()));
            auto* output = reply.mutable_ray();
            if (result) {
                auto* hit = output->mutable_hit();
                hit->mutable_uv()->set_x(result->uv[0]); hit->mutable_uv()->set_y(result->uv[1]);
                assign(hit->mutable_pixel(), result->pixel); hit->set_distance(result->distance);
            }
            break;
        }
        case wire::Request::kIntegrate: {
            const auto& operation = request.integrate();
            require(operation.has_pose() && operation.has_linear() && operation.has_angular() &&
                    operation.has_seconds(), "Missing motion parameters");
            assign(reply.mutable_pose()->mutable_pose(), integrate(pose(operation.pose()), vector(operation.linear()),
                                                                  vector(operation.angular()), operation.seconds()));
            break;
        }
        case wire::Request::OPERATION_NOT_SET:
            error(reply, wire::UNKNOWN_METHOD, "No recognized operation");
            break;
        }
    } catch (const std::invalid_argument& exception) {
        error(reply, wire::INVALID_ARGUMENT, exception.what());
    } catch (const std::exception&) {
        error(reply, wire::INTERNAL_ERROR, "Request failed internally");
    }
    return reply;
}

wire::Reply handle_frame(std::string_view frame) {
    wire::Request request;
    if (frame.size() <= max_frame_bytes) {
        google::protobuf::io::ArrayInputStream array(frame.data(), static_cast<int>(frame.size()));
        google::protobuf::io::CodedInputStream input(&array);
        input.SetTotalBytesLimit(static_cast<int>(max_frame_bytes));
        input.SetRecursionLimit(64);
        if (request.ParseFromCodedStream(&input) && input.ConsumedEntireMessage()) return dispatch(request);
    }
    wire::Reply reply;
    reply.set_protocol_version(1);
    error(reply, wire::INVALID_ARGUMENT, "Malformed protobuf payload");
    return reply;
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

void write_frame(std::ostream& output, const wire::Reply& reply) {
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
