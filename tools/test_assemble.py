#!/usr/bin/env python3
"""
test_assemble.py — assemble.py 的自我測試

不依賴 pytest（tools/requirements.txt 只有 numpy 與 Pillow）。
用合成的純色方塊當假部件，驗證的是幾何與契約，不是美術：

    1  輸出尺寸、模式、洋紅底
    2  無旋轉時 pivot 精準落在指定座標
    3  旋轉 0/90/180/270 度時 pivot 逐像素精準；任意角度誤差 ≤ 1 來源像素
    4  旋轉方向是逆時針，而且真的繞 pivot 轉（不是繞圖片中心）
    5  同一圖層群內依 z 由小到大疊，大的蓋住小的
    6  圖層群互不汙染
    7  姿勢規格缺部件時有回報，並用 rig.json 的 offset 當預設
    8  x/y 不是 21 的倍數時警告但不中止
    9  畫布不是 64×56 的整數倍時報錯
    10 兩次執行逐位元相同
    11 真 rig 端到端：16 個部件 → 7 個圖層群 × 3 個姿勢 = 21 張圖

用法
----
    .venv/bin/python tools/test_assemble.py
    .venv/bin/python tools/test_assemble.py --keep     # 保留產物以便肉眼檢查
"""

import argparse
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CANVAS = list(assemble.DEFAULT_CANVAS)          # 1344 x 1176
GRID = 21
MAGENTA = (255, 0, 255)

# 假部件的顏色。彼此不相同，也都不等於洋紅，才能用顏色反查位置。
BASE_BODY, BASE_MARK = (200, 20, 20), (0, 255, 0)
ARM_BODY,  ARM_MARK = (20, 20, 200), (0, 255, 255)
FLAG_BODY, FLAG_MARK = (230, 200, 30), (60, 60, 60)
DOT_BODY,  DOT_MARK = (240, 240, 240), (10, 90, 40)


# --------------------------------------------------------------------------
# 假資料
# --------------------------------------------------------------------------

def make_part(path: Path, size, body, mark, pivot) -> None:
    """畫一個純色方塊，並在 pivot 上蓋一個 3×3 的標記色。

    標記用 3×3 而不是單像素：NEAREST 旋轉時單一像素可能被重複取樣或整個漏掉，
    3×3 區塊的重心才穩定。
    """
    w, h = size
    img = Image.new("RGBA", (w, h), tuple(body) + (255,))
    px = img.load()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            x, y = pivot[0] + dx, pivot[1] + dy
            if 0 <= x < w and 0 <= y < h:
                px[x, y] = tuple(mark) + (255,)
    img.save(path)


FAKE_PARTS = {
    # 長條，pivot 靠左端 → 旋轉方向一眼看得出來
    "base": {"size": [105, 63], "pivot": [10, 31], "parent": None,
             "z": 10, "offset": [0, 0], "body": BASE_BODY, "mark": BASE_MARK},
    "arm":  {"size": [105, 21], "pivot": [10, 10], "parent": "base",
             "z": 20, "offset": [21, 0], "body": ARM_BODY, "mark": ARM_MARK},
    "flag": {"size": [63, 63], "pivot": [31, 31], "parent": "base",
             "z": 30, "offset": [0, -42], "body": FLAG_BODY, "mark": FLAG_MARK},
    "dot":  {"size": [21, 21], "pivot": [10, 10], "parent": None,
             "z": 5, "offset": [0, 0], "body": DOT_BODY, "mark": DOT_MARK},
}

FAKE_GROUPS = {"core": ["base", "arm"], "flag": ["flag"], "dot": ["dot"]}


