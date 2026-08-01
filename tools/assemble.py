#!/usr/bin/env python3
"""
assemble.py — 階段 A：把高解析部件組成姿勢，輸出七個圖層群的來源圖

這支程式做什麼
--------------
讀 rig.json（部件的 pivot / parent / z）與姿勢規格（每個部件的 pivot 要落在畫布
哪個座標、繞 pivot 轉幾度），把部件組成完整姿勢，再依「像素領域圖層群」拆成
七張 1344×1176 的洋紅底圖，交給 pixelate.py 一次降採樣到 64×56。

    art/rigs/<id>/*.png  ──┐
    art/rigs/<id>/rig.json ├──→ assemble.py ──→ build/poses/<id>_<pose>_<group>.png
    specs/poses/*.json   ──┘                          ↓ pixelate.py --no-crop
                                            build/pixparts/<id>_<pose>_<group>.png
                                                      ↓ bake.py（只做整數平移）
                                                   spritesheet

為什麼要有階段 A / 階段 B 的分界
--------------------------------
降採樣比是 **21 : 1**。一條腿在輸出端只有 7 像素寬，眼瞼只有 3.5 像素高。
「把後腿旋轉 25 度」在輸出端的全部意義，是把 3 個像素往旁邊挪 2 格——而挪去
哪一格是由降採樣格線與旋轉角度的交互作用決定的，不受我們控制。每格都重算就
是閃爍（Dead Cells 的美術總監說他們至今沒找到解法，見 docs/05 第三節）。

所以把降採樣的次數從「每格一次」降到「每個姿勢一次」：

    階段 A（本程式）   自由旋轉與位移 → 降採樣一次 → 人眼核可 → 進版控
    階段 B（bake.py）  只做整數像素平移 + 水平翻轉 + z 排序，絕不重新採樣

> CLAUDE.md 規則 4「不可以用旋轉」講的是**階段 B**。本程式旋轉是合法的，
> 因為每個姿勢只結算一次而且要人眼核可。**不要把這裡的旋轉搬進 bake.py。**

為什麼 x / y 必須是 21 的整數倍
-------------------------------
這條規則約束的是「pivot 落在畫布上的哪裡」，不是部件內部的像素。

兩個姿勢若某部件的 rot 相同、x/y 差 21k，該部件降採樣後會是**逐位元相同的像素
叢集**、剛好平移 k 個目標像素——階段 B 需要的正是這個。差 21k±1 的話，部件會
跨過格線邊界，眾數降採樣可能在邊緣多吃或少吃一格，兩個姿勢之間就會抖。

旋轉不受這條約束（旋轉本來就會換一組像素叢集），因為每個角度只結算一次。

畫布為什麼是 1344×1176
----------------------
1344 = 64×21，1176 = 56×21。選這個尺寸就是為了讓比例是**整數**，每個來源像素
才會決定性地映射到同一個目標格。docs/05 舊文寫的 1341×1173 是 gpt-image-2 的
原始輸出尺寸，1341/64 = 20.95 不是整數，不能當畫布用。

用法
----
    python tools/assemble.py --rig art/rigs/brown_mixed/rig.json \\
        --poses specs/poses/brown_mixed.poses.json --out-dir build/poses

    # 只組一個姿勢，另外輸出一張「全部件依真實 z 疊好」的核可用大圖
    python tools/assemble.py --rig ... --poses ... --pose sit --preview

接下來
------
    python tools/pixelate.py build/poses/brown_mixed_stand_*.png \\
        --width 64 --height 56 --no-crop \\
        --palette specs/palettes/brown_mixed.json \\
        --remap art/rigs/brown_mixed/remap.json --out-dir build/pixparts
"""

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


# 目標 sprite 尺寸。畫布必須是它的整數倍，否則來源像素落不進固定的格線。
TARGET_W, TARGET_H = 64, 56
DEFAULT_CANVAS = (1344, 1176)          # 64×21, 56×21
BG = (255, 0, 255)                     # 洋紅底，交給 pixelate.py 去背

