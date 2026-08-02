#!/bin/sh
# 從 AI 原始輸出重建 master sprite。任何一步的參數改了都要重跑這個。
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python
$PY tools/pixelate.py art/approved/brindle_guard/master_stand_r_source.png \
    --width 50 --colors 14 \
    --palette specs/palettes/brindle_guard.json \
    --remap  art/approved/brindle_guard/master_stand_r.remap.json \
    --out-dir build/bg
$PY tools/pixedit.py build/bg/master_stand_r_source_50px.png \
    --patch   art/approved/brindle_guard/master_stand_r.patch.json \
    --palette specs/palettes/brindle_guard.json \
    --out     art/approved/brindle_guard/master_stand_r_64px.png --scale 8
