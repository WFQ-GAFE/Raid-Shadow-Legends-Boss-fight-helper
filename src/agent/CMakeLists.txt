cmake_minimum_required(VERSION 3.24)
project(RaidChimeraAgent LANGUAGES CXX)

set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
option(RAID_HYDRA_RESEARCH_CAPTURE "Retired live Hydra research capture; enabling this option is prohibited" OFF)
option(RAID_HYDRA_SELECTOR_HOOK "Retired live Hydra selector interception; enabling this option is prohibited" OFF)
if(RAID_HYDRA_RESEARCH_CAPTURE)
  message(FATAL_ERROR "Live Hydra research input capture is retired after a repeatable GameAssembly access violation in the MessagePack/GC handle path. Use offline capture and simulation; do not build or inject this feature.")
endif()
if(RAID_HYDRA_SELECTOR_HOOK)
  message(FATAL_ERROR "The live Hydra selector hook is retired after repeated game-client crashes. Use the offline simulator; do not build or inject this hook.")
endif()

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
  add_executable(test_battle_random_observation test_battle_random_observation.cpp)
  target_compile_features(test_battle_random_observation PRIVATE cxx_std_20)
  # These checks must also execute in the Release test configuration.
  target_compile_options(test_battle_random_observation PRIVATE /UNDEBUG)
  add_test(NAME battle_random_observation COMMAND test_battle_random_observation)
  add_executable(test_selection_observation test_selection_observation.cpp)
  target_compile_features(test_selection_observation PRIVATE cxx_std_20)
  target_compile_options(test_selection_observation PRIVATE /UNDEBUG)
  add_test(NAME selection_observation COMMAND test_selection_observation)
endif()
