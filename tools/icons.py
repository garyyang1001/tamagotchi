#!/usr/bin/env python3
"""icons.py — 把管線產出的動作圖示置中並歸檔。

為什麼動作圖示走 AI 而不是像其他 UI 元素那樣手繪
--------------------------------------------------
手繪版本（第一版）能認得出來，但**平**——單色塊、沒有份量。
這幾個是小孩每天要看幾十次的東西，值得多花一輪。

CLAUDE.md 規則 2 的分工在這裡是有彈性的：
「幾何走工具」指的是**牆、地板、窗框、碗**這種大片平塗的**場景**元素——
它們的價值在精確（接縫要對齊、地板不能抖），AI 在那裡只會多一輪重映射。
動作圖示不一樣：它們的價值在**一眼認得出來而且討喜**，那正是模型擅長的。
判準不是「它是不是幾何」，是「**這個東西的價值在精確還是在造型**」。

（10×10 的需求圖示仍然手繪：那個尺寸低於模型的有效控制解析度，
和 style_lock 裡「5 像素以下的特徵無法用提示詞控制」是同一條。）

為什麼要這支工具
----------------
`cutstrip.py` 的水平/垂直對齊是為**動物**設計的：腳底貼地面線、水平用軀幹帶對齊。
圖示沒有腳也沒有軀幹，那個對齊會讓六個圖示在格子裡高低不一。
這支把每一個裁到內容範圍再**置中**回 40×40，UI 才不會看起來歪七扭八。

置中的量是**算出來的**，不是填的——和 base_row、頭部錨點同一條原則。
"""

import glob
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "art/approved/_icons"
CELL = 40


def content_box(im):
    """內容範圍。回傳 None 代表整張空的。"""
    px = im.load()
    xs = [x for y in range(im.height) for x in range(im.width) if px[x, y][3]]
    ys = [y for y in range(im.height) for x in range(im.width) if px[x, y][3]]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def main():
    spec = json.loads((ROOT / "specs/icons.json").read_text(encoding="utf-8"))
    fs = sorted(glob.glob(str(ROOT / "build/strip/icons_px/*.png")))
    fs = [f for f in fs if "_x8" not in f]
    if len(fs) != len(spec["icons"]):
        raise SystemExit("規格有 %d 個圖示，build/strip/icons_px 有 %d 張——先跑 REBUILD.sh"
                         % (len(spec["icons"]), len(fs)))
    OUT.mkdir(parents=True, exist_ok=True)
    for e, f in zip(spec["icons"], fs):
        im = Image.open(f).convert("RGBA")
        b = content_box(im)
        if b is None:
            raise SystemExit("%s 是空的" % f)
        crop = im.crop(b)
        if crop.width > CELL or crop.height > CELL:
            raise SystemExit("%s 的內容 %dx%d 超過 %d——調小 cutstrip 的 --target-area"
                             % (f, crop.width, crop.height, CELL))
        out = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
        ox, oy = (CELL - crop.width) // 2, (CELL - crop.height) // 2
        out.paste(crop, (ox, oy), crop)
        p = OUT / ("%s.png" % e["id"])
        out.save(p)
        print("  %-12s 內容 %2dx%-2d → 置中於 %dx%d，偏移 (%d,%d)"
              % (e["id"], crop.width, crop.height, CELL, CELL, ox, oy))


if __name__ == "__main__":
    main()
