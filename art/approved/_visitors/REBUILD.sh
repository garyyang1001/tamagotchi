#!/bin/sh
# 從 AI 生成的 2×2 影格圖重建閒置訪客。產出必須逐位元相同。
#
# 為什麼走 AI 而不是像其他場景物件那樣用 tools/scene.py 畫幾何：
# CLAUDE.md 規則 2 的分工——造型、體型、剪影歸影像模型，幾何（牆、地板、碗、床）歸工具。
# 松鼠和小鳥是生物，屬於前者。手繪像素試過八次，見 specs/visitors.json 的 _handdrawn_note。
set -e
cd "$(dirname "$0")/../../.."
PY=.venv/bin/python

# 松鼠 40×40：單一暖色系，automap 用亮度階梯。protect 只保護奶油腹——
# 橡實的深褐和身體暗色分不開（tol 20 就三倍誤傷），接受它糊進暗色。
# 小鳥 28×26：藍／奶油／橘三個色系，automap 用色相族群。protect 保護橘喙與橘腳。
for V in "squirrel 40 40 900 2x2" "bird 28 26 380 2x2"; do
  set -- $V
  C=$1; W=$2; H=$3; A=$4; G=$5
  D=build/strip/visitors/$C
  rm -rf $D ${D}_cl ${D}_px
  $PY tools/cutstrip.py art/generated/V0_${C}.png --grid $G --cell ${W}x${H} \
      --name $C --out-dir $D --target-area $A --palette specs/palettes/$C.json >/dev/null
  $PY tools/pixelate.py $D/*.png --width $W --height $H --no-crop --colors 11 \
      --palette specs/palettes/$C.json --dump-clusters --out-dir ${D}_cl >/dev/null
  $PY tools/automap.py --from-clusters ${D}_cl/*_remap.json \
      --palette specs/palettes/$C.json --out art/generated/V0_${C}.remap.json >/dev/null
  $PY tools/pixelate.py $D/*.png --width $W --height $H --no-crop --colors 11 \
      --palette specs/palettes/$C.json --remap art/generated/V0_${C}.remap.json \
      --out-dir ${D}_px >/dev/null
done
$PY tools/visitors.py
