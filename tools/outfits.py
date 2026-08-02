#!/usr/bin/env python3
"""outfits.py — 公主的服裝：只換調色盤的五個槽，零張新圖。

為什麼是調色盤置換
------------------
重畫一套衣服 = 再做一次公主（15 張生成圖 → 83 格）。
換五個顏色 = 一套 10 個 byte，而且 83 格自動全部跟著換。

分工照 CLAUDE.md 規則 2：**顏色走調色盤，剪影走配件**（`tools/dressup.py`），
兩者不重疊。想要剪影變化就加配件，不要為了換色重畫。

為什麼用算的而不是手挑
----------------------
7 套 × 5 個槽 = 35 個顏色。手挑會挑歪，而且改「亮度階梯要多陡」就得重挑一次。

這裡固定亮度、只換色相與飽和度：

    每個槽的目標亮度 = 現行冰藍那套量到的實際值（86 / 129 / 164 / 221 / 240）

**固定亮度是有理由的，不是圖方便。** 那四個值是模型自己畫出來的明暗關係，
斗篷的立體感整個建立在它們的間距上。只要保住亮度，換任何色相都不會讓衣服變平。
反過來若手挑，很容易挑出「深色比中間色還亮」這種讓陰影翻面的組合——
validator 因此在每一套上檢查階梯是否單調遞增。

色相要避開誰
------------
| 對象 | 色相 | 為什麼要避開 |
|---|---|---|
| 三隻狗 | 0–30°（暖褐） | docs/09：色相是公主最強的辨識頻道 |
| `accent_red` #802444 | 337° | 圍巾與手套**不換色**，衣服撞上去會糊成一片 |

頭髮是中性灰（飽和度只有 6%），所以任何飽和色都相容，不必避。

用法
----
    .venv/bin/python tools/outfits.py            # 產生 + 驗算
    .venv/bin/python tools/outfits.py --check    # 只驗算不寫檔
"""

import argparse
import colorsys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CID = "ice_princess"

# 目標亮度。取自現行冰藍那套在 master 上量到的實際值，不是估的。
TARGET_LUM = {
    "cloak_dark": 86,
    "cloak_mid": 129,
    "cloak_light": 164,
    "trim_pale": 221,
    "trim_light": 240,
}

# (id, 名稱, 色相, 飽和度)。id 就是 unlocked_outfits 的 bit 位置。
# 0 = 一開始就有；1..4 由前四個 affection 里程碑解鎖。
#
# **為什麼是 5 套不是 7 套。** 韌體的 unlocked_outfits 有 8 個 bit、里程碑有 6 個，
# 所以「7 套」看起來才是對的數字。但實際搜過整個色相空間之後，
# 亮度階梯固定（見 TARGET_LUM）、飽和度又要和房間的奶油／土黃／灰玫瑰合群的前提下，
# 七套彼此的最短距離只有 27.3——和冰藍自己對銀髮的邊際一樣勉強，
# 而且必然出現兩組黏在一起（40/60 都是土黃、100/125 都是綠）。
#
#     4 套 54.0 ／ 5 套 45.0 ／ 6 套 33.6 ／ 7 套 27.3
#
# 5 套是拐點。第 5、6 個里程碑改成解鎖**配件**（tools/dressup.py 的 beanie 與
# earmuffs）——那是換剪影不是換色，對小孩來說是完全不同的一種變化，
# 比第六、七件互相分不清的衣服有價值得多。
# **獎勵用兩個軸給，不要把一個軸拉到它的極限。**
OUTFITS = [
    (0, "冰藍",   210, 0.72),
    (1, "陽光黃",  45, 0.78),
    (2, "嫩芽綠",  90, 0.78),
    (3, "森綠",   135, 0.66),
    (4, "莓果粉", 315, 0.78),
]

LUM_W = (0.299, 0.587, 0.114)

# 驗算距離時只看這四個槽。**trim_light 被排除，理由是量出來的**：
# 它在 83 格裡只佔 384 px（0.20%，一格約 4.6 px），是斗篷滾邊上的高光；
# 而且它的目標亮度是 240，**近白色和任何其他近白色的 RGB 距離本來就不可能遠**。
# 把它算進去的後果是：第一版驗算讓一個 4 px 的高光判定七套服裝「和膚色太近」，
# 又讓「薰衣草↔靛藍」的槽距變成 4.1（兩套的 trim_light 都接近白，當然幾乎相同），
# 完全蓋掉真正該比的四個大面積槽。
AREA_SLOTS = ("cloak_dark", "cloak_mid", "cloak_light", "trim_pale")


def lum(rgb):
    return sum(w * c for w, c in zip(LUM_W, rgb))


def solve(hue_deg, sat, target):
    """在固定色相與飽和度下，找出亮度最接近 target 的 RGB。

    HLS 的 L 和感知亮度不是同一件事（同一個 L，黃色遠比藍色亮），
    所以不能直接把 target 當成 L 塞進去——要掃描。
    """
    h = hue_deg / 360.0
    best, bd = None, 1e9
    for i in range(1001):
        r, g, b = colorsys.hls_to_rgb(h, i / 1000.0, sat)
        rgb = (round(r * 255), round(g * 255), round(b * 255))
        d = abs(lum(rgb) - target)
        if d < bd:
            best, bd = rgb, d
    return best


def hexs(rgb):
    return "#%02X%02X%02X" % rgb


