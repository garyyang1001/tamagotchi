#!/usr/bin/env python3
"""anchors.py — 從烘焙好的影格算出每一格的頭部錨點。

為什麼要有這支
--------------
公主要能戴帽子、圍圍巾、別髮飾，而且**在房間裡走動時配件要跟著頭走**，
不能只在換裝畫面戴著。要做到這件事，每一格都得知道頭在哪。

原本評估的結論是「配件要逐格量頭部位置，我們沒有那份資料，成本太高」。
那個結論是錯的——**那份資料算得出來**：
公主的調色盤把頭髮獨立成三格（`hair_dark` / `hair_mid` / `hair_light`），
所以在影格裡找出所有髮色像素，最上緣就是頭頂、上緣帶的重心就是頭的水平中心。

實測（`ice_princess`，(x, y)）：

    idle_breathe  (28,7)  (28,6)  (28,6)  (28,7)      呼吸時頭浮動 1 px
    walk          (28,10) (28,10) (28,10) (28,10)     走路時頭高度不變
    sit_down      (28,12) (28,17) (28,21) (29,27)     坐下，頭一路降 15 px
    lie_down      (28,23) (31,41) (32,51) (33,55)     躺下，頭往前 5 px 往下 32 px

86 個影格全部有解，而且值本身就說明了動作是對的。

這和 `base_row` 是同一個原則：**能量出來的東西不要讓人填**。
物件的接地列是物件的內在屬性，頭部錨點是影格的內在屬性——
手填 86 組座標一定會填錯，而且改一次動畫就要重填一次。

限制
----
只對「頭髮有獨立調色盤格」的角色有效。三隻狗的毛色佔滿整個調色盤，
頭和身體是同一批顏色，這個方法對牠們**不成立**——
狗不戴配件，所以現在不需要，但別以為這支工具是通用的。

用法
----
    .venv/bin/python tools/anchors.py -c ice_princess
    .venv/bin/python tools/anchors.py -c ice_princess --check   # 只印表不寫檔
"""

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SHEETS = ROOT / "build/sheets"

# 上緣往下幾列拿來算水平重心。1 列太少（髮尖的單一像素會把重心拉歪），
# 太多會把兩側的丸子頭一起算進去反而失準。實測 6 列最穩。
TOP_BAND = 6


def hair_hexes(character: str) -> set:
    """調色盤裡屬於頭髮的那幾格。找不到就回空集合，呼叫端要擋。"""
    p = ROOT / ("specs/palettes/%s.json" % character)
    d = json.loads(p.read_text(encoding="utf-8"))
    return {c["hex"].upper() for c in d["colors"] if c["role"].startswith("hair")}


def anchor_of(img: Image.Image, hexes: set):
    """回傳 (中心 x, 頭頂 y)；整格找不到髮色就回 None。"""
    d = img.load()
    pts = []
    for y in range(img.height):
        for x in range(img.width):
            px = d[x, y]
            if px[3] and ("#%02X%02X%02X" % px[:3]) in hexes:
                pts.append((x, y))
    if not pts:
        return None
    top = min(p[1] for p in pts)
    band = [p for p in pts if p[1] < top + TOP_BAND]
    return (round(sum(p[0] for p in band) / len(band)), top)


def build(character: str):
    hexes = hair_hexes(character)
    if not hexes:
        raise SystemExit(
            "%s 的調色盤沒有 hair_* 格。這支工具靠髮色定位頭部，"
            "沒有獨立的髮色格就算不出來（三隻狗就是這種情況）。" % character)

    atlas = json.loads((SHEETS / ("%s_atlas.json" % character)).read_text(encoding="utf-8"))
    out = {
        "character_id": character,
        "generator": "tools/anchors.py",
        "method": "髮色像素的最上緣 = 頭頂 y；上緣往下 %d 列的重心 = 中心 x" % TOP_BAND,
        "hair_hexes": sorted(hexes),
        "coordinate_space": "影格左上角為原點，單位是目標像素",
        "animations": {},
    }
    missing = []
    for a in atlas["animations"]:
        ap = SHEETS / a["atlas"] if a.get("atlas") else None
        if ap is None or not ap.exists():
            ap = SHEETS / ("%s_%s.json" % (character, a["id"]))
        ad = json.loads(ap.read_text(encoding="utf-8"))
        sheet = Image.open(SHEETS / ad["sheet"]).convert("RGBA")
        pts = []
        for f in ad["frames"]:
            cell = sheet.crop((f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]))
            p = anchor_of(cell, hexes)
            if p is None:
                missing.append("%s#%d" % (a["id"], f["index"]))
                p = pts[-1] if pts else [0, 0]
            pts.append(list(p))
        out["animations"][a["id"]] = pts
    return out, missing


def main():
    ap = argparse.ArgumentParser(description="從影格算出每一格的頭部錨點")
    ap.add_argument("-c", "--character", required=True)
    ap.add_argument("--check", action="store_true", help="只印表，不寫檔")
    args = ap.parse_args()

    out, missing = build(args.character)

    n = sum(len(v) for v in out["animations"].values())
    print("%s：%d 個動畫、%d 格" % (args.character, len(out["animations"]), n))
    for aid, pts in sorted(out["animations"].items()):
        span = max(p[1] for p in pts) - min(p[1] for p in pts)
        print("  %-16s %-42s 垂直位移 %2d px" % (
            aid, " ".join("(%d,%d)" % (x, y) for x, y in pts[:4]), span))
    if missing:
        print("\n⚠️  這些影格找不到髮色，沿用前一格：%s" % ", ".join(missing))

    if not args.check:
        p = ROOT / ("specs/anchors/%s.json" % args.character)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("\n→ %s" % p.relative_to(ROOT))


if __name__ == "__main__":
    main()
