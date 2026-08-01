#!/bin/sh
# 從 AI 生成的連續影格圖重建全部動畫。
#
#   AI 一次生成一張含全部影格的圖（2x2）
#     → cutstrip.py   切開、對齊、面積正規化（切開前先量化前景）
#     → pixelate --dump-clusters  產出降採樣「之後」的色群
#     → automap --from-clusters   對那些色群做語意分類
#     → pixelate --remap          正式產出
#     → bake.py       串成 spritesheet
#     → preview.py    GIF + 接觸表 + 實機模擬
#
# stand_up / wake_up 不生成——反向播放 sit_down / lie_down。
set -e
cd "$(dirname "$0")/../../.."
C=brown_mixed
for A in walk idle_blink idle_look idle_ear_twitch tail_wag sit_down lie_down \
         eat eat_happy sleep_breathe yawn play_bow chase_ball pet_react toilet; do
  printf "  %-18s" "$A"
  sh tools/procanim.sh $C $A && echo "✓"
done
.venv/bin/python tools/bake.py --character $C
.venv/bin/python tools/preview.py --character $C
