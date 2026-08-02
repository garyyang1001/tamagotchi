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
# 「亮而不飽和」那一格要歸給誰，是**每隻狗不一樣的**。
# brown_mixed 是灰白吻部；brindle_guard 幾乎沒有灰白吻部，
# 它那一格是淡粉色胸背帶——實測整條背帶被判成 muzzle_grey_light，
# 畫面上是狗身上一條灰色帶子。用調色盤的 automap_bands.bright_low_sat_role 覆蓋。

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


def classify(c, split, low_sat_role=None, bright_bands=None):
    """把一個色群分到調色盤角色。

    亮部有兩種模式：
    · 預設 —— 一刀飽和度，分「暖色標記 vs 去飽和的灰白」。前兩隻狗夠用。
    · bright_bands —— 亮部改用一條明確的亮度階梯。chihuahua 需要這個：
      牠的亮部有五階（金褐/亮金/奶油金/奶油/純白），一刀切不開。
      實測牠的亮部色群是 111-149 / 153-180 / 189-211 / 217-238 / 246-251，
      中間都有明顯斷層，用亮度就分得乾淨。
      給了 bright_bands 就**不套** eye_highlight 那條特例——
      那條規則假設「極亮且極低飽和」是只有幾個像素的眼睛高光，
      對一隻胸前有一大片白圍脖的狗完全錯誤（實測整片白被判成 eye_highlight，
      白色標記在畫面上整個消失）。這隻的眼睛高光是 patch.json 手工畫的。
    """
    l, s = lum(c), sat(c)
    if not bright_bands and l >= HIGHLIGHT_LUM and s <= HIGHLIGHT_SAT:
        return "eye_highlight"
    for hi, role in LUM_BANDS:
        if l < hi:
            return role
    if bright_bands:
        for hi, role in bright_bands:
            if l <= hi:
                return role
        return bright_bands[-1][1]
    return BRIGHT_HIGH_SAT if s >= split else (low_sat_role or BRIGHT_LOW_SAT)


def hue(c):
    """0-360。飽和度很低時色相沒有意義，呼叫端要先看 sat()。"""
    r, g, b = (v / 255.0 for v in c)
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        return 0.0
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0


def in_hue_range(h, lo, hi):
    """色相區間，允許跨 0°（例如紅色的 [330, 30]）。"""
    return (lo <= h <= hi) if lo <= hi else (h >= lo or h <= hi)


def classify_families(c, families):
    """先分色系，再在色系內分亮度階。

    為什麼需要：`ice_princess` 有四個色系（藍斗篷／銀髮／暖白膚色／莓紅圍巾），
    亮度互相重疊——銀髮 137/181/218 卡在斗篷的 129/164 之間，
    莓紅 67 又和斗篷最暗的 86 只差 19。一條亮度階梯不管怎麼調都分不開。
    但它們的**色相**分得很開（藍 204-214°、暖 30-40°、紅 339°、銀 飽和度<15），
    所以改成色相先分家、家內再排亮度。

    三隻狗不用這個：牠們全身只有一個色系（暖褐），亮度階梯就夠了。
    """
    h, s, l = hue(c), sat(c), lum(c)
    for fam in families:
        lo, hi = fam.get("hue", [0, 360])
        if not in_hue_range(h, lo, hi):
            continue
        if s < fam.get("sat_min", 0) or s > fam.get("sat_max", 999):
            continue
        for cut, role in fam["bands"]:
            if l <= cut:
                return role
        return fam["bands"][-1][1]
    return None


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

    pal_doc = json.loads(args.palette.read_text())
    roles = {c["role"] for c in pal_doc["colors"] if not c.get("transparent")}

    # 亮度分界可以由調色盤覆寫。預設值是照 brown_mixed 的深巧克力毛校準的，
    # 底色更亮的角色（例如 brindle_guard 的金褐）整組分界要上移，否則
    # 整隻狗會被歸進 coat_light 以上，色階全部塌掉。
    LOW_SAT_ROLE = None
    BRIGHT_BANDS = None
    FAMILIES = [f for f in pal_doc.get("automap_families", []) if f.get("bands")]
    if FAMILIES:
        print(f"  調色盤用色相族群分類（{len(FAMILIES)} 族），不走預設的亮度階梯")
    bands = pal_doc.get("automap_bands")
    if bands and not FAMILIES:
        global LUM_BANDS, SAT_SPLIT
        LUM_BANDS = [(bands[k], k) for k in
                     ("outline", "coat_dark", "coat_mid", "coat_light")
                     if k in bands]
        LUM_BANDS.sort()
        SAT_SPLIT = bands.get("sat_split", SAT_SPLIT)
        LOW_SAT_ROLE = bands.get("bright_low_sat_role")
        BRIGHT_BANDS = [tuple(x) for x in bands.get("bright_bands", [])] or None
        print(f"  調色盤覆寫亮度分界："
              + "、".join(f"{r}<{v}" for v, r in LUM_BANDS)
              + f"，飽和分界 {SAT_SPLIT}")
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
        role = (classify_families(c, FAMILIES) if FAMILIES
                else classify(c, split, LOW_SAT_ROLE, BRIGHT_BANDS))
        if role is None:
            role = "outline"   # 色相族群沒中，退回描邊（一定是很暗的邊緣像素）
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
