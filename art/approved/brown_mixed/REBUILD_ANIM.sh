#!/bin/sh
# 從 AI 生成的連續影格圖重建動畫。
#
#   AI 一次生成一張含全部影格的圖（2x2 或 4x1）
#     → cutstrip.py  切開、對齊、共用縮放（切開前先量化前景）
#     → pixelate.py  降採樣 + 語意重映射
#     → pixedit.py   補 5px 以下的細節（眼睛）
#     → bake.py      串成 spritesheet
#     → preview.py   GIF + 接觸表 + 實機模擬
#
# 部件旋轉路線已移除。實測整條腿繞肩關節轉會讓腳掌畫弧離開地面，
# 而 AI 一次畫四格的結果腳掌是貼地的。見 docs/05。
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python
C=brown_mixed

for A in walk; do
  SHEET=art/generated/B4_${C}_${A}cycle.png
  [ -f "$SHEET" ] || { echo "跳過 $A（缺 $SHEET）"; continue; }
  echo "── $A ──"
  $PY tools/cutstrip.py "$SHEET" --grid 2x2 --out-dir build/strip/$A --name ${C}_${A}
  $PY tools/pixelate.py build/strip/$A/*.png --width 64 --height 56 --no-crop \
      --colors 12 --palette specs/palettes/$C.json \
      --remap "${SHEET%.png}.remap.json" --out-dir build/strip/${A}_px
done

$PY tools/bake.py --character $C --keep-going
$PY tools/preview.py --character $C
