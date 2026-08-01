#!/usr/bin/env python3
"""
pixedit.py — 逐像素手工修補

為什麼需要這個工具
------------------
影像生成模型控制不了 5 像素以下的特徵。實測 gpt-image-2：

    要求「眼睛放大 20%，虹膜與高光都要讀得出來」
    實得   3×2 像素的純暗色塊，沒有虹膜、沒有高光

在 64px 的 sprite 上，眼睛只有 2–3 像素。這個尺度已經低於模型的有效控制解析度，
再怎麼改提示詞都是擲骰子。**小特徵要手工畫，不要重生。**

這不是妥協，是正確的分工：
    AI      決定造型、體型、剪影、大面積配色
    remap   決定材質的精確顏色
    pixedit 決定 5 像素以下的關鍵細節（眼睛、鼻子高光、牙齒）

眼睛尤其重要——一隻角色有沒有生命感，往往就取決於那一個高光像素。

補丁格式
--------
    {
      "key": { "O": "eye_dark", "I": "eye_iris", "H": "eye_highlight", ".": null },
      "patches": [
        { "name": "eye_near", "x": 54, "y": 8, "rows": ["OOOO",
                                                        "OIIH",
                                                        "OOOO"] }
      ]
    }

`.` 代表不動該像素。角色名對應 specs/palettes/<char>.json 的 role。

用法
----
    python tools/pixedit.py sprite.png --patch eyes.json \
        --palette specs/palettes/brown_mixed.json --out fixed.png --scale 8
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


def parse_hex(s: str) -> tuple:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def load_roles(path: Path) -> dict:
    data = json.loads(path.read_text())
    entries = data["colors"] if isinstance(data, dict) else data
    return {c["role"]: parse_hex(c["hex"])
            for c in entries
            if isinstance(c, dict) and not c.get("transparent")}


def apply_patches(img: np.ndarray, spec: dict, roles: dict) -> list:
    """就地套用補丁。回傳每個補丁改了幾個像素，用來抓座標寫錯的情況。"""
    key = spec["key"]
    h, w = img.shape[:2]
    report = []

    for p in spec["patches"]:
        px0, py0 = p["x"], p["y"]
        changed = 0
        oob = 0

        for dy, row in enumerate(p["rows"]):
            for dx, ch in enumerate(row):
                if ch not in key:
                    raise SystemExit(
                        f"補丁 '{p['name']}' 用了未定義的符號 '{ch}'，"
                        f"可用的是 {sorted(key)}")
                role = key[ch]
                if role is None:
                    continue          # '.' = 保留原像素

                x, y = px0 + dx, py0 + dy
                if not (0 <= x < w and 0 <= y < h):
                    oob += 1
                    continue
                if role not in roles:
                    raise SystemExit(
                        f"補丁 '{p['name']}' 指到不存在的角色 '{role}'")

                img[y, x, :3] = roles[role]
                img[y, x, 3] = 255
                changed += 1

        report.append({"name": p["name"], "changed": changed, "out_of_bounds": oob})
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="逐像素手工修補 sprite")
    ap.add_argument("input", type=Path)
    ap.add_argument("--patch", type=Path, required=True, help="補丁 JSON")
    ap.add_argument("--palette", type=Path, required=True,
                    help="specs/palettes/<char>.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scale", type=int, default=8, help="另存放大預覽（0 = 不存）")
    args = ap.parse_args()

    img = np.array(Image.open(args.input).convert("RGBA"))
    roles = load_roles(args.palette)
    spec = json.loads(args.patch.read_text())

    report = apply_patches(img, spec, roles)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(args.out)

    for r in report:
        flag = ""
        if r["changed"] == 0:
            flag = "  ⚠️ 沒有改到任何像素，檢查座標"
        elif r["out_of_bounds"]:
            flag = f"  ⚠️ {r['out_of_bounds']} 個像素超出畫布"
        print(f"  {r['name']:<16} 改了 {r['changed']:3d} 個像素{flag}")

    print(f"  → {args.out}")

    if args.scale:
        h, w = img.shape[:2]
        prev = args.out.with_name(args.out.stem + f"_x{args.scale}.png")
        Image.fromarray(img).resize(
            (w * args.scale, h * args.scale), Image.Resampling.NEAREST).save(prev)
        print(f"  → {prev}")


if __name__ == "__main__":
    main()
