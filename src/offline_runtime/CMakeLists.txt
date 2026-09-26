cmake_minimum_required(VERSION 3.20)
project(RaidOfflineRuntimeProbe LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
# Windows subsystem: the desktop app runs this at every Hydra battle start and
# it must never create a console window, whichever caller starts it. Results
# still flow through inherited pipe handles; wmain attaches to a parent console
# for manual use.
add_executable(raid_offline_probe WIN32 probe.cpp)
target_link_options(raid_offline_probe PRIVATE /ENTRY:wmainCRTStartup)
target_compile_definitions(raid_offline_probe PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(raid_offline_probe PRIVATE userenv advapi32 ole32)