# 像素領域的七個圖層群（docs/05 第三節）。
# 只拆真正需要獨立運動的部件，其餘合併——單部件降採樣時 --coverage 0.45 比合成時
# 更容易吃掉細部：腿與軀幹重疊處兩者各只佔 30% 的格，合成圖是實心，分開就兩邊都
# 變透明。16 個部件在像素領域收斂成 7 個，同時解決覆蓋率問題。
DEFAULT_LAYER_GROUPS = {
    "core":     ["shadow", "torso", "leg_hind_far", "leg_fore_far",
                 "leg_hind_near", "leg_fore_near"],
    "head":     ["head", "muzzle", "eye_far", "eye_near"],
    "ear_far":  ["ear_far"],
    "ear_near": ["ear_near"],
    "tail":     ["tail"],
    "eyelid":   ["eyelid"],
    "jaw":      ["jaw", "tongue"],
}

# 檔案輸出順序。**不是**繪製順序——繪製順序由 z 決定，見 solve_group_order()。
GROUP_OUTPUT_ORDER = ["core", "head", "ear_far", "ear_near",
                      "tail", "eyelid", "jaw"]


# --------------------------------------------------------------------------
# 訊息收集（沿用 validate.py 的形式）
# --------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.errors = []
        self.warns = []
        self.notes = []

    def err(self, m):  self.errors.append(m)
    def warn(self, m): self.warns.append(m)
    def note(self, m): self.notes.append(m)

    @property
    def ok(self):
        return not self.errors

    def dump(self):
        for m in self.notes:  print(f"  ·  {m}")
        for m in self.warns:  print(f"  ⚠️  {m}")
        for m in self.errors: print(f"  ✗  {m}")


# --------------------------------------------------------------------------
# 幾何：繞 pivot 旋轉
# --------------------------------------------------------------------------

def rotate_with_pivot(img: Image.Image, pivot, deg: float) -> tuple:
    """繞部件自己的 pivot 旋轉。回傳 (旋轉後的圖, 旋轉後 pivot 的像素座標)。

    PIL 的 `Image.rotate(expand=True)` 是繞**圖片幾何中心**旋轉再把畫布撐大，
    不是繞 pivot 轉。所以旋轉交給 PIL，pivot 的新位置自己算，貼上時用
    「目標座標 − 新 pivot」補回位移——合起來等效於繞 pivot 旋轉。

    重採樣一律 NEAREST。這裡引入插值只會製造調色盤外的中間色，而下游還要再
    降採樣一次，那些中間色會被眾數統計吃進去變成雜訊。

    座標慣例：像素 (x, y) 的幾何中心是 (x+0.5, y+0.5)。
    必須用中心而非左上角推算，才會和 PIL 在 0/90/180/270 度走的整數快速路徑
    （transpose）一致——用左上角推算在這三個角度會整整差 1 px。

    推導：expand=True 時，來源中心 c=(w/2,h/2) 對應輸出中心 c'=(nw/2,nh/2)，
    中間是純旋轉。deg 正值 = 逆時針（PIL 慣例）。

    任意角度下，落點可能與 PIL 實際取樣差 1 個來源像素（floor 的邊界不對稱）。
    1 個來源像素是 1/21 個目標像素，對降採樣結果沒有影響。
    """
    if deg % 360 == 0:
        return img.copy(), (int(pivot[0]), int(pivot[1]))

    out = img.rotate(deg, resample=Image.Resampling.NEAREST,
                     expand=True, fillcolor=(0, 0, 0, 0))

    th = math.radians(deg)
    cos_t, sin_t = math.cos(th), math.sin(th)
    dx = pivot[0] + 0.5 - img.width / 2.0
    dy = pivot[1] + 0.5 - img.height / 2.0
    px = cos_t * dx + sin_t * dy + out.width / 2.0
    py = -sin_t * dx + cos_t * dy + out.height / 2.0
    return out, (math.floor(px), math.floor(py))


