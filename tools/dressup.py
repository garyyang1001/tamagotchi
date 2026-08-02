#!/usr/bin/env python3
"""dressup.py — 公主的配件：算出 sprite，並驗證它在每一格都戴得住。

為什麼配件做得起來
------------------
原本評估的結論是「配件要逐格量頭部位置，我們沒有那份資料」。
`tools/anchors.py` 推翻了那個結論——**那份資料算得出來**，
因為公主的調色盤把頭髮獨立成三格，髮色像素的最上緣就是頭頂。

所以配件不必只能待在換裝畫面：帽子可以真的戴在頭上，跟著 83 格動畫走。
`sit_down` 頭降 15 px、`lie_down` 頭降 32 px，配件跟著降，因為錨點是逐格量的。

配件為什麼是疊加而不是重畫
--------------------------
重畫一套服裝 = 再做一次公主（15 張生成圖 → 83 格）。
疊加一件配件 = 一張 30×14 以內的小圖 + 一組相對錨點的偏移。
成本差三個數量級，而剪影一樣會變（這是調色盤置換做不到的）。

分工仍然照 CLAUDE.md 規則 2：**顏色走調色盤，剪影走配件，兩者不重疊**。
`specs/palettes/ice_princess.json` 的五個 `outfit_slots` 換的是斗篷與滾邊的顏色；
配件換的是輪廓。

設計限制（不是技術限制）
------------------------
**不做皇冠／頭冠。** CLAUDE.md 規則 7：原始設計的「及地冰藍禮服 + 單側白金長辮 +
半透明披肩 + 頭冠」那一整組被影像模型的輸出審核連續擋掉四次。
頭冠是那組指向某商業角色的元素之一，即使現在是自己畫的像素也不要放回去。

**要和兩個丸子頭共存。** 她的髮型是雙丸子，一頂普通毛帽蓋下去會把兩個丸子壓掉，
剪影反而變得不像她。所以配件的形狀都是「繞過丸子」或「掛在丸子上」。

用法
----
    .venv/bin/python tools/dressup.py                  # 產生 sprite
    .venv/bin/python tools/dressup.py --preview walk   # 疊到影格上驗證有沒有戴歪
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "art/approved/_dressup"
SHEETS = ROOT / "build/sheets"


def load_palette(cid):
    d = json.loads((ROOT / ("specs/palettes/%s.json" % cid)).read_text(encoding="utf-8"))
    roles = {}
    for c in d["colors"]:
        h = c["hex"].lstrip("#")
        roles[c["role"]] = (None if c.get("transparent")
                            else tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
    return roles


def draw(art, key, roles, where):
    h, w = len(art), max(len(r) for r in art)
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            role = key.get(ch)
            if role is None:
                continue
            if role not in roles:
                raise SystemExit("%s 用了調色盤沒有的角色 %r" % (where, role))
            arr[y, x] = (*roles[role], 255)
    return Image.fromarray(arr, "RGBA")


def build(cid):
    spec = json.loads((ROOT / ("specs/accessories/%s.json" % cid)).read_text(encoding="utf-8"))
    roles = load_palette(cid)
    OUT.mkdir(parents=True, exist_ok=True)
    made = {}
    for a in spec["accessories"]:
        if a["id"].startswith("_"):
            continue
        im = draw(a["art"], a["key"], roles, "配件 %s" % a["id"])
        if [im.width, im.height] != a["size"]:
            raise SystemExit("配件 %s 的 art 是 %dx%d，size 宣告 %s"
                             % (a["id"], im.width, im.height, a["size"]))
        p = OUT / ("acc_%s_%s.png" % (cid, a["id"]))
        im.save(p)
        im.resize((im.width * 8, im.height * 8), Image.NEAREST).save(
            OUT / ("acc_%s_%s_x8.png" % (cid, a["id"])))
        made[a["id"]] = im
        print("  配件 %-12s %dx%d  掛在頭錨點 (%+d,%+d)  %s"
              % (a["id"], im.width, im.height, a["dx"], a["dy"], a["name"]))
    return spec, made


def preview(cid, spec, made, anim):
    """把每一件配件疊到指定動畫的每一格上，用來看有沒有戴歪、有沒有飄掉。"""
    anch = json.loads((ROOT / ("specs/anchors/%s.json" % cid)).read_text(encoding="utf-8"))
    if anim not in anch["animations"]:
        raise SystemExit("沒有動畫 %s。有的是：%s" % (anim, ", ".join(sorted(anch["animations"]))))
    pts = anch["animations"][anim]
    ad = json.loads((SHEETS / ("%s_%s.json" % (cid, anim))).read_text(encoding="utf-8"))
    sheet = Image.open(SHEETS / ad["sheet"]).convert("RGBA")

    ids = [a["id"] for a in spec["accessories"] if not a["id"].startswith("_")]
    by = {a["id"]: a for a in spec["accessories"]}
    fw, fh = ad["frames"][0]["w"], ad["frames"][0]["h"]
    cols, rows = len(pts), len(ids)
    canvas = Image.new("RGBA", (fw * cols, fh * rows), (58, 52, 46, 255))
    for r, aid in enumerate(ids):
        a, acc = by[aid], made[aid]
        for c, f in enumerate(ad["frames"]):
            cell = sheet.crop((f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])).copy()
            hx, hy = pts[c]
            cell.paste(acc, (hx + a["dx"], hy + a["dy"]), acc)
            canvas.paste(cell, (c * fw, r * fh))
    p = ROOT / ("build/dressup_%s_%s.png" % (cid, anim))
    p.parent.mkdir(parents=True, exist_ok=True)
    canvas.resize((canvas.width * 3, canvas.height * 3), Image.NEAREST).save(p)
    print("\n驗證圖 → %s（每列一件配件，每欄一格）" % p.relative_to(ROOT))
    print("要看的是：配件在每一格都貼著頭，不會飄、不會沉進頭裡、不會蓋掉眼睛。")


def main():
    ap = argparse.ArgumentParser(description="公主的配件 sprite 與試戴驗證")
    ap.add_argument("-c", "--character", default="ice_princess")
    ap.add_argument("--preview", metavar="ANIM", help="疊到這個動畫的每一格上")
    args = ap.parse_args()

    spec, made = build(args.character)
    if args.preview:
        preview(args.character, spec, made, args.preview)


if __name__ == "__main__":
    main()
