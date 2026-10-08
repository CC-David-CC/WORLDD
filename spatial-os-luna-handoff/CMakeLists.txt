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
find_package(OpenGL REQUIRED)
find_package(GLEW REQUIRED)
configure_file(config/conformance.textproto runtime.textproto COPYONLY)
set(Protobuf_IMPORT_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1")
protobuf_generate_cpp(proto_sources proto_headers proto/spatial/core/v1/core.proto)
protobuf_generate_cpp(input_proto_sources input_proto_headers proto/spatial/input/v1/input.proto)
protobuf_generate_cpp(security_proto_sources security_proto_headers proto/spatial/security/v1/security.proto)
protobuf_generate_cpp(broker_proto_sources broker_proto_headers proto/spatial/workspace/v1/broker.proto)
protobuf_generate_cpp(key_proto_sources key_proto_headers proto/spatial/identity/v1/key.proto)
protobuf_generate_cpp(issuer_proto_sources issuer_proto_headers proto/spatial/identity/v1/issuer.proto)
protobuf_generate_cpp(world_session_proto_sources world_session_proto_headers proto/spatial/identity/v1/world_session.proto)
protobuf_generate_cpp(grants_proto_sources grants_proto_headers proto/spatial/authority/v1/grants.proto)
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/python")
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
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/broker.proto
    DEPENDS proto/spatial/workspace/v1/broker.proto proto/spatial/core/v1/core.proto proto/spatial/authority/v1/grants.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(broker-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py")
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
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/issuer.proto
    DEPENDS proto/spatial/identity/v1/issuer.proto proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
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
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1/grants.proto
    DEPENDS proto/spatial/authority/v1/grants.proto proto/spatial/security/v1/security.proto protobuf::protoc VERBATIM)
add_custom_target(grants-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/grants_pb2.py")

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

add_library(spatial-protobuf STATIC ${proto_sources} ${proto_headers} ${broker_proto_sources} ${broker_proto_headers} ${key_proto_sources} ${key_proto_headers} ${issuer_proto_sources} ${issuer_proto_headers} ${world_session_proto_sources} ${world_session_proto_headers} ${grants_proto_sources} ${grants_proto_headers} ${security_proto_sources} ${security_proto_headers})
target_link_libraries(spatial-protobuf PUBLIC protobuf::libprotobuf)
target_include_directories(spatial-protobuf PUBLIC "${CMAKE_CURRENT_BINARY_DIR}")
add_library(spatial-scene STATIC src/core/framing.cpp src/core/geometry.cpp src/core/collision.cpp src/core/navigation.cpp src/core/scene.cpp)
target_include_directories(spatial-scene PUBLIC src)
target_link_libraries(spatial-scene PUBLIC spatial-protobuf)
add_library(spatial-identity STATIC src/identity/keys.cpp)
target_link_libraries(spatial-identity PUBLIC spatial-scene OpenSSL::Crypto)
add_executable(spatial-key src/identity/key_main.cpp)
target_link_libraries(spatial-key PRIVATE spatial-identity)
add_library(spatial-identity-tls STATIC src/identity/tls_support.cpp)
target_link_libraries(spatial-identity-tls PUBLIC spatial-identity OpenSSL::SSL)
add_library(spatial-issuer-client-lib STATIC src/identity/issuer_client.cpp)
target_link_libraries(spatial-issuer-client-lib PUBLIC spatial-identity-tls)
add_executable(spatial-issuer-client src/identity/client_main.cpp)
target_link_libraries(spatial-issuer-client PRIVATE spatial-issuer-client-lib)
add_library(spatial-issuer-watch-lib STATIC src/identity/issuer_watch.cpp)
target_link_libraries(spatial-issuer-watch-lib PUBLIC spatial-issuer-client-lib Threads::Threads)
add_executable(spatial-issuer-watch src/identity/watch_main.cpp)
target_link_libraries(spatial-issuer-watch PRIVATE spatial-issuer-watch-lib)
add_library(spatial-issuer STATIC src/identity/issuer.cpp src/identity/protocol.cpp src/identity/tls_server.cpp)
add_library(spatial-authority STATIC src/authority/store.cpp)
target_link_libraries(spatial-authority PUBLIC spatial-scene SQLite::SQLite3 OpenSSL::Crypto)
target_link_libraries(spatial-issuer PUBLIC spatial-identity-tls SQLite::SQLite3)
add_executable(identityd src/identity/issuer_main.cpp)
target_link_libraries(identityd PRIVATE spatial-issuer)
add_library(spatial-native-input STATIC src/x11/input.cpp ${input_proto_sources} ${input_proto_headers})
target_link_libraries(spatial-native-input PUBLIC spatial-scene X11::X11)
add_library(spatial-core STATIC src/core/protocol.cpp src/world/store.cpp src/world/storage_vfs.cpp src/world/actors.cpp src/world/objects.cpp src/world/surfaces.cpp src/platform/render_worker.cpp src/platform/broker.cpp)
target_include_directories(spatial-core PUBLIC src)
target_link_libraries(spatial-core PUBLIC spatial-scene SQLite::SQLite3)
add_executable(spatialwm src/render/main.cpp src/render/renderer.cpp src/x11/surfaces.cpp src/x11/input_router.cpp)
target_link_libraries(spatialwm PRIVATE spatial-native-input X11::X11 X11::Xcomposite X11::Xdamage OpenGL::GL GLEW::GLEW)
add_executable(worldd src/world/main.cpp src/world/server.cpp src/world/identity_sessions.cpp src/world/authority.cpp)
target_link_libraries(worldd PRIVATE spatial-core spatial-issuer-watch-lib spatial-authority)
add_dependencies(worldd spatialwm)
add_executable(spatial-app-init src/workspace/app_init.cpp)
target_link_libraries(spatial-app-init PRIVATE spatial-scene)
add_executable(workspaced src/workspace/main.cpp src/workspace/store.cpp src/workspace/git_command.cpp src/workspace/status.cpp src/workspace/process.cpp src/workspace/authority.cpp src/workspace/native_launch.cpp)
target_include_directories(workspaced PRIVATE src)
target_link_libraries(workspaced PRIVATE spatial-scene SQLite::SQLite3 spatial-authority spatial-issuer-watch-lib)
add_dependencies(workspaced spatial-app-init)
add_executable(spatial-test-host src/testing/main.cpp src/platform/worker.cpp)
target_link_libraries(spatial-test-host PRIVATE spatial-core)
add_dependencies(spatial-test-host worldd workspaced)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
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
    add_test(NAME workspace-authority COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-authority PROPERTIES TIMEOUT 180
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
    add_test(NAME issuer-client COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_issuer_client.py" $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(issuer-client PROPERTIES TIMEOUT 90
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
    add_executable(spatial-xprobe native/xprobe.c)
    set_target_properties(spatial-xprobe PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    target_compile_definitions(spatial-xprobe PRIVATE SPATIAL_HAVE_XI2=1)
    target_link_libraries(spatial-xprobe PRIVATE X11::X11 X11::Xi)
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
        foreach(identity_suite surface_identity compositor_identity world_surface_groups)
            add_test(NAME native-${identity_suite} COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_${identity_suite}.py" $<TARGET_FILE:spatial-security-auth>)
            set_tests_properties(native-${identity_suite} PROPERTIES TIMEOUT 180
                ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        endforeach()
        add_test(NAME native-application-launch COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_native_launch.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-application-launch PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-actor-binding COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_actor_binding.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-actor-binding PROPERTIES TIMEOUT 180
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_git.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:spatial-git-job-probe>)
    set_tests_properties(workspace-git PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-context COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspaces.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-context PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-processes COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_processes.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-processes PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-git-job-probe src/testing/git_job_probe.cpp src/workspace/git_command.cpp)
    target_link_libraries(spatial-git-job-probe PRIVATE spatial-scene)
    add_executable(spatial-storage-vfs-probe src/testing/storage_vfs_probe.cpp src/world/storage_vfs.cpp)
    target_include_directories(spatial-storage-vfs-probe PRIVATE src)
    target_link_libraries(spatial-storage-vfs-probe PRIVATE SQLite::SQLite3)
    add_test(NAME storage-faults COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_storage.py" $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-storage-vfs-probe>)
    set_tests_properties(storage-faults PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
endif()
