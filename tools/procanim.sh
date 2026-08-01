#!/bin/sh
# 把一張生成的連續影格圖跑完整條管線。
#   cutstrip  切開/對齊/面積正規化（切開前先量化前景）
#   pixelate --dump-clusters  先產出「降採樣之後」的色群
#   automap --from-clusters   對那些色群做語意分類
#   pixelate --remap          正式產出
#
# 為什麼要先 dump 再 map：分類的對象必須是降採樣「之後」的色群。
# 從來源圖直接讀是降採樣「之前」的，兩者不保證相同——
# 實測會在影格邊緣留下調色盤外的雜色，bake.py 會擋下來。
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
C=$1; A=$2; G=${3:-2x2}
SHEET=art/generated/B4_${C}_${A}.png
[ -f "$SHEET" ] || { echo "  缺 $SHEET"; exit 1; }
rm -rf build/strip/$A build/strip/${A}_px build/strip/${A}_cl
$PY tools/cutstrip.py "$SHEET" --grid $G --out-dir build/strip/$A --name ${C}_${A} >/dev/null
$PY tools/pixelate.py build/strip/$A/*.png --width 64 --height 56 --no-crop \
    --colors 12 --palette specs/palettes/$C.json --dump-clusters \
    --out-dir build/strip/${A}_cl >/dev/null
$PY tools/automap.py --from-clusters build/strip/${A}_cl/*_remap.json \
    --palette specs/palettes/$C.json --out "${SHEET%.png}.remap.json" >/dev/null
$PY tools/pixelate.py build/strip/$A/*.png --width 64 --height 56 --no-crop \
    --colors 12 --palette specs/palettes/$C.json \
    --remap "${SHEET%.png}.remap.json" --out-dir build/strip/${A}_px >/dev/null
