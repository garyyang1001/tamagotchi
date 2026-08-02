#!/usr/bin/env python3
"""
pack.py — 把定稿的資產打包成一個 `data/assets.bin`

燒進 ESP32 的 `assets` 分區之後用 `spi_flash_mmap` 映射，
索引表可以直接當 C 結構陣列存取（零複製），blob 用到時才 RLE 解碼進 PSRAM。

為什麼整包自己寫
----------------
`lv_img_conv` 兩個版本都實測過（docs/10 第四節）：npm 版會重新量化、
連 index 0 = 透明都保不住，日夜換盤直接壞掉（27% 的像素變色）；
Python 版保真但吃不了我們的 RGBA 輸入。六個步驟裡它只覆蓋第 2 步，
而第 2 步是一行。結論是 (c) 整包自己寫，**不引進任何外部相依**。

blob 的單位：每格影格，不是每張 spritesheet
--------------------------------------------
這是唯一需要量了才能決定的事，兩種都實作、都量過（`--blob-unit` 可以切換，
每次執行都會把對照表印出來）。實測 94 個資產、351 格：

    方案                     全部 blob    壓縮率   解一格要解碼的像素（平均／最大）
    ------------------------------------------------------------------------
    每張 spritesheet          257,003 B    2.89x      16,209 / 36,864
    每格影格                  271,497 B    2.74x       4,123 /  6,144
    每格影格 + 相同 blob 去重  204,147 B    3.64x       4,123 /  6,144   <- 採用

直覺會以為 per-sheet 比較小（一列橫跨四格，透明 run 可以接起來），
量出來也確實小 5.34%。**但那個優勢被去重整個吃掉還倒賠。**

原因是 transform 型動畫（呼吸、難過等待、跳躍）把位移記在 `screen_dy` 而不是
畫進像素裡——同一個動畫的四格是**逐位元相同的點陣圖**。351 格裡只有 266 格
是唯一的，去重省下 24.8%。而去重只有在 blob 是「每格」時才做得到：
blob 是整張 sheet 的話，四格黏在一起，沒有東西可以共用。

決定性的那一項是解碼成本：渲染層一次只畫一格，per-sheet 要**多解 3.93 倍的
像素**、佔 3.93 倍的 PSRAM（公主一張 sheet 是 36,864 px，一格只要 6,144）。
一個 3 歲小孩的裝置在 240 MHz 上跑，這是每一格都要付的代價，
而換到的是**更大**的檔案。兩個軸都輸，所以預設是每格影格。

RLE token 版面（docs/04 D 節，逃脫版）
--------------------------------------
    0nnnnnnn            接下來 n 個像素透明          n = 1..127
    1nnniiii            接下來 n 個像素是索引 i      n = 1..7
    1000iiii + <count>  接下來 count 個像素是索引 i  count = 8..255（2 bytes）

**run 不跨列**（逐列編碼），代價 2.3%，換到逐列索引與局部重繪的可能。
原始文件寫的 `1nnnniiii` 是 9 bit，一個位元組放不下，從來沒有辦法實作
（docs/10 第 4.6 節撿到的）。

硬性檢查（違反就中止，不會默默通過）
------------------------------------
1. 每個不透明像素都必須**精確**命中調色盤裡的某一色（不做最近距離猜測，
   那正是把狗肚子吸成灰白的那個做法，docs/08 第 2.4 節）
2. `room_day` 與 `room_night` 的索引平面必須逐位元相同——
   「日夜共用同一張點陣圖只換調色盤」是整個場景設計的地基
3. 影格尺寸必須等於 `specs/characters/<id>.json` 的 `render.sprite_cell`
   （經 `tools/cell.py`，單一真相來源）
4. `name_hash`（FNV-1a 32 bit）碰撞 → 中止並回傳非零

用法
----
    .venv/bin/python tools/pack.py
    .venv/bin/python tools/pack.py --blob-unit sheet --out /tmp/sheet.bin
"""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cell import cell_for   # noqa: E402

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


ROOT = Path(__file__).resolve().parent.parent
SHEET_DIR = ROOT / "build/sheets"
SCENE_DIR = ROOT / "art/approved/_scene"
DRESS_DIR = ROOT / "art/approved/_dressup"
VISITOR_DIR = ROOT / "art/approved/_visitors"
PALETTE_DIR = ROOT / "specs/palettes"

MAGIC = b"IPA1"
FORMAT_VERSION = 1

# 結構大小。C 端有對應的 _Static_assert（見 docs/04 D 節），
# 兩邊對不上就是有人動了其中一邊。
HEADER_BYTES = 48
ASSET_BYTES = 20
FRAME_BYTES = 16
PALETTE_BYTES = 32          # 16 色 × RGB565

