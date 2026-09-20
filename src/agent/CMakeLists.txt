cmake_minimum_required(VERSION 3.24)
project(RaidChimeraAgent LANGUAGES CXX)

set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)

add_subdirectory(
  "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/minhook"
  "${CMAKE_BINARY_DIR}/minhook"
)

add_library(RaidChimeraAgent SHARED agent.cpp)
target_compile_features(RaidChimeraAgent PRIVATE cxx_std_20)
target_compile_definitions(RaidChimeraAgent PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(RaidChimeraAgent PRIVATE minhook)
set_target_properties(RaidChimeraAgent PROPERTIES OUTPUT_NAME "RaidChimeraAgent")

include(CTest)
if(BUILD_TESTING)
  add_executable(test_shared_json_payload test_shared_json_payload.cpp)
  target_compile_features(test_shared_json_payload PRIVATE cxx_std_20)
  add_test(NAME shared_json_overflow COMMAND test_shared_json_payload)
  add_executable(test_result_confirmation test_result_confirmation.cpp)
  target_compile_features(test_result_confirmation PRIVATE cxx_std_20)
  add_test(NAME result_confirmation COMMAND test_result_confirmation)
endif()
