cmake_minimum_required(VERSION 3.22)
project(spatial_runtime VERSION 0.1.0 LANGUAGES C CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "Build the runtime on sb16 or sb16-2 with Linux.")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
find_package(Protobuf 3.21 REQUIRED)
find_package(SQLite3 REQUIRED)
find_package(OpenSSL 3.0 REQUIRED COMPONENTS Crypto SSL)
find_package(Threads REQUIRED)
find_package(X11 REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(XCB REQUIRED IMPORTED_TARGET xcb)
find_package(OpenGL REQUIRED)
find_package(GLEW REQUIRED)
configure_file(config/conformance.textproto runtime.textproto COPYONLY)
set(Protobuf_IMPORT_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1")
list(APPEND Protobuf_IMPORT_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1")
protobuf_generate_cpp(presentation_probe_sources presentation_probe_headers tests/proto/presentation_probe.proto)
protobuf_generate_cpp(presentation_proto_sources presentation_proto_headers proto/spatial/render/v1/presentation.proto)
protobuf_generate_cpp(compositor_proto_sources compositor_proto_headers proto/spatial/render/v1/compositor_native.proto)
protobuf_generate_cpp(proto_sources proto_headers proto/spatial/core/v1/core.proto)
protobuf_generate_cpp(input_proto_sources input_proto_headers proto/spatial/input/v1/input.proto)
protobuf_generate_cpp(security_proto_sources security_proto_headers proto/spatial/security/v1/security.proto)
protobuf_generate_cpp(selection_scope_proto_sources selection_scope_proto_headers proto/spatial/security/v1/selection_scope.proto)
protobuf_generate_cpp(native_scope_proto_sources native_scope_proto_headers proto/spatial/supervisor/v1/native_scope.proto)
protobuf_generate_cpp(broker_proto_sources broker_proto_headers proto/spatial/workspace/v1/broker.proto)
protobuf_generate_cpp(storage_proto_sources storage_proto_headers proto/spatial/workspace/v1/storage.proto)
protobuf_generate_cpp(key_proto_sources key_proto_headers proto/spatial/identity/v1/key.proto)
protobuf_generate_cpp(issuer_proto_sources issuer_proto_headers proto/spatial/identity/v1/issuer.proto)
protobuf_generate_cpp(world_session_proto_sources world_session_proto_headers proto/spatial/identity/v1/world_session.proto)
protobuf_generate_cpp(grants_proto_sources grants_proto_headers proto/spatial/authority/v1/grants.proto)
protobuf_generate_cpp(scope_proto_sources scope_proto_headers proto/spatial/supervisor/v1/process_scope.proto)
protobuf_generate_cpp(launch_proto_sources launch_proto_headers proto/spatial/supervisor/v1/launch.proto)
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/python")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/compositor_native_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1/compositor_native.proto
    DEPENDS proto/spatial/render/v1/compositor_native.proto protobuf::protoc VERBATIM)
add_custom_target(compositor-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/compositor_native_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1/presentation.proto
    DEPENDS proto/spatial/render/v1/presentation.proto proto/spatial/render/v1/compositor_native.proto
        proto/spatial/core/v1/core.proto protobuf::protoc VERBATIM)
add_custom_target(presentation-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_pb2.py")

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_probe_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/presentation_probe.proto
    DEPENDS tests/proto/presentation_probe.proto proto/spatial/render/v1/compositor_native.proto
        proto/spatial/core/v1/core.proto protobuf::protoc VERBATIM)
add_custom_target(presentation-probe-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_probe_pb2.py")

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/launch_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/launch.proto
    DEPENDS proto/spatial/workspace/v1/broker.proto proto/spatial/workspace/v1/storage.proto proto/spatial/supervisor/v1/launch.proto proto/spatial/supervisor/v1/process_scope.proto
        proto/spatial/supervisor/v1/native_scope.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(launch-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/launch_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/process_scope_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/process_scope.proto
    DEPENDS proto/spatial/supervisor/v1/process_scope.proto protobuf::protoc VERBATIM)
add_custom_target(scope-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/process_scope_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/core_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1/core.proto
    DEPENDS proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/core_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/input_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/input/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/input/v1/input.proto
    DEPENDS proto/spatial/input/v1/input.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(input-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/input_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/security_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1/security.proto
    DEPENDS proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(security-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/security_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/selection_scope_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1/selection_scope.proto
    DEPENDS proto/spatial/security/v1/selection_scope.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(selection-scope-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/selection_scope_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/native_scope_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/native_scope.proto
    DEPENDS proto/spatial/supervisor/v1/native_scope.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(native-scope-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/native_scope_pb2.py")

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/broker.proto
    DEPENDS proto/spatial/supervisor/v1/process_scope.proto proto/spatial/supervisor/v1/native_scope.proto proto/spatial/workspace/v1/storage.proto proto/spatial/workspace/v1/broker.proto proto/spatial/core/v1/core.proto proto/spatial/authority/v1/grants.proto
        proto/spatial/identity/v1/issuer.proto proto/spatial/identity/v1/key.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(broker-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/storage_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/storage.proto
    DEPENDS proto/spatial/workspace/v1/storage.proto proto/spatial/supervisor/v1/process_scope.proto proto/spatial/authority/v1/grants.proto
        protobuf::protoc VERBATIM)
add_custom_target(storage-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/storage_pb2.py")
add_dependencies(storage-python-protobuf grants-python-protobuf python-protobuf security-python-protobuf scope-python-protobuf)
add_dependencies(broker-python-protobuf storage-python-protobuf)
add_dependencies(launch-python-protobuf broker-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/key_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/key.proto
    DEPENDS proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(key-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/key_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/issuer_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/issuer.proto
    DEPENDS proto/spatial/identity/v1/issuer.proto proto/spatial/authority/v1/grants.proto proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(issuer-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/issuer_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/world_session_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/world_session.proto
    DEPENDS proto/spatial/identity/v1/world_session.proto proto/spatial/authority/v1/grants.proto proto/spatial/identity/v1/issuer.proto
        proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(world-session-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/world_session_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/grants_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1/grants.proto
    DEPENDS proto/spatial/authority/v1/grants.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(grants-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/grants_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/crosshost_revocation_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/crosshost_revocation.proto
    DEPENDS tests/proto/crosshost_revocation.proto proto/spatial/identity/v1/issuer.proto
        proto/spatial/supervisor/v1/launch.proto proto/spatial/workspace/v1/broker.proto protobuf::protoc VERBATIM)
add_custom_target(crosshost-revocation-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/crosshost_revocation_pb2.py")
add_dependencies(crosshost-revocation-python-protobuf issuer-python-protobuf launch-python-protobuf)

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/world_topology_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/world_topology.proto
    DEPENDS tests/proto/world_topology.proto protobuf::protoc VERBATIM)
add_custom_target(world-topology-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/world_topology_pb2.py")

option(SPATIAL_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)
option(SPATIAL_INPUT_TESTS "Require the project XLibre actor-input counterexamples" OFF)
add_compile_options(-Wall -Wextra -Wpedantic -Werror)
if(SPATIAL_SANITIZERS)
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer)
    add_link_options(-fsanitize=address,undefined)
endif()

# Include all production inputs and build settings in the reported identity.
# Reconfigure on edits as well as newly added sources. Never report only HEAD
# for a binary that may contain uncommitted source changes.
file(GLOB_RECURSE build_inputs CONFIGURE_DEPENDS src/*.cpp src/*.hpp proto/*.proto config/*.textproto)
list(APPEND build_inputs "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
list(SORT build_inputs)
set(build_material "${CMAKE_CXX_COMPILER_ID}:${CMAKE_CXX_COMPILER_VERSION}:${CMAKE_BUILD_TYPE}:${CMAKE_CXX_FLAGS}:${CMAKE_CXX_FLAGS_DEBUG}:${CMAKE_CXX_FLAGS_RELEASE}:${CMAKE_CXX_FLAGS_RELWITHDEBINFO}:${SPATIAL_SANITIZERS}:${Protobuf_VERSION}:${SQLite3_VERSION}:${OPENSSL_VERSION}")
foreach(input IN LISTS build_inputs)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${input}")
    file(SHA256 "${input}" input_hash)
    file(RELATIVE_PATH input_name "${CMAKE_CURRENT_SOURCE_DIR}" "${input}")
    string(APPEND build_material "\n${input_name}:${input_hash}")
endforeach()
string(SHA256 build_hash "${build_material}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/build_id.hpp" "#pragma once\n#define SPATIAL_BUILD_ID \"sha256:${build_hash}\"\n")

add_library(spatial-protobuf STATIC ${presentation_proto_sources} ${presentation_proto_headers} ${compositor_proto_sources} ${compositor_proto_headers} ${native_scope_proto_sources} ${native_scope_proto_headers} ${proto_sources} ${proto_headers} ${broker_proto_sources} ${broker_proto_headers} ${key_proto_sources} ${key_proto_headers} ${issuer_proto_sources} ${issuer_proto_headers} ${world_session_proto_sources} ${world_session_proto_headers} ${grants_proto_sources} ${grants_proto_headers} ${security_proto_sources} ${security_proto_headers} ${selection_scope_proto_sources} ${selection_scope_proto_headers})
target_link_libraries(spatial-protobuf PUBLIC protobuf::libprotobuf)
target_sources(spatial-protobuf PRIVATE ${scope_proto_sources} ${scope_proto_headers})
target_sources(spatial-protobuf PRIVATE ${storage_proto_sources} ${storage_proto_headers})
target_include_directories(spatial-protobuf PUBLIC "${CMAKE_CURRENT_BINARY_DIR}")
add_library(spatial-scene STATIC src/x11/native_channel.cpp src/core/framing.cpp src/core/geometry.cpp src/core/collision.cpp src/core/navigation.cpp src/core/scene.cpp)
target_include_directories(spatial-scene PUBLIC src)
target_link_libraries(spatial-scene PUBLIC spatial-protobuf)
add_library(spatial-presentation-stream STATIC src/platform/presentation_stream.cpp)
target_include_directories(spatial-presentation-stream PUBLIC src)
target_link_libraries(spatial-presentation-stream PUBLIC spatial-scene)
add_executable(spatial-presentation-stream-probe src/testing/presentation_stream_probe.cpp)
target_link_libraries(spatial-presentation-stream-probe PRIVATE spatial-presentation-stream)
add_library(spatial-control-reply STATIC src/supervisor/control_reply.cpp)
target_link_libraries(spatial-control-reply PUBLIC spatial-scene)
add_executable(spatial-control-reply-probe src/testing/control_reply_probe.cpp)
target_link_libraries(spatial-control-reply-probe PRIVATE spatial-control-reply)
add_dependencies(spatial-control-reply-probe broker-python-protobuf scope-python-protobuf native-scope-python-protobuf security-python-protobuf python-protobuf issuer-python-protobuf key-python-protobuf grants-python-protobuf)
add_library(spatial-compositor-config STATIC src/platform/compositor_config.cpp)
target_link_libraries(spatial-compositor-config PUBLIC spatial-scene)
add_library(spatial-identity STATIC src/identity/keys.cpp)
add_library(spatial-process-scope STATIC src/supervisor/process_scope.cpp)
target_link_libraries(spatial-process-scope PUBLIC spatial-scene)
add_library(spatial-storage-channel STATIC src/workspace/storage_channel.cpp)
target_link_libraries(spatial-storage-channel PUBLIC spatial-scene)
add_library(spatial-storage-client STATIC src/workspace/storage_client.cpp)
target_link_libraries(spatial-storage-client PUBLIC spatial-storage-channel)
add_library(spatial-storage-devices STATIC src/workspace/storage_devices.cpp)
target_link_libraries(spatial-storage-devices PUBLIC spatial-scene)
add_library(spatial-storage-owner STATIC src/workspace/storage_owner.cpp)
target_link_libraries(spatial-storage-owner PUBLIC spatial-scene spatial-storage-devices SQLite::SQLite3)
add_executable(spatial-storage-owner-service src/workspace/storage_main.cpp)
set_target_properties(spatial-storage-owner-service PROPERTIES OUTPUT_NAME spatial-storage-owner)
target_link_libraries(spatial-storage-owner-service PRIVATE spatial-storage-owner spatial-storage-channel)
add_library(spatial-workspace-resources STATIC src/workspace/resources.cpp)
target_link_libraries(spatial-workspace-resources PUBLIC spatial-process-scope spatial-identity-tls spatial-storage-devices)
add_library(spatial-storage-launch STATIC src/supervisor/storage_launch.cpp)
target_link_libraries(spatial-storage-launch PUBLIC spatial-process-scope spatial-identity-tls)
add_executable(spatial-storage-launch-probe src/testing/storage_launch_probe.cpp)
target_link_libraries(spatial-storage-launch-probe PRIVATE spatial-storage-launch)
add_dependencies(spatial-storage-launch-probe storage-python-protobuf)
add_library(spatial-authority-launch STATIC src/supervisor/launch.cpp src/supervisor/child_handle.cpp src/supervisor/child_handle_client.cpp src/workspace/native_launch.cpp ${launch_proto_sources} ${launch_proto_headers})
target_link_libraries(spatial-authority-launch PUBLIC spatial-process-scope spatial-control-reply SQLite::SQLite3)
add_executable(spatial-authority-launch-probe src/testing/authority_launch_probe.cpp)
add_executable(spatial-authority-supervisor src/supervisor/main.cpp)
target_link_libraries(spatial-authority-supervisor PRIVATE spatial-authority-launch spatial-workspace-resources spatial-storage-launch)
target_link_libraries(spatial-authority-launch-probe PRIVATE spatial-authority-launch)
add_dependencies(spatial-authority-launch-probe broker-python-protobuf launch-python-protobuf scope-python-protobuf native-scope-python-protobuf security-python-protobuf python-protobuf issuer-python-protobuf key-python-protobuf grants-python-protobuf)
add_executable(spatial-process-scope-probe src/testing/process_scope_probe.cpp)
target_link_libraries(spatial-process-scope-probe PRIVATE spatial-process-scope)
add_dependencies(spatial-process-scope-probe scope-python-protobuf python-protobuf security-python-protobuf)
target_link_libraries(spatial-identity PUBLIC spatial-scene OpenSSL::Crypto)
add_executable(spatial-key src/identity/key_main.cpp)
target_link_libraries(spatial-key PRIVATE spatial-identity)
add_library(spatial-identity-tls STATIC src/identity/tls_support.cpp)
target_link_libraries(spatial-identity-tls PUBLIC spatial-identity OpenSSL::SSL)
add_library(spatial-issuer-client-lib STATIC src/identity/issuer_client.cpp)
target_link_libraries(spatial-issuer-client-lib PUBLIC spatial-identity-tls)
add_executable(spatial-issuer-client src/identity/client_main.cpp)
target_link_libraries(spatial-issuer-client PRIVATE spatial-issuer-client-lib)
add_executable(spatial-revoke src/identity/revoke_main.cpp)
target_link_libraries(spatial-revoke PRIVATE spatial-issuer-client-lib)
add_library(spatial-participant-runtime-lib STATIC src/identity/participant_runtime.cpp)
target_link_libraries(spatial-participant-runtime-lib PUBLIC spatial-issuer-client-lib Threads::Threads)
target_link_libraries(spatial-authority-launch PUBLIC spatial-participant-runtime-lib)
add_executable(spatial-participant-runtime-probe src/testing/participant_runtime_probe.cpp)
target_link_libraries(spatial-participant-runtime-probe PRIVATE spatial-participant-runtime-lib)
add_library(spatial-issuer-watch-lib STATIC src/identity/issuer_watch.cpp)
target_link_libraries(spatial-issuer-watch-lib PUBLIC spatial-issuer-client-lib Threads::Threads)
add_executable(spatial-issuer-watch src/identity/watch_main.cpp)
target_link_libraries(spatial-issuer-watch PRIVATE spatial-issuer-watch-lib)
add_library(spatial-issuer STATIC src/identity/issuer.cpp src/identity/protocol.cpp src/identity/tls_server.cpp)
add_library(spatial-authority STATIC src/authority/store.cpp)
target_link_libraries(spatial-authority PUBLIC spatial-scene SQLite::SQLite3 OpenSSL::Crypto)
add_executable(spatial-authority-migrate src/authority/migrate_main.cpp)
target_link_libraries(spatial-authority-migrate PRIVATE spatial-authority)
add_library(spatial-bootstrap STATIC src/identity/bootstrap.cpp)
target_link_libraries(spatial-bootstrap PUBLIC spatial-authority)
target_link_libraries(spatial-issuer-client-lib PUBLIC spatial-bootstrap)
target_link_libraries(spatial-issuer PUBLIC spatial-identity-tls spatial-bootstrap SQLite::SQLite3)
add_executable(identityd src/identity/issuer_main.cpp)
target_link_libraries(identityd PRIVATE spatial-issuer)
add_library(spatial-native-input STATIC src/x11/input.cpp ${input_proto_sources} ${input_proto_headers})
target_link_libraries(spatial-native-input PUBLIC spatial-scene spatial-compositor-config X11::X11)
add_library(spatial-core STATIC src/core/protocol.cpp src/world/store.cpp src/world/storage_vfs.cpp src/world/actors.cpp src/world/objects.cpp src/world/surfaces.cpp src/platform/render_process.cpp src/platform/render_worker.cpp src/platform/broker.cpp)
target_include_directories(spatial-core PUBLIC src)
target_link_libraries(spatial-core PUBLIC spatial-scene SQLite::SQLite3 spatial-authority spatial-workspace-resources spatial-storage-launch)
add_executable(spatial-storage-broker-launch-probe src/testing/storage_broker_launch_probe.cpp)
target_link_libraries(spatial-storage-broker-launch-probe PRIVATE spatial-core)
add_dependencies(spatial-storage-broker-launch-probe broker-python-protobuf)
add_executable(spatialwm src/render/presentation_server.cpp src/render/presentation_inputs.cpp src/x11/compositor_connection.cpp src/render/main.cpp src/render/renderer.cpp src/x11/surfaces.cpp src/x11/input_router.cpp)
target_link_options(spatialwm PRIVATE "LINKER:--export-dynamic-symbol=xcb_connect_to_display_with_auth_info")
target_link_libraries(spatialwm PRIVATE spatial-presentation-stream spatial-compositor-config PkgConfig::XCB ${CMAKE_DL_LIBS} spatial-native-input X11::X11 X11::Xcomposite X11::Xdamage OpenGL::GL GLEW::GLEW)
add_executable(spatial-presentation-probe src/testing/presentation_probe.cpp ${presentation_probe_sources}
    src/render/presentation_inputs.cpp src/x11/compositor_connection.cpp src/render/renderer.cpp src/x11/surfaces.cpp src/x11/input_router.cpp)
target_link_options(spatial-presentation-probe PRIVATE "LINKER:--export-dynamic-symbol=xcb_connect_to_display_with_auth_info")
target_link_libraries(spatial-presentation-probe PRIVATE spatial-compositor-config PkgConfig::XCB ${CMAKE_DL_LIBS} spatial-native-input X11::X11 X11::Xcomposite X11::Xdamage OpenGL::GL GLEW::GLEW)
add_dependencies(spatial-presentation-probe presentation-probe-python)
add_executable(worldd src/world/actor_tls.cpp src/world/main.cpp src/world/server.cpp src/world/identity_sessions.cpp src/world/authority.cpp)
target_link_libraries(worldd PRIVATE spatial-compositor-config spatial-core spatial-issuer-watch-lib spatial-authority spatial-participant-runtime-lib)
add_dependencies(worldd spatialwm)
add_library(spatial-app-initializer STATIC src/workspace/app_initializer.cpp)
target_link_libraries(spatial-app-initializer PUBLIC spatial-scene)
add_executable(spatial-app-init src/workspace/app_init.cpp src/workspace/hardened_sanitizer.cpp)
target_link_libraries(spatial-app-init PRIVATE spatial-app-initializer)
add_executable(workspaced src/workspace/main.cpp src/workspace/store.cpp src/workspace/git_command.cpp src/workspace/status.cpp src/workspace/process.cpp src/workspace/authority.cpp src/workspace/native_launch.cpp src/workspace/control.cpp src/workspace/control_channels.cpp)
target_include_directories(workspaced PRIVATE src)
target_link_libraries(workspaced PRIVATE spatial-storage-client spatial-workspace-resources spatial-scene SQLite::SQLite3 spatial-authority spatial-participant-runtime-lib)
add_dependencies(workspaced spatial-app-init)
add_executable(spatial-test-host src/testing/main.cpp src/platform/worker.cpp)
target_link_libraries(spatial-test-host PRIVATE spatial-core)
add_dependencies(spatial-test-host worldd workspaced)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    add_executable(spatial-app-initializer-probe src/testing/app_initializer_probe.cpp)
    target_link_libraries(spatial-app-initializer-probe PRIVATE spatial-app-initializer)
    add_dependencies(spatial-app-initializer-probe broker-python-protobuf native-scope-python-protobuf scope-python-protobuf
        python-protobuf security-python-protobuf issuer-python-protobuf grants-python-protobuf key-python-protobuf)
    set(initializer_check_arguments)
    if(SPATIAL_SANITIZERS)
        list(APPEND initializer_check_arguments --sanitizers)
    endif()
    add_test(NAME app-initializer-lifecycle COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_app_initializer.py"
        $<TARGET_FILE:spatial-app-initializer-probe> ${initializer_check_arguments})
    set_tests_properties(app-initializer-lifecycle PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python;ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_executable(spatial-storage-client-probe src/testing/storage_client_probe.cpp)
    target_link_libraries(spatial-storage-client-probe PRIVATE spatial-storage-client)
    add_executable(spatial-storage-owner-probe src/testing/storage_owner_probe.cpp)
    target_link_libraries(spatial-storage-owner-probe PRIVATE spatial-storage-owner)
    add_dependencies(workspaced broker-python-protobuf native-scope-python-protobuf scope-python-protobuf
        python-protobuf security-python-protobuf issuer-python-protobuf grants-python-protobuf key-python-protobuf)
    add_executable(spatial-native-scope-probe src/testing/native_scope_probe.cpp src/workspace/native_launch.cpp)
    target_link_libraries(spatial-native-scope-probe PRIVATE spatial-scene)
    add_dependencies(spatial-native-scope-probe native-scope-python-protobuf security-python-protobuf python-protobuf)
    add_executable(spatial-native-config-witness src/testing/native_scope_config_witness.cpp)
    target_link_libraries(spatial-native-config-witness PRIVATE spatial-authority-launch)
    add_test(NAME control-supervisor-reply COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_control_reply.py" $<TARGET_FILE:spatial-control-reply-probe>)
    set_tests_properties(control-supervisor-reply PROPERTIES
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python" TIMEOUT 180)
    add_test(NAME authority-native-config COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_native_scope_config_ownership.py"
        $<TARGET_FILE:spatial-native-config-witness> "${CMAKE_CURRENT_SOURCE_DIR}" $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(authority-native-config PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")

    add_test(NAME authority-native-scope COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_authority_native_scope.py"
        $<TARGET_FILE:spatial-native-scope-probe> $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(authority-native-scope PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")

    add_test(NAME world-revision-interleave COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_revision_interleave.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-revision-interleave PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-bootstrap-owner-idle COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap_owner_idle.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-bootstrap-owner-idle PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME supervisor-handoff-lifetime COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-handoff-lifetime-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_handoff_lifetime.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(supervisor-handoff-lifetime PROPERTIES TIMEOUT 180)
    add_test(NAME authority-supervisor COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-supervisor-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_authority_supervisor.py"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-supervisor PROPERTIES TIMEOUT 120)
    add_test(NAME authority-approval-interruption COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-approval-interruption-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_approval_interruption.py"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-approval-interruption PROPERTIES TIMEOUT 120)
    add_test(NAME authority-broker-handoff COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-broker-handoff-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_broker_handoff.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-broker-handoff PROPERTIES TIMEOUT 120)
    add_test(NAME authority-broker-handoff-regressions COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_broker_handoff_regressions.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-broker-handoff-regressions PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME authority-world-handoff COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-world-handoff-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_world_handoff.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-world-handoff PROPERTIES TIMEOUT 120)
    add_test(NAME authority-world-handoff-regressions COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-world-handoff-regressions-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_world_handoff_regressions.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-world-handoff-regressions PROPERTIES TIMEOUT 180)
    add_test(NAME authority-supervisor-fencing COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-supervisor-fencing-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_fencing.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-supervisor-fencing PROPERTIES TIMEOUT 180)
    add_test(NAME authority-control-binding COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-control-binding-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_control_owner_binding.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-control-binding PROPERTIES TIMEOUT 180)
    add_test(NAME authority-supervisor-crash-fencing COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-supervisor-crash-fencing-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_crash_fencing.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-supervisor-crash-fencing PROPERTIES TIMEOUT 180)
    add_test(NAME authority-participant-relay COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-participant-relay-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_authority_participant_relay.py"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-participant-relay PROPERTIES TIMEOUT 120)
    add_test(NAME authority-owned-participant COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-owned-participant-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_authority_owned_participant.py"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(authority-owned-participant PROPERTIES TIMEOUT 120)
    add_test(NAME authority-launch-coordinator COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-coordinator-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_authority_launch.py"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(authority-launch-coordinator PROPERTIES TIMEOUT 120)
    add_test(NAME authority-prepared-launch COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-prepared-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_prepared_launch.py"
        $<TARGET_FILE:spatial-process-scope-probe>)
    set_tests_properties(authority-prepared-launch PROPERTIES TIMEOUT 120)
    add_test(NAME authority-process-scope COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-scope-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_process_scope.py"
        $<TARGET_FILE:spatial-process-scope-probe>)
    set_tests_properties(authority-process-scope PROPERTIES TIMEOUT 120)
    add_test(NAME process-scope-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_process_scope_resources.py"
        $<TARGET_FILE:spatial-process-scope-probe>)
    set_tests_properties(process-scope-resources PROPERTIES TIMEOUT 150
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME broker-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_resources.py"
        $<TARGET_FILE:spatial-process-scope-probe> $<TARGET_FILE:workspaced>)
    add_test(NAME production-broker-profile COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_production_profile.py" $<TARGET_FILE:workspaced>)
    set_tests_properties(production-broker-profile PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    set_tests_properties(broker-resources PROPERTIES TIMEOUT 150
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_executable(spatial-process-termination-probe src/testing/process_termination_probe.cpp src/workspace/process.cpp src/workspace/native_launch.cpp)
    target_link_libraries(spatial-process-termination-probe PRIVATE spatial-workspace-resources spatial-scene SQLite::SQLite3 spatial-authority spatial-issuer-watch-lib)
    add_dependencies(spatial-process-termination-probe spatial-app-init broker-python-protobuf native-scope-python-protobuf
        scope-python-protobuf python-protobuf security-python-protobuf issuer-python-protobuf grants-python-protobuf key-python-protobuf)
    add_test(NAME process-termination COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_process_termination.py" $<TARGET_FILE:spatial-process-termination-probe>)
    set_tests_properties(process-termination PROPERTIES TIMEOUT 150
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME workspace-participant-races COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant_races.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-participant-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-participant COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-participant PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-participant COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_participant.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-participant PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-revocation-receipts COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_revocation_receipts.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-revocation-receipts PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-authority-retirement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_retirement.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-authority-retirement PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-authority-retirement-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_retirement_client.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-authority-retirement-client PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-authority-fencing COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_fencing.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-authority-fencing PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-authority-fencing-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_fencing_client.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-authority-fencing-client PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-launch-authorization COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_launch_authorization.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-launch-authorization PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-supervisor-registry COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_supervisor_registry.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-supervisor-registry PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-incarnation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_participant_incarnation.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-participant-runtime-probe>)
    set_tests_properties(identity-participant-incarnation PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-channel COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_participant_channel.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-participant-runtime-probe>)
    set_tests_properties(identity-participant-channel PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-runtime COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participant_runtime.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-participant-runtime-probe>)
    set_tests_properties(identity-participant-runtime PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-barrier-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participant_barrier_client.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-participant-barrier-client PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-barrier COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participant_barrier.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-participant-barrier PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-session-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participant_session_client.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-participant-session-client PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-session COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participant_session.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-participant-session PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participants COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participants.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-participants PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-participant-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_participant_client.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-participant-client PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-actor-tls-retry-probe src/testing/actor_tls_retry_probe.cpp src/world/actor_tls.cpp)
    target_link_libraries(spatial-actor-tls-retry-probe PRIVATE spatial-identity-tls)
    target_link_options(spatial-actor-tls-retry-probe PRIVATE -Wl,--wrap=SSL_read_ex -Wl,--wrap=SSL_get_error)
    add_test(NAME world-remote-retries COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_tls_retries.py" $<TARGET_FILE:spatial-actor-tls-retry-probe>)
    set_tests_properties(world-remote-retries PROPERTIES TIMEOUT 30)
    add_test(NAME world-remote-security COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote_security.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-remote-security PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-remote-lifecycle COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote_lifecycle.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-remote-lifecycle PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-remote-native COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote_native.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(world-remote-native PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-remote-races COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote_races.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-remote-races PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-remote-config COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote_config.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-remote-config PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-remote COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_remote.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-remote PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-bootstrap-races COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap_races.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-bootstrap-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-bootstrap PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-bootstrap-races COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_bootstrap_races.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-bootstrap-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME broker-bootstrap-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_bootstrap_client.py" $<TARGET_FILE:spatial-broker-control-client-probe>)
    set_tests_properties(broker-bootstrap-client PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_bootstrap.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(world-bootstrap PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME native-display-ready COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_display_ready.py")
    set_tests_properties(native-display-ready PROPERTIES TIMEOUT 10)
    add_executable(spatial-render-observation-probe src/testing/render_observation_probe.cpp)
    target_link_libraries(spatial-render-observation-probe PRIVATE spatial-core)
    add_test(NAME render-observation-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_render_observation_client.py" $<TARGET_FILE:spatial-render-observation-probe>)
    set_tests_properties(render-observation-client PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-broker-control-client-probe src/testing/broker_control_client_probe.cpp)
    target_link_libraries(spatial-broker-control-client-probe PRIVATE spatial-core)
    add_test(NAME broker-control-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_control_client.py" $<TARGET_FILE:spatial-broker-control-client-probe>)
    set_tests_properties(broker-control-client PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(native-launch-checkpoint-probe src/testing/native_launch_checkpoint_probe.cpp src/workspace/native_launch.cpp)
    target_include_directories(native-launch-checkpoint-probe PRIVATE src)
    target_link_libraries(native-launch-checkpoint-probe PRIVATE spatial-scene)
    protobuf_generate_cpp(grants_probe_sources grants_probe_headers proto/spatial/authority/v1/grants_probe.proto)
    add_executable(spatial-authority-probe src/testing/authority_probe.cpp ${grants_probe_sources} ${grants_probe_headers})
    target_link_libraries(spatial-authority-probe PRIVATE spatial-authority)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/grants_probe_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1/grants_probe.proto
        DEPENDS proto/spatial/authority/v1/grants_probe.proto proto/spatial/authority/v1/grants.proto
            proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
    add_custom_target(grants-probe-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/grants_probe_pb2.py")
    add_test(NAME authority-store COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority.py" $<TARGET_FILE:spatial-authority-probe>)
    set_tests_properties(authority-store PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME authority-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_bootstrap.py"
        $<TARGET_FILE:spatial-authority-probe> $<TARGET_FILE:spatial-authority-migrate>)
    set_tests_properties(authority-bootstrap PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-authority COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-authority PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME authenticated-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authenticated_resources.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(authenticated-resources PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-keys COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_keys.py" $<TARGET_FILE:spatial-key>)
    set_tests_properties(identity-keys PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-issuer COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_issuer.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-issuer PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-enrollment COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_enrollment.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-enrollment PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_bootstrap.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-bootstrap PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-revocation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_revocation.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-revocation PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME identity-tls COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_tls.py" $<TARGET_FILE:identityd>)
    set_tests_properties(identity-tls PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME actor-status COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_status.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(actor-status PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-issuer-client-probe src/testing/issuer_client_probe.cpp)
    target_link_libraries(spatial-issuer-client-probe PRIVATE spatial-issuer-client-lib)
    add_test(NAME identity-revocation-command COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_revocation_command.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-revoke>)
    set_tests_properties(identity-revocation-command PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME presentation-stream COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_presentation_stream.py" $<TARGET_FILE:spatial-presentation-stream-probe>)
    set_tests_properties(presentation-stream PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME issuer-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_issuer_client.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(issuer-client PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME issuer-client-defaults COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_issuer_client_defaults.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(issuer-client-defaults PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-input-bridge tests/native/input_bridge.cpp)
    add_executable(spatial-issuer-watch-probe src/testing/issuer_watch_probe.cpp)
    target_link_libraries(spatial-issuer-watch-probe PRIVATE spatial-issuer-watch-lib)
    add_test(NAME identity-watch COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_identity_watch.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-issuer-watch>)
    set_tests_properties(identity-watch PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(spatial-input-bridge PRIVATE spatial-native-input)
    add_executable(spatial-scoped-input-probe src/testing/scoped_input_probe.cpp)
    target_link_libraries(spatial-scoped-input-probe PRIVATE spatial-native-input)
    add_test(NAME scoped-native-input COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_scoped_input.py"
        $<TARGET_FILE:spatial-scoped-input-probe> $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(scoped-native-input PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-xprobe native/xprobe.c)
    set_target_properties(spatial-xprobe PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    target_compile_definitions(spatial-xprobe PRIVATE SPATIAL_HAVE_XI2=1)
    target_link_libraries(spatial-xprobe PRIVATE X11::X11 X11::Xi)
    # ASan's shadow reservation exceeds the production application's RLIMIT_AS.
    # Keep the confined independent witness uninstrumented, not the runtime.
    add_executable(spatial-confined-xprobe native/xprobe.c)
    set_target_properties(spatial-confined-xprobe PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    target_compile_definitions(spatial-confined-xprobe PRIVATE SPATIAL_HAVE_XI2=1)
    target_compile_options(spatial-confined-xprobe PRIVATE -fno-sanitize=all)
    target_link_options(spatial-confined-xprobe PRIVATE -fno-sanitize=all)
    target_link_libraries(spatial-confined-xprobe PRIVATE X11::X11 X11::Xi)
    add_executable(spatial-security-auth native/security_auth.c)
    set_target_properties(spatial-security-auth PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    target_link_libraries(spatial-security-auth PRIVATE X11::X11)
    add_test(NAME render-runtime COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_render.py" $<TARGET_FILE:spatial-test-host>
        $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
    set_tests_properties(render-runtime PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME surface-runtime COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_surfaces.py" $<TARGET_FILE:spatial-test-host>
        $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
    set_tests_properties(surface-runtime PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME actor-observation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_observation.py" $<TARGET_FILE:spatial-test-host>
        $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
    set_tests_properties(actor-observation PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME actor-collision COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_collision.py" $<TARGET_FILE:spatial-test-host>
        $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
    set_tests_properties(actor-collision PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    if(SPATIAL_INPUT_TESTS)
        add_test(NAME broker-control-client-live COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_control_client_live.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(broker-control-client-live PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME workspace-participant-native COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant_native.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(workspace-participant-native PROPERTIES TIMEOUT 120
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME workspace-participant-settlement COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant_settlement.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(workspace-participant-settlement PROPERTIES TIMEOUT 120
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME control-owner-channels COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_control_owner_channels.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(control-owner-channels PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME application-control-lease COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_application_control_lease.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(application-control-lease PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME application-observation COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_application_observation.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(application-observation PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME world-application-control COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_application_control.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(world-application-control PROPERTIES TIMEOUT 240
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME actor-pixels COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_pixels.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(actor-pixels PROPERTIES TIMEOUT 240
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME application-access COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_application_access.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(application-access PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        foreach(identity_suite surface_identity compositor_identity world_surface_groups)
            add_test(NAME native-${identity_suite} COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_${identity_suite}.py" $<TARGET_FILE:spatial-security-auth>)
            set_tests_properties(native-${identity_suite} PROPERTIES TIMEOUT 180
                ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        endforeach()
        add_test(NAME native-application-launch COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_native_launch.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-application-launch PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-actor-binding COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_actor_binding.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-actor-binding PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME presentation-server COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_presentation_server.py" $<TARGET_FILE:spatialwm> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(presentation-server PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME presentation-input-bindings COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_presentation_bindings.py" $<TARGET_FILE:spatial-presentation-probe> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(presentation-input-bindings PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-application-input COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_application_input.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-application-input PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-device-administration COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_device_administration.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-device-administration PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-global-isolation COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_global_isolation.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-global-isolation PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-selection-isolation COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_selection_isolation.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-selection-isolation PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-selection-namespace COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_selection_namespace.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-selection-namespace PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-event-metadata COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_event_metadata.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-event-metadata PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-parent-metadata COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_parent_metadata.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-parent-metadata PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-topology-pixels COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_topology_pixels.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-topology-pixels PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME qualification-parent-mutation-parser COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_parent_mutation_parser.py")
        set_tests_properties(qualification-parent-mutation-parser PROPERTIES TIMEOUT 30
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-atom-privacy COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_atom_privacy.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-atom-privacy PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-extension-isolation COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_extension_isolation.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-extension-isolation PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-resource-isolation COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_resource_isolation.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-resource-isolation PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-security-identity COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_security_identity.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-security-identity PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME world-participant-native COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_participant_native.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
        set_tests_properties(world-participant-native PROPERTIES TIMEOUT 120
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_XPROBE=$<TARGET_FILE:spatial-xprobe>")
        add_test(NAME world-identity COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_identity.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
        set_tests_properties(world-identity PROPERTIES TIMEOUT 120
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_XPROBE=$<TARGET_FILE:spatial-xprobe>")
        add_test(NAME world-authority COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_authority.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
        set_tests_properties(world-authority PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_XPROBE=$<TARGET_FILE:spatial-xprobe>")
        add_test(NAME world-shared-state COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_shared_state.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
        set_tests_properties(world-shared-state PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME actor-objects COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_objects.py" $<TARGET_FILE:spatial-test-host>
            $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
        set_tests_properties(actor-objects PROPERTIES TIMEOUT 90
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME actor-portals COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_portals.py" $<TARGET_FILE:spatial-test-host>
            $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
        set_tests_properties(actor-portals PROPERTIES TIMEOUT 90
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-actor-input COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_input.py" $<TARGET_FILE:spatial-test-host>
            $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-xprobe>)
        set_tests_properties(native-actor-input PROPERTIES TIMEOUT 90
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    endif()
    add_test(NAME core-protocol COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_protocol.py" $<TARGET_FILE:spatial-test-host>)
    set_tests_properties(core-protocol PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME worldd-lifecycle COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_worldd.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:worldd>)
    set_tests_properties(worldd-lifecycle PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME actor-runtime COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actors.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:worldd>)
    set_tests_properties(actor-runtime PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME recovery-boundaries COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_recovery.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:worldd>)
    set_tests_properties(recovery-boundaries PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-git COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_git.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:spatial-git-job-probe> $<TARGET_FILE:spatial-git-boundary-probe>)
    set_tests_properties(workspace-git PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-context COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspaces.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-context PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-processes COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_processes.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-processes PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME adapter-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_adapter_resources.py"
        $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(adapter-resources PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-git-boundary-probe src/testing/git_boundary_probe.cpp src/workspace/git_command.cpp)
    target_include_directories(spatial-git-boundary-probe PRIVATE src)
    target_link_libraries(spatial-git-boundary-probe PRIVATE spatial-scene spatial-workspace-resources)
    add_executable(spatial-git-job-probe src/testing/git_job_probe.cpp src/workspace/git_command.cpp)
    target_include_directories(spatial-git-job-probe PRIVATE src)
    target_link_libraries(spatial-git-job-probe PRIVATE spatial-scene spatial-workspace-resources)
    add_executable(spatial-storage-vfs-probe src/testing/storage_vfs_probe.cpp src/world/storage_vfs.cpp)
    target_include_directories(spatial-storage-vfs-probe PRIVATE src)
    target_link_libraries(spatial-storage-vfs-probe PRIVATE SQLite::SQLite3)
    add_test(NAME storage-faults COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_storage.py" $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-storage-vfs-probe>)
    set_tests_properties(storage-faults PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
endif()

# Private administrative transport; the fixture launches only an independent packet witness.
add_library(spatial-control-admin STATIC src/supervisor/control_admin.cpp)
target_link_libraries(spatial-control-admin PUBLIC spatial-control-reply)
add_library(spatial-session-broker STATIC src/supervisor/session_broker.cpp src/supervisor/session_launch.cpp src/supervisor/session_coordinator.cpp)
target_link_libraries(spatial-session-broker PUBLIC spatial-authority-launch spatial-core spatial-control-admin)
if(BUILD_TESTING)
    add_executable(spatial-control-admin-probe src/testing/control_admin_probe.cpp)
    target_link_libraries(spatial-control-admin-probe PRIVATE spatial-control-admin)
    add_dependencies(spatial-control-admin-probe broker-python-protobuf security-python-protobuf python-protobuf)
    add_test(NAME control-administrative-transport COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_control_admin.py" $<TARGET_FILE:spatial-control-admin-probe>)
    set_tests_properties(control-administrative-transport PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
endif()

if(BUILD_TESTING)
    add_executable(spatial-retained-child-handle-probe src/testing/retained_child_handle_probe.cpp)
    target_link_libraries(spatial-retained-child-handle-probe PRIVATE spatial-process-scope)
    add_test(NAME retained-child-handle COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-retained-handle-${build_hash} --property=Delegate=yes
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_retained_child_handle.py"
        $<TARGET_FILE:spatial-retained-child-handle-probe>)
    set_tests_properties(retained-child-handle PROPERTIES TIMEOUT 120)
    add_test(NAME supervisor-child-handle COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-child-handle-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_supervisor_child_handle.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(supervisor-child-handle PROPERTIES TIMEOUT 180)
    add_executable(spatial-session-broker-probe src/testing/session_broker_probe.cpp)
    target_link_libraries(spatial-session-broker-probe PRIVATE spatial-session-broker)
    add_executable(spatial-session-launch-exit-probe src/testing/session_launch_exit_probe.cpp)
    target_link_libraries(spatial-session-launch-exit-probe PRIVATE spatial-session-broker)
    add_test(NAME supervisor-session-exit COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_session_launch_exit.py" $<TARGET_FILE:spatial-session-launch-exit-probe>)
    set_tests_properties(supervisor-session-exit PROPERTIES TIMEOUT 45
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME session-broker COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-session-broker-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_broker.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-broker PROPERTIES TIMEOUT 180)
    add_executable(spatial-session-world-probe src/testing/session_world_probe.cpp)
    target_link_libraries(spatial-session-world-probe PRIVATE spatial-session-broker)
    add_test(NAME session-world COMMAND systemd-run --user --wait --pipe --collect
        --unit=spatial-session-world-${build_hash} --property=Delegate=yes
        "--setenv=PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}"
        "--setenv=SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER}"
        "--setenv=SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}"
        --setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1
        --setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_world.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-world PROPERTIES TIMEOUT 180)
    protobuf_generate_cpp(session_probe_sources session_probe_headers src/testing/session_probe.proto)
    add_executable(spatial-session-application-probe src/testing/session_application_probe.cpp ${session_probe_sources} ${session_probe_headers})
    target_link_libraries(spatial-session-application-probe PRIVATE spatial-session-broker)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/session_probe_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/src/testing
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/src/testing/session_probe.proto
        DEPENDS src/testing/session_probe.proto protobuf::protoc VERBATIM)
    add_custom_target(session-probe-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/session_probe_pb2.py")
    add_dependencies(spatial-session-application-probe session-probe-python world-session-python-protobuf)
    add_test(NAME session-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_resources.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-resources PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-operator-revocation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_operator_revocation.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-operator-revocation PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-application COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_application.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-application PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-storage-owner COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_storage_session.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    add_test(NAME session-storage-contention COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_storage_contention.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    add_test(NAME operator-storage COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_operator_storage.py"
        "${CMAKE_CURRENT_BINARY_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")
    set_tests_properties(operator-storage PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    set_tests_properties(session-storage-contention PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    set_tests_properties(session-storage-owner PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-dependent-cleanup COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_dependent_cleanup.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-dependent-cleanup PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-coordinator-retirement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_coordinator_retirement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-coordinator-retirement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-broker-cleanup COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_broker_cleanup.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-broker-cleanup PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-broker-retirement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_broker_retirement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-broker-retirement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-stopped-broker-dependency COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_stopped_broker_dependency.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-stopped-broker-dependency PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-deferred-fence COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_deferred_fence.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-deferred-fence PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-replacement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_replacement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-replacement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-broker-replacement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_broker_replacement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-broker-replacement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_executable(spatial-child-handle-client-probe src/testing/child_handle_client_probe.cpp)
    target_link_libraries(spatial-child-handle-client-probe PRIVATE spatial-authority-launch)
    add_test(NAME supervisor-child-handle-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_child_handle_client.py" $<TARGET_FILE:spatial-child-handle-client-probe>)
    set_tests_properties(supervisor-child-handle-client PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
endif()