def write_fake_rig(rig_dir: Path) -> Path:
    rig_dir.mkdir(parents=True, exist_ok=True)
    parts = {}
    for name, spec in FAKE_PARTS.items():
        fn = f"fake_{name}.png"
        make_part(rig_dir / fn, spec["size"], spec["body"],
                  spec["mark"], spec["pivot"])
        parts[name] = {"file": fn, "size": spec["size"], "pivot": spec["pivot"],
                       "parent": spec["parent"], "z": spec["z"],
                       "offset": spec["offset"]}
    rig = {"character_id": "fake", "format_version": 1, "parts": parts}
    p = rig_dir / "rig.json"
    p.write_text(json.dumps(rig, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def write_poses(path: Path, poses: dict, canvas=None, groups=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"character_id": "fake",
           "canvas": canvas or CANVAS,
           "layer_groups": groups or FAKE_GROUPS,
           "poses": poses}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def flat_pose(base=(420, 315), base_rot=0):
    """一個所有部件都對齊 21 格線的基準姿勢。"""
    return {
        "base": {"x": base[0], "y": base[1], "rot": base_rot},
        "arm":  {"x": 441, "y": 315, "rot": 0},
        "flag": {"x": 630, "y": 210, "rot": 0},
        "dot":  {"x": 420, "y": 630, "rot": 0},
    }


# --------------------------------------------------------------------------
# 影像查詢工具
# --------------------------------------------------------------------------

def load_rgb(path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"))


def mask_of(arr: np.ndarray, colour) -> np.ndarray:
    return np.all(arr == np.array(colour, dtype=np.uint8), axis=-1)


def bbox_of(arr, colour):
    ys, xs = np.where(mask_of(arr, colour))
    assert xs.size, f"影像中找不到顏色 {colour}"
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def centroid_of(arr, colour):
    ys, xs = np.where(mask_of(arr, colour))
    assert xs.size, f"影像中找不到顏色 {colour}"
    return float(xs.mean()), float(ys.mean())


def count_of(arr, colour) -> int:
    return int(mask_of(arr, colour).sum())


def run(work: Path, poses: dict, tag: str, canvas=None, groups=None, **kw):
    """建假 rig + 假姿勢並跑一次 assemble。回傳 (報告, 輸出目錄)。"""
    root = work / tag
    rig = write_fake_rig(root / "rig")
    pose_path = write_poses(root / "poses.json", poses, canvas, groups)
    out = root / "out"
    res = assemble.assemble_all(rig, pose_path, out, quiet=True, **kw)
    res.pop("_report", None)
    return res, out


# --------------------------------------------------------------------------
# 測試案例
# --------------------------------------------------------------------------

CASES = []


def case(title):
    def deco(fn):
        CASES.append((title, fn))
        return fn
    return deco


@case("輸出尺寸 1344×1176、RGB、洋紅底、每群一張")
def t_output_shape(work):
    res, out = run(work, {"stand": flat_pose()}, "shape")
    assert res["errors"] == [], res["errors"]

    files = sorted(p.name for p in out.glob("*.png"))
    assert files == ["fake_stand_core.png", "fake_stand_dot.png",
                     "fake_stand_flag.png"], files

    for p in out.glob("*.png"):
        im = Image.open(p)
        assert im.mode == "RGB", f"{p.name} 的模式是 {im.mode}，應為 RGB"
        assert im.size == tuple(CANVAS), f"{p.name} 尺寸 {im.size}，應為 {CANVAS}"
        assert im.getpixel((0, 0)) == MAGENTA, f"{p.name} 的背景不是純洋紅"
        assert im.getpixel((CANVAS[0] - 1, CANVAS[1] - 1)) == MAGENTA

    assert res["grid"] == GRID, res["grid"]


@case("無旋轉時 pivot 精準落在指定座標")
def t_pivot_exact(work):
    tx, ty = 420, 315
    res, out = run(work, {"stand": flat_pose(base=(tx, ty))}, "pivot")
    assert res["errors"] == [], res["errors"]

    arr = load_rgb(out / "fake_stand_core.png")

    # 3×3 標記的重心就是 pivot
    cx, cy = centroid_of(arr, BASE_MARK)
    assert (round(cx), round(cy)) == (tx, ty), (cx, cy)
    assert count_of(arr, BASE_MARK) == 9, count_of(arr, BASE_MARK)

    # 整個部件的外接框也要對：pivot [10,31]、尺寸 105×63
    x0, y0, x1, y1 = bbox_of(arr, BASE_BODY)
    assert (x0, y0, x1, y1) == (tx - 10, ty - 31, tx - 10 + 104, ty - 31 + 62), \
        (x0, y0, x1, y1)


@case("旋轉 0/90/180/270 度時 pivot 逐像素精準")
def t_pivot_right_angles(work):
    tx, ty = 420, 315
    for deg in (0, 90, 180, 270, 360, -90):
        pose = flat_pose(base=(tx, ty), base_rot=deg)
        res, out = run(work, {"stand": pose}, f"rot{deg}")
        assert res["errors"] == [], res["errors"]
        arr = load_rgb(out / "fake_stand_core.png")
        cx, cy = centroid_of(arr, BASE_MARK)
        assert abs(cx - tx) < 1e-6 and abs(cy - ty) < 1e-6, \
            f"rot={deg} 時 pivot 落在 ({cx}, {cy})，應為 ({tx}, {ty})"


@case("任意角度時 pivot 誤差 ≤ 1 來源像素（= 1/21 目標像素）")
def t_pivot_free_angles(work):
    tx, ty = 630, 630
    for deg in (12, 25, -37.5, 137.5, 214.75):
        pose = flat_pose(base=(tx, ty), base_rot=deg)
        # arm 移開，免得蓋住 base 的標記
        pose["arm"] = {"x": 1050, "y": 1050, "rot": 0}
        res, out = run(work, {"stand": pose}, f"free{str(deg).replace('.','_').replace('-','m')}")
        assert res["errors"] == [], res["errors"]
        arr = load_rgb(out / "fake_stand_core.png")
        cx, cy = centroid_of(arr, BASE_MARK)
        d = max(abs(cx - tx), abs(cy - ty))
        assert d <= 1.0, f"rot={deg} 時 pivot 偏移 {d:.3f} px，落在 ({cx}, {cy})"


@case("旋轉是繞 pivot、方向為逆時針")
def t_rotation_geometry(work):
    tx, ty = 630, 630
    pose = flat_pose(base=(tx, ty), base_rot=90)
    pose["arm"] = {"x": 1050, "y": 1050, "rot": 0}
    res, out = run(work, {"stand": pose}, "ccw")
    assert res["errors"] == [], res["errors"]
    arr = load_rgb(out / "fake_stand_core.png")

    x0, y0, x1, y1 = bbox_of(arr, BASE_BODY)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    # 105×63 轉 90 度 → 63×105
    assert abs(w - 63) <= 2 and abs(h - 105) <= 2, (w, h)

    # 繞 pivot 轉（不是繞圖片中心）：外接框仍以 pivot 為軸心
    # 原本向 +x 伸出 94 px 的長邊，逆時針 90 度後應該指向 -y（畫面上方）
    assert y0 < ty - 80, f"長邊沒有轉到上方：bbox y0={y0}, pivot y={ty}"
    assert y1 < ty + 20, f"長邊有殘留在下方：bbox y1={y1}, pivot y={ty}"
    assert abs(x0 - (tx - 31)) <= 2 and abs(x1 - (tx + 31)) <= 2, (x0, x1)

    # 若旋轉是繞圖片中心，外接框會整個偏掉；重心必須明顯在 pivot 上方
    cx, cy = centroid_of(arr, BASE_BODY)
    assert abs(cx - tx) <= 2, cx
    assert cy < ty - 30, cy


@case("同群內依 z 由小到大疊，z 大的蓋住 z 小的")
def t_z_order(work):
    res, out = run(work, {"stand": flat_pose()}, "z")
    assert res["errors"] == [], res["errors"]
    arr = load_rgb(out / "fake_stand_core.png")

    # base(z=10) 佔 410..514 × 284..346；arm(z=20) 佔 431..535 × 305..325
    assert tuple(arr[315, 470]) == ARM_BODY, \
        f"重疊處是 {tuple(arr[315, 470])}，z 大的 arm 應該蓋住 base"
    assert tuple(arr[290, 415]) == BASE_BODY, \
        f"只有 base 的地方是 {tuple(arr[290, 415])}"

    # 反過來：把 arm 的 z 壓到 base 之下，同一點就該變成 base
    root = work / "z_flip"
    rig_path = write_fake_rig(root / "rig")
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    rig["parts"]["arm"]["z"] = 1
    rig_path.write_text(json.dumps(rig, ensure_ascii=False), encoding="utf-8")
    pose_path = write_poses(root / "poses.json", {"stand": flat_pose()})
    res2 = assemble.assemble_all(rig_path, pose_path, root / "out", quiet=True)
    res2.pop("_report", None)
    arr2 = load_rgb(root / "out" / "fake_stand_core.png")
    assert tuple(arr2[315, 470]) == BASE_BODY, \
        f"z 交換後重疊處是 {tuple(arr2[315, 470])}，應該變成 base"

    # 群內順序是 z 由小到大
    zs = [p["z"] for p in res["poses"]["stand"]["groups"]["core"]["parts"]]
    assert zs == sorted(zs), zs


@case("圖層群互不汙染：每張圖只有自己群的部件")
def t_group_isolation(work):
    res, out = run(work, {"stand": flat_pose()}, "iso")
    assert res["errors"] == [], res["errors"]

    core = load_rgb(out / "fake_stand_core.png")
    flag = load_rgb(out / "fake_stand_flag.png")
    dot = load_rgb(out / "fake_stand_dot.png")

    assert count_of(core, BASE_BODY) > 0 and count_of(core, ARM_BODY) > 0
    assert count_of(core, FLAG_BODY) == 0 and count_of(core, DOT_BODY) == 0
    assert count_of(flag, FLAG_BODY) > 0
    assert count_of(flag, BASE_BODY) == 0 and count_of(flag, ARM_BODY) == 0
    assert count_of(dot, DOT_BODY) > 0
    assert count_of(dot, BASE_BODY) == 0 and count_of(dot, FLAG_BODY) == 0

    # 每張圖的其餘部分都必須是純洋紅，不能有其他雜色
    for arr, cols in ((core, (BASE_BODY, BASE_MARK, ARM_BODY, ARM_MARK)),
                      (flag, (FLAG_BODY, FLAG_MARK)),
                      (dot, (DOT_BODY, DOT_MARK))):
        used = count_of(arr, MAGENTA) + sum(count_of(arr, c) for c in cols)
        assert used == arr.shape[0] * arr.shape[1], \
            f"圖層有預期外的顏色（{arr.shape[0] * arr.shape[1] - used} 個像素）"


@case("缺部件時有回報，並用 rig.json 的 offset 當預設")
def t_missing_part(work):
    pose = flat_pose()
    del pose["flag"]                     # flag: parent=base, offset=[0,-42]
    res, out = run(work, {"stand": pose}, "missing")
    assert res["errors"] == [], res["errors"]

    st = res["poses"]["stand"]
    assert st["defaulted"] == ["flag"], st["defaulted"]
    assert st["placements"]["flag"]["source"] == "rig_offset"
    # base 落在 (420,315)，flag 的 offset 是 [0,-42] → (420, 273)
    assert (st["placements"]["flag"]["x"], st["placements"]["flag"]["y"]) == (420, 273), \
        st["placements"]["flag"]

    assert any("flag" in w and "offset" in w for w in res["warnings"]), res["warnings"]

    # 有回報之外，圖也真的畫在預設位置上（不是憑空消失）
    arr = load_rgb(out / "fake_stand_flag.png")
    cx, cy = centroid_of(arr, FLAG_MARK)
    assert (round(cx), round(cy)) == (420, 273), (cx, cy)


@case("x/y 不是 21 的倍數時警告，但不中止")
def t_grid_warning(work):
    pose = flat_pose(base=(430, 316))    # 430%21=10, 316%21=1
    pose["arm"] = {"x": 1050, "y": 1050, "rot": 0}   # 移開，免得蓋住 base 的標記
    res, out = run(work, {"stand": pose}, "grid")

    assert res["errors"] == [], "格線沒對齊只該警告，不該當成錯誤"
    mis = res["poses"]["stand"]["misaligned"]
    assert [m["part"] for m in mis] == ["base"], mis
    assert any("21" in w and "base" in w for w in res["warnings"]), res["warnings"]

    # 仍然照樣輸出，讓人先看到問題
    assert (out / "fake_stand_core.png").exists()
    arr = load_rgb(out / "fake_stand_core.png")
    assert centroid_of(arr, BASE_MARK) == (430.0, 316.0)


@case("畫布不是 64×56 的整數倍時報錯")
def t_canvas_ratio(work):
    # 1341×1173 是 gpt-image-2 的原始輸出，1341/64 = 20.95，不能當畫布
    res, _ = run(work, {"stand": flat_pose()}, "badcanvas", canvas=[1341, 1173])
    assert res["errors"], "1341×1173 應該被擋下"
    assert any("1344" in e for e in res["errors"]), res["errors"]
    assert res["grid"] is None


@case("兩次執行逐位元相同")
def t_deterministic(work):
    pose = {"stand": flat_pose(base_rot=17.5)}
    res_a, out_a = run(work, pose, "det_a")
    res_b, out_b = run(work, pose, "det_b")
    assert res_a["errors"] == [] and res_b["errors"] == []

    names = sorted(p.name for p in out_a.glob("*.png"))
    assert names, "沒有產出任何檔案"
    for n in names:
        a = (out_a / n).read_bytes()
        b = (out_b / n).read_bytes()
        assert a == b, f"{n} 兩次產出不同（{len(a)} vs {len(b)} bytes）"


@case("真 rig 端到端：16 部件 → 7 群 × 3 姿勢 = 21 張")
def t_real_rig(work):
    rig_path = ROOT / "art/rigs/brown_mixed/rig.json"
    if not rig_path.exists():
        print("      （跳過：找不到 art/rigs/brown_mixed/rig.json）")
        return

    stand = {
        "shadow": {"x": 630, "y": 1008, "rot": 0},
        "torso": {"x": 630, "y": 630, "rot": 0},
        "tail": {"x": 357, "y": 588, "rot": 0},
        "leg_hind_far": {"x": 399, "y": 777, "rot": 0},
        "leg_hind_near": {"x": 441, "y": 777, "rot": 0},
        "leg_fore_far": {"x": 735, "y": 777, "rot": 0},
        "leg_fore_near": {"x": 777, "y": 777, "rot": 0},
        "head": {"x": 861, "y": 483, "rot": 0},
        "ear_far": {"x": 798, "y": 336, "rot": 0},
        "ear_near": {"x": 861, "y": 336, "rot": 0},
        "muzzle": {"x": 966, "y": 462, "rot": 0},
        "jaw": {"x": 924, "y": 504, "rot": 0},
        "tongue": {"x": 945, "y": 546, "rot": 0},
        "eye_far": {"x": 924, "y": 420, "rot": 0},
        "eye_near": {"x": 945, "y": 420, "rot": 0},
        "eyelid": {"x": 945, "y": 399, "rot": 0},
    }

    def variant(dy, rots):
        p = {}
        for k, v in stand.items():
            p[k] = {"x": v["x"], "y": v["y"] + (dy if k != "shadow" else 0),
                    "rot": rots.get(k, 0)}
        return p

    # sit / lie 只是煙霧測試，幾何合理性是姿勢作者的事，這裡只驗管線
    sit = variant(42, {"leg_hind_far": -55, "leg_hind_near": -55, "tail": -20})
    lie = variant(126, {"leg_hind_far": -80, "leg_hind_near": -80,
                        "leg_fore_far": -75, "leg_fore_near": -75, "ear_near": 15})

    root = work / "real"
    pose_path = write_poses(root / "poses.json",
                            {"stand": stand, "sit": sit, "lie": lie},
                            groups=assemble.DEFAULT_LAYER_GROUPS)
    doc = json.loads(pose_path.read_text(encoding="utf-8"))
    doc["character_id"] = "brown_mixed"
    pose_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    out = root / "out"
    res = assemble.assemble_all(rig_path, pose_path, out, quiet=True, preview=True)
    res.pop("_report", None)

    assert res["errors"] == [], res["errors"]
    assert res["grid"] == GRID
    assert res["canvas"] == CANVAS

    files = sorted(p.name for p in out.glob("*.png"))
    assert len(files) == 21, f"應有 21 張，實得 {len(files)}：{files}"
    for pose in ("stand", "sit", "lie"):
        for g in assemble.GROUP_OUTPUT_ORDER:
            n = f"brown_mixed_{pose}_{g}.png"
            assert n in files, f"缺 {n}"
            im = Image.open(out / n)
            assert im.size == tuple(CANVAS), (n, im.size)
            assert im.mode == "RGB", (n, im.mode)

    # 七個群必須恰好覆蓋 16 個部件一次
    covered = [p for g in res["layer_groups"].values() for p in g]
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    assert sorted(covered) == sorted(rig["parts"]), \
        set(covered) ^ set(rig["parts"])
    assert len(covered) == 16, len(covered)

    # 沒有空圖層——pixelate.py 遇到全背景的圖會直接 SystemExit
    for pose, pr in res["poses"].items():
        for g, st in pr["groups"].items():
            assert st["opaque_pixels"] > 0, f"{pose}/{g} 是空圖層"
        assert pr["defaulted"] == [], pr["defaulted"]
        assert pr["misaligned"] == [], pr["misaligned"]
        assert Path(pr["preview"]).exists()

    # 這份 rig 的 z 區間確實交錯，工具必須把它報出來
    assert any("交錯" in w for w in res["warnings"]), res["warnings"]
    assert len(res["group_draw_order"]) == 7, res["group_draw_order"]


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="assemble.py 的自我測試")
    ap.add_argument("--keep", action="store_true", help="保留產物以便肉眼檢查")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="assemble_test_"))
    passed, failed = 0, []

    print(f"工作目錄：{work}\n")
    for i, (title, fn) in enumerate(CASES, 1):
        try:
            fn(work)
        except Exception:
            failed.append(title)
            print(f"  ✗  {i:2d}. {title}")
            for line in traceback.format_exc().rstrip().splitlines():
                print(f"        {line}")
        else:
            passed += 1
            print(f"  ✓  {i:2d}. {title}")

    print(f"\n{passed}/{len(CASES)} 通過")
    if failed:
        print("失敗：" + "、".join(failed))

    if args.keep:
        print(f"產物保留在 {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
