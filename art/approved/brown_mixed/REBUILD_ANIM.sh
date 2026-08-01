#!/bin/sh
# 從部件重建全部動畫。任何一步的參數改了都要重跑這個。
#
# 階段 A（允許重新採樣，只跑一次，人工核可）
#   assemble → pixelate(--no-crop) → pixedit(5px 以下的細節)
# 階段 B（純整數運算，逐位元決定性）
#   bake → preview
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python
C=brown_mixed

echo "── 階段 A：組姿勢 ──"
$PY tools/assemble.py --rig art/rigs/$C/rig.json \
    --poses specs/poses/$C.poses.json --out-dir build/poses

echo "── 階段 A：降採樣（每個姿勢只做一次）──"
$PY tools/pixelate.py build/poses/*.png --width 64 --height 56 --no-crop \
    --colors 12 --palette specs/palettes/$C.json \
    --remap art/rigs/$C/remap.json --remap-fallback \
    --out-dir build/pixparts

echo "── 階段 A：手工修補眼睛與眉點 ──"
# 眼睛在 64px 上只有 3 個像素，remap 會把虹膜吸成暗色、高光吸成灰色。
# 依 CLAUDE.md 規則 2，5 像素以下的細節一律手工畫。
for P in stand sit lie; do
  $PY tools/pixedit.py build/pixparts/${C}_${P}_head_64px.png \
      --patch art/rigs/$C/eyes_${P}.patch.json \
      --palette specs/palettes/$C.json \
      --out build/pixparts/${C}_${P}_head_64px.png --scale 0
done

echo "── 階段 B：烘焙（絕不重新採樣）──"
$PY tools/bake.py --character $C

echo "── 預覽 ──"
$PY tools/preview.py --character $C
