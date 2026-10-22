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
list(APPEND Protobuf_IMPORT_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1" "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1")
protobuf_generate_cpp(presentation_probe_sources presentation_probe_headers tests/proto/presentation_probe.proto)
protobuf_generate_cpp(presentation_proto_sources presentation_proto_headers proto/spatial/render/v1/presentation.proto)
protobuf_generate_cpp(compositor_proto_sources compositor_proto_headers proto/spatial/render/v1/compositor_native.proto)
protobuf_generate_cpp(file_actions_proto_sources file_actions_proto_headers proto/spatial/workspace/v1/file_actions.proto)
protobuf_generate_cpp(copy_job_journal_proto_sources copy_job_journal_proto_headers
    proto/spatial/workspace/v1/file_copy_job_journal.proto)
protobuf_generate_cpp(proto_sources proto_headers proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto)
protobuf_generate_cpp(input_proto_sources input_proto_headers proto/spatial/input/v1/input.proto)
protobuf_generate_cpp(security_proto_sources security_proto_headers proto/spatial/security/v1/security.proto)
protobuf_generate_cpp(selection_scope_proto_sources selection_scope_proto_headers proto/spatial/security/v1/selection_scope.proto)
protobuf_generate_cpp(native_scope_proto_sources native_scope_proto_headers proto/spatial/supervisor/v1/native_scope.proto)
protobuf_generate_cpp(broker_proto_sources broker_proto_headers proto/spatial/workspace/v1/broker.proto)
protobuf_generate_cpp(storage_proto_sources storage_proto_headers proto/spatial/workspace/v1/storage.proto)
protobuf_generate_cpp(cabinet_proto_sources cabinet_proto_headers proto/spatial/workspace/v1/cabinet.proto)
protobuf_generate_cpp(copy_worker_proto_sources copy_worker_proto_headers proto/spatial/workspace/v1/copy_worker.proto)
protobuf_generate_cpp(key_proto_sources key_proto_headers proto/spatial/identity/v1/key.proto)
protobuf_generate_cpp(issuer_proto_sources issuer_proto_headers proto/spatial/identity/v1/issuer.proto)
protobuf_generate_cpp(world_session_proto_sources world_session_proto_headers proto/spatial/identity/v1/world_session.proto)
protobuf_generate_cpp(geometry_view_proto_sources geometry_view_proto_headers proto/spatial/identity/v1/geometry_view.proto)
protobuf_generate_cpp(actor_client_tls_proto_sources actor_client_tls_proto_headers proto/spatial/identity/v1/actor_session_client.proto)
protobuf_generate_cpp(actor_transition_proto_sources actor_transition_proto_headers proto/spatial/identity/v1/actor_transition.proto)
protobuf_generate_cpp(actor_transition_crosshost_proto_sources actor_transition_crosshost_proto_headers
    tests/proto/actor_transition_crosshost.proto)
protobuf_generate_cpp(host_actor_controller_proto_sources host_actor_controller_proto_headers proto/spatial/identity/v1/host_actor_controller.proto)
protobuf_generate_cpp(host_session_proto_sources host_session_proto_headers proto/spatial/supervisor/v1/host_session.proto)
protobuf_generate_cpp(grants_proto_sources grants_proto_headers proto/spatial/authority/v1/grants.proto)
set(presentation_interface_generated_dir "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(presentation_interface_proto_cc "${presentation_interface_generated_dir}/spatial/workspace/presentation/v1/presentation.pb.cc")
set(presentation_interface_proto_h "${presentation_interface_generated_dir}/spatial/workspace/presentation/v1/presentation.pb.h")
add_custom_command(OUTPUT "${presentation_interface_proto_cc}" "${presentation_interface_proto_h}"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${presentation_interface_generated_dir}/spatial/workspace/presentation/v1"
    COMMAND protobuf::protoc "--cpp_out=${presentation_interface_generated_dir}"
        "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto"
        "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1"
        "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1"
        "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1"
        "${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/presentation/v1/presentation.proto"
    DEPENDS proto/spatial/workspace/presentation/v1/presentation.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/copy/v1/copy.proto protobuf::protoc VERBATIM)
protobuf_generate_cpp(scope_proto_sources scope_proto_headers proto/spatial/supervisor/v1/process_scope.proto)
protobuf_generate_cpp(launch_proto_sources launch_proto_headers proto/spatial/supervisor/v1/launch.proto)
protobuf_generate_cpp(cleanup_bootstrap_proto_sources cleanup_bootstrap_proto_headers
    proto/spatial/supervisor/v1/broker_cleanup_bootstrap.proto)
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/python")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/compositor_native_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1"
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1/compositor_native.proto
    DEPENDS proto/spatial/render/v1/compositor_native.proto protobuf::protoc VERBATIM)
add_custom_target(compositor-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/compositor_native_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1/presentation.proto
    DEPENDS proto/spatial/render/v1/presentation.proto proto/spatial/render/v1/compositor_native.proto
        proto/spatial/identity/v1/geometry_view.proto proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(presentation-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_pb2.py")
add_dependencies(presentation-python-protobuf geometry-view-python-protobuf)
add_dependencies(presentation-python-protobuf compositor-python-protobuf)

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_probe_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/presentation_probe.proto
    DEPENDS tests/proto/presentation_probe.proto proto/spatial/render/v1/compositor_native.proto
        proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(presentation-probe-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/presentation_probe_pb2.py")
add_dependencies(presentation-probe-python compositor-python-protobuf)

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/launch_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/launch.proto
    DEPENDS proto/spatial/workspace/v1/broker.proto proto/spatial/workspace/v1/storage.proto proto/spatial/supervisor/v1/launch.proto proto/spatial/supervisor/v1/process_scope.proto
        proto/spatial/supervisor/v1/native_scope.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
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
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1/core.proto
    DEPENDS proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/file_actions_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/file_actions.proto
    DEPENDS proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/file_copy_job_journal_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/file_copy_job_journal.proto
    DEPENDS proto/spatial/workspace/v1/file_copy_job_journal.proto proto/spatial/workspace/v1/file_actions.proto
        proto/spatial/authority/v1/grants.proto proto/spatial/supervisor/v1/process_scope.proto protobuf::protoc VERBATIM)
add_custom_target(copy-job-journal-python-protobuf DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/file_copy_job_journal_pb2.py")
add_custom_target(file-actions-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/file_actions_pb2.py")
add_dependencies(presentation-python-protobuf file-actions-python-protobuf)
add_dependencies(presentation-probe-python file-actions-python-protobuf)
add_dependencies(launch-python-protobuf file-actions-python-protobuf)
add_custom_target(python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/core_pb2.py")
add_dependencies(python-protobuf file-actions-python-protobuf)
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
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/broker.proto
    DEPENDS proto/spatial/supervisor/v1/process_scope.proto proto/spatial/supervisor/v1/native_scope.proto proto/spatial/workspace/v1/storage.proto proto/spatial/workspace/v1/broker.proto proto/spatial/core/v1/core.proto proto/spatial/authority/v1/grants.proto
        proto/spatial/identity/v1/issuer.proto proto/spatial/identity/v1/key.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(broker-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/broker_pb2.py")
add_dependencies(broker-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/storage_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/storage.proto
    DEPENDS proto/spatial/workspace/v1/storage.proto proto/spatial/supervisor/v1/process_scope.proto proto/spatial/authority/v1/grants.proto proto/spatial/workspace/v1/file_actions.proto
        protobuf::protoc VERBATIM)
add_custom_target(storage-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/storage_pb2.py")
add_dependencies(storage-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/cabinet_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/cabinet.proto
    DEPENDS proto/spatial/workspace/v1/cabinet.proto proto/spatial/authority/v1/grants.proto
        proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto
        proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(cabinet-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/cabinet_pb2.py")
add_dependencies(cabinet-python-protobuf file-actions-python-protobuf)
add_dependencies(cabinet-python-protobuf grants-python-protobuf python-protobuf security-python-protobuf)
add_dependencies(storage-python-protobuf grants-python-protobuf python-protobuf security-python-protobuf scope-python-protobuf)
add_dependencies(broker-python-protobuf storage-python-protobuf workspace-copy-python-protobuf)
add_dependencies(broker-python-protobuf cabinet-python-protobuf)
add_dependencies(launch-python-protobuf broker-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/key_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/key.proto
    DEPENDS proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(key-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/key_pb2.py")
add_dependencies(key-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/issuer_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/issuer.proto
    DEPENDS proto/spatial/identity/v1/issuer.proto proto/spatial/authority/v1/grants.proto proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(issuer-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/issuer_pb2.py")
add_dependencies(issuer-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/geometry_view_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/geometry_view.proto
    DEPENDS proto/spatial/identity/v1/geometry_view.proto proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto
        proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(geometry-view-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/geometry_view_pb2.py")
add_dependencies(geometry-view-python-protobuf file-actions-python-protobuf)
add_dependencies(geometry-view-python-protobuf python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/world_session_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/world_session.proto
    DEPENDS proto/spatial/identity/v1/world_session.proto proto/spatial/identity/v1/geometry_view.proto proto/spatial/authority/v1/grants.proto proto/spatial/identity/v1/issuer.proto
        proto/spatial/identity/v1/key.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(world-session-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/world_session_pb2.py")
add_dependencies(world-session-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/grants_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1/grants.proto
    DEPENDS proto/spatial/authority/v1/grants.proto proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(grants-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/grants_pb2.py")
add_dependencies(grants-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/copy_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1/copy.proto
    DEPENDS proto/spatial/workspace/copy/v1/copy.proto protobuf::protoc VERBATIM)
add_custom_target(workspace-copy-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/copy_pb2.py")
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/copy_worker_pb2.py"
        "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/v1/copy_worker_pb2.py"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/v1"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1/copy_worker.proto
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/src/workspace/copy_worker_python_compat.py"
        "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/v1/copy_worker_pb2.py"
    DEPENDS proto/spatial/workspace/v1/copy_worker.proto proto/spatial/workspace/copy/v1/copy.proto
        src/workspace/copy_worker_python_compat.py protobuf::protoc VERBATIM)
add_custom_target(copy-worker-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/copy_worker_pb2.py"
    "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/v1/copy_worker_pb2.py")
add_dependencies(copy-worker-python-protobuf workspace-copy-python-protobuf)
add_dependencies(storage-python-protobuf copy-worker-python-protobuf)
add_dependencies(python-protobuf copy-worker-python-protobuf)
add_dependencies(python-protobuf workspace-copy-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/presentation/v1/presentation_pb2.py"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/presentation/v1"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/presentation/v1/presentation.proto
    DEPENDS proto/spatial/workspace/presentation/v1/presentation.proto
        proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto
        proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(workspace-presentation-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/spatial/workspace/presentation/v1/presentation_pb2.py")
add_dependencies(workspace-presentation-python-protobuf file-actions-python-protobuf)
add_dependencies(workspace-presentation-python-protobuf python-protobuf)
add_dependencies(native-scope-python-protobuf security-python-protobuf)
add_dependencies(key-python-protobuf python-protobuf security-python-protobuf)
add_dependencies(issuer-python-protobuf key-python-protobuf grants-python-protobuf python-protobuf security-python-protobuf)
add_dependencies(broker-python-protobuf issuer-python-protobuf native-scope-python-protobuf key-python-protobuf
    python-protobuf grants-python-protobuf security-python-protobuf scope-python-protobuf storage-python-protobuf
    workspace-copy-python-protobuf)
add_custom_target(provision-storage-python-bindings)
add_dependencies(provision-storage-python-bindings broker-python-protobuf)
add_dependencies(world-session-python-protobuf geometry-view-python-protobuf issuer-python-protobuf grants-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/crosshost_revocation_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/crosshost_revocation.proto
    DEPENDS tests/proto/crosshost_revocation.proto proto/spatial/identity/v1/issuer.proto
        proto/spatial/supervisor/v1/launch.proto proto/spatial/workspace/v1/broker.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(crosshost-revocation-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/crosshost_revocation_pb2.py")
add_dependencies(crosshost-revocation-python-protobuf file-actions-python-protobuf)
add_dependencies(crosshost-revocation-python-protobuf issuer-python-protobuf launch-python-protobuf)

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/world_topology_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/world_topology.proto
    DEPENDS tests/proto/world_topology.proto protobuf::protoc VERBATIM)
add_custom_target(world-topology-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/world_topology_pb2.py")

add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_crosshost_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/actor_transition_crosshost.proto
    DEPENDS tests/proto/actor_transition_crosshost.proto proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto
        proto/spatial/identity/v1/actor_transition.proto proto/spatial/identity/v1/world_session.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(actor-transition-crosshost-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_crosshost_pb2.py")
add_dependencies(actor-transition-crosshost-python-protobuf file-actions-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/actor_session_client_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/actor_session_client.proto
    DEPENDS proto/spatial/identity/v1/actor_session_client.proto proto/spatial/identity/v1/issuer.proto
        proto/spatial/authority/v1/grants.proto proto/spatial/identity/v1/key.proto
        proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(actor-session-client-python-protobuf ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/actor_session_client_pb2.py")
add_dependencies(actor-session-client-python-protobuf file-actions-python-protobuf)

option(SPATIAL_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)
option(SPATIAL_INPUT_TESTS "Require the project XLibre actor-input counterexamples" OFF)
if(SPATIAL_SANITIZERS)
    set(m08_adapter_resource_profile "ASan-UBSan")
else()
    set(m08_adapter_resource_profile "Debug")
endif()
set(m08_adapter_resource_profile_argument "--copy-profile=${m08_adapter_resource_profile}")
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

add_library(spatial-protobuf STATIC ${presentation_proto_sources} ${presentation_proto_headers} ${compositor_proto_sources} ${compositor_proto_headers} ${native_scope_proto_sources} ${native_scope_proto_headers} ${proto_sources} ${proto_headers} ${broker_proto_sources} ${broker_proto_headers} ${key_proto_sources} ${key_proto_headers} ${issuer_proto_sources} ${issuer_proto_headers} ${world_session_proto_sources} ${world_session_proto_headers} ${geometry_view_proto_sources} ${geometry_view_proto_headers} ${actor_client_tls_proto_sources} ${actor_client_tls_proto_headers} ${actor_transition_proto_sources} ${actor_transition_proto_headers} ${host_actor_controller_proto_sources} ${host_actor_controller_proto_headers} ${grants_proto_sources} ${grants_proto_headers}
    ${presentation_interface_proto_cc} ${presentation_interface_proto_h} ${security_proto_sources} ${security_proto_headers} ${selection_scope_proto_sources} ${selection_scope_proto_headers})
target_link_libraries(spatial-protobuf PUBLIC protobuf::libprotobuf)
target_sources(spatial-protobuf PRIVATE ${scope_proto_sources} ${scope_proto_headers})
target_sources(spatial-protobuf PRIVATE ${storage_proto_sources} ${storage_proto_headers})
target_sources(spatial-protobuf PRIVATE ${cabinet_proto_sources} ${cabinet_proto_headers})
target_sources(spatial-protobuf PRIVATE ${file_actions_proto_sources} ${file_actions_proto_headers})
target_sources(spatial-protobuf PRIVATE ${copy_job_journal_proto_sources} ${copy_job_journal_proto_headers})
target_sources(spatial-protobuf PRIVATE ${copy_worker_proto_sources} ${copy_worker_proto_headers})
target_include_directories(spatial-protobuf PUBLIC "${CMAKE_CURRENT_BINARY_DIR}" "${presentation_interface_generated_dir}")
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
if(BUILD_TESTING)
    add_executable(process-scope-recovery-validation-test tests/core/process_scope_recovery.cpp)
    target_link_libraries(process-scope-recovery-validation-test PRIVATE spatial-process-scope)
    add_test(NAME process-scope-recovery-validation COMMAND process-scope-recovery-validation-test)
    add_executable(process-scope-named-regression-test tests/core/process_scope_named_regression.cpp)
    target_link_libraries(process-scope-named-regression-test PRIVATE spatial-process-scope)
    add_dependencies(process-scope-named-regression-test scope-python-protobuf)
endif()
add_library(spatial-storage-channel STATIC src/workspace/storage_channel.cpp)
target_link_libraries(spatial-storage-channel PUBLIC spatial-scene)
add_library(spatial-storage-client STATIC src/workspace/storage_client.cpp)
target_link_libraries(spatial-storage-client PUBLIC spatial-storage-channel PRIVATE OpenSSL::Crypto)
add_library(spatial-storage-devices STATIC src/workspace/storage_devices.cpp)
target_link_libraries(spatial-storage-devices PUBLIC spatial-scene)
add_library(spatial-storage-owner STATIC src/workspace/storage_owner.cpp)
add_library(spatial-file-operation-journal STATIC src/workspace/file_operation_journal.cpp)
target_include_directories(spatial-file-operation-journal PUBLIC src)
target_link_libraries(spatial-file-operation-journal PUBLIC spatial-protobuf SQLite::SQLite3 OpenSSL::Crypto)
add_library(spatial-broker-cleanup-bootstrap STATIC src/supervisor/broker_cleanup_bootstrap.cpp
    ${cleanup_bootstrap_proto_sources} ${cleanup_bootstrap_proto_headers})
target_include_directories(spatial-broker-cleanup-bootstrap PUBLIC src ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(spatial-broker-cleanup-bootstrap PUBLIC spatial-protobuf)
add_library(spatial-broker-copy-cleanup STATIC src/workspace/broker_copy_cleanup.cpp)
target_include_directories(spatial-broker-copy-cleanup PUBLIC src)
target_link_libraries(spatial-broker-copy-cleanup PUBLIC spatial-file-operation-journal spatial-process-scope spatial-authority-launch spatial-broker-cleanup-bootstrap)
add_library(spatial-file-engine STATIC src/workspace/file_engine.cpp)
target_include_directories(spatial-file-engine PUBLIC src)
target_link_libraries(spatial-file-engine PUBLIC spatial-protobuf spatial-workspace-copy-worker spatial-file-operation-journal OpenSSL::Crypto)
target_link_libraries(spatial-storage-owner PUBLIC spatial-scene spatial-storage-devices SQLite::SQLite3 OpenSSL::Crypto)
add_executable(spatial-storage-owner-service src/workspace/storage_main.cpp)
set_target_properties(spatial-storage-owner-service PROPERTIES OUTPUT_NAME spatial-storage-owner)
target_link_libraries(spatial-storage-owner-service PRIVATE spatial-storage-owner spatial-storage-channel)
add_library(spatial-workspace-resources STATIC src/workspace/resources.cpp)
target_link_libraries(spatial-workspace-resources PUBLIC spatial-process-scope spatial-identity-tls spatial-storage-devices spatial-file-operation-journal)
add_library(spatial-storage-launch STATIC src/supervisor/storage_launch.cpp)
target_link_libraries(spatial-storage-launch PUBLIC spatial-process-scope spatial-identity-tls)
add_executable(spatial-storage-launch-probe src/testing/storage_launch_probe.cpp)
target_link_libraries(spatial-storage-launch-probe PRIVATE spatial-storage-launch)
add_dependencies(spatial-storage-launch-probe storage-python-protobuf)
add_library(spatial-authority-launch STATIC src/supervisor/launch.cpp src/supervisor/child_handle.cpp src/supervisor/child_handle_client.cpp src/workspace/native_launch.cpp ${launch_proto_sources} ${launch_proto_headers})
target_link_libraries(spatial-authority-launch PUBLIC spatial-process-scope spatial-control-reply SQLite::SQLite3)
add_executable(spatial-authority-launch-probe src/testing/authority_launch_probe.cpp)
add_executable(spatial-authority-supervisor src/supervisor/main.cpp)
target_link_libraries(spatial-authority-supervisor PRIVATE spatial-authority-launch spatial-workspace-resources spatial-storage-launch spatial-broker-copy-cleanup spatial-broker-cleanup-bootstrap)
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
add_library(spatial-core STATIC src/core/protocol.cpp src/world/store.cpp src/world/storage_vfs.cpp src/world/workspace_presentations.cpp src/world/presentation_geometry.cpp src/world/actors.cpp src/world/objects.cpp src/world/surfaces.cpp src/platform/render_process.cpp src/platform/presentation_owner.cpp src/platform/render_worker.cpp src/platform/broker.cpp)
target_include_directories(spatial-core PUBLIC src)
target_link_libraries(spatial-core PUBLIC spatial-compositor-config spatial-presentation-stream spatial-scene SQLite::SQLite3 spatial-authority spatial-workspace-resources spatial-storage-launch spatial-broker-copy-cleanup spatial-broker-cleanup-bootstrap)
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
add_library(spatial-actor-session-client STATIC src/world/actor_session_client.cpp)
target_link_libraries(spatial-actor-session-client PUBLIC spatial-scene spatial-identity-tls)
add_library(spatial-actor-transition STATIC src/world/actor_transition_coordinator.cpp)
target_link_libraries(spatial-actor-transition PUBLIC spatial-actor-session-client spatial-protobuf)
add_executable(worldd src/world/actor_tls.cpp src/world/main.cpp src/world/server.cpp src/world/identity_sessions.cpp src/world/authority.cpp)
target_link_libraries(worldd PRIVATE spatial-compositor-config spatial-core spatial-identity spatial-actor-session-client spatial-issuer-watch-lib spatial-authority spatial-participant-runtime-lib)
add_dependencies(worldd spatialwm)
add_library(spatial-app-initializer STATIC src/workspace/app_initializer.cpp)
target_link_libraries(spatial-app-initializer PUBLIC spatial-scene)
add_executable(spatial-app-init src/workspace/app_init.cpp src/workspace/hardened_sanitizer.cpp)
target_link_libraries(spatial-app-init PRIVATE spatial-app-initializer)
add_executable(spatial-copy-finalize-worker src/workspace/copy_finalize_worker.cpp)
target_include_directories(spatial-copy-finalize-worker PRIVATE src)
target_link_libraries(spatial-copy-finalize-worker PRIVATE spatial-scene OpenSSL::Crypto)
install(TARGETS spatial-copy-finalize-worker RUNTIME DESTINATION libexec)
add_executable(spatial-copy-tree-worker src/workspace/copy_tree_worker.cpp)
target_link_libraries(spatial-copy-tree-worker PRIVATE OpenSSL::Crypto spatial-protobuf)
add_dependencies(spatial-copy-tree-worker copy-worker-python-protobuf)
install(TARGETS spatial-copy-tree-worker RUNTIME DESTINATION libexec)
add_library(spatial-workspace-copy-worker STATIC src/workspace/copy_worker.cpp)
target_include_directories(spatial-workspace-copy-worker PUBLIC src)
target_link_libraries(spatial-workspace-copy-worker PUBLIC spatial-storage-client spatial-workspace-resources spatial-scene spatial-protobuf)
add_executable(workspaced src/workspace/main.cpp src/workspace/store.cpp src/workspace/copy_journal.cpp src/workspace/git_command.cpp src/workspace/status.cpp src/workspace/process.cpp src/workspace/authority.cpp src/workspace/native_launch.cpp src/workspace/control.cpp src/workspace/control_channels.cpp)
target_include_directories(workspaced PRIVATE src)
target_link_libraries(workspaced PRIVATE spatial-file-operation-journal spatial-file-engine spatial-workspace-copy-worker spatial-storage-client spatial-workspace-resources spatial-scene SQLite::SQLite3 spatial-authority spatial-participant-runtime-lib spatial-broker-cleanup-bootstrap)
add_dependencies(workspaced spatial-copy-tree-worker spatial-copy-finalize-worker)
add_dependencies(workspaced spatial-app-init)
add_executable(spatial-test-host src/testing/main.cpp src/platform/worker.cpp)
target_link_libraries(spatial-test-host PRIVATE spatial-core)
add_dependencies(spatial-test-host worldd workspaced)
add_custom_target(spatial-m07-fence-runtime-fixture)
add_dependencies(spatial-m07-fence-runtime-fixture
    identityd spatial-issuer-client worldd workspaced spatial-app-init spatial-process-scope-probe
    broker-python-protobuf workspace-copy-python-protobuf python-protobuf grants-python-protobuf
    issuer-python-protobuf key-python-protobuf native-scope-python-protobuf scope-python-protobuf
    security-python-protobuf storage-python-protobuf geometry-view-python-protobuf world-session-python-protobuf)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/native_input_observer_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/testing/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/testing/v1/native_input_observer.proto
        DEPENDS proto/spatial/testing/v1/native_input_observer.proto protobuf::protoc VERBATIM)
    add_custom_target(native-input-observer-python-protobuf ALL
        DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/native_input_observer_pb2.py")
    add_test(NAME native-input-observer-artifact COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_input_state_observer.py")
    set_tests_properties(native-input-observer-artifact PROPERTIES TIMEOUT 20
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME native-input-observer-collector COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_input_state_observer_collector.py")
    set_tests_properties(native-input-observer-collector PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME broker-instance-registry-provisioning COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_instance_registry.py")
    set_tests_properties(broker-instance-registry-provisioning PROPERTIES TIMEOUT 20)
    add_test(NAME broker-cgroup-events-parser COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_cgroup_events.py")
    set_tests_properties(broker-cgroup-events-parser PROPERTIES TIMEOUT 20)
    add_executable(process-scope-controller-reset-test tests/core/process_scope_controller_reset.cpp)
    target_include_directories(process-scope-controller-reset-test PRIVATE src)
    add_test(NAME process-scope-controller-reset COMMAND $<TARGET_FILE:process-scope-controller-reset-test>)
    set_tests_properties(process-scope-controller-reset PROPERTIES TIMEOUT 20)
    add_executable(storage-owner-state-metadata-test tests/core/storage_owner_state_metadata.cpp)
    target_include_directories(storage-owner-state-metadata-test PRIVATE src)
    add_test(NAME storage-owner-state-metadata COMMAND $<TARGET_FILE:storage-owner-state-metadata-test>)
    set_tests_properties(storage-owner-state-metadata PROPERTIES TIMEOUT 20)
    target_compile_definitions(spatial-process-scope PRIVATE SPATIAL_PROCESS_SCOPE_READTRACE=1)
    target_compile_definitions(spatial-file-engine PRIVATE SPATIAL_TEST_COPY_RECOVERY_FAULTS=1)
    target_compile_definitions(spatial-file-operation-journal PRIVATE SPATIAL_TEST_FILE_JOURNAL_FAULTS=1)
    target_compile_definitions(workspaced PRIVATE
        SPATIAL_TEST_FILE_JOURNAL_FAULTS=1
        SPATIAL_TEST_FILE_ACTION_DIAGNOSTICS=1)
    target_compile_definitions(spatial-process-scope PRIVATE SPATIAL_TEST_SECURITY_EVIDENCE_FORWARD=1)
    target_compile_definitions(spatial-process-scope-probe PRIVATE SPATIAL_TEST_SECURITY_EVIDENCE_FORWARD=1)
    add_executable(broker-copy-cleanup-test tests/core/broker_copy_cleanup.cpp)
    target_link_libraries(broker-copy-cleanup-test PRIVATE spatial-broker-copy-cleanup spatial-file-engine spatial-workspace-resources)
    add_dependencies(broker-copy-cleanup-test copy-job-journal-python-protobuf
        python-protobuf security-python-protobuf workspace-copy-python-protobuf)
    add_test(NAME broker-copy-preprune-cleanup COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_copy_cleanup.py" $<TARGET_FILE:broker-copy-cleanup-test>
        --participant-mode-set controls-plus-15)
    set_tests_properties(broker-copy-preprune-cleanup PROPERTIES TIMEOUT 90 RUN_SERIAL TRUE
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME broker-copy-preprune-deadline-multirow COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_copy_cleanup.py" $<TARGET_FILE:broker-copy-cleanup-test>
        --only-participant-mode deadline-multirow)
    set_tests_properties(broker-copy-preprune-deadline-multirow PROPERTIES TIMEOUT 90 RUN_SERIAL TRUE
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_executable(workspace-file-operation-journal-test tests/core/file_operation_journal.cpp)
    target_include_directories(workspace-file-operation-journal-test PRIVATE src)
    target_link_libraries(workspace-file-operation-journal-test PRIVATE spatial-file-operation-journal)
    add_test(NAME workspace-file-operation-journal COMMAND $<TARGET_FILE:workspace-file-operation-journal-test>)
    set_tests_properties(workspace-file-operation-journal PROPERTIES TIMEOUT 60)
    add_executable(file-action-effect-wait-test tests/core/file_action_effect_wait.cpp)
    target_link_libraries(file-action-effect-wait-test PRIVATE spatial-file-engine)
    add_test(NAME workspace-file-effect-observer-exception COMMAND $<TARGET_FILE:file-action-effect-wait-test>)
    set_tests_properties(workspace-file-effect-observer-exception PROPERTIES TIMEOUT 30)
    add_executable(broker-file-action-client-test tests/core/broker_file_action_client.cpp)
    target_link_libraries(broker-file-action-client-test PRIVATE spatial-core)
    add_test(NAME broker-file-action-client COMMAND $<TARGET_FILE:broker-file-action-client-test>)
    set_tests_properties(broker-file-action-client PROPERTIES TIMEOUT 30)
    add_dependencies(copy-job-journal-python-protobuf grants-python-protobuf scope-python-protobuf file-actions-python-protobuf)
    add_custom_target(workspace-file-action-fixtures
        COMMAND "${CMAKE_COMMAND}" -E env "PYTHONPYCACHEPREFIX=${CMAKE_CURRENT_BINARY_DIR}/pycache-file-action-fixtures" "${Python3_EXECUTABLE}" -m py_compile "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_file_action_broker.py" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_copy_engine.py" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_staging_mask.py" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_storage_git_integration.py"
        DEPENDS copy-job-journal-python-protobuf workspaced worldd identityd spatial-issuer-client spatial-process-scope-probe spatial-storage-launch-probe storage-python-protobuf workspace-file-operation-journal-test file-action-effect-wait-test broker-file-action-client-test broker-python-protobuf cabinet-python-protobuf file-actions-python-protobuf world-session-python-protobuf compositor-python-protobuf
        SOURCES tests/core/test_file_action_broker.py tests/core/test_workspace_authority.py tests/core/test_workspace_copy_engine.py tests/core/test_copy_staging_mask.py tests/native/test_storage_git_integration.py tests/core/file_operation_journal.cpp tests/core/broker_file_action_client.cpp tests/core/file_action_effect_wait.cpp)
    add_test(NAME copy-progress-timing-tooling COMMAND Python3::Interpreter -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_progress_timing.py")
    set_tests_properties(copy-progress-timing-tooling PROPERTIES TIMEOUT 30)
    add_test(NAME storage-owner-fixture-tooling COMMAND Python3::Interpreter -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_storage_owner_fixture_tooling.py")
    set_tests_properties(storage-owner-fixture-tooling PROPERTIES TIMEOUT 30 ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python")
    add_test(NAME provision-storage-root-owner-tooling COMMAND Python3::Interpreter -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_provision_storage_root_owner.py")
    set_tests_properties(provision-storage-root-owner-tooling PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-copy-staging-mask COMMAND Python3::Interpreter -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_staging_mask.py")
    set_tests_properties(workspace-copy-staging-mask PROPERTIES TIMEOUT 30)
    add_test(NAME workspace-file-action-broker COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native --copy-engine-v2 -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_file_action_broker.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k file_action_)
    set_tests_properties(workspace-file-action-broker PROPERTIES TIMEOUT 1080
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core")
    add_test(NAME workspace-file-action-storage-owner-btrfs COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_file_action_broker.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k storage_owner_authenticated_btrfs_copy_and_quota_rejection)
    set_tests_properties(workspace-file-action-storage-owner-btrfs PROPERTIES TIMEOUT 360
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core")
    add_executable(workspace-file-engine-test tests/core/file_engine.cpp)
    target_link_libraries(workspace-file-engine-test PRIVATE spatial-file-engine)
    add_test(NAME workspace-file-engine COMMAND $<TARGET_FILE:workspace-file-engine-test>)
    set_tests_properties(workspace-file-engine PROPERTIES TIMEOUT 60)
    add_test(NAME copy-worker-stream COMMAND $<TARGET_FILE:workspace-file-engine-test> --copy-worker-stream-only)
    set_tests_properties(copy-worker-stream PROPERTIES TIMEOUT 30
        ENVIRONMENT "ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_dependencies(workspace-file-action-fixtures workspace-file-engine-test)
    add_executable(queued-copy-status-replacement-test tests/core/queued_copy_status_replacement.cpp)
    target_include_directories(queued-copy-status-replacement-test PRIVATE src)
    target_link_libraries(queued-copy-status-replacement-test PRIVATE spatial-protobuf)
    add_test(NAME queued-copy-status-replacement COMMAND $<TARGET_FILE:queued-copy-status-replacement-test>)
    set_tests_properties(queued-copy-status-replacement PROPERTIES TIMEOUT 30)
    add_executable(spatial-copy-tree-worker-fixture src/workspace/copy_tree_worker.cpp)
    target_compile_definitions(spatial-copy-tree-worker-fixture PRIVATE SPATIAL_COPY_WORKER_TEST_FAULTS=1)
    target_link_libraries(spatial-copy-tree-worker-fixture PRIVATE OpenSSL::Crypto spatial-protobuf)
    add_dependencies(spatial-copy-tree-worker-fixture copy-worker-python-protobuf)
    add_test(NAME copy-tree-worker COMMAND Python3::Interpreter
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_tree_worker.py" $<TARGET_FILE:spatial-copy-tree-worker-fixture>
        CopyTreeWorkerTests)
    set_tests_properties(copy-tree-worker PROPERTIES TIMEOUT 60)
    add_test(NAME copy-tree-worker-nested-mount COMMAND Python3::Interpreter
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/run_copy_nested_mount_fixture.py"
        $<TARGET_FILE:spatial-copy-tree-worker-fixture>
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_tree_worker.py")
    set_tests_properties(copy-tree-worker-nested-mount PROPERTIES TIMEOUT 90)
    add_executable(cabinet-copy-path-test tests/core/cabinet_copy_path.cpp)
    target_link_libraries(cabinet-copy-path-test PRIVATE spatial-workspace-copy-worker)
    add_test(NAME cabinet-copy-path COMMAND $<TARGET_FILE:cabinet-copy-path-test>)
    set_tests_properties(cabinet-copy-path PROPERTIES TIMEOUT 30)
    add_test(NAME copy-finalize-worker COMMAND Python3::Interpreter
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_finalize_worker.py" $<TARGET_FILE:spatial-copy-finalize-worker>)
    set_tests_properties(copy-finalize-worker PROPERTIES TIMEOUT 180)

    add_executable(presentation-metadata-test tests/core/presentation_metadata.cpp)
    target_include_directories(presentation-metadata-test PRIVATE src)
    target_link_libraries(presentation-metadata-test PRIVATE spatial-scene)
    add_test(NAME presentation-metadata COMMAND $<TARGET_FILE:presentation-metadata-test>)
    set_tests_properties(presentation-metadata PROPERTIES TIMEOUT 30)
    add_executable(world-diagnostic-timing-test tests/core/world_diagnostic_timing.cpp)
    target_include_directories(world-diagnostic-timing-test PRIVATE src)
    add_dependencies(spatial-test-host world-diagnostic-timing-test)
    add_test(NAME world-diagnostic-timing COMMAND $<TARGET_FILE:world-diagnostic-timing-test>)
    protobuf_generate_cpp(actor_transition_integration_sources actor_transition_integration_headers
        tests/proto/actor_transition_integration.proto)
    add_executable(spatial-actor-transition-integration-driver src/testing/actor_transition_integration_driver.cpp
        ${actor_transition_integration_sources} ${actor_transition_integration_headers})
    target_link_libraries(spatial-actor-transition-integration-driver PRIVATE spatial-actor-transition spatial-core)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_integration_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/actor_transition_integration.proto
        DEPENDS tests/proto/actor_transition_integration.proto proto/spatial/identity/v1/actor_transition.proto
            proto/spatial/identity/v1/issuer.proto proto/spatial/identity/v1/world_session.proto
            proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
    add_custom_target(actor-transition-integration-python DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_integration_pb2.py")
    add_dependencies(actor-transition-integration-python file-actions-python-protobuf)
    add_dependencies(actor-transition-integration-python python-protobuf issuer-python-protobuf
        host-actor-python-protobuf key-python-protobuf grants-python-protobuf security-python-protobuf world-session-python-protobuf broker-python-protobuf scope-python-protobuf)
    add_dependencies(spatial-actor-transition-integration-driver actor-transition-integration-python spatial-process-scope-probe)
    add_test(NAME host-session-startup COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_host_session_startup.py" $<TARGET_FILE:spatial-host-session>)
    set_tests_properties(host-session-startup PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-actor-session-client-probe src/testing/actor_session_client_probe.cpp)
    target_link_libraries(spatial-actor-session-client-probe PRIVATE spatial-actor-session-client Threads::Threads)
    add_test(NAME actor-session-client COMMAND $<TARGET_FILE:spatial-actor-session-client-probe>)
    add_test(NAME actor-geometry-client-hostile-peer
        COMMAND $<TARGET_FILE:spatial-actor-session-client-probe> --geometry-only)
    set_tests_properties(actor-geometry-client-hostile-peer PROPERTIES TIMEOUT 30)
    add_test(NAME actor-geometry-world-headless COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_geometry_view.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(actor-geometry-world-headless PROPERTIES TIMEOUT 600
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}/tests/core:${CMAKE_CURRENT_SOURCE_DIR}")
    set_tests_properties(actor-session-client PROPERTIES TIMEOUT 30)
    add_executable(spatial-actor-session-client-tls-probe src/testing/actor_session_client_tls_probe.cpp)
    target_link_libraries(spatial-actor-session-client-tls-probe PRIVATE spatial-actor-session-client Threads::Threads)
    add_test(NAME actor-session-client-tls COMMAND $<TARGET_FILE:spatial-actor-session-client-tls-probe>)
    set_tests_properties(actor-session-client-tls PROPERTIES TIMEOUT 60)

    add_executable(spatial-actor-transition-coordinator-probe src/testing/actor_transition_coordinator_probe.cpp)
    target_link_libraries(spatial-actor-transition-coordinator-probe PRIVATE spatial-actor-transition Threads::Threads)
    add_test(NAME actor-transition-coordinator COMMAND $<TARGET_FILE:spatial-actor-transition-coordinator-probe>)
    set_tests_properties(actor-transition-coordinator PROPERTIES TIMEOUT 30)
    add_test(NAME actor-transition-real-world COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_transition_integration.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>
        $<TARGET_FILE:spatial-actor-transition-integration-driver>)
    set_tests_properties(actor-transition-real-world PROPERTIES TIMEOUT 300
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-actor-transition-crosshost-probe src/testing/actor_transition_crosshost_probe.cpp)
    add_library(spatial-actor-transition-crosshost-test-protobuf STATIC
        ${actor_transition_crosshost_proto_sources} ${actor_transition_crosshost_proto_headers})
    target_link_libraries(spatial-actor-transition-crosshost-test-protobuf PUBLIC spatial-protobuf)
    target_link_libraries(spatial-actor-transition-crosshost-probe PRIVATE
        spatial-actor-transition spatial-actor-transition-crosshost-test-protobuf)
    add_dependencies(spatial-actor-transition-crosshost-probe actor-transition-crosshost-python-protobuf)
    add_dependencies(spatial-actor-transition-crosshost-probe actor-session-client-python-protobuf)
    set(SPATIAL_ACTOR_TRANSITION_PLAN "" CACHE FILEPATH "Private protobuf plan for real cross-host actor qualification")
    if(SPATIAL_ACTOR_TRANSITION_PLAN)
        add_test(NAME actor-transition-crosshost COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_transition_crosshost.py"
            $<TARGET_FILE:spatial-actor-transition-crosshost-probe> "${SPATIAL_ACTOR_TRANSITION_PLAN}")
        set_tests_properties(actor-transition-crosshost PROPERTIES TIMEOUT 720
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    endif()
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
    add_executable(spatial-copy-worker-production-probe src/testing/copy_worker_production_probe.cpp)
    target_link_libraries(spatial-copy-worker-production-probe PRIVATE spatial-workspace-copy-worker OpenSSL::Crypto)
    add_dependencies(spatial-copy-worker-production-probe storage-python-protobuf broker-python-protobuf scope-python-protobuf copy-worker-python-protobuf)
    add_dependencies(spatial-copy-worker-production-probe spatial-copy-tree-worker
        spatial-storage-owner-service spatial-storage-owner-probe)
    add_test(NAME copy-worker-production COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_copy_worker_production.py"
        "${CMAKE_CURRENT_BINARY_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")
    set_tests_properties(copy-worker-production PROPERTIES TIMEOUT 900 RUN_SERIAL TRUE
        RESOURCE_LOCK spatial-copy-worker
        ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1;PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_executable(spatial-storage-client-probe src/testing/storage_client_probe.cpp)
    target_link_libraries(spatial-storage-client-probe PRIVATE spatial-storage-client)
    add_executable(spatial-storage-owner-probe src/testing/storage_owner_probe.cpp)
    target_link_libraries(spatial-storage-owner-probe PRIVATE spatial-storage-owner)
    add_dependencies(spatial-storage-owner-probe broker-python-protobuf storage-python-protobuf)
    add_test(NAME storage-snapshot-owner COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_storage_snapshot.py"
        $<TARGET_FILE:spatial-storage-owner-probe> "${CMAKE_CURRENT_SOURCE_DIR}")
    set_tests_properties(storage-snapshot-owner PROPERTIES TIMEOUT 900
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}")
    add_executable(workspace-copy-journal-test tests/core/workspace_copy_journal.cpp src/workspace/copy_journal.cpp)
    target_include_directories(workspace-copy-journal-test PRIVATE src)
    target_link_libraries(workspace-copy-journal-test PRIVATE spatial-scene SQLite::SQLite3 OpenSSL::Crypto)
    add_test(NAME workspace-copy-journal COMMAND $<TARGET_FILE:workspace-copy-journal-test>)
    set_tests_properties(workspace-copy-journal PROPERTIES TIMEOUT 60)
    add_test(NAME workspace-copy-journal-migration COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_copy_journal_migration.py" $<TARGET_FILE:workspaced>)
    set_tests_properties(workspace-copy-journal-migration PROPERTIES TIMEOUT 60)
    add_test(NAME workspace-execution-v7-migration COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_m07_execution_migrations.py" $<TARGET_FILE:workspaced>)
    set_tests_properties(workspace-execution-v7-migration PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};PYTHONDONTWRITEBYTECODE=1;SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_custom_target(workspace-execution-v7-migration-artifacts
        DEPENDS workspaced workspace-copy-python-protobuf)
    add_executable(workspace-presentation-store-test tests/core/workspace_presentation_store.cpp)
    target_include_directories(workspace-presentation-store-test PRIVATE src)
    target_link_libraries(workspace-presentation-store-test PRIVATE spatial-core)
    add_test(NAME workspace-presentation-store COMMAND $<TARGET_FILE:workspace-presentation-store-test>)
    set_tests_properties(workspace-presentation-store PROPERTIES TIMEOUT 60)
    add_executable(workspace-presentation-projection-test tests/core/test_workspace_presentation_projection.cpp)
    target_include_directories(workspace-presentation-projection-test PRIVATE src)
    target_link_libraries(workspace-presentation-projection-test PRIVATE spatial-core)
    add_test(NAME workspace-presentation-projection COMMAND $<TARGET_FILE:workspace-presentation-projection-test>)
    set_tests_properties(workspace-presentation-projection PROPERTIES TIMEOUT 60)
    add_test(NAME workspace-presentation-actor COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_presentation_actor.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-presentation-actor PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_custom_target(workspace-presentation-actor-fixture
        DEPENDS identityd spatial-issuer-client worldd workspaced
            broker-python-protobuf world-session-python-protobuf spatial-process-scope-probe
            spatial-security-auth)
    add_test(NAME workspace-actor-cabinet-gateway COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_private_test_evidence.py" --
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_cabinet_gateway.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-actor-cabinet-gateway PROPERTIES TIMEOUT 300
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core;PYTHONDONTWRITEBYTECODE=1;PYTHONPYCACHEPREFIX=${CMAKE_CURRENT_BINARY_DIR}/pycache-actor-cabinet-gateway")
    add_test(NAME actor-cabinet-gateway-instrumentation COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_cabinet_gateway_instrumentation.py")
    set_tests_properties(actor-cabinet-gateway-instrumentation PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core;PYTHONDONTWRITEBYTECODE=1;PYTHONPYCACHEPREFIX=${CMAKE_CURRENT_BINARY_DIR}/pycache-actor-cabinet-gateway-instrumentation")
    add_custom_target(actor-cabinet-gateway-instrumentation-fixture
        SOURCES tests/core/test_actor_cabinet_gateway_instrumentation.py tests/core/gateway_trace.py)
    add_dependencies(actor-cabinet-gateway-instrumentation-fixture broker-python-protobuf grants-python-protobuf world-session-python-protobuf)
    add_test(NAME workspace-private-test-evidence COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_private_test_evidence.py")
    set_tests_properties(workspace-private-test-evidence PROPERTIES TIMEOUT 30)
    add_custom_target(workspace-actor-cabinet-gateway-fixture
        COMMAND "${CMAKE_COMMAND}" -E env "PYTHONPYCACHEPREFIX=${CMAKE_CURRENT_BINARY_DIR}/pycache-actor-cabinet-gateway"
            "${Python3_EXECUTABLE}" -m py_compile
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_cabinet_gateway.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_presentation_actor.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_private_test_evidence.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_private_test_evidence.py"
        DEPENDS workspace-presentation-actor-fixture
            spatial-copy-tree-worker spatial-copy-finalize-worker copy-worker-python-protobuf
            python-protobuf file-actions-python-protobuf grants-python-protobuf
            world-session-python-protobuf broker-python-protobuf workspace-copy-python-protobuf
            issuer-python-protobuf key-python-protobuf
        SOURCES tests/core/test_actor_cabinet_gateway.py
            tools/with_private_test_evidence.py tools/test_private_test_evidence.py
        VERBATIM)
    add_test(NAME workspace-presentation-shared-representation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_shared_representation_actor.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-presentation-shared-representation PROPERTIES TIMEOUT 300
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_custom_target(workspace-presentation-shared-representation-fixture
        DEPENDS identityd spatial-issuer-client worldd workspaced spatial-security-auth
            spatial-process-scope-probe broker-python-protobuf world-session-python-protobuf)
    add_custom_target(actor-pixels-fixture
        DEPENDS identityd spatial-issuer-client worldd workspaced spatial-security-auth
            spatial-process-scope-probe spatial-confined-xprobe
            broker-python-protobuf world-session-python-protobuf)
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
    add_test(NAME authority-launch-denial-trace-contract COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_issuer_launch_denial_trace.py")
    set_tests_properties(authority-launch-denial-trace-contract PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
    add_test(NAME process-scope-named-regression COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_process_scope_named_regression.py"
        $<TARGET_FILE:process-scope-named-regression-test>)
    set_tests_properties(process-scope-named-regression PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant_races.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-participant-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-participant COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
    add_test(NAME identity-participant-domain-addition COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_participant_domain_addition.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(identity-participant-domain-addition PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_custom_target(identity-participant-domain-addition-artifacts
        DEPENDS identityd spatial-issuer-client issuer-python-protobuf key-python-protobuf
                grants-python-protobuf python-protobuf)
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap_races.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-bootstrap-races PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1/grants_probe.proto
        DEPENDS proto/spatial/authority/v1/grants_probe.proto proto/spatial/authority/v1/grants.proto
            proto/spatial/core/v1/core.proto proto/spatial/security/v1/security.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
    add_custom_target(grants-probe-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/grants_probe_pb2.py")
    add_dependencies(grants-probe-python file-actions-python-protobuf)
    add_test(NAME authority-store COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority.py" $<TARGET_FILE:spatial-authority-probe>)
    set_tests_properties(authority-store PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME authority-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_authority_bootstrap.py"
        $<TARGET_FILE:spatial-authority-probe> $<TARGET_FILE:spatial-authority-migrate>)
    set_tests_properties(authority-bootstrap PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_custom_target(workspace-copy-engine-fixture DEPENDS workspaced worldd identityd spatial-issuer-client spatial-security-auth
        spatial-process-scope-probe spatial-storage-owner-service spatial-app-init
        spatial-copy-tree-worker spatial-copy-finalize-worker broker-python-protobuf
        copy-worker-python-protobuf storage-python-protobuf issuer-python-protobuf grants-python-protobuf
        world-session-python-protobuf)
    add_test(NAME workspace-copy-fixture-profiles COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_copy_fixture_profiles.py")
    set_tests_properties(workspace-copy-fixture-profiles PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME observation-resource-fixture-tooling COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_observation_resource_fixture.py")
    set_tests_properties(observation-resource-fixture-tooling PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-copy-worker-helpers-tooling COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_copy_worker_helpers.py")
    set_tests_properties(workspace-copy-worker-helpers-tooling PROPERTIES TIMEOUT 30)
    add_test(NAME workspace-copy-process-scope-readtrace-source COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_process_scope_readtrace_source.py")
    set_tests_properties(workspace-copy-process-scope-readtrace-source PROPERTIES TIMEOUT 30)
    set(copy_fixture_profile_arguments)
    if(SPATIAL_SANITIZERS)
        list(APPEND copy_fixture_profile_arguments --profile=ASan-UBSan)
    endif()
    add_test(NAME workspace-copy-engine COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_workspace_copy_engine.py" "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env ${copy_fixture_profile_arguments})
    set_tests_properties(workspace-copy-engine PROPERTIES TIMEOUT 1080 RUN_SERIAL TRUE
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core")
    add_custom_target(workspace-copy-fault-fixture
        DEPENDS workspace-copy-engine-fixture spatial-authority-supervisor launch-python-protobuf
            native-scope-python-protobuf scope-python-protobuf
        SOURCES tests/core/test_workspace_copy_faults.py
            tests/native/copy_fault_supervised_world.py
            harness/copy_fault_evidence.py
            tools/test_copy_fault_evidence.py
            tools/test_workspace_copy_revocation_denial.py
            tools/test_workspace_copy_journal_observer.py
            tools/inspect_workspace_copy_supervisor_closure.py
            tests/native/storage_owner_copy_fault_relay.py
            tools/inspect_workspace_copy_fault_fixture.py
            tools/run_workspace_copy_faults.py)
    add_test(NAME workspace-copy-revocation-denial COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_workspace_copy_revocation_denial.py")
    set_tests_properties(workspace-copy-revocation-denial PROPERTIES TIMEOUT 30)
    add_test(NAME workspace-copy-journal-observer COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_workspace_copy_journal_observer.py")
    set_tests_properties(workspace-copy-journal-observer PROPERTIES TIMEOUT 30)
    add_test(NAME workspace-copy-fault-evidence-durability COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_copy_fault_evidence.py")
    set_tests_properties(workspace-copy-fault-evidence-durability PROPERTIES TIMEOUT 30)
    add_test(NAME workspace-copy-fault-relay-timing COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_storage_owner_copy_fault_relay_timing.py")
    set_tests_properties(workspace-copy-fault-relay-timing PROPERTIES TIMEOUT 30)
    add_test(NAME workspace-copy-supervised-recovery-adapter-contract COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_supervised_copy_recovery_adapter_contract.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-copy-supervised-recovery-adapter-contract PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core:${CMAKE_CURRENT_SOURCE_DIR}/tests/native")
    add_test(NAME workspace-copy-storage-broker-constructor-failures COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_copy_faults.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>
        StorageBrokerConstructorFailureTests)
    set_tests_properties(workspace-copy-storage-broker-constructor-failures PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core:${CMAKE_CURRENT_SOURCE_DIR}/tests/native")
    add_test(NAME workspace-copy-supervised-sigkill-recovery COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_file_action_supervised_copy_recovery.py"
        "${CMAKE_CURRENT_BINARY_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
        test_sigkill_checked_retirement_and_restart_reconcile_same_copy_journal ${copy_fixture_profile_arguments})
    set_tests_properties(workspace-copy-supervised-sigkill-recovery PROPERTIES TIMEOUT 1080 RUN_SERIAL TRUE RESOURCE_LOCK spatial-workspace-copy
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core:${CMAKE_CURRENT_SOURCE_DIR}/tests/native:${CMAKE_CURRENT_SOURCE_DIR}/tools")
    function(add_supervised_copy_recovery_case case_name selector)
        add_test(NAME workspace-copy-supervised-${case_name}-recovery COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_file_action_supervised_copy_recovery.py"
            "${CMAKE_CURRENT_BINARY_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
            ${selector} ${copy_fixture_profile_arguments})
        set_tests_properties(workspace-copy-supervised-${case_name}-recovery PROPERTIES
            TIMEOUT 1080 RUN_SERIAL TRUE RESOURCE_LOCK spatial-workspace-copy
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core:${CMAKE_CURRENT_SOURCE_DIR}/tests/native:${CMAKE_CURRENT_SOURCE_DIR}/tools")
    endfunction()
    add_supervised_copy_recovery_case(prepared-worker-proof
        test_prepared_worker_proof_survives_supervised_prepublication_crash)
    add_supervised_copy_recovery_case(published-prepared
        test_published_prepared_supervised_restart_syncs_before_complete)
    add_supervised_copy_recovery_case(published-sync-failure
        test_published_prepared_sync_failure_stays_uncertain_after_supervised_restart)
    add_custom_target(workspace-copy-supervised-recovery-fixture
        DEPENDS workspace-copy-fault-fixture copy-job-journal-python-protobuf
        SOURCES tests/core/test_supervised_copy_recovery_adapter_contract.py
            tests/core/test_workspace_copy_faults.py
            tests/core/test_file_action_supervised_copy_recovery.py
            tests/core/test_workspace_copy_engine.py
            tests/core/test_workspace_authority.py
            tests/core/test_world_identity.py
            tests/core/test_issuer_client.py
            tests/native/copy_fault_supervised_world.py
            tests/native/supervised_copy_broker_recovery.py
            harness/issuer_relay.py
            harness/issuer_launch_denial.py
            harness/resource_broker.py
            tests/core/test_issuer_launch_denial_trace.py
            tests/native/supervised_delegation_parent.py
            tests/native/storage_owner_copy_fault_relay.py
            tests/native/test_security_identity.py
            tests/native/display_ready.py
            tests/native/native_display_geometry.py
            harness/copy_fault_evidence.py
            harness/issuer_progress.py
            harness/wire.py
            tools/run_file_action_supervised_copy_recovery.py
            tools/inspect_file_action_supervised_copy_recovery.py
            tools/copy_fixture_profiles.py
            tools/with_resource_delegate.py
            tools/with_adapter_resources.py
            tools/with_copy_worker_helpers.py)
    add_test(NAME workspace-copy-absolute-deadline COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_workspace_copy_faults.py" "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
        test_absolute_copy_budget_expires_across_real_subtimeout_owner_replies ${copy_fixture_profile_arguments})
    add_test(NAME workspace-copy-actor-revocation COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_workspace_copy_faults.py" "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
        test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication ${copy_fixture_profile_arguments})
    add_test(NAME workspace-copy-lost-publication-ack COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_workspace_copy_faults.py" "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
        test_lost_publication_ack_restart_reconciles_exact_receipt_without_duplicate_publish ${copy_fixture_profile_arguments})
    add_test(NAME workspace-copy-hostile-git COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_workspace_copy_faults.py" "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
        test_hostile_git_hooks_filters_and_config_copy_sanitizes_admin_without_host_effects ${copy_fixture_profile_arguments})
    add_test(NAME workspace-copy-worker-death-recovery COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/run_workspace_copy_faults.py" "${CMAKE_CURRENT_BINARY_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}" --evidence-from-env
        test_real_copy_worker_death_restart_preserves_unresolved_output_without_relaunch ${copy_fixture_profile_arguments})
    set_tests_properties(workspace-copy-absolute-deadline workspace-copy-actor-revocation
        workspace-copy-lost-publication-ack workspace-copy-hostile-git workspace-copy-worker-death-recovery
        PROPERTIES TIMEOUT 450 RUN_SERIAL TRUE RESOURCE_LOCK spatial-workspace-copy
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/core")
    add_test(NAME workspace-authority COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-authority PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-authorization-fence COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k authorization_fence)
    set_tests_properties(workspace-authorization-fence PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-authorization-fence-scoped COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k scoped_authority)
    add_test(NAME workspace-authorization-fence-registry COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k registry_authority_binding)
    add_test(NAME workspace-authorization-fence-credential COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k credential_revocation_preserves_valid_workspace_process)
    add_test(NAME workspace-authorization-fence-unrelated-revocation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k unrelated_revocation_refuses_inflight_fence)
    add_test(NAME workspace-authorization-fence-no-authority COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_authority.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k application_access_fails_closed_without_authority_fence)
    add_test(NAME workspace-authorization-fence-bootstrap COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_bootstrap.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> -k authorization_fence)
    set_tests_properties(workspace-authorization-fence-registry workspace-authorization-fence-scoped workspace-authorization-fence-credential
        workspace-authorization-fence-unrelated-revocation workspace-authorization-fence-no-authority workspace-authorization-fence-bootstrap PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME authenticated-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
    add_executable(spatial-issuer-failure-diagnostic tests/core/issuer_failure_diagnostic.cpp)
    target_link_libraries(spatial-issuer-failure-diagnostic PRIVATE spatial-issuer-client-lib)
    add_dependencies(spatial-issuer-client spatial-issuer-failure-diagnostic)
    add_test(NAME identity-revocation-command COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_revocation_command.py" $<TARGET_FILE:identityd>
        $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-revoke>)
    set_tests_properties(identity-revocation-command PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_executable(spatial-presentation-owner-probe src/testing/presentation_owner_probe.cpp)
    target_link_libraries(spatial-presentation-owner-probe PRIVATE spatial-core)
    add_test(NAME presentation-owner COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_presentation_owner.py" $<TARGET_FILE:spatial-presentation-owner-probe>)
    set_tests_properties(presentation-owner PROPERTIES TIMEOUT 90
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
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_control_client_live.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(broker-control-client-live PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME workspace-participant-native COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant_native.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(workspace-participant-native PROPERTIES TIMEOUT 120
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME workspace-participant-settlement COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant_settlement.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(workspace-participant-settlement PROPERTIES TIMEOUT 120
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME control-owner-channels COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_control_owner_channels.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(control-owner-channels PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME application-control-lease COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_application_control_lease.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(application-control-lease PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME workspace-revocation-durability COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_revocation_durability.py"
            $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(workspace-revocation-durability PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME application-observation COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_application_observation.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(application-observation PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME world-application-control COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native --runtime-seconds 600 -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_application_control.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(world-application-control PROPERTIES TIMEOUT 660
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME actor-pixels COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actor_pixels.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(actor-pixels PROPERTIES TIMEOUT 240
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME application-access COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_native_launch.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-application-launch PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME native-actor-binding COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_actor_binding.py" $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(native-actor-binding PROPERTIES TIMEOUT 180
            ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
        add_test(NAME shared-render-client COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_shared_render_client.py" $<TARGET_FILE:spatialwm> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(shared-render-client PROPERTIES TIMEOUT 90
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
        add_custom_target(broker-issuer-progress-fixture DEPENDS workspaced identityd spatial-issuer-client worldd
            spatial-security-auth spatial-process-scope-probe spatial-app-init broker-python-protobuf
            world-session-python-protobuf issuer-python-protobuf key-python-protobuf grants-python-protobuf
            security-python-protobuf native-scope-python-protobuf)
        add_test(NAME broker-issuer-progress COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_broker_issuer_progress.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd> $<TARGET_FILE:spatial-security-auth>)
        set_tests_properties(broker-issuer-progress PROPERTIES TIMEOUT 180 RUN_SERIAL TRUE
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1;PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1;SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}")
        add_custom_target(world-issuer-progress-fixture DEPENDS identityd spatial-issuer-client worldd spatialwm spatial-xprobe
            python-protobuf issuer-python-protobuf key-python-protobuf grants-python-protobuf world-session-python-protobuf)
        add_test(NAME world-issuer-progress COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_world_issuer_progress.py" $<TARGET_FILE:identityd>
            $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
        set_tests_properties(world-issuer-progress PROPERTIES TIMEOUT 120 RUN_SERIAL TRUE
            ENVIRONMENT "PYTHONDONTWRITEBYTECODE=1;PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_XPROBE=$<TARGET_FILE:spatial-xprobe>;ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1;SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE}")
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
    add_executable(spatial-snapshot-size-regression tests/core/test_snapshot_size.cpp)
    target_link_libraries(spatial-snapshot-size-regression PRIVATE spatial-core)
    add_test(NAME snapshot-size-regression COMMAND $<TARGET_FILE:spatial-snapshot-size-regression>)
    set_tests_properties(snapshot-size-regression PROPERTIES TIMEOUT 60)
    add_test(NAME actor-runtime COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_actors.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:worldd>)
    set_tests_properties(actor-runtime PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME recovery-boundaries COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_recovery.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:worldd>)
    set_tests_properties(recovery-boundaries PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-git COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native --runtime-seconds 360 -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_git.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:spatial-git-job-probe> $<TARGET_FILE:spatial-git-boundary-probe>)
    # The 360s native service budget leaves 60s under CTest for wrapper and process retirement.
    set_tests_properties(workspace-git PROPERTIES TIMEOUT 420
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-context COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspaces.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-context PROPERTIES TIMEOUT 90
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME workspace-processes COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_processes.py" $<TARGET_FILE:spatial-test-host> $<TARGET_FILE:workspaced> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-processes PROPERTIES TIMEOUT 120
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME adapter-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
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
if(BUILD_TESTING)
    target_compile_definitions(spatial-session-broker PRIVATE SPATIAL_TEST_SECURITY_EVIDENCE_FORWARD=1)
endif()
target_link_libraries(spatial-session-broker PUBLIC spatial-authority-launch spatial-core spatial-control-admin)
add_library(spatial-host-actor-controller STATIC src/supervisor/host_actor_controller.cpp)
target_link_libraries(spatial-host-actor-controller PUBLIC spatial-session-broker spatial-actor-transition spatial-protobuf)
add_executable(spatial-host-session src/supervisor/host_session_main.cpp ${host_session_proto_sources} ${host_session_proto_headers})
target_include_directories(spatial-host-session PRIVATE src)
target_link_libraries(spatial-host-session PRIVATE spatial-host-actor-controller spatial-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/host_session_pb2.py"
    COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
        ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1/host_session.proto
    DEPENDS proto/spatial/supervisor/v1/host_session.proto proto/spatial/supervisor/v1/launch.proto
        proto/spatial/identity/v1/host_actor_controller.proto proto/spatial/identity/v1/actor_transition.proto
        proto/spatial/identity/v1/actor_session_client.proto proto/spatial/identity/v1/issuer.proto
        proto/spatial/identity/v1/world_session.proto
        proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
add_custom_target(host-session-python-protobuf ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/host_session_pb2.py")
add_dependencies(host-session-python-protobuf file-actions-python-protobuf)
add_dependencies(spatial-host-session provision-storage-python-bindings host-session-python-protobuf actor-session-client-python-protobuf launch-python-protobuf scope-python-protobuf
    native-scope-python-protobuf broker-python-protobuf storage-python-protobuf key-python-protobuf
    issuer-python-protobuf world-session-python-protobuf grants-python-protobuf python-protobuf security-python-protobuf)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/host_actor_controller_pb2.py"
        "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/host_actor_controller.proto
            ${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1/actor_transition.proto
        DEPENDS proto/spatial/identity/v1/host_actor_controller.proto proto/spatial/identity/v1/actor_transition.proto
            proto/spatial/core/v1/core.proto proto/spatial/workspace/copy/v1/copy.proto proto/spatial/workspace/v1/file_actions.proto
            protobuf::protoc VERBATIM)
add_custom_target(host-actor-python-protobuf DEPENDS
        "${CMAKE_CURRENT_BINARY_DIR}/python/host_actor_controller_pb2.py"
        "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_pb2.py")
add_dependencies(host-actor-python-protobuf file-actions-python-protobuf)
add_dependencies(spatial-host-session host-actor-python-protobuf)
if(BUILD_TESTING)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/geometry_native_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/proto
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/render/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/proto/geometry_native.proto
        DEPENDS tests/proto/geometry_native.proto proto/spatial/supervisor/v1/host_session.proto proto/spatial/workspace/v1/file_actions.proto
            protobuf::protoc VERBATIM)
    add_custom_target(geometry-native-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/geometry_native_pb2.py")
    add_dependencies(geometry-native-python file-actions-python-protobuf)
    add_dependencies(geometry-native-python host-session-python-protobuf)
    add_dependencies(spatial-host-session geometry-native-python presentation-python-protobuf)
    add_test(NAME geometry-process-identity-witness COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_geometry_process_identity.py")
    set_tests_properties(geometry-process-identity-witness PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME geometry-native-fixture-regressions COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_geometry_host_control_handoff.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(geometry-native-fixture-regressions PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/native")
    add_test(NAME workspace-native-factory COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_native_factory.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-native-factory PROPERTIES TIMEOUT 60
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}:${CMAKE_CURRENT_SOURCE_DIR}/tests/native")
    add_test(NAME geometry-presentation-native COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_geometry_presentation.py"
        $<TARGET_FILE:spatialwm> $<TARGET_FILE:spatial-security-auth>)
    set_tests_properties(geometry-presentation-native PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME geometry-native-crosshost COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_geometry_native_crosshost.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(geometry-native-crosshost PROPERTIES TIMEOUT 1800
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};SPATIAL_GEOMETRY_EVIDENCE=$ENV{SPATIAL_GEOMETRY_EVIDENCE};SPATIAL_GEOMETRY_OBSERVER_HOST=$ENV{SPATIAL_GEOMETRY_OBSERVER_HOST};SPATIAL_GEOMETRY_AUTHORITY_ADDRESS=$ENV{SPATIAL_GEOMETRY_AUTHORITY_ADDRESS};SPATIAL_GEOMETRY_REMOTE_SOURCE=$ENV{SPATIAL_GEOMETRY_REMOTE_SOURCE};SPATIAL_GEOMETRY_REMOTE_BIN=$ENV{SPATIAL_GEOMETRY_REMOTE_BIN};SPATIAL_GEOMETRY_REMOTE_PYTHON=$ENV{SPATIAL_GEOMETRY_REMOTE_PYTHON};SPATIAL_GEOMETRY_REMOTE_XSERVER=$ENV{SPATIAL_GEOMETRY_REMOTE_XSERVER};SPATIAL_GEOMETRY_REMOTE_EVIDENCE=$ENV{SPATIAL_GEOMETRY_REMOTE_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME workspace-geometry-native-crosshost COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- env
        "SPATIAL_GEOMETRY_EVIDENCE=$ENV{SPATIAL_GEOMETRY_EVIDENCE}"
        "SPATIAL_GEOMETRY_OBSERVER_HOST=$ENV{SPATIAL_GEOMETRY_OBSERVER_HOST}"
        "SPATIAL_GEOMETRY_AUTHORITY_ADDRESS=$ENV{SPATIAL_GEOMETRY_AUTHORITY_ADDRESS}"
        "SPATIAL_GEOMETRY_REMOTE_SOURCE=$ENV{SPATIAL_GEOMETRY_REMOTE_SOURCE}"
        "SPATIAL_GEOMETRY_REMOTE_BIN=$ENV{SPATIAL_GEOMETRY_REMOTE_BIN}"
        "SPATIAL_GEOMETRY_REMOTE_PYTHON=$ENV{SPATIAL_GEOMETRY_REMOTE_PYTHON}"
        "SPATIAL_GEOMETRY_REMOTE_XSERVER=$ENV{SPATIAL_GEOMETRY_REMOTE_XSERVER}"
        "SPATIAL_GEOMETRY_REMOTE_EVIDENCE=$ENV{SPATIAL_GEOMETRY_REMOTE_EVIDENCE}"
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_geometry_native_crosshost.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-geometry-native-crosshost PROPERTIES TIMEOUT 1800
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME workspace-cabinet-native-crosshost COMMAND
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- env
        "SPATIAL_GEOMETRY_EVIDENCE=$ENV{SPATIAL_GEOMETRY_EVIDENCE}"
        "SPATIAL_GEOMETRY_OBSERVER_HOST=$ENV{SPATIAL_GEOMETRY_OBSERVER_HOST}"
        "SPATIAL_GEOMETRY_AUTHORITY_ADDRESS=$ENV{SPATIAL_GEOMETRY_AUTHORITY_ADDRESS}"
        "SPATIAL_GEOMETRY_REMOTE_SOURCE=$ENV{SPATIAL_GEOMETRY_REMOTE_SOURCE}"
        "SPATIAL_GEOMETRY_REMOTE_BIN=$ENV{SPATIAL_GEOMETRY_REMOTE_BIN}"
        "SPATIAL_GEOMETRY_REMOTE_PYTHON=$ENV{SPATIAL_GEOMETRY_REMOTE_PYTHON}"
        "SPATIAL_GEOMETRY_REMOTE_XSERVER=$ENV{SPATIAL_GEOMETRY_REMOTE_XSERVER}"
        "SPATIAL_GEOMETRY_REMOTE_EVIDENCE=$ENV{SPATIAL_GEOMETRY_REMOTE_EVIDENCE}"
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_test_display.py" --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_cabinet_native_crosshost.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-cabinet-native-crosshost PROPERTIES TIMEOUT 1800
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_custom_target(workspace-cabinet-native-crosshost-fixture
        DEPENDS identityd spatial-issuer-client worldd spatial-authority-launch-probe spatial-security-auth
            spatial-host-session spatialwm workspaced spatial-process-scope-probe
            spatial-storage-broker-launch-probe spatial-confined-xprobe session-probe-python compositor-python-protobuf
            broker-python-protobuf world-session-python-protobuf geometry-native-python
            host-actor-python-protobuf actor-session-client-python-protobuf grants-python-protobuf python-protobuf)
    add_custom_target(workspace-geometry-native-crosshost-fixture
        DEPENDS identityd spatial-issuer-client worldd spatial-authority-launch-probe spatial-security-auth
            spatial-host-session spatialwm workspaced spatial-process-scope-probe
            spatial-storage-broker-launch-probe spatial-confined-xprobe session-probe-python compositor-python-protobuf
            broker-python-protobuf world-session-python-protobuf geometry-native-python
            host-actor-python-protobuf actor-session-client-python-protobuf grants-python-protobuf python-protobuf)
endif()

if(BUILD_TESTING)
    add_dependencies(spatial-actor-transition-crosshost-probe host-actor-python-protobuf
        world-session-python-protobuf issuer-python-protobuf key-python-protobuf
        grants-python-protobuf security-python-protobuf python-protobuf)
    add_executable(spatial-host-actor-native-runner src/testing/host_actor_native_runner.cpp)
    target_link_libraries(spatial-host-actor-native-runner PRIVATE spatial-host-actor-controller)
    add_dependencies(spatial-host-actor-native-runner host-actor-python-protobuf world-session-python-protobuf
        compositor-python-protobuf
        spatial-authority-launch-probe spatial-security-auth identityd spatial-issuer-client
        spatial-authority-supervisor worldd spatialwm workspaced spatial-confined-xprobe
        spatial-storage-broker-launch-probe session-probe-python)
    add_test(NAME host-actor-controller-native COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_host_actor_controller.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(host-actor-controller-native PROPERTIES TIMEOUT 300
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME host-session-production-native COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_host_session_production.py"
        "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:spatial-host-session>)
    set_tests_properties(host-session-production-native PROPERTIES
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    set_tests_properties(host-session-production-native PROPERTIES TIMEOUT 1200)
    add_dependencies(spatial-host-session spatial-authority-launch-probe spatial-security-auth
        identityd spatial-issuer-client spatial-authority-supervisor worldd spatialwm workspaced)
    add_executable(spatial-host-actor-controller-probe src/testing/host_actor_controller_probe.cpp)
    target_link_libraries(spatial-host-actor-controller-probe PRIVATE spatial-host-actor-controller Threads::Threads)
    add_test(NAME host-actor-controller COMMAND $<TARGET_FILE:spatial-host-actor-controller-probe>)
    set_tests_properties(host-actor-controller PROPERTIES TIMEOUT 45)
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
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/src/testing/session_probe.proto
        DEPENDS src/testing/session_probe.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
    add_custom_target(session-probe-python ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/python/session_probe_pb2.py")
    add_dependencies(session-probe-python file-actions-python-protobuf)
    add_dependencies(spatial-session-application-probe session-probe-python world-session-python-protobuf)
    add_test(NAME session-resources COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_resources.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-resources PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-operator-revocation COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_operator_revocation.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-operator-revocation PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-application COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_application.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-application PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME storage-session-launcher-contract COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_storage_session_launcher.py")
    set_tests_properties(storage-session-launcher-contract PROPERTIES TIMEOUT 30)
    add_test(NAME proc-identity-observer-contract COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_proc_identity.py")
    set_tests_properties(proc-identity-observer-contract PROPERTIES TIMEOUT 30)
    add_test(NAME session-storage-device-bindings COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_storage_session_resources.py")
    set_tests_properties(session-storage-device-bindings PROPERTIES TIMEOUT 30
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python")
    add_test(NAME session-storage-owner COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_storage_session.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    add_test(NAME session-storage-contention COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_storage_contention.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    add_test(NAME operator-storage COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
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
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_dependent_cleanup.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-dependent-cleanup PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-coordinator-retirement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_coordinator_retirement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-coordinator-retirement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-broker-cleanup COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_broker_cleanup.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-broker-cleanup PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-broker-retirement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_broker_retirement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-broker-retirement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-stopped-broker-dependency COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_stopped_broker_dependency.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-stopped-broker-dependency PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-deferred-fence COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_deferred_fence.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-deferred-fence PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    protobuf_generate_cpp(recovery_probe_sources recovery_probe_headers src/testing/actor_transition_recovery_probe.proto)
    add_executable(spatial-actor-transition-recovery-probe src/testing/actor_transition_recovery_probe.cpp
        ${session_probe_sources} ${session_probe_headers} ${recovery_probe_sources} ${recovery_probe_headers})
    target_link_libraries(spatial-actor-transition-recovery-probe PRIVATE spatial-session-broker spatial-actor-transition)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_recovery_probe_pb2.py"
        COMMAND protobuf::protoc --python_out=${CMAKE_CURRENT_BINARY_DIR}/python
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/src/testing
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/supervisor/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/security/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/identity/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/authority/v1
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/core/v1
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto/spatial/workspace/copy/v1
            ${CMAKE_CURRENT_SOURCE_DIR}/src/testing/actor_transition_recovery_probe.proto
        DEPENDS src/testing/actor_transition_recovery_probe.proto src/testing/session_probe.proto
            proto/spatial/identity/v1/actor_transition.proto proto/spatial/workspace/v1/file_actions.proto protobuf::protoc VERBATIM)
    add_custom_target(actor-transition-recovery-python DEPENDS
        "${CMAKE_CURRENT_BINARY_DIR}/python/actor_transition_recovery_probe_pb2.py")
    add_dependencies(actor-transition-recovery-python file-actions-python-protobuf)
    add_dependencies(spatial-actor-transition-recovery-probe actor-transition-recovery-python host-actor-python-protobuf session-probe-python
        world-session-python-protobuf compositor-python-protobuf native-scope-python-protobuf
        broker-python-protobuf scope-python-protobuf python-protobuf security-python-protobuf
        issuer-python-protobuf grants-python-protobuf key-python-protobuf
        worldd spatialwm workspaced spatial-authority-supervisor spatial-authority-launch-probe
        spatial-security-auth identityd spatial-issuer-client spatial-storage-broker-launch-probe
        spatial-app-init spatial-confined-xprobe spatial-storage-owner-service spatial-process-scope-probe
        spatial-key spatial-participant-runtime-probe storage-python-protobuf presentation-python-protobuf)
    add_test(NAME actor-transition-recovery COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_actor_transition_recovery.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(actor-transition-recovery PROPERTIES TIMEOUT 360
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-replacement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
        "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/native/test_session_replacement.py" "${CMAKE_CURRENT_SOURCE_DIR}"
        $<TARGET_FILE:spatial-authority-launch-probe> $<TARGET_FILE:spatial-security-auth>
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client>)
    set_tests_properties(session-replacement PROPERTIES TIMEOUT 240
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR};SPATIAL_TEST_XSERVER=$ENV{SPATIAL_TEST_XSERVER};SPATIAL_SECURITY_EVIDENCE=$ENV{SPATIAL_SECURITY_EVIDENCE};ASAN_OPTIONS=detect_leaks=1:abort_on_error=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
    add_test(NAME session-broker-replacement COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_resource_delegate.py" --native -- "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tools/with_adapter_resources.py" ${m08_adapter_resource_profile_argument} --
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