HEADER_FMT = "<4sHHHHHHIIIIIIII"
ASSET_FMT = "<IIHHHBBBBH"
FRAME_FMT = "<IIHbbBBBB"

# 資產型別
TYPE_ANIM = 0               # 角色動畫
TYPE_SCENE = 1              # 房間底圖
TYPE_OBJECT = 2             # 場景物件

# header flags
HF_BLOB_PER_FRAME = 1 << 0
HF_BLOB_DEDUP = 1 << 1

# asset flags
AF_LOOP = 1 << 0

FLASH_BYTES = 16 * 1024 * 1024          # 整片 flash
ASSETS_PARTITION_BYTES = 6 * 1024 * 1024  # assets 分區（docs/04）

PALETTE_SIZE = 16           # 4bpp


class PackError(Exception):
    """資料違反打包的契約。一律中止，不做任何猜測性的修補。"""


# --------------------------------------------------------------------------
# 名稱雜湊
# --------------------------------------------------------------------------

def fnv1a_32(name):
    """FNV-1a 32 bit。韌體端可以用同一個十行實作，不需要查表。

    刻意用位元組而不是字元：資產名一律 ASCII，但寫成 encode() 之後
    非 ASCII 也有定義良好的行為，不會兩邊算出不同的值。
    """
    h = 0x811C9DC5
    for b in name.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def check_collisions(names):
    """撞到就丟例外。呼叫端負責 exit(非零)。

    32 bit 對 94 個名字碰撞機率約一億分之一，但這是**燒進 flash** 的索引：
    撞到的後果是小孩看到錯的動畫，而且完全不會有錯誤訊息。
    """
    seen = {}
    for n in names:
        h = fnv1a_32(n)
        if h in seen:
            raise PackError(
                "name_hash 碰撞：%r 與 %r 都是 0x%08X。"
                "改其中一個名字，或換成 64 bit 雜湊" % (seen[h], n, h))
        seen[h] = n
    return seen


# --------------------------------------------------------------------------
# 調色盤
# --------------------------------------------------------------------------

