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

# ---- 内嵌推理进程 tbt_ai_worker（复制改编自 FromLev 的 sd_ai_worker）----
# 输出到 game/bin，Agent 服务按相对路径找到它；模型放在 game/models（不进 git）。
# llama.cpp 固定 b11700（MIT），静态链接，只用 CPU。本机已有源码时直接用：
#   默认找 ~/WorkSpace/GameDev/FromLev/build/gd/_deps/llama-src，或 -DFETCHCONTENT_SOURCE_DIR_LLAMA=<目录>
# TBT_AI_NATIVE：按本机 CPU 优化（开发用）；发行包要关掉，否则别的 CPU 上可能出现非法指令。
option(TBT_AI_WORKER "build tbt_ai_worker" ON)
option(TBT_AI_LLAMA "link llama.cpp into tbt_ai_worker" ON)
option(TBT_AI_NATIVE "optimize llama.cpp for this CPU (turn off for release builds)" ON)
if(TBT_AI_WORKER)
  find_package(Threads REQUIRED)
  add_executable(tbt_ai_worker ai/worker.cpp ai/json.cpp)
  target_link_libraries(tbt_ai_worker PRIVATE Threads::Threads)
  set_target_properties(tbt_ai_worker PROPERTIES RUNTIME_OUTPUT_DIRECTORY "$<1:${CMAKE_CURRENT_SOURCE_DIR}/../game/bin>")
  if(TBT_AI_LLAMA)
    include(FetchContent)
    set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
    set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
    set(LLAMA_ALL_WARNINGS OFF CACHE BOOL "" FORCE)
    set(GGML_METAL OFF CACHE BOOL "" FORCE)
    set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
    set(GGML_NATIVE ${TBT_AI_NATIVE} CACHE BOOL "" FORCE)
    set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
    set(_fromlev_llama "$ENV{HOME}/WorkSpace/GameDev/FromLev/build/gd/_deps/llama-src")
    if(NOT FETCHCONTENT_SOURCE_DIR_LLAMA AND EXISTS "${_fromlev_llama}/CMakeLists.txt")
      set(FETCHCONTENT_SOURCE_DIR_LLAMA "${_fromlev_llama}")
      message(STATUS "tbt_ai_worker: using local llama.cpp source ${_fromlev_llama}")
    endif()
    FetchContent_Declare(llama
      URL https://github.com/ggml-org/llama.cpp/archive/refs/tags/b11700.tar.gz
      URL_HASH SHA256=02b2dfbaea4c24719aa2aa11f0a5a1b77df05f351156c9f9040305cd489e3d55
      DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
    FetchContent_MakeAvailable(llama)
    target_link_libraries(tbt_ai_worker PRIVATE llama)
    target_compile_definitions(tbt_ai_worker PRIVATE TBT_WITH_LLAMA=1)
  endif()
endif()
