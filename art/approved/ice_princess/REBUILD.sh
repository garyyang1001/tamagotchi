#!/bin/sh
# 從 AI 原始輸出重建 master sprite。
# --width 38 是量出來的：38x90、2298 個不透明像素。
# 高度是 brown_mixed（54）的 1.67 倍，對應 docs/03 訂的設計比例 420/250 = 1.68。
# 影格格是 64x96（狗是 64x56），由 specs/characters/ice_princess.json 的
# render.sprite_cell 提供給 tools/cell.py。
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python
$PY tools/pixelate.py art/approved/ice_princess/master_stand_r_source.png \
    --width 38 --colors 14 \
    --palette specs/palettes/ice_princess.json \
    --remap  art/approved/ice_princess/master_stand_r.remap.json \
    --out-dir build/ip
$PY tools/pixedit.py build/ip/master_stand_r_source_38px.png \
    --patch   art/approved/ice_princess/master_stand_r.patch.json \
    --palette specs/palettes/ice_princess.json \
    --out     art/approved/ice_princess/master_stand_r_64px.png --scale 11