def parse_hex(s):
    s = s.lstrip("#")
    if len(s) != 6:
        raise PackError("顏色 %r 不是 RRGGBB 格式" % s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def rgb565(rgb):
    r, g, b = rgb
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


class Palette:
    """16 色調色盤。`lut` 只收不透明色——透明是靠 alpha=0 表示的，
    不是靠某個 RGB 值（和 bake.py 的 load_palette 同一個約定）。"""

    def __init__(self, name, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = data["colors"]
        if len(entries) != PALETTE_SIZE:
            raise PackError("%s 有 %d 色，4bpp 要求剛好 %d 色"
                            % (path, len(entries), PALETTE_SIZE))
        self.name = name
        self.path = path
        self.rgb = [None] * PALETTE_SIZE
        self.lut = {}
        self.dup = {}
        for c in entries:
            i = int(c["index"])
            if not 0 <= i < PALETTE_SIZE:
                raise PackError("%s 的 index %d 超出 0..15" % (path, i))
            if self.rgb[i] is not None:
                raise PackError("%s 的 index %d 重複" % (path, i))
            rgb = parse_hex(c["hex"])
            self.rgb[i] = rgb
            if c.get("transparent"):
                if i != 0:
                    raise PackError(
                        "%s 把透明放在 index %d。RLE 的 `0nnnnnnn` token 建立在"
                        "「index 0 就是透明」這條約定上，不可以改" % (path, i))
                continue
            if rgb in self.lut:
                # 兩個索引同色 → 從 RGB 反推索引會有歧義。
                # 只有真的要拿這份調色盤去建索引平面時才致命（index_plane 會擋），
                # 光是存進 bin 沒有問題——實測 brindle_guard_night 的 index 2 與 11
                # 都是 #202030，那份調色盤只拿來換色、不拿來反推。
                self.dup.setdefault(rgb, [self.lut[rgb]]).append(i)
                continue
            self.lut[rgb] = i
        if self.rgb[0] is None or None in self.rgb:
            raise PackError("%s 的 index 不連續" % path)

    def to_bytes(self):
        return struct.pack("<16H", *[rgb565(c) for c in self.rgb])


# --------------------------------------------------------------------------
# 影像 → 索引平面
# --------------------------------------------------------------------------

def index_plane(path, palette):
    """RGBA/RGB PNG → uint8 索引平面。

    **精確比對，不做最近距離。** docs/08 第 2.4 節：用數值距離套調色盤會把
    整片肚子吸成灰白。到了打包這一步，上游（bake.py / scene.py）已經保證
    每個像素都是調色盤裡的色，對不上就是有人繞過了管線，該中止而不是猜。
    """
    if palette.dup:
        rgb, idxs = sorted(palette.dup.items())[0]
        raise PackError(
            "調色盤 %s 的 index %s 是同一個顏色 #%02X%02X%02X，"
            "從 RGB 反推索引會有歧義，不能拿它來建索引平面"
            % (palette.name, "/".join(str(i) for i in idxs), *rgb))
    arr = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
    alpha = arr[..., 3]
    semi = (alpha != 0) & (alpha != 255)
    if semi.any():
        raise PackError("%s 有 %d 個半透明像素。4bpp 索引色沒有半透明"
                        % (path, int(semi.sum())))
    opaque = alpha == 255
    rgb = arr[..., :3]

    plane = np.zeros(arr.shape[:2], dtype=np.uint8)
    done = np.zeros(arr.shape[:2], dtype=bool)
    for color, idx in sorted(palette.lut.items()):
        m = opaque & np.all(rgb == np.array(color, dtype=np.uint8), axis=2)
        plane[m] = idx
        done |= m

    missed = opaque & ~done
    if missed.any():
        bad = np.unique(rgb[missed].reshape(-1, 3), axis=0)[:4]
        raise PackError(
            "%s 有 %d 個像素不在調色盤 %s 裡：%s"
            % (path, int(missed.sum()), palette.name,
               ", ".join("#%02X%02X%02X" % tuple(int(v) for v in c) for c in bad)))
    return plane


# --------------------------------------------------------------------------
# RLE
# --------------------------------------------------------------------------

def rle_encode(plane):
    """逐列編碼成 docs/04 D 節的逃脫版 token 流。

    run 不跨列是刻意的：代價 2.3%，換到的是可以對每一列建索引、支援局部重繪。
    """
    out = bytearray()
    for row in plane:
        n = row.shape[0]
        if n == 0:
            continue
        cuts = np.flatnonzero(row[1:] != row[:-1]) + 1
        bounds = np.concatenate(([0], cuts, [n]))
        for a, b in zip(bounds[:-1], bounds[1:]):
            idx = int(row[a])
            cnt = int(b - a)
            if idx == 0:
                while cnt > 0:
                    k = min(cnt, 127)
                    out.append(k)              # 0nnnnnnn
                    cnt -= k
            else:
                while cnt > 0:
                    if cnt <= 7:
                        out.append(0x80 | (cnt << 4) | idx)   # 1nnniiii
                        cnt = 0
                    else:
                        k = min(cnt, 255)
                        out.append(0x80 | idx)                # 1000iiii
                        out.append(k)                         # <count>
                        cnt -= k
    return bytes(out)


# --------------------------------------------------------------------------
# 收集資產
# --------------------------------------------------------------------------

class Frame:
    __slots__ = ("plane", "ms", "screen_dx", "screen_dy", "flip",
                 "blob_offset", "blob_bytes")

    def __init__(self, plane, ms, screen_dx, screen_dy, flip):
        self.plane = plane
        self.ms = ms
        self.screen_dx = screen_dx
        self.screen_dy = screen_dy
        self.flip = flip
        self.blob_offset = 0
        self.blob_bytes = 0


class Asset:
    __slots__ = ("name", "kind", "type", "w", "h", "loop",
                 "pal_day", "pal_night", "frames", "sheet_plane", "source")

    def __init__(self, name, kind, atype, w, h, loop, pal_day, pal_night,
                 frames, sheet_plane, source):
        self.name = name
        self.kind = kind            # 報表分類用的中文字串
        self.type = atype
        self.w = w
        self.h = h
        self.loop = loop
        self.pal_day = pal_day
        self.pal_night = pal_night
        self.frames = frames
        self.sheet_plane = sheet_plane
        self.source = source


def split_frames(sheet, w, h, n, where):
    """把橫向排列的 spritesheet 切成 n 個 h×w 的索引平面。"""
    H, W = sheet.shape
    if H != h or W != w * n:
        raise PackError("%s 的圖是 %d×%d，但規格說是 %d 格 %d×%d（應為 %d×%d）"
                        % (where, W, H, n, w, h, w * n, h))
    return [sheet[:, i * w:(i + 1) * w] for i in range(n)]


def collect_characters(palettes, log):
    """四個角色 × 21 個動畫。影格尺寸以 specs/characters 為準（tools/cell.py）。"""
    assets = []
    spec_paths = sorted((ROOT / "specs/characters").glob("*.json"))
    if not spec_paths:
        raise PackError("找不到任何 specs/characters/*.json")
    for sp in spec_paths:
        spec = json.loads(sp.read_text(encoding="utf-8"))
        cid = spec["character_id"]
        cell_w, cell_h = cell_for(cid)

        pal_day = palettes.add(cid, PALETTE_DIR / ("%s.json" % cid))
        night_path = PALETTE_DIR / ("%s_night.json" % cid)
        pal_night = palettes.add("%s_night" % cid, night_path) \
            if night_path.exists() else pal_day

        atlas_path = SHEET_DIR / ("%s_atlas.json" % cid)
        if not atlas_path.exists():
            raise PackError("找不到 %s——先跑 tools/bake.py --character %s"
                            % (atlas_path, cid))
        atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
        pal = palettes.get(pal_day)

        for entry in atlas["animations"]:
            adef = json.loads(
                (SHEET_DIR / entry["atlas"]).read_text(encoding="utf-8"))
            w, h = int(adef["frame_w"]), int(adef["frame_h"])
            if (w, h) != (cell_w, cell_h):
                raise PackError(
                    "%s/%s 的影格是 %d×%d，但 specs/characters/%s.json 的 "
                    "render.sprite_cell 是 %d×%d"
                    % (cid, entry["id"], w, h, cid, cell_w, cell_h))
            sheet = index_plane(SHEET_DIR / entry["sheet"], pal)
            n = int(adef["frame_count"])
            planes = split_frames(sheet, w, h, n, "%s/%s" % (cid, entry["id"]))
            frames = []
            for i, fr in enumerate(adef["frames"]):
                frames.append(Frame(planes[i], int(fr["ms"]),
                                    int(fr["screen_dx"]), int(fr["screen_dy"]),
                                    1 if fr["flip"] else 0))
            assets.append(Asset(
                "%s/%s" % (cid, entry["id"]), "角色動畫", TYPE_ANIM, w, h,
                bool(adef["loop"]), pal_day, pal_night, frames, sheet,
                SHEET_DIR / entry["sheet"]))
        log("  %-14s %2d 個動畫  影格 %d×%d  調色盤 %s/%s"
            % (cid, len(atlas["animations"]), cell_w, cell_h,
               cid, palettes.name_of(pal_night)))
    return assets


def collect_scene(palettes, log):
    """房間底圖 + 物件。全部共用 specs/palettes/scene.json。"""
    spec = json.loads((ROOT / "specs/scene.json").read_text(encoding="utf-8"))
    pal_day = palettes.add("scene", PALETTE_DIR / "scene.json")
    pal_night = palettes.add("scene_night", PALETTE_DIR / "scene_night.json")
    day, night = palettes.get(pal_day), palettes.get(pal_night)

    assets = []

    # --- 房間底圖 -------------------------------------------------------
    # 日夜共用同一張點陣圖。這不是最佳化，是設計的地基：
    # 兩張圖的索引平面若不同，「夜間版的窗戶少了一格」這種錯就做得出來。
    day_plane = index_plane(SCENE_DIR / "room_day.png", day)
    night_plane = index_plane(SCENE_DIR / "room_night.png", night)
    if day_plane.shape != night_plane.shape or \
            not np.array_equal(day_plane, night_plane):
        diff = int((day_plane != night_plane).sum()) \
            if day_plane.shape == night_plane.shape else -1
        raise PackError(
            "room_day 與 room_night 的索引平面不同（%s 個像素）。"
            "specs/palettes/scene.json 明寫日夜共用同一張點陣圖只換調色盤，"
            "違反的話夜間會畫出和白天不同的房間" % diff)
    h, w = day_plane.shape
    if [w, h] != list(spec["size"]):
        raise PackError("room_day 是 %d×%d，但 specs/scene.json 的 size 是 %s"
                        % (w, h, spec["size"]))
    assets.append(Asset("scene/room", "房間底圖", TYPE_SCENE, w, h, False,
                        pal_day, pal_night, [Frame(day_plane, 0, 0, 0, 0)],
                        day_plane, SCENE_DIR / "room_day.png"))
    log("  room           日夜共用一張點陣圖 %d×%d（省一份 blob）" % (w, h))

    # --- 物件 -----------------------------------------------------------
    for key in sorted(k for k, v in spec["objects"].items()
                      if isinstance(v, dict) and "size" in v):
        o = spec["objects"][key]
        png = SCENE_DIR / ("obj_%s.png" % key)
        if not png.exists():
            raise PackError("找不到 %s——先跑 art/approved/_scene/REBUILD.sh" % png)
        w, h = int(o["size"][0]), int(o["size"][1])
        n = int(o.get("frame_count", 1))
        ms = int(o.get("frame_ms", 0))
        sheet = index_plane(png, day)
        planes = split_frames(sheet, w, h, n, "obj/%s" % key)
        frames = [Frame(p, ms, 0, 0, 0) for p in planes]
        assets.append(Asset("obj/%s" % key, "場景物件", TYPE_OBJECT, w, h,
                            n > 1, pal_day, pal_night, frames, sheet, png))
    log("  objects        %d 個（%d 格）"
        % (len(assets) - 1, sum(len(a.frames) for a in assets[1:])))
    return assets


def collect_visitors(palettes, log):
    """閒置訪客（松鼠、小鳥）。**用自己的調色盤不是 scene 的**——
    牠們是走 AI 管線的生物，不是 scene.py 畫的幾何，色彩需求也不同
    （松鼠是暖 russet、小鳥是藍 + 橘，兩者都不在房間那 16 色裡）。"""
    spec_path = ROOT / "specs/visitors.json"
    if not spec_path.exists():
        return []
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assets = []
    for v in spec["visitors"]:
        cid = v["id"]
        pal_path = ROOT / v["palette"]
        pal_day = palettes.add(cid, pal_path)
        pal = palettes.get(pal_day)
        night_path = ROOT / ("specs/palettes/%s_night.json" % cid)
        pal_night = (palettes.add("%s_night" % cid, night_path)
                     if night_path.exists() else pal_day)
        png = ROOT / v["sheet"]
        if not png.exists():
            raise PackError("找不到 %s——先跑 art/approved/_visitors/REBUILD.sh" % png)
        w, h = int(v["size"][0]), int(v["size"][1])
        n = int(v["frame_count"])
        ms = int(v.get("frame_ms", 200))
        sheet = index_plane(png, pal)
        planes = split_frames(sheet, w, h, n, "visitor/%s" % cid)
        frames = [Frame(p, ms, 0, 0, 0) for p in planes]
        assets.append(Asset("visitor/%s" % cid, "閒置訪客", TYPE_OBJECT, w, h,
                            True, pal_day, pal_night, frames, sheet, png))
    if assets:
        log("  visitors       %d 隻（%d 格）"
            % (len(assets), sum(len(a.frames) for a in assets)))
    return assets


def collect_accessories(palettes, log):
    """公主的配件。**用角色的調色盤不是場景的**——它們畫在她身上，
    共用同一份 16 色，所以換服裝（outfit_slots）時配件的顏色也跟著一致。

    配件是單格的靜態圖：它不自己動，是跟著頭走。位移由渲染層依
    `specs/anchors/<id>.json` 的逐格頭部錨點 + 配件自己的 dx/dy 算出來，
    **不烘進資產**——和 screen_dx/screen_dy 同一個道理。"""
    assets = []
    for spec_path in sorted((ROOT / "specs/accessories").glob("*.json")):
        cid = spec_path.stem
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        pal_day = palettes.add(cid, ROOT / ("specs/palettes/%s.json" % cid))
        pal = palettes.get(pal_day)
        night_path = ROOT / ("specs/palettes/%s_night.json" % cid)
        pal_night = (palettes.add("%s_night" % cid, night_path)
                     if night_path.exists() else pal_day)
        for a in spec["accessories"]:
            if a["id"].startswith("_"):
                continue
            png = DRESS_DIR / ("acc_%s_%s.png" % (cid, a["id"]))
            if not png.exists():
                raise PackError("找不到 %s——先跑 tools/dressup.py" % png)
            w, h = int(a["size"][0]), int(a["size"][1])
            plane = index_plane(png, pal)
            name = "acc/%s/%s" % (cid, a["id"])
            assets.append(Asset(name, "公主配件", TYPE_OBJECT, w, h,
                                False, pal_day, pal_night,
                                [Frame(plane, 0, 0, 0, 0)], plane, png))
    if assets:
        log("  accessories    %d 件" % len(assets))
    return assets


class PaletteTable:
    """調色盤表。同一份檔案只收一次，id 是表裡的位置。"""

    def __init__(self):
        self._by_name = {}
        self._order = []

    def add(self, name, path):
        if name in self._by_name:
            return self._by_name[name][0]
        path = Path(path)
        if not path.exists():
            raise PackError("找不到調色盤 %s" % path)
        pid = len(self._order)
        pal = Palette(name, path)
        self._by_name[name] = (pid, pal)
        self._order.append(pal)
        return pid

    def get(self, pid):
        return self._order[pid]

    def name_of(self, pid):
        return self._order[pid].name

    def __len__(self):
        return len(self._order)

    def to_bytes(self):
        return b"".join(p.to_bytes() for p in self._order)


# --------------------------------------------------------------------------
# 編碼與量測
# --------------------------------------------------------------------------

def encode_blobs(assets, per_frame, dedup):
    """把每個 asset 的影格編成 blob，回傳 (blob 位元組串, 統計)。

    per_frame=False 時整張 sheet 是一個 blob，同一個 asset 的每一格都指向它
    （第 i 格在解開的平面裡是 x = i*w 那一段）。那條路只是為了留下可量測的
    對照組，預設不用——理由見模組 docstring。
    """
    chunks = []
    total = 0
    seen = {}
    dedup_saved = 0
    unique = 0
    for a in assets:
        if per_frame:
            for fr in a.frames:
                blob = rle_encode(fr.plane)
                key = blob if dedup else None
                if dedup and key in seen:
                    fr.blob_offset, fr.blob_bytes = seen[key]
                    dedup_saved += len(blob)
                    continue
                fr.blob_offset, fr.blob_bytes = total, len(blob)
                if dedup:
                    seen[key] = (total, len(blob))
                chunks.append(blob)
                total += len(blob)
                unique += 1
        else:
            blob = rle_encode(a.sheet_plane)
            off = total
            chunks.append(blob)
            total += len(blob)
            unique += 1
            for fr in a.frames:
                fr.blob_offset, fr.blob_bytes = off, len(blob)
    return b"".join(chunks), {
        "bytes": total, "dedup_saved": dedup_saved, "unique": unique,
    }


def measure(assets):
    """per-frame vs per-sheet 的對照數字。決定預設值的那張表就是這裡算的。"""
    raw = frame_bytes = sheet_bytes = 0
    nframes = 0
    seen = set()
    dedup_bytes = 0
    decode_frame = decode_sheet = 0
    multi = 0
    max_frame_px = max_sheet_px = 0
    for a in assets:
        n = len(a.frames)
        px = a.w * a.h
        raw += ((px * n) + 1) // 2                # 4bpp 未壓縮
        sheet_bytes += len(rle_encode(a.sheet_plane))
        for fr in a.frames:
            b = rle_encode(fr.plane)
            frame_bytes += len(b)
            if b not in seen:
                seen.add(b)
                dedup_bytes += len(b)
        nframes += n
        if n > 1:                                  # 單格資產兩種方案一樣，不列入
            multi += 1
            decode_frame += px
            decode_sheet += px * n
            max_frame_px = max(max_frame_px, px)
            max_sheet_px = max(max_sheet_px, px * n)
    return {
        "raw": raw, "frames": nframes, "assets": len(assets),
        "per_frame": frame_bytes, "per_sheet": sheet_bytes,
        "per_frame_dedup": dedup_bytes, "unique": len(seen),
        "multi": multi,
        "decode_frame_avg": decode_frame / max(multi, 1),
        "decode_sheet_avg": decode_sheet / max(multi, 1),
        "decode_frame_max": max_frame_px, "decode_sheet_max": max_sheet_px,
    }


# --------------------------------------------------------------------------
# 組檔
# --------------------------------------------------------------------------

def build(assets, palettes, per_frame=True, dedup=True):
    """回傳完整的 assets.bin 位元組串。

    資產表**照 name_hash 排序**，韌體可以二分搜尋；也讓輸出與收集順序脫鉤，
    多一層決定性的保險。
    """
    names = [a.name for a in assets]
    check_collisions(names)
    assets = sorted(assets, key=lambda a: fnv1a_32(a.name))

    nassets = len(assets)
    nframes = sum(len(a.frames) for a in assets)
    npal = len(palettes)

    asset_off = HEADER_BYTES
    frame_off = asset_off + nassets * ASSET_BYTES
    pal_off = frame_off + nframes * FRAME_BYTES
    blob_off = pal_off + npal * PALETTE_BYTES
    if blob_off % 4:                                # 表都是 4 的倍數，這裡只是保險
        blob_off += 4 - (blob_off % 4)

    blobs, stat = encode_blobs(assets, per_frame, dedup)

    asset_rows = []
    frame_rows = []
    fidx = 0
    for a in assets:
        flags = AF_LOOP if a.loop else 0
        asset_rows.append(struct.pack(
            ASSET_FMT, fnv1a_32(a.name), fidx, len(a.frames), a.w, a.h,
            a.pal_day, a.pal_night, a.type, flags, 0))
        for fr in a.frames:
            if not -128 <= fr.screen_dx <= 127 or not -128 <= fr.screen_dy <= 127:
                raise PackError("%s 的 screen offset (%d, %d) 超出 int8"
                                % (a.name, fr.screen_dx, fr.screen_dy))
            if not 0 <= fr.ms <= 65535:
                raise PackError("%s 的 ms=%d 超出 uint16" % (a.name, fr.ms))
            frame_rows.append(struct.pack(
                FRAME_FMT, blob_off + fr.blob_offset, fr.blob_bytes,
                fr.ms, fr.screen_dx, fr.screen_dy, fr.flip, 0, 0, 0))
            fidx += 1

    body = b"".join(asset_rows) + b"".join(frame_rows) + palettes.to_bytes()
    pad = b"\x00" * (blob_off - HEADER_BYTES - len(body))
    content = body + pad + blobs
    total = HEADER_BYTES + len(content)

    flags = (HF_BLOB_PER_FRAME if per_frame else 0) | (HF_BLOB_DEDUP if dedup else 0)
    header = struct.pack(
        HEADER_FMT, MAGIC, FORMAT_VERSION, HEADER_BYTES,
        nassets, nframes, npal, flags,
        asset_off, frame_off, pal_off, blob_off, len(blobs), total,
        zlib.crc32(content) & 0xFFFFFFFF, 0)
    assert len(header) == HEADER_BYTES
    return header + content, assets, stat


def manifest(assets, palettes, blob, per_frame, dedup):
    """給人和韌體看的對照表：名字 → 雜湊。決定性，不含時間戳。"""
    return {
        "format": "IPA1",
        "version": FORMAT_VERSION,
        "generator": "tools/pack.py",
        "blob_unit": "frame" if per_frame else "sheet",
        "dedup": dedup,
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "palettes": [{"id": i, "name": palettes.name_of(i)}
                     for i in range(len(palettes))],
        "assets": [{
            "name": a.name,
            "name_hash": "0x%08X" % fnv1a_32(a.name),
            "type": a.type,
            "w": a.w, "h": a.h,
            "frame_count": len(a.frames),
            "loop": a.loop,
            "palette_day": a.pal_day,
            "palette_night": a.pal_night,
        } for a in assets],
    }


# --------------------------------------------------------------------------
# 報表
# --------------------------------------------------------------------------

def report(assets, palettes, blob, m, per_frame, dedup, stat, log):
    by_kind = {}
    for a in assets:
        k = by_kind.setdefault(a.kind, {"n": 0, "f": 0, "raw": 0, "rle": 0})
        k["n"] += 1
        k["f"] += len(a.frames)
        k["raw"] += ((a.w * a.h * len(a.frames)) + 1) // 2
        k["rle"] += sum(fr.blob_bytes for fr in a.frames)

    log("")
    log("每一類資產")
    log("  %-10s %4s %5s %12s %12s %8s" % ("類別", "資產", "影格",
                                           "4bpp 原始", "RLE", "壓縮率"))
    log("  " + "-" * 56)
    tot = {"n": 0, "f": 0, "raw": 0, "rle": 0}
    order = [k for k in ("角色動畫", "房間底圖", "場景物件") if k in by_kind]
    order += [k for k in sorted(by_kind) if k not in order]   # 新分類不會被吃掉
    for kind in order:
        k = by_kind[kind]
        log("  %-10s %4d %5d %12s %12s %7.2fx"
            % (kind, k["n"], k["f"], "{:,}".format(k["raw"]),
               "{:,}".format(k["rle"]), k["raw"] / k["rle"]))
        for f in tot:
            tot[f] += k[f]
    log("  " + "-" * 56)
    # 註：去重之後各類的 RLE 相加會大於實際 blob 區（同一份 blob 被算了多次）
    log("  %-10s %4d %5d %12s %12s %7.2fx"
        % ("合計", tot["n"], tot["f"], "{:,}".format(tot["raw"]),
           "{:,}".format(tot["rle"]), tot["raw"] / tot["rle"]))
    if dedup and stat["dedup_saved"]:
        log("  相同 blob 去重後實際寫入 %s B（%d/%d 格是唯一的，省 %s B）"
            % ("{:,}".format(stat["bytes"]), stat["unique"], tot["f"],
               "{:,}".format(stat["dedup_saved"])))

    nassets = len(assets)
    nframes = sum(len(a.frames) for a in assets)
    log("")
    log("檔案組成")
    log("  檔頭        %8s B" % "{:,}".format(HEADER_BYTES))
    log("  資產表      %8s B  (%d × %d)"
        % ("{:,}".format(nassets * ASSET_BYTES), nassets, ASSET_BYTES))
    log("  影格表      %8s B  (%d × %d)"
        % ("{:,}".format(nframes * FRAME_BYTES), nframes, FRAME_BYTES))
    log("  調色盤表    %8s B  (%d × %d，RGB565)"
        % ("{:,}".format(len(palettes) * PALETTE_BYTES), len(palettes),
           PALETTE_BYTES))
    log("  blob        %8s B" % "{:,}".format(stat["bytes"]))
    log("  " + "-" * 34)
    log("  合計        %8s B  = %.1f KB"
        % ("{:,}".format(len(blob)), len(blob) / 1024))
    log("")
    log("  整體壓縮率 %.2fx（4bpp 原始 %s B → 檔案 %s B）"
        % (m["raw"] / len(blob), "{:,}".format(m["raw"]),
           "{:,}".format(len(blob))))
    log("  佔 16 MB flash 的 %.2f%%；佔 6 MB assets 分區的 %.2f%%"
        % (len(blob) / FLASH_BYTES * 100,
           len(blob) / ASSETS_PARTITION_BYTES * 100))

    log("")
    log("blob 單位對照（兩種都實作、都量，預設用每格影格）")
    log("  %-24s %12s %8s %10s %10s"
        % ("方案", "全部 blob", "壓縮率", "解碼 平均", "最大"))
    log("  " + "-" * 68)
    rows = [
        ("每張 spritesheet", m["per_sheet"], m["decode_sheet_avg"],
         m["decode_sheet_max"], not per_frame and not dedup),
        ("每格影格", m["per_frame"], m["decode_frame_avg"],
         m["decode_frame_max"], per_frame and not dedup),
        ("每格影格 + 去重", m["per_frame_dedup"], m["decode_frame_avg"],
         m["decode_frame_max"], per_frame and dedup),
    ]
    for label, size, davg, dmax, cur in rows:
        log("  %-24s %12s %7.2fx %10.0f %10d%s"
            % (label, "{:,}".format(size), m["raw"] / size, davg, dmax,
               "   <- 本次" if cur else ""))
    log("  「解碼」= 為了畫出一格必須解開的像素數，只計多格資產（%d 個）"
        % m["multi"])
    log("  唯一 blob %d/%d 格——transform 型動畫把位移記在 screen_dy，"
        % (m["unique"], m["frames"]))
    log("  同一個動畫的每一格是逐位元相同的點陣圖，所以去重省下 %.1f%%"
        % ((m["per_frame"] - m["per_frame_dedup"]) / m["per_frame"] * 100))


# --------------------------------------------------------------------------

def run(out_path=None, per_frame=True, dedup=True, quiet=False,
        write_manifest=True):
    lines = []

    def log(s):
        lines.append(s)
        if not quiet:
            print(s)

    out_path = Path(out_path) if out_path else ROOT / "data/assets.bin"

    log("ICEPET 資產包  →  %s" % out_path)
    log("")
    log("來源")
    palettes = PaletteTable()
    assets = collect_characters(palettes, log)
    assets += collect_scene(palettes, log)
    assets += collect_visitors(palettes, log)
    assets += collect_accessories(palettes, log)

    m = measure(assets)
    blob, ordered, stat = build(assets, palettes, per_frame, dedup)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(blob)

    report(ordered, palettes, blob, m, per_frame, dedup, stat, log)

    if write_manifest:
        mf = out_path.with_suffix(".json")
        mf.write_text(json.dumps(
            manifest(ordered, palettes, blob, per_frame, dedup),
            ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8")
        log("")
        log("對照表      %s（名字 → name_hash，給韌體與人看）" % mf)

    digest = hashlib.sha256(blob).hexdigest()
    log("sha256      %s" % digest)
    return {"bytes": len(blob), "sha256": digest, "assets": ordered,
            "palettes": palettes, "measure": m, "stat": stat,
            "path": out_path, "log": lines}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="把定稿的資產打包成 data/assets.bin")
    ap.add_argument("--out", help="輸出路徑，預設 data/assets.bin")
    ap.add_argument("--blob-unit", choices=("frame", "sheet"), default="frame",
                    help="blob 的單位。預設 frame，理由見模組 docstring")
    ap.add_argument("--no-dedup", action="store_true",
                    help="關掉相同 blob 去重（只有量測時才需要）")
    ap.add_argument("--no-manifest", action="store_true",
                    help="不要寫出 assets.json 對照表")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        run(out_path=args.out,
            per_frame=(args.blob_unit == "frame"),
            dedup=not args.no_dedup,
            quiet=args.quiet,
            write_manifest=not args.no_manifest)
    except PackError as e:
        print("打包失敗：%s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
