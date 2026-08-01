#!/bin/sh
# 從 AI 原始輸出重建 master sprite。任何一步的參數改了都要重跑這個。
set -e
cd "$(dirname "$0")/../../.."
.venv/bin/python tools/pixelate.py art/approved/brown_mixed/master_stand_r_source.png \
    --width 64 --colors 12 \
    --palette specs/palettes/brown_mixed.json \
    --remap  art/approved/brown_mixed/master_stand_r.remap.json \
    --out-dir build/v4
.venv/bin/python tools/pixedit.py build/v4/master_stand_r_source_64px.png \
    --patch   art/approved/brown_mixed/master_stand_r.patch.json \
    --palette specs/palettes/brown_mixed.json \
    --out     art/approved/brown_mixed/master_stand_r_64px.png --scale 8
