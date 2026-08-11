# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
cmake_minimum_required(VERSION 3.22)
project(fabpipe_tools CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Read-only references into the MusaCAD dev tree: public headers + prebuilt
# release static libraries. Nothing in musa_cad is modified or rebuilt here.
set(MUSACAD_ROOT "/home/pranay/projects/musa_cad" CACHE PATH "MusaCAD source tree")
set(MUSACAD_BUILD "${CMAKE_CURRENT_SOURCE_DIR}/musacad-libs" CACHE PATH
    "MusaCAD libs built out-of-tree by tools/build.sh (musa_cad stays untouched)")

find_package(Qt6 REQUIRED COMPONENTS Gui Widgets)

add_executable(musa_plot musa_plot.cpp)
target_include_directories(musa_plot PRIVATE "${MUSACAD_ROOT}/include")
target_link_libraries(musa_plot PRIVATE
    "${MUSACAD_BUILD}/src/ui/libmusacad_ui.a"
    "${MUSACAD_BUILD}/src/command/libmusacad_command.a"
    "${MUSACAD_BUILD}/src/render/libmusacad_render.a"
    "${MUSACAD_BUILD}/src/core/libmusacad_core.a"
    Qt6::Gui
    Qt6::Widgets)
set_target_properties(musa_plot PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin")
