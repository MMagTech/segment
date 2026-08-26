#!/bin/sh
# Builds the headless bench harness. Point LIBRETRO_INCLUDE at a directory
# holding libretro.h; any copy works, the header is stable across cores.
set -e
cd "$(dirname "$0")/../.."
INC="${LIBRETRO_INCLUDE:-$HOME/Development/RetroArch-reference/libretro-common/include}"
[ -f "$INC/libretro.h" ] || { echo "no libretro.h under $INC; set LIBRETRO_INCLUDE" >&2; exit 1; }
cc -O2 -Wall -Wextra -I "$INC" -o tools/bench/libretro_bench tools/bench/libretro_bench.c
echo "Wrote tools/bench/libretro_bench"
