#!/bin/sh
# 從 AI 生成的 3×2 圖示表重建動作圖示。產出必須逐位元相同。
#
# 為什麼這幾個走 AI 而其他 UI 元素手繪：見 specs/icons.json 的 _why_ai。
# 一句話——**判準不是「它是不是幾何」，是「這個東西的價值在精確還是在造型」**。
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python
rm -rf build/strip/icons build/strip/icons_cl build/strip/icons_px
# 面積 620：再大 f2（手掌）會貼在負的 x，左緣被切掉。
$PY tools/cutstrip.py art/generated/V1_icons.png --grid 3x2 --cell 40x40 \
    --name icon --out-dir build/strip/icons --target-area 620 \
    --palette specs/palettes/icons.json >/dev/null
# **一定要先 dump 再 map**：分類的對象必須是降採樣「之後」的色群。
$PY tools/pixelate.py build/strip/icons/*.png --width 40 --height 40 --no-crop \
    --colors 14 --palette specs/palettes/icons.json --dump-clusters \
    --out-dir build/strip/icons_cl >/dev/null
$PY tools/mkiconmap.py
$PY tools/pixelate.py build/strip/icons/*.png --width 40 --height 40 --no-crop \
    --colors 14 --palette specs/palettes/icons.json \
    --remap art/generated/V1_icons.remap.json --out-dir build/strip/icons_px >/dev/null
$PY tools/icons.py
