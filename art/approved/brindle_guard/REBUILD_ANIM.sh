#!/bin/sh
# 從 AI 生成的連續影格圖重建 brindle_guard 的全部動畫。
# 流程與 brown_mixed 相同，額外兩件事是這隻狗特有的：
#   1) --match-area 對齊 master 的 1253 px。預設 1800 會讓動畫比站姿大 40%。
#   2) 調色盤的 protect 段救回眼睛與背帶。k-means 照面積分群，
#      淡藍眼在原圖只佔 0.06%，不保護的話 15 個動畫全部沒有眼睛。
#
# stand_up / wake_up 不生成——反向播放 sit_down / lie_down。
set -e
cd "$(dirname "$0")/../../.."
C=brindle_guard
for A in walk idle_blink idle_look idle_ear_twitch tail_wag sit_down lie_down \
         eat eat_happy sleep_breathe yawn play_bow chase_ball pet_react toilet; do
  printf "  %-18s" "$A"
  sh tools/procanim.sh $C $A >/dev/null && echo "✓"
done
.venv/bin/python tools/bake.py --character $C
.venv/bin/python tools/preview.py --character $C
