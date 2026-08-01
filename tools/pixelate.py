#!/usr/bin/env python3
"""
pixelate.py — 把 AI 生成的「偽像素藝術」轉成真正的像素藝術

問題背景
--------
影像生成模型無法產出真正的像素藝術。實測 gpt-image-2 的輸出：

    宣稱  乾淨的像素網格、10 色
    實際  27,990 色、色塊長度 2/3/4/5 混雜、背景「純洋紅」有 10 幾種雜訊值

視覺上像像素藝術，但每個色塊大小不一、邊緣有反鋸齒、色數爆炸。
直接拿去量化成 4bpp 索引色會產生嚴重色帶，RLE 壓縮率也會崩掉。

本工具把它修成真的像素藝術：對齊到單一網格、精確的色數、乾淨的 alpha。

演算法
------
1. 去背    以色度距離把背景色（預設洋紅）判為透明
2. 預量化  先把前景壓到 48 色，讓後續的「眾數」有意義
           （原始 28k 色的話，每個 cell 幾乎每個像素都是不同顏色，取眾數沒用）
3. 降採樣  每個目標格取「前景眾數色」而非平均值
           平均值會在色塊邊界產生原本不存在的中間色，眾數不會
4. 終量化  用 median cut 壓到指定色數
5. 輸出    真解析度 sprite + 放大預覽 + 調色盤 JSON

用法
----
    python tools/pixelate.py art/generated/foo.png --width 64 --colors 12
    python tools/pixelate.py art/generated/foo.png --width 64 --colors 12 \
        --bg FF00FF --tol 90 --scale 8 --out-dir build/pixelated
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


# remap 殘留色的預設吸附候選。只放深色階——殘渣是描邊附近的邊緣像素。
DEFAULT_FALLBACK_ROLES = "outline,coat_dark,coat_mid"


# --------------------------------------------------------------------------
# 步驟 1：去背
# --------------------------------------------------------------------------

def build_foreground_mask(rgb: np.ndarray, bg_rgb: tuple, tol: float) -> np.ndarray:
    """以歐氏色度距離判斷前景。回傳 bool 陣列，True = 前景。

    洋紅背景配棕色主體時，兩者的色度距離極大（>300），
    所以 tol=90 這種寬鬆的門檻就足夠，且能一併吃掉背景的雜訊值。
    """
    diff = rgb.astype(np.int16) - np.array(bg_rgb, dtype=np.int16)
    dist = np.sqrt((diff.astype(np.float32) ** 2).sum(axis=-1))
    return dist > tol


def content_bbox(mask: np.ndarray, pad: int = 0) -> tuple:
    """取前景的外接框。全透明時回傳整張。"""
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return 0, 0, mask.shape[1], mask.shape[0]
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    h, w = mask.shape
    return (max(0, x0 - pad), max(0, y0 - pad),
            min(w, x1 + 1 + pad), min(h, y1 + 1 + pad))


# --------------------------------------------------------------------------
# 步驟 2：預量化
# --------------------------------------------------------------------------

def prequantize(rgb: np.ndarray, mask: np.ndarray, n: int = 48) -> np.ndarray:
    """只對前景像素做預量化，背景填成黑色（反正之後會被 mask 掉）。

    這一步是關鍵。不先做的話，28k 色的來源每個 cell 內幾乎全是相異色，
    取眾數等同於隨機取一個像素，結果會有雜訊。
    """
    work = rgb.copy()
    work[~mask] = 0
    img = Image.fromarray(work, "RGB").quantize(
        colors=n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    return np.array(img.convert("RGB"))


# --------------------------------------------------------------------------
# 步驟 3：降採樣（眾數）
# --------------------------------------------------------------------------

def downsample_mode(rgb: np.ndarray, mask: np.ndarray,
                    tw: int, th: int, coverage: float = 0.45) -> np.ndarray:
    """降採樣到 tw x th，每格取前景眾數色。回傳 RGBA。

    coverage: 一格內前景比例低於此值就判定為透明。
              0.45 略低於半數，讓細長的部位（腿、尾巴、耳朵尖）不會被吃掉。
    """
    sh, sw = mask.shape
    out = np.zeros((th, tw, 4), dtype=np.uint8)

    # 用浮點邊界切格，避免整數除法在邊緣累積誤差
    xs = [round(i * sw / tw) for i in range(tw + 1)]
    ys = [round(i * sh / th) for i in range(th + 1)]

    for ty in range(th):
        y0, y1 = ys[ty], max(ys[ty] + 1, ys[ty + 1])
        for tx in range(tw):
            x0, x1 = xs[tx], max(xs[tx] + 1, xs[tx + 1])

            cell_mask = mask[y0:y1, x0:x1]
            total = cell_mask.size
            fg_count = int(cell_mask.sum())

            if total == 0 or fg_count / total < coverage:
                continue  # 保持透明

            cell_rgb = rgb[y0:y1, x0:x1][cell_mask]
            counts = Counter(map(tuple, cell_rgb))
            colour = counts.most_common(1)[0][0]
            out[ty, tx] = (*colour, 255)

    return out


# --------------------------------------------------------------------------
# 步驟 4：終量化
# --------------------------------------------------------------------------

def snap_to_palette(rgb: np.ndarray, opaque: np.ndarray, palette: list) -> np.ndarray:
    """把每個不透明像素吸附到固定調色盤中最接近的顏色（純數值距離）。

    ⚠️ 只適用於「已經照著調色盤畫」的來源，用途是修掉重採樣產生的微小色偏。

    **不要**拿來把手工調色盤硬套在 AI 產出上。最近 RGB 距離不懂語意：
    實測時，狗身上的中棕色像素因為數值上離 muzzle_grey_dark 比離 coat_mid 更近，
    整片肚子和腿都被吸成灰白色。語意指派請用 remap_by_role()。
    """
    pal = np.array(palette, dtype=np.int16)          # (P, 3)
    px = rgb[opaque].astype(np.int16)                # (N, 3)
    d = ((px[:, None, :] - pal[None, :, :]) ** 2).sum(axis=-1)
    out = rgb.copy()
    out[opaque] = pal[d.argmin(axis=1)].astype(np.uint8)
    return out


def remap_by_role(rgb: np.ndarray, opaque: np.ndarray,
                  remap: dict, roles: dict, fallback=None) -> tuple:
    """依「來源色 → 語意角色 → 調色盤色」的對照表重新上色。

    這是把 AI 產出接上手工調色盤的正確做法。AI 決定「哪些像素是同一個材質」，
    人決定「那個材質該是什麼顏色」。兩件事分開，才不會發生灰白肚子那種事故。

    值有兩種寫法：

        "#D0A980": "tan_mid"                       整張圖都指到同一個角色

        "#D0A980": [                               區域限定，依序比對，先中先贏
            {"role": "muzzle_grey_light",
             "bbox": [45, 10, 60, 23]},            吻部：去飽和灰白
            {"role": "tan_mid"}                    其餘（腳、胸口）：淺褐
        ]

    區域限定是為了解決「同一個來源色在不同身體部位有不同語意」。
    實測 AI 在吻部和小腿用了同一個 #D0A980，整張圖一起 remap 會讓腳跟著變灰。
    bbox 用最終 sprite 的像素座標，格式 [x0, y0, x1, y1]，右下界不含。

    回傳 (rgb, 未對應到的來源色清單)。
    """
    def resolve(role_name):
        if role_name not in roles:
            raise SystemExit(f"remap 指到不存在的角色 '{role_name}'，檢查調色盤檔")
        return roles[role_name]

    h, w = opaque.shape
    yy, xx = np.mgrid[0:h, 0:w]

    out, missing = rgb.copy(), []
    present = {tuple(int(v) for v in c) for c in rgb[opaque]}

    for colour in present:
        key = "#%02X%02X%02X" % colour
        rule = remap.get(key)
        if rule is None:
            missing.append(key)
            if fallback:
                # 殘留色吸附到**指定的安全色階**，不是整個調色盤。
                #
                # 實測教訓：一開始讓它吸附整個調色盤，殘留色被拉到
                # muzzle_grey_light 與 eye_highlight 這兩個亮色，
                # 角色臉上腿上出現大片白斑——正是本檔 snap_to_palette()
                # 警告過的「最近距離不懂語意」。
                #
                # 殘留色的來源是縮放與旋轉在描邊附近留下的邊緣殘渣，
                # 語意上它們就是描邊或深色毛，所以候選集必須限定在深色階。
                pal = np.array(fallback, dtype=np.int16)
                d2 = ((np.array(colour, dtype=np.int16) - pal) ** 2).sum(axis=1)
                out[opaque & np.all(rgb == np.array(colour, dtype=np.uint8), axis=-1)] = \
                    pal[int(d2.argmin())].astype(np.uint8)
            continue

        match = opaque & np.all(rgb == np.array(colour, dtype=np.uint8), axis=-1)
        entries = rule if isinstance(rule, list) else [{"role": rule}]

        remaining = match.copy()
        for e in entries:
            target = resolve(e["role"])
            if "bbox" in e:
                x0, y0, x1, y1 = e["bbox"]
                sel = remaining & (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)
            else:
                sel = remaining               # 無 bbox 的條目吃掉剩下的全部
            out[sel] = target
            remaining &= ~sel
            if not remaining.any():
                break

    return out, sorted(missing)


def final_quantize(rgba: np.ndarray, n_colors: int,
                   fixed_palette=None, remap=None, roles=None,
                   remap_fallback=False) -> tuple:
    """把不透明像素壓到 n_colors 色。回傳 (rgba, palette, missing)。

    透明像素不參與量化，否則會浪費一個調色盤格子在背景色上。

    三種模式：
      remap + roles   語意指派（正確做法，用於把 AI 產出接上手工調色盤）
      fixed_palette   數值吸附（只用於已照調色盤畫的來源，修微小色偏）
      都沒給          自適應量化到 n_colors（探索階段用）
    """
    alpha = rgba[..., 3]
    opaque = alpha > 0
    if not opaque.any():
        return rgba, list(fixed_palette or []), []

    rgb = rgba[..., :3]
    missing = []
    fallback = fixed_palette

    # 先自適應量化成穩定的少數色群，remap 的對照表才有固定的鍵可以指
    distinct = len(set(map(tuple, rgb[opaque])))
    if distinct > n_colors and not fixed_palette:
        flat = rgb.copy()
        flat[~opaque] = 0
        img = Image.fromarray(flat).quantize(
            colors=n_colors, method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE
        )
        rgb = np.array(img.convert("RGB"))

    if remap:
        rgb, missing = remap_by_role(rgb, opaque, remap, roles or {},
                                     remap_fallback)
    elif fixed_palette:
        rgb = snap_to_palette(rgb, opaque, fixed_palette)

    out = rgba.copy()
    out[..., :3] = rgb
    out[~opaque] = 0  # 透明處清成 0，讓輸出可重現
    palette = sorted({tuple(int(v) for v in c) for c in rgb[opaque]})
    return out, palette, missing


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def pixelate(src: Path, target_w: int, n_colors: int, bg_rgb: tuple,
             tol: float, scale: int, coverage: float, out_dir: Path,
             fixed_palette=None, crop: bool = True,
             target_h=None, remap=None, roles=None,
             remap_fallback=False) -> dict:
    img = Image.open(src).convert("RGB")
    rgb = np.array(img)

    mask = build_foreground_mask(rgb, bg_rgb, tol)
    if not mask.any():
        raise SystemExit(f"找不到前景，檢查 --bg / --tol 設定（來源 {src.name}）")

    if crop:
        # 單張設定圖：裁到內容範圍，角色填滿畫布，比例可預測
        x0, y0, x1, y1 = content_bbox(mask)
        rgb, mask = rgb[y0:y1, x0:x1], mask[y0:y1, x0:x1]
    else:
        # 動畫影格：**絕對不能**逐格裁切。每一格的內容範圍都不同，
        # 裁切會讓降採樣的網格錨點在格與格之間漂移，播放時角色會抖。
        # 用固定的來源畫布，讓所有影格共用同一組格線。
        x0, y0, x1, y1 = 0, 0, rgb.shape[1], rgb.shape[0]

    ch, cw = mask.shape
    if target_h is None:
        target_h = max(1, round(target_w * ch / cw))

    pre = prequantize(rgb, mask, n=max(32, n_colors * 4))
    small = downsample_mode(pre, mask, target_w, target_h, coverage)
    small, palette, missing = final_quantize(
        small, n_colors, fixed_palette, remap, roles, remap_fallback)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    sprite_path = out_dir / f"{stem}_{target_w}px.png"
    Image.fromarray(small).save(sprite_path)

    preview_path = out_dir / f"{stem}_{target_w}px_x{scale}.png"
    Image.fromarray(small).resize(
        (target_w * scale, target_h * scale), Image.Resampling.NEAREST
    ).save(preview_path)

    palette_path = out_dir / f"{stem}_{target_w}px_palette.json"
    report = {
        "source": str(src),
        "source_size": [int(img.width), int(img.height)],
        "content_bbox": [int(x0), int(y0), int(x1), int(y1)],
        "sprite_size": [target_w, target_h],
        "colors": len(palette),
        "palette": [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in palette],
        "opaque_pixels": int((small[..., 3] > 0).sum()),
        "unmapped_colors": missing,
        "sprite": str(sprite_path),
        "preview": str(preview_path),
    }
    palette_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def parse_hex(s: str) -> tuple:
    s = s.lstrip("#")
    if len(s) != 6:
        raise argparse.ArgumentTypeError("顏色請用 RRGGBB 格式")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def main() -> None:
    ap = argparse.ArgumentParser(description="把 AI 偽像素圖轉成真像素藝術")
    ap.add_argument("input", type=Path, nargs="+", help="來源 PNG")
    ap.add_argument("--width", type=int, default=64, help="目標 sprite 寬（預設 64）")
    ap.add_argument("--colors", type=int, default=12, help="最終色數，不含透明（預設 12）")
    ap.add_argument("--bg", type=parse_hex, default=(255, 0, 255), help="去背色（預設 FF00FF）")
    ap.add_argument("--tol", type=float, default=90.0, help="去背色度容差（預設 90）")
    ap.add_argument("--coverage", type=float, default=0.45,
                    help="一格內前景比例門檻，低於此判為透明（預設 0.45）")
    ap.add_argument("--scale", type=int, default=8, help="預覽放大倍率（預設 8）")
    ap.add_argument("--out-dir", type=Path, default=Path("build/pixelated"))
    ap.add_argument("--palette", type=Path,
                    help="固定調色盤 JSON（specs/palettes/<char>.json）。"
                         "烘焙動畫影格時必須指定，否則各格會色彩閃爍")
    ap.add_argument("--height", type=int,
                    help="目標 sprite 高。省略則依來源比例推算")
    ap.add_argument("--no-crop", action="store_true",
                    help="不裁切到內容範圍。烘焙動畫影格時必須加，"
                         "否則各格的網格錨點會漂移")
    ap.add_argument("--remap", type=Path,
                    help="語意對照表 JSON {來源色: 調色盤角色}。搭配 --palette 使用")
    ap.add_argument("--remap-fallback", nargs="?", const=DEFAULT_FALLBACK_ROLES,
                    default=None, metavar="ROLES",
                    help="remap 沒涵蓋的殘留色吸附到指定角色（逗號分隔）。"
                         f"預設 {DEFAULT_FALLBACK_ROLES}。"
                         "**不要**放亮色進去——殘渣是描邊附近的邊緣像素，"
                         "吸到亮色會在角色身上產生白斑")
    ap.add_argument("--dump-clusters", action="store_true",
                    help="輸出對照表範本，填好角色後再用 --remap")
    args = ap.parse_args()

    fixed, roles = None, None
    if args.palette:
        data = json.loads(args.palette.read_text())
        entries = data["colors"] if isinstance(data, dict) else data
        entries = [c for c in entries
                   if not (isinstance(c, dict) and c.get("transparent"))]
        fixed = [parse_hex(c["hex"] if isinstance(c, dict) else c) for c in entries]
        roles = {c["role"]: parse_hex(c["hex"]) for c in entries if isinstance(c, dict)}
        print(f"調色盤：{args.palette}（{len(fixed)} 色）")

    remap = json.loads(args.remap.read_text()) if args.remap else None

    fb = None
    if args.remap_fallback:
        want = [r.strip() for r in args.remap_fallback.split(",") if r.strip()]
        unknown = [r for r in want if r not in (roles or {})]
        if unknown:
            sys.exit(f"--remap-fallback 指到不存在的角色：{', '.join(unknown)}")
        fb = [roles[r] for r in want]
        print(f"  殘留色吸附候選：{', '.join(want)}")
    if remap and not roles:
        sys.exit("--remap 需要同時給 --palette")
    if args.palette and not remap and not args.dump_clusters:
        print("  ⚠️  只給 --palette 會走純數值吸附，不懂語意，可能把毛色吸成別的材質。\n"
              "     正式資產請先用 --dump-clusters 產生對照表，再用 --remap 指派。")

    for src in args.input:
        r = pixelate(src, args.width, args.colors, args.bg,
                     args.tol, args.scale, args.coverage, args.out_dir,
                     # dump 模式要看 AI 自然的色群，不能先被吸附掉
                     fixed_palette=None if (remap or args.dump_clusters) else fixed,
                     crop=not args.no_crop, target_h=args.height,
                     remap=remap, roles=roles, remap_fallback=fb)
        print(f"{src.name}")
        print(f"  來源 {r['source_size'][0]}x{r['source_size'][1]}"
              f"  →  sprite {r['sprite_size'][0]}x{r['sprite_size'][1]}"
              f"  {r['colors']} 色  {r['opaque_pixels']} 個不透明像素")
        if args.dump_clusters:
            stub = {h: "TODO_ROLE" for h in r["palette"]}
            path = args.out_dir / f"{src.stem}_remap.json"
            path.write_text(json.dumps(stub, indent=2))
            print(f"  對照表範本已寫出，請填入語意角色再用 --remap：{path}")
            print(f"  可用角色：{', '.join(roles) if roles else '(先給 --palette)'}")
        if r["unmapped_colors"]:
            print(f"  ⚠️  未對應的來源色（維持原色）：{', '.join(r['unmapped_colors'])}")
        print(f"  {r['preview']}")


if __name__ == "__main__":
    main()
