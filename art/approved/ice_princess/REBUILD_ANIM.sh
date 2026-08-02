#!/bin/sh
# 從 AI 生成的連續影格圖重建 ice_princess 的全部動畫。
# 她和三隻狗不同的地方：
#   1) 影格格是 64×96（狗是 64×56），由 specs/characters/ice_princess.json 的
#      render.sprite_cell 提供給 tools/cell.py，procanim.sh 自動帶。
#   2) --match-area 對齊 master 的 2298 px。四角色是 1809 / 1253 / 986 / 2298。
#   3) 21 個 anim_id_t 是照狗設計的，她的 ear_twitch / tail_wag / play_bow /
#      chase_ball / toilet 由 specs/anim_prompt/ice_princess.json 的
#      FRAMES_OVERRIDE 換成人形動作（撥髮／裙襬搖晃／屈膝禮／丟球／蹲下）。
set -e
cd "$(dirname "$0")/../../.."
C=ice_princess
for A in walk idle_blink idle_look idle_ear_twitch tail_wag sit_down lie_down \
         eat eat_happy sleep_breathe yawn play_bow chase_ball pet_react toilet; do
  printf "  %-18s" "$A"
  sh tools/procanim.sh $C $A >/dev/null && echo "✓"
done
.venv/bin/python tools/bake.py --character $C
.venv/bin/python tools/preview.py --character $C
