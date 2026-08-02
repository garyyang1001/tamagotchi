#!/usr/bin/env python3
"""
cutstrip.py — 把「一張圖含多個連續影格」切成對齊的動畫影格

為什麼有這支工具
----------------
原本的路線是：切成 16 個部件 → 擺姿勢 → 旋轉 → 合成。
實測失敗：整條腿繞肩關節旋轉會讓腳掌畫弧離開地面，
看起來像桌子在甩腿。真實的腿在膝肘彎曲、腳掌貼地推進，
單段式部件做不到。

改成請影像模型**一次生成一張含全部影格的圖**。
關鍵差別：那是單次推理，同一張圖內的一致性模型做得到
（B3 部件分解圖一次生 16 個部件就很成功）。
分開多次生成才會漂移，那條教訓仍然成立。

對齊為什麼是這支工具的核心
--------------------------
每一格的內容範圍都不同（伸腿的那格比較寬）。若各自裁切再降採樣，
每格的降採樣格線錨點就不一樣，播放時整隻角色會抖——
這是 docs/05 鐵律 2 的同一個問題。

所以這裡把每一格**貼到同一張畫布的同一個位置**（腳底對齊地面線、
身體水平置中），再交給 pixelate.py 用 --no-crop 一起降採樣。
所有影格共用一組格線。

對齊基準用「腳底 + 軀幹水平中心」而不是外接框中心：
伸腿的那格外接框會往前偏，用框中心對齊會讓身體前後晃。

用法
----
    python tools/cutstrip.py art/generated/B4_brown_mixed_walkcycle.png \\
        --grid 2x2 --out-dir build/strip/walk --name brown_mixed_walk

    # 水平長條
    python tools/cutstrip.py sheet.png --grid 4x1 ...

接下來
------
    python tools/pixelate.py build/strip/walk/*.png --width 64 --height 56 \\
        --no-crop --colors 12 --palette specs/palettes/brown_mixed.json \\
        --remap ... --remap-fallback --out-dir build/strip/walk_px
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


from cell import GRID, cell_for, canvas_for, ground_row_for   # noqa: E402

BG = (255, 0, 255)


def foreground(rgb: np.ndarray, bg, tol: float) -> np.ndarray:
    d = np.sqrt(((rgb.astype(np.int16) - np.array(bg, dtype=np.int16))
                 .astype(np.float32) ** 2).sum(axis=-1))
    mask = d > tol
    # 去溢色：偏向背景色相的邊緣殘渣直接判為透明（同 cutparts.py）
    r, g, b = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    return mask & ~((r > bg[0] * 0.42) & (g < 80) & (b > bg[2] * 0.42))


def body_center_x(mask: np.ndarray, y0: int, y1: int) -> float:
    """取軀幹段的水平中心。

    不用整體外接框的中心：伸腿的影格外接框會往前偏，
    用它對齊會讓身體在播放時前後晃。軀幹（上半部）才是穩定的參考。
    """
    band = mask[y0:y1]
    if not band.any():
        cols = np.where(mask.any(axis=0))[0]
        return (cols[0] + cols[-1]) / 2.0
    cols = np.where(band.any(axis=0))[0]
    return (cols[0] + cols[-1]) / 2.0


def hex2rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def protect_rules(palette_path) -> list:
    """讀調色盤裡的 protect 段。沒給調色盤或沒有 protect 就回空清單。"""
    if not palette_path:
        return []
    data = json.loads(Path(palette_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    return [r for r in data.get("protect", []) if r.get("role")]


def main() -> None:
    ap = argparse.ArgumentParser(description="把連續影格圖切開並對齊")
    ap.add_argument("input", type=Path)
    ap.add_argument("--grid", default="2x2", help="影格排列，例如 2x2 或 4x1（欄x列）")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--name", required=True, help="輸出檔名前綴")
    ap.add_argument("--order", default="rows", choices=["rows", "cols"],
                    help="讀取順序：rows = 由左至右再往下（預設）")
    ap.add_argument("--tol", type=float, default=120.0)
    ap.add_argument("--colors", type=int, default=16,
                    help="**切開之前**先對整張來源圖量化到這麼多色（預設 16）。"
                         "12 太少：實測 eat_happy / pet_react 的吻部與腳掌會被合併成"
                         "同一個色群，automap 就分不出灰白與淺褐。16 剛好分得開。"
                         "這是關鍵：若讓每一格各自量化，色群值會不一樣，"
                         "同一份 remap 對照表只會中第一格，其餘全部落到 fallback。"
                         "見 docs/05 鐵律 1。")
    ap.add_argument("--target-area", type=int, default=1800,
                    help="角色在最終 sprite 上的不透明像素數（預設 1800，對齊 master 的 1809）。"
                         "**用面積不用高度**：趴著的狗很矮，用高度正規化會把它橫向拉爆畫布。"
                         "面積比高度、寬度都更不受姿勢影響。")
    ap.add_argument("--character",
                    help="從 specs/characters/<id>.json 讀 render.sprite_cell。"
                         "省略就用 64×56。公主是 64×112——人形站起來是狗的兩倍高，"
                         "寫死的話她的頭會被畫布切掉。")
    ap.add_argument("--palette", type=Path,
                    help="調色盤 JSON。這裡只用它的 protect 段——量化前先把那些像素"
                         "標記起來，量化後還原成原色，色相才傳得到 pixelate。")
    ap.add_argument("--match-area", type=Path,
                    help="改用某張已定稿 sprite 的不透明像素數當目標面積。"
                         "動畫必須和 master 同尺度：turn / happy / sad_wait / idle_breathe "
                         "是直接畫 master 的，走路卻是這條管線出來的，兩者面積不一致的話"
                         "角色會在切換動畫時忽大忽小。實測 brindle_guard 用 1800 的預設值"
                         "會比它的 master（1253）大 40%。")
    ap.add_argument("--torso-band", default="0.10,0.55",
                    help="軀幹取樣的高度區間（相對內容高度），用來算水平對齊基準")
    args = ap.parse_args()

    CELL = cell_for(args.character) if args.character else (64, 56)
    CANVAS = canvas_for(CELL)
    GROUND_ROW = ground_row_for(CELL)
    if CELL != (64, 56):
        print(f"  影格格 {CELL[0]}×{CELL[1]}，對齊畫布 {CANVAS[0]}×{CANVAS[1]}，"
              f"地面線在第 {GROUND_ROW} 列")

    if args.match_area:
        ref = np.array(Image.open(args.match_area).convert("RGBA"))
        args.target_area = int((ref[..., 3] > 0).sum())
        print(f"  目標面積對齊 {args.match_area.name}：{args.target_area} 個像素")

    cols, rows = (int(v) for v in args.grid.lower().split("x"))
    src = Image.open(args.input).convert("RGB")

    # 先整張量化，再切。所有影格因此天生共用同一組顏色——
    # 這樣一份 remap 對照表就能涵蓋全部影格。
    if args.colors:
        # 只量化前景。整張一起量化的話，洋紅底佔了 68% 面積會吃掉大部分配額，
        # 實測角色只分到 3 色。先把背景填成單一色再量化，背景只佔 1 格。
        arr = np.array(src)
        fg = foreground(arr, BG, args.tol)
        work = arr.copy()
        work[~fg] = BG                     # 背景壓成單一純色
        q = np.array(Image.fromarray(work).quantize(
            colors=args.colors + 1, method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE).convert("RGB"))
        q[~fg] = BG                        # 背景復原成乾淨的洋紅

        # 保護色要在這裡就救回來。這一步是 16 色量化，眼睛的藍在原圖只佔 0.06%，
        # 一定會被併掉——等到 pixelate 才想保護就來不及了，它看到的已經是量化後的圖。
        # 做法是把命中的像素還原成原始 RGB，讓色相原封不動傳到下一關。
        for rule in protect_rules(args.palette):
            tgt = np.array(hex2rgb(rule["src_hex"]), dtype=float)
            d = np.sqrt(((arr.astype(float) - tgt) ** 2).sum(axis=2))
            hit = (d <= float(rule.get("tol", 45))) & fg
            if hit.any():
                q[hit] = arr[hit]
                print(f"  保護 {rule['role']}：{int(hit.sum())} 個原始像素未經量化")

        src = Image.fromarray(q)
        n = len({tuple(c) for c in q[fg]})
        print(f"  來源先量化前景到 {n} 色，確保各格共用同一組色群")

    W, H = src.size
    cw, ch = W // cols, H // rows

    cells = []
    for r in range(rows):
        for c in range(cols):
            cells.append((c, r))
    if args.order == "cols":
        cells.sort(key=lambda t: (t[0], t[1]))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    b0, b1 = (float(v) for v in args.torso_band.split(","))
    ground_y = GROUND_ROW * GRID
    canvas_cx = CANVAS[0] // 2

    # 第一輪：量出每一格的內容，算共用縮放倍率
    scan = []
    for i, (c, r) in enumerate(cells):
        tile = np.array(src.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)))
        m = foreground(tile, BG, args.tol)
        if not m.any():
            print(f"  ⚠️ 第 {i} 格沒有前景，跳過")
            continue
        ys, xs = np.where(m)
        scan.append((i, tile, m, int(xs.min()), int(ys.min()),
                     int(xs.max()) + 1, int(ys.max()) + 1))

    if not scan:
        raise SystemExit("整張圖都沒有前景，檢查 --grid 與 --tol")

    # 有些動畫刻意留空格（idle_blink 只有 3 格，第四象限是空的洋紅；turn 只有 2 格）。
    # 那些格子上通常有幾個去背沒清乾淨的雜點——不是「沒有前景」，所以上面的檢查放它過去。
    # 但只要它進了 scan，就會把平均面積拉低，整批影格跟著放大：
    # 實測 idle_blink 的第四格只有 6 個像素，其餘三格因此被放大 22%，超出畫布。
    # 用中位數的比例判空，不用絕對值——不同角色的面積量級差很多。
    med = sorted(int(m[y0:y1, x0:x1].sum()) for _, _, m, x0, y0, x1, y1 in scan)[len(scan) // 2]
    keep = []
    for item in scan:
        _, _, m, x0, y0, x1, y1 = item
        a = int(m[y0:y1, x0:x1].sum())
        if a < med * 0.20:
            print(f"  ⚠️ 第 {item[0]} 格只有 {a} px（中位數的 {a / med:.1%}），"
                  f"判定為刻意留空的象限，不計入")
        else:
            keep.append(item)
    scan = keep

    # 全部影格共用同一個倍率，相對大小才不會在播放時變動。
    #
    # 以**面積**為準，不是高度。實測：sleep_breathe 的狗是趴著的，
    # 內容高度只有站姿的一半，用「最高的那格 = 54px」正規化會把它放大兩倍，
    # 橫向直接爆出 64px 的畫布。面積在不同姿勢之間穩定得多
    # （躺著和站著的剪影面積接近）。
    areas = [int(m[y0:y1, x0:x1].sum()) for _, _, m, x0, y0, x1, y1 in scan]
    mean_area = sum(areas) / len(areas)
    scale = ((args.target_area * GRID * GRID) / mean_area) ** 0.5
    print(f"  共用縮放 {scale:.3f}×（平均內容面積 {mean_area:.0f}px² "
          f"→ 目標 {args.target_area} 個像素）\n")

    report = []
    for i, tile, m, x0, y0, x1, y1 in scan:
        content_h = y1 - y0
        cx = body_center_x(m, y0 + int(content_h * b0), y0 + int(content_h * b1))

        rgba = np.zeros((*m.shape, 4), dtype=np.uint8)
        rgba[..., :3] = tile
        rgba[..., 3] = np.where(m, 255, 0)
        piece = Image.fromarray(rgba).crop((x0, y0, x1, y1))
        if abs(scale - 1.0) > 1e-6:
            # NEAREST：之後還要降採樣 21:1，這裡引入插值只會製造調色盤外的中間色
            piece = piece.resize((max(1, int(round(piece.width * scale))),
                                  max(1, int(round(piece.height * scale)))),
                                 Image.Resampling.NEAREST)

        dst = Image.new("RGB", CANVAS, BG)
        px = int(round(canvas_cx - (cx - x0) * scale))
        py = ground_y - piece.height
        dst.paste(piece, (px, py), piece)

        out = args.out_dir / f"{args.name}_f{i}.png"
        dst.save(out)
        report.append({"frame": i, "content": [x1 - x0, y1 - y0],
                       "scaled": [piece.width, piece.height],
                       "paste": [px, py], "torso_cx": round(cx - x0, 1)})
        print(f"  f{i}  {x1-x0}x{y1-y0} → {piece.width}x{piece.height}  貼於 ({px},{py})  → {out.name}")

    (args.out_dir / f"{args.name}_align.json").write_text(
        json.dumps({"source": str(args.input), "canvas": list(CANVAS),
                    "grid": GRID, "ground_row": GROUND_ROW, "scale": round(scale, 4),
                    "frames": report}, indent=2, ensure_ascii=False))
    print(f"\n{len(report)} 格已對齊到 {CANVAS[0]}×{CANVAS[1]} 的同一組格線")
    print(f"接著：pixelate.py {args.out_dir}/*.png --width 64 --height 56 --no-crop ...")


if __name__ == "__main__":
    main()
