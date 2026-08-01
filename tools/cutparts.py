#!/usr/bin/env python3
"""
cutparts.py — 把部件分解圖切成獨立的部件檔，並產生 rig.json

輸入是一張「所有部件攤開排列在洋紅底上」的分解圖，輸出是每個部件的獨立 PNG
加上一份 rig.json（含階層、pivot、z-order）。

分割方式
--------
不用連通區域標記，用**投影分割**：先把 alpha 遮罩投影到 y 軸找出橫向帶（每一列部件），
再在每一帶內投影到 x 軸找出各個部件。

理由：部件分解圖本來就是規則排列的，投影分割又快又不需要額外依賴，
而且失敗時的表現是可預測的（切太多或切太少），比連通區域的「兩個部件黏在一起
變成一個」好除錯。若日後遇到不規則排版，再換連通區域。

pivot
-----
pivot 是部件旋轉的軸心，動畫的所有 rot 都繞它轉。用部件類型的慣例自動推算：

    腿 / 耳 / 眼瞼   上緣中點      關節在上方
    尾              左緣中點      尾根在身體側
    頭              下緣中點      頸部連接點
    下顎            上緣中點      顎關節
    其餘            幾何中心

自動推算只是起點，實際要在 preview.py 看動起來才知道對不對，
不對就直接改 rig.json 的 pivot，不必重切。

用法
----
    python tools/cutparts.py art/generated/B3_brown_mixed_parts_stand.png \\
        --character brown_mixed --out-dir art/rigs/brown_mixed
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


# 分解圖上的閱讀順序（左到右、上到下）。順序必須和生成提示詞裡的清單一致。
DOG_PARTS = [
    "torso", "tail", "leg_fore_near", "leg_fore_far",
    "leg_hind_near", "leg_hind_far", "head", "ear_near", "ear_far",
    "muzzle", "jaw", "tongue", "eye_near", "eye_far",
    "eyelid", "shadow",
]

# pivot 慣例。自動推算只是起點，看過 preview 之後直接改 rig.json 即可。
PIVOT_RULE = {
    # 關節在部件上緣
    "top":          ["leg_fore_near", "leg_fore_far", "leg_hind_near", "leg_hind_far",
                     "ear_near", "ear_far", "eyelid", "jaw"],
    # 尾根在右下。這隻狗的尾巴朝左上彎，原本寫「左緣中點」會讓 pivot 落在形狀外面。
    "bottom_right": ["tail"],
    # 頸部連接點在下緣
    "bottom":       ["head"],
}

# 階層與繪製順序。z 小的先畫（在後面）。
DOG_RIG = {
    "shadow":        {"parent": None,    "z":  0},
    "tail":          {"parent": "torso", "z": 10},
    "leg_hind_far":  {"parent": "torso", "z": 20},
    "leg_fore_far":  {"parent": "torso", "z": 21},
    "torso":         {"parent": None,    "z": 30},
    "leg_hind_near": {"parent": "torso", "z": 40},
    "leg_fore_near": {"parent": "torso", "z": 41},
    "head":          {"parent": "torso", "z": 50},
    "ear_far":       {"parent": "head",  "z": 45},
    "muzzle":        {"parent": "head",  "z": 60},
    "jaw":           {"parent": "head",  "z": 58},
    "tongue":        {"parent": "jaw",   "z": 57},
    "eye_far":       {"parent": "head",  "z": 61},
    "eye_near":      {"parent": "head",  "z": 62},
    "eyelid":        {"parent": "head",  "z": 63},
    "ear_near":      {"parent": "head",  "z": 70},
}


def label_components(mask: np.ndarray) -> tuple:
    """兩趟掃描的連通區域標記（8 連通，union-find）。

    原本用投影分割（先切橫向帶再切直行），對規則排版夠用但不夠穩健：
    實測在 brown_mixed 的分解圖上只找到 15 個區塊而非 16，
    因為兩個部件的投影範圍重疊——調整間隙門檻無法解決，那不是間隙問題。

    連通區域標記不管排版，只看像素是否相連，任何佈局都正確。
    回傳 (labels, count)，labels 中 0 表示背景。
    """
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent = [0]                       # parent[0] 不用

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:       # 路徑壓縮
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # 第一趟：指派暫時標籤，記錄等價關係
    for y in range(h):
        row, prev_row = mask[y], (mask[y - 1] if y else None)
        for x in range(w):
            if not row[x]:
                continue
            # 已掃過的 8 連通鄰居：左、左上、上、右上
            neigh = []
            if x and labels[y, x - 1]:
                neigh.append(labels[y, x - 1])
            if y:
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < w and prev_row[nx]:
                        neigh.append(labels[y - 1, nx])
            if neigh:
                m = min(neigh)
                labels[y, x] = m
                for n in neigh:
                    union(m, n)
            else:
                parent.append(len(parent))
                labels[y, x] = len(parent) - 1

    # 第二趟：解析等價關係並壓成連續編號
    remap, nxt = {}, 1
    for y in range(h):
        for x in range(w):
            if not labels[y, x]:
                continue
            r = find(labels[y, x])
            if r not in remap:
                remap[r] = nxt
                nxt += 1
            labels[y, x] = remap[r]

    return labels, nxt - 1


def sort_reading_order(boxes: list) -> list:
    """把外接框排成閱讀順序：由上到下分列，列內由左到右。

    不能用 `y0 // 固定門檻` 分列。同一列的部件高矮不一，
    矮的部件（例如尾巴）上緣位置較低，會被誤分到下一列。
    改用「垂直中心是否落在該列已知的 y 範圍內」來歸屬，列的範圍隨新成員擴張。
    """
    if not boxes:
        return boxes

    rows = []
    for b in sorted(boxes, key=lambda b: b[0]):     # 依上緣掃描
        y0, y1 = b[0], b[1]
        centre = (y0 + y1) / 2
        placed = False
        for row in rows:
            if row["top"] <= centre <= row["bot"]:
                row["items"].append(b)
                row["top"] = min(row["top"], y0)
                row["bot"] = max(row["bot"], y1)
                placed = True
                break
        if not placed:
            rows.append({"top": y0, "bot": y1, "items": [b]})

    out = []
    for row in rows:
        out.extend(sorted(row["items"], key=lambda b: b[2]))
    return out


def compute_pivot(name: str, w: int, h: int) -> list:
    if name in PIVOT_RULE["top"]:
        return [w // 2, 1]
    if name in PIVOT_RULE["bottom_right"]:
        return [w - 2, h - 2]
    if name in PIVOT_RULE["bottom"]:
        return [w // 2, h - 1]
    return [w // 2, h // 2]


def main() -> None:
    ap = argparse.ArgumentParser(description="切分部件分解圖並產生 rig.json")
    ap.add_argument("input", type=Path)
    ap.add_argument("--character", required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--bg", default="FF00FF", help="去背色（預設 FF00FF）")
    ap.add_argument("--tol", type=float, default=90.0)
    ap.add_argument("--min-area", type=int, default=200,
                    help="像素數少於此的區塊視為雜訊丟棄（預設 200）")
    ap.add_argument("--names", nargs="*", default=DOG_PARTS,
                    help="部件名稱，依分解圖的閱讀順序")
    args = ap.parse_args()

    bg = tuple(int(args.bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))

    rgb = np.array(Image.open(args.input).convert("RGB"))
    dist = np.sqrt(((rgb.astype(np.int16) - np.array(bg, dtype=np.int16))
                    .astype(np.float32) ** 2).sum(axis=-1))
    mask = dist > args.tol

    # 去溢色。單靠色度距離門檻擋不掉邊緣的混色像素：
    # 洋紅與深色描邊的 50% 混色距離純洋紅約 165，門檻拉到那麼高會開始吃掉真的像素。
    # 改成針對性判斷——把「偏向背景色相」的像素直接判為透明。
    # 對這批角色是安全的：棕、白、金、冰藍沒有任何一個是洋紅傾向。
    r, g_, b = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    spill = (r > bg[0] * 0.42) & (g_ < 80) & (b > bg[2] * 0.42)
    removed = int((mask & spill).sum())
    mask &= ~spill
    if removed:
        print(f"去溢色：移除 {removed} 個殘留背景色的邊緣像素")

    labels, n = label_components(mask)

    # 收集每個連通區域的外接框，丟掉太小的雜訊
    boxes = []
    for lab in range(1, n + 1):
        ys, xs = np.where(labels == lab)
        if ys.size < args.min_area:
            continue
        boxes.append((int(ys.min()), int(ys.max()) + 1,
                      int(xs.min()), int(xs.max()) + 1, lab))

    boxes = sort_reading_order(boxes)

    print(f"偵測到 {len(boxes)} 個區塊，預期 {len(args.names)} 個")
    if len(boxes) != len(args.names):
        print(f"  ⚠️ 數量不符。若偏多，調高 --min-area 濾掉雜訊；"
              f"若偏少，可能有兩個部件在分解圖上真的相連，需重生該張圖。")
        print("  以下先照偵測結果輸出，名稱可能錯位，請檢查後手動改 rig.json。")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    src_arr = np.array(Image.open(args.input).convert("RGBA"))

    parts = {}
    for i, (y0, y1, x0, x1, lab) in enumerate(boxes):
        name = args.names[i] if i < len(args.names) else f"unknown_{i:02d}"

        # 只取屬於這個連通區域的像素。用外接框裁切會把鄰近部件
        # 探進框內的像素一起帶走，部件圖上會出現別人的碎片。
        sub_rgba = src_arr[y0:y1, x0:x1].copy()
        sub_rgba[..., 3] = np.where(labels[y0:y1, x0:x1] == lab, 255, 0)
        piece = Image.fromarray(sub_rgba)

        fn = f"{args.character}_stand_{name}_r.png"
        piece.save(args.out_dir / fn)

        w, h = piece.size
        meta = DOG_RIG.get(name, {"parent": None, "z": 99})
        parts[name] = {
            "file": fn,
            "size": [w, h],
            "pivot": compute_pivot(name, w, h),
            "parent": meta["parent"],
            "z": meta["z"],
            "offset": [0, 0],
            "_sheet_bbox": [int(x0), int(y0), int(x1), int(y1)],
        }
        print(f"  {name:<15} {w:4d}x{h:<4d}  pivot={parts[name]['pivot']}  → {fn}")

    rig = {
        "character_id": args.character,
        "format_version": 1,
        "source_sheet": str(args.input),
        "note": ("pivot 由部件類型的慣例自動推算，是起點不是定案。"
                 "跑 preview.py 看動起來哪裡不對，直接改這個檔的 pivot，不必重切。"
                 "offset 是部件相對父階層 pivot 的位置，切分後全為 0，"
                 "要靠 assemble 步驟或手動對位填入。"),
        "parts": parts,
    }
    (args.out_dir / "rig.json").write_text(
        json.dumps(rig, indent=2, ensure_ascii=False))
    print(f"\n→ {args.out_dir / 'rig.json'}")


if __name__ == "__main__":
    main()
