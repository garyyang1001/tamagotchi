#!/usr/bin/env python3
"""nightpal.py — 從角色的日間調色盤推出夜間調色盤。

為什麼要有夜間調色盤
--------------------
`game.c` 已經有夜間模式（動畫放慢到 0.7 倍），房間也有夜間底圖。
但角色的調色盤不變的話，房間暗下來之後四個角色會像自己在發光。

做法和房間、和公主的服裝置換完全一樣：**同一張點陣圖，只換調色盤**。
一個角色的夜間版成本是一份 16 色的 JSON（32 B 燒進 flash），零張新圖。

為什麼用算的而不是手挑
----------------------
四個角色共 60 個顏色。手挑是 60 個獨立決定，一定會有幾個挑歪，
而且改天調整「夜要多暗」就得重挑一次。

這裡用一條仿射變換：

    out = day * k + tint * (1 - k)

**選它的理由不是方便，是它保證 docs/09 的三頻道在夜間仍然成立**——
仿射變換保序，任意兩個顏色的亮度差都被壓縮同一個比例，
所以四個角色的平均亮度排序（61 / 96 / 121 / 162）不會在夜裡糊在一起。
挑色相就沒有這個保證。

房間的夜間調色盤**不用**這條變換，那是手挑的：
窗戶要從最亮反轉成最暗、地毯要刻意保留暖色，那些是設計決定不是打光運算。
角色是被同一盞燈照的，才適合用運算。

用法
----
    .venv/bin/python tools/nightpal.py --all
    .venv/bin/python tools/nightpal.py -c brown_mixed --check
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 夜色。和 specs/palettes/scene_night.json 的牆面同一個色系，
# 這樣角色和房間看起來是被同一盞燈照的。
TINT = (28, 36, 70)
K = 0.45          # 保留多少原色。0.45 是實測：再低角色的毛色會全部糊成一片藍


def mix(hexv, k=K, tint=TINT):
    h = hexv.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    out = [int(round(c * k + t * (1 - k))) for c, t in zip(rgb, tint)]
    return "#%02X%02X%02X" % tuple(out)


def lum(hexv):
    h = hexv.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b


def build(cid, k=K):
    src = ROOT / ("specs/palettes/%s.json" % cid)
    d = json.loads(src.read_text(encoding="utf-8"))
    out = {
        "palette_id": "%s_night" % cid,
        "bit_depth": d.get("bit_depth", 4),
        "max_colors": 16,
        "derived_from": "specs/palettes/%s.json" % cid,
        "generator": "tools/nightpal.py",
        "transform": {"formula": "out = day * k + tint * (1 - k)", "k": k, "tint": list(TINT)},
        "note": ("夜間置換。**角色編號逐一對應日間調色盤**，換調色盤就換夜間，不換圖。\n"
                 "用仿射變換而不是手挑：它保序，所以 docs/09 的明度分離在夜間仍然成立"
                 "（任意兩色的亮度差被壓縮同一個比例）。\n"
                 "改「夜要多暗」只要改 tools/nightpal.py 的 K 再重跑，四個角色一起變。"),
        "colors": [],
    }
    for c in d["colors"]:
        e = {"index": c["index"], "role": c["role"]}
        if c.get("transparent"):
            e["hex"] = c["hex"]
            e["transparent"] = True
        else:
            e["hex"] = mix(c["hex"], k)
        out["colors"].append(e)
    return src, out


def main():
    ap = argparse.ArgumentParser(description="從日間調色盤推出夜間調色盤")
    ap.add_argument("-c", "--char", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--k", type=float, default=K, help="保留多少原色（預設 %.2f）" % K)
    ap.add_argument("--check", action="store_true", help="只印驗算表，不寫檔")
    args = ap.parse_args()

    chars = args.char or []
    if args.all or not chars:
        chars = sorted(p.stem for p in (ROOT / "specs/characters").glob("*.json"))

    rows = []
    for cid in chars:
        if not (ROOT / ("specs/palettes/%s.json" % cid)).exists():
            continue
        src, out = build(cid, args.k)
        day = json.loads(src.read_text(encoding="utf-8"))["colors"]
        dl = [lum(c["hex"]) for c in day if not c.get("transparent")]
        nl = [lum(c["hex"]) for c in out["colors"] if not c.get("transparent")]
        rows.append((cid, sum(dl) / len(dl), sum(nl) / len(nl)))
        if not args.check:
            p = ROOT / ("specs/palettes/%s_night.json" % cid)
            p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("  → %s（%d 色）" % (p.name, len(out["colors"])))

    print("\n驗算：仿射變換保序，所以日間的排序在夜間必須完全保留")
    print("  %-16s %8s %8s" % ("角色", "日間", "夜間"))
    for cid, d_, n_ in rows:
        print("  %-16s %8.1f %8.1f" % (cid, d_, n_))
    order_d = [c for c, d_, _ in sorted(rows, key=lambda r: r[1])]
    order_n = [c for c, _, n_ in sorted(rows, key=lambda r: r[2])]
    if order_d == order_n:
        print("  ✅ 排序一致：%s" % " < ".join(order_d))
    else:
        sys.exit("  ❌ 夜間排序變了！日 %s / 夜 %s" % (order_d, order_n))


if __name__ == "__main__":
    main()
