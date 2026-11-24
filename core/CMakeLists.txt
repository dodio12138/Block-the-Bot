cmake_minimum_required(VERSION 3.16)
project(tbt_core VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_library(tbt_obj OBJECT src/pieces.cpp src/search.cpp src/game.cpp src/capi.cpp)
set_target_properties(tbt_obj PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(tbt_obj PUBLIC include)
if(NOT MSVC)
  target_compile_options(tbt_obj PRIVATE -Wall -Wextra)
endif()

# Shared library loaded by the Python agent service (ctypes).
add_library(tbt_core SHARED $<TARGET_OBJECTS:tbt_obj>)
target_include_directories(tbt_core PUBLIC include)

add_library(tbt_core_static STATIC $<TARGET_OBJECTS:tbt_obj>)
target_include_directories(tbt_core_static PUBLIC include)

add_executable(tbt_headless tools/headless.cpp)
target_link_libraries(tbt_headless PRIVATE tbt_core_static)

add_executable(tbt_tests ../tests/core/test_core.cpp)
target_link_libraries(tbt_tests PRIVATE tbt_core_static)

enable_testing()
add_test(NAME core_tests COMMAND tbt_tests)