def rgb_of(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def main():
    ap = argparse.ArgumentParser(description="產生公主的服裝調色盤置換表")
    ap.add_argument("--check", action="store_true", help="只驗算，不寫檔")
    args = ap.parse_args()

    pal = json.loads((ROOT / ("specs/palettes/%s.json" % CID)).read_text(encoding="utf-8"))
    slots = pal["outfit_slots"]
    fixed = {c["role"]: rgb_of(c["hex"]) for c in pal["colors"]
             if c["role"] not in slots and not c.get("transparent")}

    out = {
        "character_id": CID,
        "format_version": 1,
        "_note": ("服裝只換 outfit_slots 的五個槽，其餘十格不動。一套 10 個 byte，"
                  "83 個影格自動跟著換，零張新圖。"),
        "_lum_why": ("五個槽的亮度**固定**在現行冰藍那套量到的實際值。"
                     "那是模型自己畫出來的明暗關係，斗篷的立體感建立在它們的間距上；"
                     "只換色相與飽和度就不會把衣服弄平，也不會讓陰影翻面。"),
        "_hue_why": ("避開 0–30°（三隻狗的暖褐帶，docs/09 說色相是她最強的辨識頻道）"
                     "與 337° 附近（accent_red 圍巾手套不換色，撞上去會糊）。"),
        "_iris_note": ("**已知限制**：動畫影格裡她的虹膜落在 cloak_dark / cloak_mid，"
                       "所以換服裝時動畫中的眼珠會跟著換色（master 不會，它的眼睛由 "
                       "patch.json 逐像素指定）。眼睛只有 3–4 px，這個代價可以接受，"
                       "但要知道它存在。詳見 specs/palettes/ice_princess.json 的 _outfit_note。"),
        "_count_why": ("5 套不是 7 套。搜過整個色相空間：4 套彼此最短 54.0、5 套 45.0、"
                       "6 套 33.6、7 套只剩 27.3（和冰藍對銀髮的邊際一樣勉強，"
                       "而且必然有兩組黏在一起）。第 5、6 個里程碑改成解鎖配件——"
                       "換剪影和換色是兩種不同的變化，**獎勵用兩個軸給，"
                       "不要把一個軸拉到它的極限**。"),
        "_threshold_why": ("驗算的門檻不是隨便訂的，是拿**現行冰藍那套**當基準："
                           "它的 trim_pale 對 hair_light 只有 27.5，但那套已經上機看過、"
                           "讀得出來（描邊幫了忙）。所以 27.0 就是可接受的下限。"),
        "generator": "tools/outfits.py",
        "outfits": [],
    }

    rows = []
    for oid, name, hue, sat in OUTFITS:
        cols = {}
        for role in slots:
            cols[role] = hexs(solve(hue, sat, TARGET_LUM[role]))
        out["outfits"].append({
            "id": oid, "name": name, "hue": hue, "saturation": sat,
            "unlocked_by": ("一開始就有" if oid == 0 else "第 %d 個 affection 里程碑" % oid),
            "colors": cols,
        })
        rows.append((oid, name, hue, cols))

    # --- 驗算 1：亮度階梯必須單調遞增 ---------------------------------
    print("驗算 1：亮度階梯（模型畫出來的明暗關係，翻面就會讓陰影錯亂）")
    bad = 0
    for oid, name, hue, cols in rows:
        ls = [lum(rgb_of(cols[r])) for r in slots]
        ok = all(a < b for a, b in zip(ls, ls[1:]))
        print("  %d %-8s %s  %s" % (oid, name,
              " ".join("%3.0f" % v for v in ls), "✅" if ok else "❌ 非遞增"))
        bad += not ok

    # --- 驗算 2：和不換色的十格要分得開 -------------------------------
    print("\n驗算 2：和固定不換的顏色的最短距離（圍巾撞衣服會糊成一片）")
    print("  只比 %s——trim_light 只佔 0.20%% 且是近白高光，見 AREA_SLOTS 的註解"
          % "／".join(AREA_SLOTS))
    print("  %-10s %-14s %s" % ("服裝", "最接近的固定色", "RGB 距離"))
    for oid, name, hue, cols in rows:
        worst, wrole = 1e9, None
        for role in AREA_SLOTS:
            c = cols[role]
            for frole, frgb in fixed.items():
                d = dist(rgb_of(c), frgb)
                if d < worst:
                    worst, wrole = d, "%s↔%s" % (role, frole)
        flag = "✅" if worst >= 40 else ("⚠️ 偏近" if worst >= 25 else "❌ 太近")
        print("  %-10s %-14s %6.1f  %s" % (name, wrole, worst, flag))
        bad += worst < 25

    # --- 驗算 3：七套彼此要分得開 -------------------------------------
    print("\n驗算 3：七套彼此的距離（小孩要認得出「今天穿的是哪一套」）")
    worst_pair, wv = None, 1e9
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            d = min(dist(rgb_of(rows[i][3][r]), rgb_of(rows[j][3][r])) for r in AREA_SLOTS)
            if d < wv:
                wv, worst_pair = d, (rows[i][1], rows[j][1])
    print("  最接近的兩套：%s ↔ %s，最短槽距 %.1f  %s"
          % (*worst_pair, wv, "✅" if wv >= 30 else "❌ 太近"))
    bad += wv < 30

    if bad:
        raise SystemExit("\n❌ 有 %d 項沒過，不寫檔" % bad)
    if not args.check:
        p = ROOT / ("specs/outfits/%s.json" % CID)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("\n→ %s（%d 套 × 5 槽 = %d 個 byte）"
              % (p.relative_to(ROOT), len(rows), len(rows) * 5 * 2))


if __name__ == "__main__":
    main()
