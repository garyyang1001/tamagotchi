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
# 輸出目錄一定要分角色：不分的話跑第二隻狗會直接蓋掉第一隻的影格，
# 而且是靜默蓋掉——bake 出來的圖會是另一隻狗。
cd "$(dirname "$0")/.."
PY=.venv/bin/python
C=$1; A=$2; G=${3:-2x2}
# 影格格從角色規格檔讀。狗是 64×56、公主是 64×112——寫死的話她的頭會被切掉。
CELL=$($PY -c "import sys;sys.path.insert(0,'tools');from cell import cell_for;print('%d %d'%cell_for('$C'))")
CW=${CELL% *}; CH=${CELL#* }
SHEET=art/generated/B4_${C}_${A}.png
[ -f "$SHEET" ] || { echo "  缺 $SHEET"; exit 1; }
rm -rf build/strip/$C/$A build/strip/$C/${A}_px build/strip/$C/${A}_cl
# master 存在就拿它當面積基準，動畫才會和站姿同尺度
M=art/approved/$C/master_stand_r_64px.png
[ -f "$M" ] && MA="--match-area $M" || MA=""
$PY tools/cutstrip.py "$SHEET" --grid $G $MA --character $C --palette specs/palettes/$C.json --out-dir build/strip/$C/$A --name ${C}_${A} >/dev/null
$PY tools/pixelate.py build/strip/$C/$A/*.png --width $CW --height $CH --no-crop \
    --colors 12 --palette specs/palettes/$C.json --dump-clusters \
    --out-dir build/strip/$C/${A}_cl >/dev/null
$PY tools/automap.py --from-clusters build/strip/$C/${A}_cl/*_remap.json \
    --palette specs/palettes/$C.json --out "${SHEET%.png}.remap.json" >/dev/null
$PY tools/pixelate.py build/strip/$C/$A/*.png --width $CW --height $CH --no-crop \
    --colors 12 --palette specs/palettes/$C.json \
    --remap "${SHEET%.png}.remap.json" --out-dir build/strip/$C/${A}_px >/dev/null
