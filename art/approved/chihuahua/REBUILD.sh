#!/bin/sh
# 從 AI 原始輸出重建 master sprite。
# --width 44 是量出來的：44x37、1010 個不透明像素。
# 高度只有 brindle_guard（55）的 67%，同框時一眼就看得出是最小的那隻，
# 但頭部比例大，眼睛在這個尺度仍然讀得到（實測「暗眼 + 白高光 + 金褐眼圈」成立）。
# master 的生成參考圖是 photo_09（側面站姿，看體態）+ photo_10（正面特寫，看斑紋）
# 併成一張，存在 build/chihuahua_ref.png。要重做的話：
#   from PIL import Image
#   a=Image.open("art/reference/chihuahua/photo_09.jpg"); b=Image.open(".../photo_10.jpg")
#   兩張各縮到高 1024，左右並排存成 build/chihuahua_ref.png
# 那張圖只用來餵影像模型，不影響本腳本的可重現性。
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python
$PY tools/pixelate.py art/approved/chihuahua/master_stand_r_source.png \
    --width 44 --colors 14 \
    --palette specs/palettes/chihuahua.json \
    --remap  art/approved/chihuahua/master_stand_r.remap.json \
    --out-dir build/ch
$PY tools/pixedit.py build/ch/master_stand_r_source_44px.png \
    --patch   art/approved/chihuahua/master_stand_r.patch.json \
    --palette specs/palettes/chihuahua.json \
    --out     art/approved/chihuahua/master_stand_r_64px.png --scale 9
