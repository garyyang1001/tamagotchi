#!/usr/bin/env python3
"""visitors.py — 把管線產出的訪客影格串成 spritesheet，並量出接地列。

閒置訪客（松鼠、小鳥）和場景物件不同：它們是**生物**，走的是
「AI 生成 → cutstrip → pixelate → automap」這條和四個角色一樣的管線
（CLAUDE.md 規則 2：造型與剪影歸影像模型，幾何歸工具），
所以有自己的調色盤，不受 scene 那 16 色的限制，`tools/scene.py` 也畫不出來。

這支只做最後一步：把 4 格橫排成一張圖，並**量出**接地列。
接地列不讓人填——和場景物件的 base_row 同一條規則。
"""
import glob
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "art/approved/_visitors"


def main():
    spec = json.loads((ROOT / "specs/visitors.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for v in spec["visitors"]:
        cid = v["id"]
        w, h = v["size"]
        fs = [f for f in sorted(glob.glob(str(ROOT / ("build/strip/visitors/%s_px/*.png" % cid))))
              if "_x8" not in f]
        if len(fs) != v["frame_count"]:
            raise SystemExit("%s 應有 %d 格，找到 %d 格——先跑 REBUILD.sh"
                             % (cid, v["frame_count"], len(fs)))
        ims = [Image.open(f).convert("RGBA") for f in fs]
        for i, im in zip(ims, fs):
            if (i.width, i.height) != (w, h):
                raise SystemExit("%s 的影格是 %dx%d，規格宣告 %dx%d"
                                 % (f, i.width, i.height, w, h))
        sheet = Image.new("RGBA", (w * len(ims), h))
        for k, i in enumerate(ims):
            sheet.paste(i, (k * w, 0), i)
        p = OUT / ("%s.png" % cid)
        sheet.save(p)
        px = sheet.load()
        base = max(y for y in range(h) for x in range(sheet.width) if px[x, y][3])
        if base != v["base_row"]:
            raise SystemExit("%s 量到的接地列是 %d，規格寫 %d——規格是給人看的，"
                             "真相在圖裡，請更新 specs/visitors.json" % (cid, base, v["base_row"]))
        print("  %-9s %dx%d ×%d 格  接地列 %d  → %s"
              % (cid, w, h, len(ims), base, p.relative_to(ROOT)))


if __name__ == "__main__":
    main()
