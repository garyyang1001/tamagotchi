#!/usr/bin/env python3
"""mkiconmap.py — 由降採樣後的色群產生圖示的語意對照表。

**這一組可以用最近色對應，其他角色不行。** 理由是 `specs/palettes/icons.json`
的 16 色就是 gpt-image-2 輸出經 cutstrip 量化後的顏色本身，最近色等於恆等對應。
公主那次不能用最近色，是因為她的銀髮和斗篷藍在 RGB 上很近——最近色會把頭髮吸成藍的。
那是把**不同語意**的色硬吸過去，和這裡不是同一件事。

`tools/automap.py` 不適用：它的分類是為「單一毛色系的動物」或「幾個色相族群的人形」
設計的，六個互不相干的物件（碗、球、手、盆、馬桶、洋裝）沒有共同的語意階梯。
"""
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    pal = [(c["role"], tuple(int(c["hex"][i:i + 2], 16) for i in (1, 3, 5)))
           for c in json.loads((ROOT / "specs/palettes/icons.json").read_text(encoding="utf-8"))["colors"]
           if not c.get("transparent") and not c["role"].startswith("spare")]
    seen = set()
    for f in glob.glob(str(ROOT / "build/strip/icons_cl/*_remap.json")):
        for h in json.loads(Path(f).read_text(encoding="utf-8")):
            if not h.startswith("_"):
                seen.add(h)
    if not seen:
        raise SystemExit("build/strip/icons_cl 是空的——先跑 pixelate --dump-clusters")
    out = {"_note": ("由**降採樣之後**的色群產生。第一版從 cutstrip 的輸出（降採樣之前）建表，"
                     "pixelate 再量化一次產生新的色群值對不上，飯碗的飼料被吸成紅色。"
                     "CLAUDE.md 明文警告過這件事。")}
    for h in sorted(seen):
        c = tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
        out[h] = min(pal, key=lambda p: sum((a - b) ** 2 for a, b in zip(p[1], c)))[0]
    p = ROOT / "art/generated/V1_icons.remap.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("  對照表 %d 個色群 → %s" % (len(out) - 1, p.relative_to(ROOT)))


if __name__ == "__main__":
    main()
