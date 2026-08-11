#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
# Build the project-local plot harness -> tools/bin/musa_plot
set -euo pipefail
cd "$(dirname "$0")"
MUSACAD_ROOT="${MUSACAD_ROOT:-/home/pranay/projects/musa_cad}"
# MusaCAD static libs, built OUT-OF-TREE here (musa_cad stays untouched);
# rebuild after pulling upstream so headers and libs never diverge
if [ ! -f musacad-libs/src/ui/libmusacad_ui.a ]; then
  cmake -S "$MUSACAD_ROOT" -B musacad-libs -DCMAKE_BUILD_TYPE=Release >/dev/null
fi
cmake --build musacad-libs --target musacad_core musacad_render       musacad_ui musacad_command -j"$(nproc)" >/dev/null
cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build -j"$(nproc)"
echo "built: $(dirname "$0")/bin/musa_plot"
