#!/usr/bin/env python3
"""scene.py — 從 specs/scene.json 算出房間底圖與物件 sprite。

為什麼房間不用 AI 生
--------------------
房間是大片平塗的幾何：牆、地板、地毯、窗框、掛畫。
CLAUDE.md 規則 2 的分工是「造型與剪影給 AI、精確顏色與網格給工具」，
房間**全部**落在後者。讓 AI 生只會多一輪 28,000 色的重映射，
而且窗框會歪、地板接縫會抖——那些正是像素領域一行程式就能保證的東西。

物件（碗、球、睡墊）小到可以逐像素寫在 JSON 裡，用 pixedit.py 的同一套
「row 字串 + key 對照表」寫法。5 像素以下的細節本來就該手工畫（規則 2）。

日夜共用同一張點陣圖
--------------------
底圖只算一次，然後套兩份調色盤。這保證日夜完全對齊——
做不出「夜間版的窗戶少了一格」這種錯，而且省一份 flash。
和公主的服裝置換是同一招。

用法
----
    .venv/bin/python tools/scene.py                 # 產出全部
    .venv/bin/python tools/scene.py --mock          # 另外輸出 320x240 的配置模擬
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "specs/scene.json"
OUT = ROOT / "art/approved/_scene"


def load_palette(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    roles, order = {}, []
    for c in d["colors"]:
        h = c["hex"].lstrip("#")
        roles[c["role"]] = (None if c.get("transparent")
                            else tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
        order.append(c["role"])
    return roles, order


def draw_background(spec, roles):
    """用 RGB 畫，因為底圖沒有透明區。"""
    w, h = spec["size"]
    im = Image.new("RGB", (w, h), roles["wall_light"])
    d = ImageDraw.Draw(im)
    for step in spec["background"]:
        col = roles[step["role"]]
        op = step["op"]
        if op == "fill":
            d.rectangle([0, 0, w, h], fill=col)
        elif op == "rect":
            x0, y0, x1, y1 = step["box"]
            d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=col)
        elif op == "ellipse":
            x0, y0, x1, y1 = step["box"]
            d.ellipse([x0, y0, x1 - 1, y1 - 1], fill=col)
        else:
            raise SystemExit("不認得的 op：%s" % op)
    return im


def _grid(art, key, roles, where):
    h = len(art)
    w = max(len(r) for r in art)
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            role = key.get(ch)
            if role is None:
                continue
            if role not in roles:
                raise SystemExit("%s 用了調色盤沒有的角色 %r" % (where, role))
            arr[y, x] = (*roles[role], 255)
    return arr


def load_object(name, obj):
    """`source` 型物件：圖是別的管線做好的，這裡只讀進來、驗尺寸。

    為什麼要有這條：動作圖示走的是 AI 管線（生成 → cutstrip → pixelate），
    不是 `art` 那種逐列明寫的幾何。但它們仍然要留在 scene.json 的 objects 裡——
    那樣 pack.py 的資產命名（obj/icon_*）、mklayout.py 的索引、render.c 的查表
    全部不必動。**只有「圖從哪來」不同，「它是什麼」沒有變。**

    這一類**不做 scene 調色盤檢查**：它們有自己的 16 色（specs/palettes/icons.json），
    理由是圖示畫在很暗的介面底色上，而 scene 的配額全給了房間的奶油／土黃／灰玫瑰。
    """
    p = ROOT / obj["source"]
    if not p.exists():
        raise SystemExit("%s 的 source %s 不存在——先跑它自己的 REBUILD.sh" % (name, p))
    im = Image.open(p).convert("RGBA")
    w, h = obj["size"]
    n = obj.get("frame_count", 1)
    if (im.width, im.height) != (w * n, h):
        raise SystemExit("%s 的 source 是 %dx%d，size %s × %d 格應為 %dx%d"
                         % (name, im.width, im.height, obj["size"], n, w * n, h))
    return im


def draw_object(name, obj, roles):
    """row 字串 + key 對照表 → RGBA。和 pixedit.py 的 patch 同一種寫法。

    `art` 是單張；`frames` 是多張，水平排成 spritesheet
    （和角色的 spritesheet 同一種佈局，渲染層不必為物件另寫一套讀法）。
    """
    key = obj["key"]
    if "frames" in obj:
        grids = [_grid(a, key, roles, "物件 %s 第 %d 格" % (name, i))
                 for i, a in enumerate(obj["frames"])]
        hs = {g.shape[0] for g in grids}
        ws = {g.shape[1] for g in grids}
        if len(hs) != 1 or len(ws) != 1:
            raise SystemExit("物件 %s 的各格尺寸不一致：%s × %s" % (name, ws, hs))
        arr = np.concatenate(grids, axis=1)
        w, h = grids[0].shape[1], grids[0].shape[0]
        n = len(grids)
    else:
        arr = _grid(obj["art"], key, roles, "物件 %s" % name)
        h, w = arr.shape[:2]
        n = 1
    dw, dh = obj.get("size", [w, h])
    if (w, h) != (dw, dh):
        raise SystemExit("物件 %s 的 art 是 %dx%d，size 宣告 %dx%d，兩者必須一致"
                         % (name, w, h, dw, dh))
    if obj.get("frame_count", n) != n:
        raise SystemExit("物件 %s 宣告 %d 格，實際 %d 格"
                         % (name, obj["frame_count"], n))
    return Image.fromarray(arr), n


def check_palette_only(im, roles, where):
    """底圖與物件只能用調色盤裡的顏色——和角色資產同一條硬性檢查。"""
    a = np.array(im.convert("RGBA"))
    op = a[..., 3] > 0
    used = {tuple(int(v) for v in c) for c in a[..., :3][op]}
    allowed = {c for c in roles.values() if c is not None}
    stray = used - allowed
    if stray:
        raise SystemExit("%s 用了調色盤外的顏色：%s"
                         % (where, ", ".join("#%02X%02X%02X" % c for c in sorted(stray))))
    return len(used)


def main():
    ap = argparse.ArgumentParser(description="產出房間底圖與物件 sprite")
    ap.add_argument("--spec", type=Path, default=SPEC)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--mock", action="store_true",
                    help="另外輸出 320x240 的配置模擬（含角色與圖示列）")
    ap.add_argument("--scale", type=int, default=3, help="模擬圖的放大倍率")
    args = ap.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    day, order = load_palette(ROOT / spec["palette"])
    night, order_n = load_palette(ROOT / spec["night_palette"])
    if order != order_n:
        raise SystemExit("日夜調色盤的角色順序不一致，換色會錯位：\n  日 %s\n  夜 %s"
                         % (order, order_n))

    # ---- 底圖：只畫一次，套兩份調色盤 ----
    bg = draw_background(spec, day)
    n = check_palette_only(bg, day, "房間底圖")
    bg.save(args.out_dir / "room_day.png")

    # 夜間 = 逐像素把日間的顏色換成同索引的夜間色
    a = np.array(bg)
    out = a.copy()
    for role in order:
        if day[role] is None:
            continue
        m = np.all(a == np.array(day[role], np.uint8), axis=-1)
        out[m] = night[role]
    Image.fromarray(out).save(args.out_dir / "room_night.png")

    w, h = spec["size"]
    print("房間底圖 %dx%d  %d 色  → room_day.png / room_night.png" % (w, h, n))

    # ---- 物件 ----
    objs = {}
    for name, obj in spec["objects"].items():
        if name.startswith("_"):
            continue
        if "source" in obj:
            im = load_object(name, obj)
            im.save(args.out_dir / ("obj_%s.png" % name))
            im.resize((im.width * 6, im.height * 6), Image.NEAREST).save(
                args.out_dir / ("obj_%s_x6.png" % name))
            objs[name] = im
            print("  物件 %-6s %dx%d  ← %s（外部管線）"
                  % (name, obj["size"][0], obj["size"][1], obj["source"]))
            continue
        im, n = draw_object(name, obj, day)
        check_palette_only(im, day, "物件 %s" % name)
        im.save(args.out_dir / ("obj_%s.png" % name))
        im.resize((im.width * 6, im.height * 6), Image.NEAREST).save(
            args.out_dir / ("obj_%s_x6.png" % name))
        objs[name] = im
        fw = im.width // n
        print("  物件 %-6s %dx%d%s  模式 %s"
              % (name, fw, im.height, ("  ×%d 格" % n) if n > 1 else "",
                 obj.get("mode", "ground")))

    if args.mock:
        night_objs = {k: swap_scene_palette(v, day, night, order) for k, v in objs.items()}
        mock(spec, bg, objs, args.out_dir, args.scale,
             night_bg=Image.fromarray(out), night_objs=night_objs)


def swap_palette(im, cid):
    """把角色 sprite 換成夜間調色盤。同一張圖，只換顏色——和房間同一招。"""
    day = json.loads((ROOT / ("specs/palettes/%s.json" % cid)).read_text(encoding="utf-8"))
    night_p = ROOT / ("specs/palettes/%s_night.json" % cid)
    if not night_p.exists():
        return im
    night = json.loads(night_p.read_text(encoding="utf-8"))
    nmap = {c["role"]: c["hex"] for c in night["colors"]}
    a = np.array(im.convert("RGBA"))
    out = a.copy()
    for c in day["colors"]:
        if c.get("transparent") or c["role"] not in nmap:
            continue
        def rgb(h):
            h = h.lstrip("#")
            return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)
        m = (a[..., 3] > 0) & np.all(a[..., :3] == rgb(c["hex"]), axis=-1)
        out[m, :3] = rgb(nmap[c["role"]])
    return Image.fromarray(out)


def swap_scene_palette(im, day, night, order):
    """物件也要換夜間色——房間暗了物件還亮著，會看起來在發光。"""
    a = np.array(im.convert("RGBA"))
    out = a.copy()
    for role in order:
        if day[role] is None:
            continue
        m = (a[..., 3] > 0) & np.all(a[..., :3] == np.array(day[role], np.uint8), axis=-1)
        out[m, :3] = night[role]
    return Image.fromarray(out)


def mock(spec, bg_day, objs, out_dir, scale, night_bg=None, night_objs=None):
    """320x240 的配置模擬：底圖 + 四個角色 + 物件 + 圖示列。給人眼看的，不是資產。"""
    lay = spec["layout"]
    W, H = 320, 240
    cues = spec.get("object_cues", {})

    def compose(bg, act="idle_breathe", night=False):
        im = Image.new("RGB", (W, H), (24, 22, 20))
        im.paste(bg, (0, 0))
        cue = cues.get(act)

        def cell_h_of(c):
            return json.loads((ROOT / ("specs/characters/%s.json" % c))
                              .read_text(encoding="utf-8"))["render"]["sprite_cell"][1]

        for slot in spec["characters"]["slots"]:
            cid = slot["id"]
            # 一定要用**真的動畫影格**，不能用站姿——
            # 站姿的吻部在頭部高度，eat 的吻部貼到地面，兩者差 20 px。
            # bath 沒有專屬的角色動畫——物件負責敘事，角色播待機
            src_act = "idle_breathe" if act == "bath" else act
            fr = sorted((ROOT / ("build/strip/%s/%s_px" % (cid, src_act))).glob("*_64px.png"))
            p = fr[2] if fr else ROOT / ("art/approved/%s/master_stand_r_64px.png" % cid)
            if not Path(p).exists():
                continue
            s = Image.open(p).convert("RGBA")
            if night:
                s = swap_palette(s, cid)
            a = np.array(s)
            ys, xs = np.where(a[..., 3] > 0)
            s = Image.fromarray(a[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
            x = slot["x"] + (64 - s.width) // 2
            gy = lay["ground_y"] - slot["dy"]

            pool = night_objs if (night and night_objs) else objs

            def put_obj():
                if not cue:
                    return
                oname = (cue.get("per_character") or {}).get(cid, cue["object"])
                od = spec["objects"][oname]
                mode = od.get("mode", "ground")
                if mode in ("track", "fixed"):
                    return                       # 不跟角色，在迴圈外畫
                o = pool.get(oname)
                if o is None:
                    return
                n = od.get("frame_count", 1)
                fw = o.width // n
                # 多影格物件：靜態模擬取中間那格
                cell = o.crop(((n // 2) * fw, 0, (n // 2 + 1) * fw, o.height))
                if mode == "float":
                    an = (od.get("anchor") or {}).get(cid)
                    if an is None:
                        return
                    top = gy - (cell_h_of(cid) - 1)
                    im.paste(cell, (slot["x"] + an[0], top + an[1]), cell)
                else:
                    axs = od.get("anchor_x", {})
                    ax = axs.get(cid, axs.get("all"))
                    if ax is None:
                        return
                    base = od.get("base_row", o.height - 1)
                    im.paste(cell, (slot["x"] + ax, gy - base), cell)

            if cue and cue.get("z") == "under":
                put_obj()
            im.paste(s, (x, gy - s.height), s)
            if not (cue and cue.get("z") == "under"):
                put_obj()

        # 固定座標的物件（窗外的雪之類）
        for oname, od in spec["objects"].items():
            if oname.startswith("_") or od.get("mode") != "fixed":  # ui 型不入房間模擬圖
                continue
            o = (night_objs or objs).get(oname) if night else objs.get(oname)
            if o is None:
                continue
            n = od.get("frame_count", 1)
            fw = o.width // n
            cell = o.crop(((n // 2) * fw, 0, (n // 2 + 1) * fw, o.height))
            im.paste(cell, tuple(od["at"]), cell)

        # 會動的物件走自己的軌跡，不跟著任何角色
        if cue and spec["objects"][cue["object"]].get("mode") == "track":
            tr = spec["objects"][cue["object"]].get("track")
            o = objs.get(cue["object"])   # 軌跡目前只用在日間的玩球
            if tr and o is not None:
                k = tr["frames"][len(tr["frames"]) // 2]
                im.paste(o, (k["x"], k["y"]), o)   # 軌跡直接給螢幕座標，不用 base_row

        d = ImageDraw.Draw(im)
        d.rectangle([0, lay["ui_bar_y"], W, H], fill=(28, 25, 22))
        for i in range(3):
            cx = W // 6 + (W // 3) * i
            d.rounded_rectangle([cx - 27, lay["ui_bar_y"] + 7, cx + 27, H - 7],
                                radius=7, fill=(50, 45, 40), outline=(122, 112, 100))
        return im

    panels = [
        ("日間 · 待機", compose(bg_day, "idle_breathe")),
        ("餵食 · 碗在吻部下", compose(bg_day, "eat")),
        ("摸摸 · 愛心浮在頭上", compose(bg_day, "pet_react")),
        ("玩球 · 球走自己的軌跡", compose(bg_day, "chase_ball")),
        ("洗澡 · 狗在盆裡、公主在淋浴間", compose(bg_day, "bath")),
        ("睡覺 · 睡墊在身下", compose(night_bg or bg_day, "sleep_breathe", night=True)),
        ("夜間 · 待機", compose(night_bg or bg_day, "idle_breathe", night=True)),
    ]
    cols = 2
    rows = (len(panels) + cols - 1) // cols
    sheet = Image.new("RGB", (W * scale * cols + 20 * (cols + 1),
                              (H * scale + 26) * rows + 8), (250, 248, 244))
    dd = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(panels):
        px = 20 + (i % cols) * (W * scale + 20)
        py = 26 + (i // cols) * (H * scale + 26)
        sheet.paste(im.resize((W * scale, H * scale), Image.NEAREST), (px, py))
        dd.text((px, py - 14), name, fill=(40, 36, 32))
    sheet.save(out_dir / "_mock.png")
    print("  配置模擬 → _mock.png（4 個狀態）")


if __name__ == "__main__":
    main()
