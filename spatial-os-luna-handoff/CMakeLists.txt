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
find_package(X11 REQUIRED)
find_package(OpenGL REQUIRED)
find_package(GLEW REQUIRED)
configure_file(config/conformance.textproto runtime.textproto COPYONLY)
protobuf_generate_cpp(proto_sources proto_headers proto/spatial/core/v1/core.proto)
protobuf_generate_cpp(input_proto_sources input_proto_headers proto/spatial/input/v1/input.proto)
set(Protobuf_IMPORT_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1")
protobuf_generate_cpp(broker_proto_sources broker_proto_headers proto/spatial/workspace/v1/broker.proto)
protobuf_generate_cpp(key_proto_sources key_proto_headers proto/spatial/identity/v1/key.proto)
protobuf_generate_cpp(issuer_proto_sources issuer_proto_headers proto/spatial/identity/v1/issuer.proto)
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/python")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/core_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1/core.proto
    DEPENDS proto/spatial/core/v1/core.proto protobuf::protoc VERBATIM)
add_custom_target(python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/core_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/input_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/input/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/input/v1/input.proto
    DEPENDS proto/spatial/input/v1/input.proto protobuf::protoc VERBATIM)
add_custom_target(input-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/input_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/broker.proto
    DEPENDS proto/spatial/workspace/v1/broker.proto proto/spatial/core/v1/core.proto protobuf::protoc VERBATIM)
add_custom_target(broker-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/key_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/key.proto
    DEPENDS proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto protobuf::protoc VERBATIM)
add_custom_target(key-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/key_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/issuer_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/issuer.proto
    DEPENDS proto/spatial/identity/v1/issuer.proto proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto protobuf::protoc VERBATIM)
add_custom_target(issuer-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/issuer_pb2.py")

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

add_library(spatial-protobuf STATIC ${proto_sources} ${proto_headers} ${broker_proto_sources} ${broker_proto_headers} ${key_proto_sources} ${key_proto_headers} ${issuer_proto_sources} ${issuer_proto_headers})
target_link_libraries(spatial-protobuf PUBLIC protobuf::libprotobuf)
target_include_directories(spatial-protobuf PUBLIC "${CMAKE_CURRENT_BINARY_DIR}")
add_library(spatial-scene STATIC src/core/framing.cpp src/core/geometry.cpp src/core/collision.cpp src/core/navigation.cpp src/core/scene.cpp)
target_include_directories(spatial-scene PUBLIC src)
target_link_libraries(spatial-scene PUBLIC spatial-protobuf)
add_library(spatial-identity STATIC src/identity/keys.cpp)
target_link_libraries(spatial-identity PUBLIC spatial-scene OpenSSL::Crypto)
add_executable(spatial-key src/identity/key_main.cpp)
target_link_libraries(spatial-key PRIVATE spatial-identity)
add_library(spatial-issuer STATIC src/identity/issuer.cpp src/identity/protocol.cpp src/identity/tls_server.cpp)
target_link_libraries(spatial-issuer PUBLIC spatial-identity SQLite::SQLite3 OpenSSL::SSL)
add_executable(identityd src/identity/issuer_main.cpp)
target_link_libraries(identityd PRIVATE spatial-issuer)
add_library(spatial-native-input STATIC src/x11/input.cpp ${input_proto_sources} ${input_proto_headers})
target_link_libraries(spatial-native-input PUBLIC spatial-scene X11::X11)
add_library(spatial-core STATIC src/core/protocol.cpp src/world/store.cpp src/world/storage_vfs.cpp src/world/actors.cpp src/world/objects.cpp src/world/surfaces.cpp src/platform/render_worker.cpp src/platform/broker.cpp)
target_include_directories(spatial-core PUBLIC src)
target_link_libraries(spatial-core PUBLIC spatial-scene SQLite::SQLite3)
add_executable(spatialwm src/render/main.cpp src/render/renderer.cpp src/x11/surfaces.cpp src/x11/input_router.cpp)
target_link_libraries(spatialwm PRIVATE spatial-native-input X11::X11 X11::Xcomposite X11::Xdamage OpenGL::GL GLEW::GLEW)
add_executable(worldd src/world/main.cpp src/world/server.cpp)
target_link_libraries(worldd PRIVATE spatial-core)
add_dependencies(worldd spatialwm)
add_executable(spatial-app-init src/workspace/app_init.cpp)
target_link_libraries(spatial-app-init PRIVATE spatial-scene)
add_executable(workspaced src/workspace/main.cpp src/workspace/store.cpp src/workspace/git_command.cpp src/workspace/status.cpp src/workspace/process.cpp)
target_include_directories(workspaced PRIVATE src)
target_link_libraries(workspaced PRIVATE spatial-scene SQLite::SQLite3)
add_dependencies(workspaced spatial-app-init)
add_executable(spatial-test-host src/testing/main.cpp src/platform/worker.cpp)
target_link_libraries(spatial-test-host PRIVATE spatial-core)
add_dependencies(spatial-test-host worldd workspaced)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
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
    add_executable(spatial-input-bridge tests/native/input_bridge.cpp)
    target_link_libraries(spatial-input-bridge PRIVATE spatial-native-input)
    add_executable(spatial-xprobe native/xprobe.c)
    set_target_properties(spatial-xprobe PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    target_compile_definitions(spatial-xprobe PRIVATE SPATIAL_HAVE_XI2=1)
    target_link_libraries(spatial-xprobe PRIVATE X11::X11 X11::Xi)
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
