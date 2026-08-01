#!/usr/bin/env python3
"""
automap.py — 依亮度與飽和度自動產生語意重映射對照表

為什麼可以自動化
----------------
`pixelate.py --remap` 需要一份「來源色 → 語意角色」的對照表，
原本是人工填的。但實測發現分類規則本身是穩定的：

    亮度  分開描邊 / 暗毛 / 中毛 / 亮毛
    飽和度 分開淺褐標記（高飽和）與灰白吻部（低飽和）

第二條是關鍵。這隻狗的 tan_mid 飽和度約 120，muzzle_grey_light 約 68——
差距夠大，用飽和度切得開。而純數值最近距離切不開（實測會把肚子吸成灰白），
因為在 RGB 空間裡它們的距離不比同色階之間遠。

所以這不是退回「最近距離」，是用**兩個語意軸**分類，只是分類器變成程式。
產出仍然要人眼看過——`--review` 會印出每個色群被指到哪裡。

用法
----
    python tools/automap.py build/strip/walk/*.png \\
        --palette specs/palettes/brown_mixed.json \\
        --out art/generated/B4_brown_mixed_walk.remap.json --review
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


# 亮度分界（0–255）。依 brown_mixed 實測校準：
#   描邊 lum 2–7 / 暗毛 42–49 / 中毛 67–72 / 亮毛 73 / 淺褐 141 / 灰白 183
LUM_BANDS = [
    (20,  "outline"),
    (55,  "coat_dark"),
    (72,  "coat_mid"),
    (100, "coat_light"),
]

# 亮度超過 LUM_BANDS 最後一段時，改用飽和度區分
SAT_SPLIT = 95          # 高於此 = 暖色標記，低於此 = 去飽和的灰白
BRIGHT_HIGH_SAT = "tan_mid"
BRIGHT_LOW_SAT = "muzzle_grey_light"

# 極亮且極低飽和 = 眼睛高光
HIGHLIGHT_LUM = 220
HIGHLIGHT_SAT = 30


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def sat(c):
    return max(c) - min(c)


def bright_split(colours):
    """算出「淺褐標記 vs 去飽和灰白」的飽和度分界。

    寫死 95 太脆——不同張生成圖裡淺褐的飽和度會變（實測 tail_wag 的腳掌
    飽和度低於 95，整批被誤判成灰白吻部）。改成看這批亮色自己的分佈：
    取最高與最低飽和度的中點，但不低於 40（避免全是灰白時硬切出一個 tan）。
    """
    bright = [sat(c) for c in colours if lum(c) >= LUM_BANDS[-1][0]]
    if len(bright) < 2:
        return SAT_SPLIT
    lo, hi = min(bright), max(bright)
    if hi - lo < 25:            # 分不開就別硬分
        return SAT_SPLIT
    return max(40, (lo + hi) / 2)


def classify(c, split):
    l, s = lum(c), sat(c)
    if l >= HIGHLIGHT_LUM and s <= HIGHLIGHT_SAT:
        return "eye_highlight"
    for hi, role in LUM_BANDS:
        if l < hi:
            return role
    return BRIGHT_HIGH_SAT if s >= split else BRIGHT_LOW_SAT


def main():
    ap = argparse.ArgumentParser(description="自動產生語意重映射對照表")
    ap.add_argument("inputs", type=Path, nargs="*",
                    help="對齊後的來源圖（cutstrip.py 的產出）")
    ap.add_argument("--from-clusters", type=Path, nargs="+",
                    help="改讀 pixelate.py --dump-clusters 產生的色群檔。"
                         "**這是正確的用法**：分類的對象必須是降採樣『之後』的色群，"
                         "從來源圖讀到的是降採樣『之前』的，兩者不保證相同——"
                         "實測會在影格邊緣留下調色盤外的雜色。")
    ap.add_argument("--palette", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--bg", default="FF00FF")
    ap.add_argument("--tol", type=float, default=120.0)
    ap.add_argument("--review", action="store_true", help="印出每個色群的分類依據")
    args = ap.parse_args()

    roles = {c["role"] for c in json.loads(args.palette.read_text())["colors"]
             if not c.get("transparent")}
    bg = tuple(int(args.bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))

    # 取全部影格的色群聯集——單一影格可能缺某個色，聯集才涵蓋得完整
    seen = set()
    if args.from_clusters:
        for f in args.from_clusters:
            for k in json.loads(f.read_text()):
                if k.startswith("#"):
                    seen.add(tuple(int(k[i:i + 2], 16) for i in (1, 3, 5)))
    else:
        for f in args.inputs:
            a = np.array(Image.open(f).convert("RGB"))
            d = np.sqrt(((a.astype(np.int16) - np.array(bg, dtype=np.int16))
                         .astype(np.float32) ** 2).sum(axis=-1))
            seen |= {tuple(int(v) for v in c) for c in a[d > args.tol]}
    if not seen:
        sys.exit("沒有讀到任何色群，檢查 inputs 或 --from-clusters")

    split = bright_split(seen)
    table, rows = {}, []
    for c in sorted(seen, key=lum):
        role = classify(c, split)
        if role not in roles:
            sys.exit(f"分類到不存在的角色 '{role}'，檢查調色盤")
        key = "#%02X%02X%02X" % c
        table[key] = role
        rows.append((key, lum(c), sat(c), role))

    doc = {
        "_note": (f"由 tools/automap.py 自動分類，來源 {len(args.inputs)} 個影格的色群聯集。"
                  f"規則：亮度分色階、飽和度分「淺褐標記 vs 去飽和灰白」"
                  f"（分界 {split:.0f}，依本批亮色的飽和度分佈自適應）。這不是最近距離吸附——那個實測會把肚子吸成灰白。"
                  f"分類結果請人眼看過再用。"),
        **table,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    if args.review:
        print(f"飽和度分界 {split:.0f}（自適應）")
        print(f"{'色':<9}{'亮度':>7}{'飽和':>6}  → 角色")
        for key, l, s, role in rows:
            print(f"{key:<9}{l:7.1f}{s:6.0f}  → {role}")
    counts = {}
    for _, _, _, r in rows:
        counts[r] = counts.get(r, 0) + 1
    print(f"\n{len(table)} 個色群 → {len(counts)} 個角色：" +
          "、".join(f"{k}×{v}" for k, v in sorted(counts.items())))
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
