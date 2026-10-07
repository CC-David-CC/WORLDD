cmake_minimum_required(VERSION 3.22)
project(spatial_runtime VERSION 0.1.0 LANGUAGES CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "Build the runtime on sb16 or sb16-2 with Linux.")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
find_package(nlohmann_json 3.11 REQUIRED)

option(SPATIAL_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)
add_compile_options(-Wall -Wextra -Wpedantic -Werror)
if(SPATIAL_SANITIZERS)
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer)
    add_link_options(-fsanitize=address,undefined)
endif()

# Include all production inputs and build settings in the reported identity.
# Reconfigure on edits as well as newly added sources. Never report only HEAD
# for a binary that may contain uncommitted source changes.
file(GLOB_RECURSE build_inputs CONFIGURE_DEPENDS src/*.cpp src/*.hpp)
list(APPEND build_inputs "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt")
list(SORT build_inputs)
set(build_material "${CMAKE_CXX_COMPILER_ID}:${CMAKE_CXX_COMPILER_VERSION}:${CMAKE_BUILD_TYPE}:${CMAKE_CXX_FLAGS}:${CMAKE_CXX_FLAGS_DEBUG}:${CMAKE_CXX_FLAGS_RELEASE}:${CMAKE_CXX_FLAGS_RELWITHDEBINFO}:${SPATIAL_SANITIZERS}:${nlohmann_json_VERSION}")
foreach(input IN LISTS build_inputs)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${input}")
    file(SHA256 "${input}" input_hash)
    file(RELATIVE_PATH input_name "${CMAKE_CURRENT_SOURCE_DIR}" "${input}")
    string(APPEND build_material "\n${input_name}:${input_hash}")
endforeach()
string(SHA256 build_hash "${build_material}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/build_id.hpp" "#pragma once\n#define SPATIAL_BUILD_ID \"sha256:${build_hash}\"\n")

add_library(spatial-core STATIC src/core/protocol.cpp)
target_include_directories(spatial-core PUBLIC src PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(spatial-core PUBLIC nlohmann_json::nlohmann_json)
add_executable(spatial-test-host src/testing/main.cpp)
target_link_libraries(spatial-test-host PRIVATE spatial-core)

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    add_test(NAME core-protocol COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_protocol.py" $<TARGET_FILE:spatial-test-host>)
    set_tests_properties(core-protocol PROPERTIES TIMEOUT 30)
endif()