# --------------------------------------------------------------------------
# rig / poses 的載入與檢查
# --------------------------------------------------------------------------

def load_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise SystemExit(f"找不到{what}：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{what} 不是合法 JSON（{path}）：{e}")


def check_canvas(canvas, report: Report):
    """驗證畫布是目標 sprite 的整數倍，回傳倍率（失敗回傳 None）。"""
    w, h = int(canvas[0]), int(canvas[1])
    if w % TARGET_W or h % TARGET_H or (w // TARGET_W) != (h // TARGET_H):
        report.err(
            f"畫布 {w}×{h} 不是目標 sprite {TARGET_W}×{TARGET_H} 的整數倍"
            f"（{w / TARGET_W:.4g} : {h / TARGET_H:.4g}）。"
            f"整數比是階段 A 的前提——每個來源像素才會決定性地落進同一格。"
            f"應為 {DEFAULT_CANVAS[0]}×{DEFAULT_CANVAS[1]}")
        return None
    return w // TARGET_W


def check_groups(groups: dict, parts: dict, report: Report) -> dict:
    """檢查圖層群是否恰好覆蓋 rig 的全部部件一次。回傳過濾掉未知部件的群組。"""
    seen, clean = {}, {}
    for gname in sorted(groups):
        members = []
        for pname in groups[gname]:
            if pname not in parts:
                report.err(f"圖層群 '{gname}' 引用的部件 '{pname}' 不在 rig.json 裡")
                continue
            if pname in seen:
                report.warn(f"部件 '{pname}' 同時屬於 '{seen[pname]}' 和 '{gname}'，"
                            f"合成時會被畫兩次")
            seen[pname] = gname
            members.append(pname)
        clean[gname] = members

    orphan = sorted(set(parts) - set(seen))
    if orphan:
        report.warn(f"{len(orphan)} 個部件不屬於任何圖層群，會從輸出中消失："
                    f"{', '.join(orphan)}")

    extra = sorted(set(groups) - set(GROUP_OUTPUT_ORDER))
    missing = [g for g in GROUP_OUTPUT_ORDER if g not in groups]
    if extra or missing:
        report.warn(f"圖層群與 docs/05 的七個群不一致"
                    f"（多：{extra or '無'}；缺：{missing or '無'}）")
    return clean


def solve_group_order(groups: dict, parts: dict, report: Report) -> list:
    """算出「把七個扁平圖層疊回去」時 z 反轉最少的繪製順序。

    為什麼需要這個：把 16 個部件壓成 7 個圖層會**遺失 z 資訊**。rig.json 裡有
    部件的 z 落在別群的 z 區間內（例如 tail=10 夾在 core 的 shadow=0 與
    leg_hind_far=20 之間），扁平化之後不管怎麼排都無法完全還原。

    七個群只有 5040 種排法，直接窮舉挑「被反轉的跨群部件對」最少的那個，
    同分時取字典序最小以保證決定性。回報無法避免的反轉，讓 bake.py 的作者
    知道哪些地方會和核可用的預覽圖不同。
    """
    names = sorted(groups)
    if len(names) > 8:                       # 保險：階乘會爆
        return sorted(names, key=lambda g: min(parts[p]["z"] for p in groups[g]))

    # cross[a][b] = a 群中應該畫在 b 群某部件「前面」的部件對數
    cross = {a: {b: 0 for b in names} for a in names}
    for a, b in itertools.permutations(names, 2):
        for pa in groups[a]:
            for pb in groups[b]:
                if parts[pa]["z"] > parts[pb]["z"]:
                    cross[a][b] += 1

    best, best_cost = None, None
    for perm in itertools.permutations(names):
        # 先畫的群若含有「z 較大」的部件，那些部件對就被反轉了
        cost = sum(cross[perm[i]][perm[j]]
                   for i in range(len(perm)) for j in range(i + 1, len(perm)))
        if best_cost is None or cost < best_cost:
            best, best_cost = perm, cost

    if best_cost:
        detail = []
        for i, a in enumerate(best):
            for b in best[i + 1:]:
                for pa in groups[a]:
                    for pb in groups[b]:
                        if parts[pa]["z"] > parts[pb]["z"]:
                            detail.append(f"{pa}(z={parts[pa]['z']}) 會被 "
                                          f"{pb}(z={parts[pb]['z']}) 蓋住")
        report.warn(
            f"七個圖層群的 z 區間互相交錯，扁平化後有 {best_cost} 組部件的前後"
            f"關係無法還原：{'；'.join(detail)}。"
            f"要嘛調整 rig.json 的 z，要嘛接受這個差異（64px 上多半看不出來）")
    report.note(f"階段 B 建議的圖層繪製順序（z 反轉最少）：{' → '.join(best)}")
    return list(best)


def hierarchy_order(parts: dict, report: Report) -> list:
    """把部件排成「父在前」的順序，讓缺漏擺位的預設值能沿階層往下傳。"""
    order, done = [], set()

    def visit(name, trail):
        if name in done:
            return
        if name in trail:
            report.err(f"rig.json 的 parent 有循環：{' → '.join(trail + [name])}")
            return
        meta = parts.get(name)
        if meta is None:
            report.err(f"部件 '{name}' 的 parent 指到不存在的部件")
            return
        parent = meta.get("parent")
        if parent:
            visit(parent, trail + [name])
        done.add(name)
        order.append(name)

    for name in sorted(parts):
        visit(name, [])
    return order


# --------------------------------------------------------------------------
# 擺位解析
# --------------------------------------------------------------------------

def resolve_placements(pose_name: str, pose_spec: dict, parts: dict,
                       grid, report: Report) -> dict:
    """算出每個部件的最終 (x, y, rot)。x/y 是 pivot 要落在畫布上的座標。

    缺擺位時的預設值：rig.json 的 offset。rig.json 自己的說明寫「offset 是部件
    相對父階層 pivot 的位置」，所以這裡解成「父的擺位 + offset」——切分後 offset
    全是 [0,0]，等於把部件的 pivot 疊在父的 pivot 上。位置一定是錯的，但至少在
    畫布上、而且緊鄰父部件，比丟到 (0,0) 的角落好找。

    注意：**旋轉不沿階層繼承。** 姿勢規格給的是每個部件的絕對 x/y/rot，
    轉了頭不會連帶轉耳朵，每個部件的 rot 都要自己寫。
    """
    placed, defaulted, misaligned = {}, [], []

    for name in hierarchy_order(parts, report):
        meta = parts[name]
        ox, oy = meta.get("offset", [0, 0])
        parent = meta.get("parent")
        base_x, base_y = 0, 0
        if parent and parent in placed:
            base_x, base_y = placed[parent]["x"], placed[parent]["y"]
        def_x, def_y = base_x + ox, base_y + oy

        entry = pose_spec.get(name)
        source = "pose"
        if entry is None:
            source = "rig_offset"
            defaulted.append(name)
            report.warn(
                f"[{pose_name}] 姿勢規格缺 '{name}'，改用 rig.json 的 offset "
                f"{[ox, oy]}（相對 parent {parent!r}）→ pivot 落在 ({def_x}, {def_y})")
            entry = {}
        else:
            for axis, dv in (("x", def_x), ("y", def_y)):
                if axis not in entry:
                    report.warn(f"[{pose_name}] '{name}' 沒給 {axis}，"
                                f"改用 rig.json 的 offset → {dv}")

        raw_x = entry.get("x", def_x)
        raw_y = entry.get("y", def_y)
        rot = float(entry.get("rot", 0) or 0)

        x, y = int(round(float(raw_x))), int(round(float(raw_y)))
        if float(raw_x) != x or float(raw_y) != y:
            report.warn(f"[{pose_name}] '{name}' 的擺位 ({raw_x}, {raw_y}) 不是整數，"
                        f"四捨五入成 ({x}, {y})")

        # 21 的整數倍檢查。不是就警告，不中止——讓人先看到問題再決定怎麼改。
        if grid:
            bad = [f"{a}={v}（餘 {v % grid}）"
                   for a, v in (("x", x), ("y", y)) if v % grid]
            if bad:
                misaligned.append({"part": name, "x": x, "y": y, "source": source})
                report.warn(f"[{pose_name}] '{name}' 的 {'、'.join(bad)} "
                            f"不是 {grid} 的整數倍，降採樣格線會漂移")

        placed[name] = {"x": x, "y": y, "rot": rot, "source": source}

    return {"placements": placed, "defaulted": defaulted, "misaligned": misaligned}


# --------------------------------------------------------------------------
# 合成
# --------------------------------------------------------------------------

def load_part_image(path: Path, name: str, report: Report) -> Image.Image:
    """載入部件並把 alpha 二值化。

    cutparts.py 產出的 alpha 本來就是二值的，這裡是保險：半透明像素貼到洋紅底
    上會混出中間色，pixelate.py 的去背門檻與眾數降採樣都會被那些混色騙到。
    """
    if not path.exists():
        report.err(f"部件檔不存在：{path}")
        return None
    img = Image.open(path).convert("RGBA")
    alpha = img.getchannel("A")
    soft = sum(alpha.histogram()[1:255])
    if soft:
        report.warn(f"部件 '{name}' 有 {soft} 個半透明像素，已二值化"
                    f"（門檻 128）以免和洋紅底混色")
    img.putalpha(alpha.point(lambda v: 255 if v >= 128 else 0))
    return img


def compose_group(members: list, placements: dict, parts: dict, part_dir: Path,
                  canvas_size: tuple, cache: dict, report: Report,
                  label: str) -> tuple:
    """把一個圖層群的部件依 z 由小到大疊在洋紅畫布上。回傳 (圖, 統計)。"""
    canvas = Image.new("RGB", canvas_size, BG)
    cover = Image.new("L", canvas_size, 0)      # 累積不透明範圍，用來抓空圖層
    stats = []

    for name in sorted(members, key=lambda n: (parts[n]["z"], n)):
        meta = parts[name]
        if name not in cache:
            im = load_part_image(part_dir / meta["file"], name, report)
            sc = meta.get("_scale", 1.0)
            if im is not None and abs(sc - 1.0) > 1e-6:
                # NEAREST：這裡引入插值只會製造中間色，而且之後還要降採樣 21:1
                im = im.resize((max(1, int(round(im.width * sc))),
                                max(1, int(round(im.height * sc)))),
                               Image.Resampling.NEAREST)
            cache[name] = im
        img = cache[name]
        if img is None:
            continue

        pl = placements[name]
        rot_img, new_pivot = rotate_with_pivot(img, meta["pivot"], pl["rot"])
        ox = pl["x"] - new_pivot[0]
        oy = pl["y"] - new_pivot[1]

        # alpha 已二值化，所以這個 paste 是硬拷貝，不會和洋紅底做 alpha 混色
        canvas.paste(rot_img, (ox, oy), rot_img)
        cover.paste(255, (ox, oy), rot_img)

        bbox = rot_img.getchannel("A").getbbox()
        if bbox is None:
            report.warn(f"[{label}] 部件 '{name}' 沒有任何不透明像素")
            continue
        cx0, cy0 = ox + bbox[0], oy + bbox[1]
        cx1, cy1 = ox + bbox[2], oy + bbox[3]
        if cx0 < 0 or cy0 < 0 or cx1 > canvas_size[0] or cy1 > canvas_size[1]:
            report.warn(f"[{label}] 部件 '{name}' 有內容超出畫布並被裁掉"
                        f"（外接框 {[cx0, cy0, cx1, cy1]}，畫布 {list(canvas_size)}）")
        stats.append({"part": name, "z": meta["z"], "rot": pl["rot"],
                      "pivot_at": [pl["x"], pl["y"]], "paste_at": [ox, oy],
                      "bbox": [cx0, cy0, cx1, cy1]})

    opaque = cover.histogram()[255]
    return canvas, {"parts": stats, "opaque_pixels": opaque,
                    "bbox": list(cover.getbbox() or [])}


def compose_preview(parts: dict, placements: dict, part_dir: Path,
                    canvas_size: tuple, cache: dict, report: Report,
                    label: str) -> Image.Image:
    """核可用大圖：忽略圖層群，全部件依 rig.json 的真實 z 疊好。

    這張才是「姿勢應該長什麼樣」的基準。七張圖層群疊回去可能和它有差異，
    差在哪由 solve_group_order() 報告。
    """
    img, _ = compose_group(sorted(parts), placements, parts, part_dir,
                           canvas_size, cache, report, label)
    return img


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def apply_part_scale(parts: dict, all_poses: dict, s: float,
                     canvas: tuple, grid: int, ground_y: int,
                     report: Report) -> None:
    """就地把整個組裝放大 s 倍：部件圖、pivot、以及所有姿勢座標。

    為什麼需要這個：部件在 `art/rigs/` 的原生解析度偏小，擺進 1344×1176
    只佔到約 903×861，降採樣後角色只有 43×41——而 master sprite 是 64×54。
    同一隻狗在 transform 動畫（用 master）與 rig 動畫之間切換會當場縮水三分之一。

    縮放發生在**階段 A**，這是合法的：階段 A 允許重新採樣，因為它只跑一次、
    有人工核可、結果進版控。階段 B 仍然是純整數運算。

    姿勢座標繞「畫布中線 × 地面線」縮放，讓腳留在地上、角色往上往外長，
    縮放後吸附回 grid 的整數倍以維持降採樣格線。
    """
    if abs(s - 1.0) < 1e-6:
        return

    canvas_cx = canvas[0] // 2

    def snap(v):
        return int(round(v / grid)) * grid

    for name, meta in parts.items():
        meta["_scale"] = s
        px, py = meta["pivot"]
        meta["pivot"] = [int(round(px * s)), int(round(py * s))]

    moved = 0
    for pose_name, pose in all_poses.items():
        placed = [pl for pl in (pose or {}).values()
                  if isinstance(pl, dict) and "x" in pl]
        if not placed:
            continue

        # 繞**該姿勢自己的水平中心**縮放，再把中心移回畫布中線。
        # 繞畫布中線縮放的話，本來就偏離中線的姿勢會被推到畫布外——
        # 實測 sit 與 lie 在 s=1.32 時左側被切掉。
        pose_cx = sum(pl["x"] for pl in placed) / len(placed)

        for pl in placed:
            nx = snap(canvas_cx + (pl["x"] - pose_cx) * s)
            ny = snap(ground_y + (pl["y"] - ground_y) * s)
            if (nx, ny) != (pl["x"], pl["y"]):
                moved += 1
            pl["x"], pl["y"] = nx, ny

    report.note(f"part_scale={s:g}：{len(parts)} 個部件放大，{moved} 個姿勢座標重算"
                f"（各姿勢繞自身中心縮放後置中於 x={canvas_cx}，"
                f"地面 y={ground_y}，全部吸附回 {grid} 的倍數）")


def autocenter_pose(pose: dict, parts: dict, part_dir: Path, cache: dict,
                    canvas: tuple, grid: int, report: Report, label: str) -> int:
    """把姿勢水平置中於畫布。回傳實際位移的像素數。

    不能用 pivot 的平均值當中心——尾巴的 pivot 在尾根（身體側），圖形卻往反方向
    伸出去，平均會被拉偏，實測 s=1.32 時三個姿勢左側全部被切掉。
    這裡實際算出每個部件旋轉、擺位之後的外接框，取聯集才是真正的視覺中心。
    """
    placed = [(n, pl) for n, pl in pose.items()
              if isinstance(pl, dict) and "x" in pl and n in parts]
    x0, x1 = None, None

    for name, pl in placed:
        if name not in cache:
            im = load_part_image(part_dir / parts[name]["file"], name, report)
            sc = parts[name].get("_scale", 1.0)
            if im is not None and abs(sc - 1.0) > 1e-6:
                im = im.resize((max(1, int(round(im.width * sc))),
                                max(1, int(round(im.height * sc)))),
                               Image.Resampling.NEAREST)
            cache[name] = im
        img = cache[name]
        if img is None:
            continue
        rot, piv = rotate_with_pivot(img, parts[name]["pivot"], pl.get("rot", 0))
        bb = rot.getchannel("A").getbbox()
        if bb is None:
            continue
        lx = pl["x"] - piv[0] + bb[0]
        rx = pl["x"] - piv[0] + bb[2]
        x0 = lx if x0 is None else min(x0, lx)
        x1 = rx if x1 is None else max(x1, rx)

    if x0 is None:
        return 0

    want = (canvas[0] - (x1 - x0)) // 2
    shift = int(round((want - x0) / grid)) * grid
    if shift:
        for _, pl in placed:
            pl["x"] += shift
        report.note(f"[{label}] 自動置中：整體平移 {shift:+d} px"
                    f"（原本佔 x{x0}–{x1}，寬 {x1 - x0}）")
    return shift


def assemble_all(rig_path: Path, poses_path: Path, out_dir: Path,
                 only_pose=None, preview=False, quiet=False,
                 part_scale=None) -> dict:
    report = Report()
    rig = load_json(rig_path, "rig.json")
    poses_doc = load_json(poses_path, "姿勢規格")

    parts = rig.get("parts") or {}
    if not parts:
        raise SystemExit(f"rig.json 沒有任何部件：{rig_path}")

    cid = poses_doc.get("character_id") or rig.get("character_id") or "unknown"
    if rig.get("character_id") and poses_doc.get("character_id") \
            and rig["character_id"] != poses_doc["character_id"]:
        report.err(f"character_id 不一致：rig={rig['character_id']!r}，"
                   f"poses={poses_doc['character_id']!r}")

    canvas = tuple(poses_doc.get("canvas") or DEFAULT_CANVAS)
    if "canvas" not in poses_doc:
        report.warn(f"姿勢規格沒給 canvas，用預設 {DEFAULT_CANVAS[0]}×{DEFAULT_CANVAS[1]}")
    grid = check_canvas(canvas, report)

    groups = poses_doc.get("layer_groups")
    if not groups:
        report.warn("姿勢規格沒給 layer_groups，用 docs/05 第三節的七個群")
        groups = DEFAULT_LAYER_GROUPS
    groups = check_groups(groups, parts, report)
    group_order = solve_group_order(groups, parts, report)

    all_poses = poses_doc.get("poses") or {}
    if not all_poses:
        raise SystemExit(f"姿勢規格沒有任何姿勢：{poses_path}")

    scale = part_scale if part_scale is not None else poses_doc.get("part_scale", 1.0)
    # grid 為 None 代表畫布檢查已經失敗（錯誤已記在 report 裡）。
    # 這裡不能再算下去，否則 None 參與運算會變成 TypeError，
    # 把一個有清楚訊息的錯誤變成堆疊追蹤。
    if grid:
        ground_y = int(poses_doc.get("ground_row", 54)) * grid
        apply_part_scale(parts, all_poses, float(scale), canvas, grid, ground_y, report)
    if only_pose:
        if only_pose not in all_poses:
            raise SystemExit(f"姿勢規格沒有 {only_pose!r}，"
                             f"有的是：{', '.join(sorted(all_poses))}")
        wanted = [only_pose]
    else:
        wanted = sorted(all_poses)

    out_dir.mkdir(parents=True, exist_ok=True)
    part_dir = rig_path.parent
    cache = {}
    results = {}

    # 檔案輸出順序固定，和繪製順序無關，只是為了讓輸出可預測
    ordered_groups = ([g for g in GROUP_OUTPUT_ORDER if g in groups]
                      + [g for g in sorted(groups) if g not in GROUP_OUTPUT_ORDER])

    # 自動置中只在有縮放時才做。無條件執行會破壞「pivot 精準落在指定座標」
    # 這個契約——姿勢規格寫的座標就該是最終座標。
    do_center = bool(grid) and abs(float(scale) - 1.0) > 1e-6

    for pose_name in wanted:
        if do_center:
            autocenter_pose(all_poses[pose_name] or {}, parts, part_dir, cache,
                            canvas, grid, report, pose_name)
        res = resolve_placements(pose_name, all_poses[pose_name] or {},
                                 parts, grid, report)
        placements = res["placements"]
        out = {"defaulted": res["defaulted"], "misaligned": res["misaligned"],
               "placements": placements, "groups": {}}

        for gname in ordered_groups:
            label = f"{pose_name}/{gname}"
            img, stats = compose_group(groups[gname], placements, parts, part_dir,
                                       canvas, cache, report, label)
            path = out_dir / f"{cid}_{pose_name}_{gname}.png"
            img.save(path)
            if stats["opaque_pixels"] == 0:
                report.warn(f"[{label}] 圖層群沒有任何不透明像素，"
                            f"pixelate.py 會因為找不到前景而中止")
            stats["file"] = str(path)
            out["groups"][gname] = stats
            if not quiet:
                print(f"  {pose_name:<6} {gname:<9} "
                      f"{len(stats['parts'])} 個部件  "
                      f"{stats['opaque_pixels']:>7d} px  → {path.name}")

        if preview:
            pv_dir = out_dir / "preview"
            pv_dir.mkdir(parents=True, exist_ok=True)
            pv = compose_preview(parts, placements, part_dir, canvas,
                                 cache, report, f"{pose_name}/preview")
            pv_path = pv_dir / f"{cid}_{pose_name}.png"
            pv.save(pv_path)
            out["preview"] = str(pv_path)
            if not quiet:
                print(f"  {pose_name:<6} {'preview':<9} 全部件依真實 z  → {pv_path}")

        results[pose_name] = out

    return {"character_id": cid, "canvas": list(canvas), "grid": grid,
            "layer_groups": groups, "group_draw_order": group_order,
            "out_dir": str(out_dir), "poses": results,
            "errors": report.errors, "warnings": report.warns,
            "notes": report.notes, "_report": report}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="階段 A：把高解析部件組成姿勢，輸出七個圖層群的來源圖")
    ap.add_argument("--rig", type=Path, required=True,
                    help="art/rigs/<id>/rig.json")
    ap.add_argument("--poses", type=Path, required=True,
                    help="specs/poses/<id>.poses.json")
    ap.add_argument("--pose", help="只組這個姿勢（stand / sit / lie），省略則全部")
    ap.add_argument("--out-dir", type=Path, default=Path("build/poses"))
    ap.add_argument("--preview", action="store_true",
                    help="另存一張「全部件依真實 z 疊好」的核可用大圖到 <out-dir>/preview/")
    ap.add_argument("--report", type=Path,
                    help="把擺位與統計寫成 JSON，供 bake.py 使用")
    ap.add_argument("--part-scale", type=float,
                    help="整體放大倍率，覆寫姿勢規格的 part_scale。"
                         "階段 A 允許重新採樣，這是修正 rig 與 master 尺度不符的手段")
    ap.add_argument("--strict", action="store_true",
                    help="有警告也視為失敗（CI 用；預設只印出來）")
    args = ap.parse_args()

    res = assemble_all(args.rig, args.poses, args.out_dir,
                       only_pose=args.pose, preview=args.preview, part_scale=args.part_scale)
    report = res.pop("_report")

    print()
    report.dump()

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(res, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        print(f"  ·  擺位報告 → {args.report}")

    if report.errors or (args.strict and report.warns):
        sys.exit(1)


if __name__ == "__main__":
    main()
