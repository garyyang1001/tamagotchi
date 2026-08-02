#!/usr/bin/env python3
"""影格格的單一真相來源。

為什麼要有這支：三隻狗都是 64×56，所以 `cutstrip.py`、`bake.py`、`procanim.sh`
各自把它寫死了。公主是 **64×112**（人形，站起來是狗的兩倍高，見 docs/03 第三節），
四個地方就會各自漂移一次。

尺寸的權威來源是 `specs/characters/<id>.json` 的 `render.sprite_cell`。
沒有那個欄位就退回 64×56——三隻狗的規格檔本來就寫著 [64, 56]，
所以這支工具進來之後它們的產出必須逐位元不變。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CELL = (64, 56)
GRID = 21               # 降採樣比。1344/64 = 2352/112 = 21，兩種格都整除


def cell_for(character: str) -> tuple:
    """回傳 (寬, 高)。找不到規格檔就用預設值。"""
    p = ROOT / f"specs/characters/{character}.json"
    if not p.exists():
        return DEFAULT_CELL
    d = json.loads(p.read_text(encoding="utf-8"))
    c = (d.get("render") or {}).get("sprite_cell")
    if not (isinstance(c, list) and len(c) == 2):
        return DEFAULT_CELL
    return (int(c[0]), int(c[1]))


def canvas_for(cell: tuple) -> tuple:
    """高解析對齊畫布。cutstrip 把每一格貼到這張畫布的同一個位置。"""
    return (cell[0] * GRID, cell[1] * GRID)


def ground_row_for(cell: tuple) -> int:
    """地面線落在第幾個目標像素列。腳底貼在這條線的**上方一列**。

    必須等於畫布高度，讓動畫影格的腳底落在最底列——因為 `bake.py` 的
    `place_master` 是把 master **底部對齊畫布**放進去的（transform 型用它）。

    這裡原本是 `cell[1] - 2`（註解說要留 2 列給 screen_offset，那是誤解：
    screen_offset 是繪製時偏移，不佔影格的列）。後果是
    `idle_breathe`（transform，腳底在第 55 列）切到 `walk`（frames，第 53 列）時
    **角色會往下掉 2 px**——待機一動就抖一下，而且四個角色都有。
    2026-08-02 貼進房間量座標時才發現。
    """
    return cell[1]
