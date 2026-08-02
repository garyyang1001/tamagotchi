#!/bin/sh
# 從 AI 生成的連續影格圖重建 chihuahua 的全部動畫。
# 這隻特有的兩件事：
#   1) --match-area 對齊 master 的 986 px。三隻狗的面積是 1809 / 1253 / 986。
#   2) protect 只保護黃項圈。眼睛與鼻子都**不能**用色相保護——
#      眼睛是暗色（靠金褐眼圈襯出來）、鼻子和巧克力毛只差 14° 色相。
#      見 specs/palettes/chihuahua.json 的 _protect_note。
set -e
cd "$(dirname "$0")/../../.."
C=chihuahua
for A in walk idle_blink idle_look idle_ear_twitch tail_wag sit_down lie_down \
         eat eat_happy sleep_breathe yawn play_bow chase_ball pet_react toilet; do
  printf "  %-18s" "$A"
  sh tools/procanim.sh $C $A >/dev/null && echo "✓"
done
.venv/bin/python tools/bake.py --character $C
.venv/bin/python tools/preview.py --character $C
