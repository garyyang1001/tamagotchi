#!/usr/bin/env python3
"""
preview.py — 把烘焙好的 spritesheet 變成能在電腦上直接看的動畫

這支工具是整條資產管線的**驗收工具**：燒板子之前，用它確認動作對不對。
它不產生任何會進裝置的資產，只產生給人眼看的東西。

產出
----
    build/preview/<anim>.gif           單一動畫的循環 GIF（NEAREST 放大）
    build/preview/_contact_sheet.png   全部動畫的接觸表（每格一個動畫的首格）
    build/preview/_device_mock.gif     320×240 實機尺寸的模擬

為什麼放大只能用 NEAREST
------------------------
任何插值都會在色塊邊界產生原本不存在的中間色，那正是像素藝術要避免的東西。
本工具的放大一律用 numpy 的整數複製（等同 NEAREST，且逐位元決定性）。
`docs/05` 第三節的三條鐵律裡，階段 B 不得重新採樣——預覽也一樣，
否則你在螢幕上核可的東西和裝置上跑的東西不是同一個。

透明背景與 GIF
--------------
GIF 只有 1 bit 透明，且沒有 alpha 混色。處理不好會出現兩種災情：

    黑框    透明色索引指到黑色，不支援透明的檢視器整張變黑底
    殘影    前一格的像素沒被清掉，動起來像拖曳

對策是三件事一起做：
  1. 調色盤 index 0 保留給透明，且那格填成**淺灰**而不是黑色，
     即使檢視器忽略透明旗標，看到的也是淺色底而不是黑框。
  2. 存檔時明確指定 `transparency=0`。
  3. `disposal=2`（還原成背景）——這是消掉殘影的關鍵，
     沒有它 GIF 預設是「保留前一格」，稀疏的像素動畫會糊成一團。

實機模擬那張是不透明畫布，反而要用 `disposal=1` 讓 Pillow 只存差異區域，
檔案會小很多。兩條路徑分開寫，不要合併。

時間軸
------
`frame_ms` 是權威欄位，`fps` 只是參考（見 `<char>.anim.json` 的 timing_note）。
真實犬隻休息呼吸 10–30 次/分，4 格的循環要 2–6 秒，換算 fps 是 0.67–2.0 的小數，
整數 fps 表達不了，所以以 ms 為準。

GIF 的延遲單位是**百分之一秒**，寫進檔案時會被截成 10ms 的倍數。
本工具先自己四捨五入到 10ms 的倍數再交給 Pillow（Pillow 是無條件捨去，
167ms 會變成 160ms），並在 `--check` 裡把誤差印出來。

`loop: false` 的動畫仍然存成無限循環的 GIF，但**最後一格延長 1 秒**，
播完會停一下再重來——這樣單看一個檔案就能判斷動作的收尾對不對。

用法
----
    # 全部動畫
    python tools/preview.py --character brown_mixed

    # 單一動畫、放大 8 倍
    python tools/preview.py --character brown_mixed --anim walk --scale 8

    # 健檢（不產圖，印出每個動畫的節奏與浪費的影格）
    python tools/preview.py --character brown_mixed --check

    # bake.py 還沒跑過時，直接用 master sprite + 動畫的整數位移合成預覽
    python tools/preview.py --character brown_mixed --from-master
"""

import argparse
import json
import math
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# 契約常數。改這些之前先讀 docs/05 第三節。
# --------------------------------------------------------------------------

SPRITE_W, SPRITE_H = 64, 56      # 目標 sprite。高解析畫布 1344×1176 的 1/21。
DEVICE_W, DEVICE_H = 320, 240    # 2.8" IPS 面板，見 docs/01

DEFAULT_FRAME_MS = 167           # 兩邊都沒給時的保底（約 6 fps）
TAIL_HOLD_MS = 1000              # loop=false 的動畫播完停在最後一格的時間

# GIF index 0 的顏色。它是透明色，正常檢視器看不到；
# 用淺灰是為了讓「不支援透明」的檢視器看到淺色底而不是黑框。
GIF_TRANSPARENT_RGB = (0xE8, 0xE4, 0xDC)

# 接觸表版面（自我測試會用同一組常數推算尺寸，改這裡測試會跟著動）
CONTACT_MARGIN = 12
CONTACT_PAD = 8
CONTACT_HEADER_H = 28
CONTACT_LABEL_H = 34
CONTACT_MAX_COLS = 6
CHECKER_SQUARE = 8
CHECKER_A = (0xCF, 0xCB, 0xC4)
CHECKER_B = (0xB9, 0xB4, 0xAC)
CONTACT_BG = (0x24, 0x22, 0x20)
CONTACT_FG = (0xF0, 0xEC, 0xE4)
CONTACT_DIM = (0xA8, 0xA0, 0x94)
CONTACT_WARN = (0xE0, 0xA8, 0x50)

# 實機模擬的配色。淺色地板是刻意的：裝置上不會是黑底，
# 深色背景會讓深棕色的狗看起來比實際上更清楚。
MOCK_WALL = (0xEE, 0xE9, 0xDF)
MOCK_FLOOR = (0xDB, 0xD2, 0xC2)
MOCK_HORIZON = (0xC4, 0xBA, 0xA8)
MOCK_SHADOW = (0xC2, 0xB6, 0xA2)
MOCK_TEXT = (0x3A, 0x33, 0x2C)
MOCK_FLOOR_Y = 176               # 地平線 y。角色的腳站在這條線上。
MOCK_GAP_MS = 400                # 動畫之間停在最後一格的時間
MOCK_CYCLES = 2                  # 循環型動畫在模擬裡播幾遍

# 呼吸類動畫的生理範圍（次/分）。真實犬隻休息時 10–30。
BREATH_MIN, BREATH_MAX = 10.0, 30.0
BREATH_ANIMS = ("idle_breathe", "sleep_breathe")

# docs/07 第三節：每角色 86 格封頂
FRAME_BUDGET_TOTAL = 86


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def resolve_path(p) -> Path:
    """相對路徑先看 CWD，找不到再看專案根目錄。讓工具從哪個目錄跑都一樣。"""
    path = Path(p)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return (ROOT / path).resolve()


_FONT_CACHE = {}


def get_font(size: int):
    """取字型。標籤一律用 ASCII——預設字型沒有中文字，畫中文會變成豆腐格。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:                      # 舊版 Pillow 的 load_default 沒有 size
        font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def scale_nearest(arr: np.ndarray, scale: int) -> np.ndarray:
    """整數倍最近鄰放大。用 np.repeat 而不是 PIL.resize，理由有兩個：

    1. 它在數學上就是「每個像素複製 scale×scale 份」，不存在插值的可能性；
    2. 逐位元決定性，不受 Pillow 版本的重採樣實作影響。
    """
    if scale == 1:
        return arr
    return np.repeat(np.repeat(arr, scale, axis=0), scale, axis=1)


def normalise_rgba(arr: np.ndarray) -> np.ndarray:
    """把透明像素的 RGB 清成 0，讓「內容相同」的比較不受透明處殘值影響。"""
    out = arr.copy()
    out[out[..., 3] == 0] = 0
    return out


def frame_key(arr: np.ndarray) -> bytes:
    return normalise_rgba(arr).tobytes()


def opaque_mask(arr: np.ndarray) -> np.ndarray:
    return arr[..., 3] > 0


def blit(dst_rgb: np.ndarray, src_rgba: np.ndarray, x: int, y: int) -> None:
    """把 RGBA 貼到 RGB 畫布上，就地修改。

    只做整數位移與二值 alpha 的覆蓋，不做 alpha 混色——
    混色會產生調色盤外的中間色，等於偷偷破壞了限色。
    """
    dh, dw = dst_rgb.shape[:2]
    sh, sw = src_rgba.shape[:2]

    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(sw - sx0, dw - dx0)
    h = min(sh - sy0, dh - dy0)
    if w <= 0 or h <= 0:
        return

    src = src_rgba[sy0:sy0 + h, sx0:sx0 + w]
    m = src[..., 3] > 0
    dst_rgb[dy0:dy0 + h, dx0:dx0 + w][m] = src[..., :3][m]


def checkerboard(w: int, h: int, square: int = CHECKER_SQUARE) -> np.ndarray:
    """棋盤格背景。接觸表用它，才看得出哪裡是透明的。"""
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // square) + (yy // square)) % 2
    out = np.empty((h, w, 3), np.uint8)
    out[m == 0] = CHECKER_A
    out[m == 1] = CHECKER_B
    return out


def union_bbox(frames: Sequence[np.ndarray]) -> Optional[Tuple[int, int, int, int]]:
    """所有影格的內容外接框聯集，格式 (x0, y0, x1, y1)，右下界不含。"""
    x0 = y0 = None
    x1 = y1 = None
    for fr in frames:
        m = opaque_mask(fr)
        if not m.any():
            continue
        rows = np.where(m.any(axis=1))[0]
        cols = np.where(m.any(axis=0))[0]
        a, b = int(rows[0]), int(rows[-1]) + 1
        c, d = int(cols[0]), int(cols[-1]) + 1
        y0 = a if y0 is None else min(y0, a)
        y1 = b if y1 is None else max(y1, b)
        x0 = c if x0 is None else min(x0, c)
        x1 = d if x1 is None else max(x1, d)
    if x0 is None:
        return None
    return x0, y0, x1, y1


def to_gif_ms(ms: int) -> int:
    """GIF 的延遲單位是 10ms。先自己四捨五入，不要交給 Pillow 無條件捨去。

    Pillow 寫檔時做的是 int(duration / 10)：167ms 會變成 160ms，
    一個 4 格的循環就少了 28ms。四捨五入到 170ms 誤差只有 +12ms。
    """
    return int(math.floor((max(0, int(ms)) + 5) / 10.0)) * 10


def hexstr(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)


# --------------------------------------------------------------------------
# 讀取動畫定義
# --------------------------------------------------------------------------

@dataclass
class AnimSpec:
    id: str
    tier: int = 0
    loop: bool = False
    frames: int = 1
    frame_ms: int = DEFAULT_FRAME_MS
    frame_ms_source: str = "default"
    status: str = ""
    desc: str = ""
    screen_offset: object = None
    track: List[dict] = field(default_factory=list)


def resolve_frame_ms(anim: dict, defaults: dict) -> Tuple[int, str]:
    """決定一格幾毫秒。優先序：動畫的 frame_ms > 動畫的 fps > 預設值。

    frame_ms 是權威欄位。動畫層的 fps 仍然優先於 defaults 的 frame_ms，
    因為那是「這個動畫自己說的話」。
    """
    if anim.get("frame_ms") is not None:
        return int(round(float(anim["frame_ms"]))), "anim.frame_ms"
    if anim.get("fps"):
        return int(round(1000.0 / float(anim["fps"]))), "anim.fps"
    if defaults.get("frame_ms") is not None:
        return int(round(float(defaults["frame_ms"]))), "defaults.frame_ms"
    if defaults.get("fps"):
        return int(round(1000.0 / float(defaults["fps"]))), "defaults.fps"
    return DEFAULT_FRAME_MS, "default"


def load_anim_specs(path: Path) -> "OrderedDict[str, AnimSpec]":
    data = json.loads(path.read_text(encoding="utf-8"))
    defaults = data.get("defaults", {}) or {}

    out = OrderedDict()
    for a in data.get("animations", []):
        ms, src = resolve_frame_ms(a, defaults)
        track = a.get("track", []) or []
        out[a["id"]] = AnimSpec(
            screen_offset=a.get("screen_offset"),
            id=a["id"],
            tier=int(a.get("tier", 0)),
            loop=bool(a.get("loop", defaults.get("loop", False))),
            frames=int(a.get("frames", len(track) or 1)),
            frame_ms=ms,
            frame_ms_source=src,
            status=a.get("status", ""),
            desc=a.get("desc", ""),
            track=track,
        )
    return out


def load_palette(path: Path) -> Optional[set]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    cols = data["colors"] if isinstance(data, dict) else data
    out = set()
    for c in cols:
        if isinstance(c, dict):
            if c.get("transparent"):
                continue
            s = c["hex"].lstrip("#")
        else:
            s = str(c).lstrip("#")
        out.add(tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)))
    return out


# --------------------------------------------------------------------------
# 讀取 spritesheet
# --------------------------------------------------------------------------

def match_anim_from_filename(stem: str, character: str,
                             anim_ids: Sequence[str]) -> Optional[str]:
    """從檔名認出是哪個動畫。命名規則見 docs/03 第八節：

        <character>_<anim>_<dir>_<profile>.png

    **必須取最長匹配。** 用 glob `brown_mixed_eat_*` 會同時撈到
    `brown_mixed_eat_happy_r.png`，那是兩個不同的動畫。
    """
    prefix = character + "_"
    if not stem.startswith(prefix):
        return None
    rest = stem[len(prefix):]

    best = None
    for a in anim_ids:
        if rest == a or rest.startswith(a + "_"):
            if best is None or len(a) > len(best):
                best = a
    return best


def is_upscaled_preview(stem: str) -> bool:
    """bake.py 會在 sheets 目錄旁邊放一份 `_x6` 的放大預覽，那不是 sheet。

    把放大版當成 sheet 來切會得到 384×336 的「影格」，
    然後接觸表和 GIF 會整個大一圈——是那種一眼看不出哪裡錯的錯。
    """
    tail = stem.rsplit("_", 1)[-1]
    return len(tail) >= 2 and tail[0] == "x" and tail[1:].isdigit()


def find_sheets(sheets_dir: Path, character: str,
                anim_ids: Sequence[str]) -> "OrderedDict[str, Path]":
    """掃描 sheets 目錄，回傳 動畫 id → PNG 路徑。

    同一個動畫有多個檔（不同方向 / profile）時，優先序：
    完全同名 > 基準方向 `_r` > 檔名字典序。基準方向是面向右，見 docs/03。
    """
    found = {}
    if sheets_dir.exists():
        for png in sorted(sheets_dir.glob("*.png")):
            if is_upscaled_preview(png.stem):
                continue
            aid = match_anim_from_filename(png.stem, character, anim_ids)
            if aid is None:
                continue
            found.setdefault(aid, []).append(png)

    def rank(p: Path, aid: str) -> tuple:
        rest = p.stem[len(character) + 1:]
        if rest == aid:
            return (0, p.name)
        if rest.startswith(aid + "_r"):
            return (1, p.name)
        return (2, p.name)

    out = OrderedDict()
    for aid in anim_ids:
        cands = found.get(aid)
        if cands:
            out[aid] = sorted(cands, key=lambda p: rank(p, aid))[0]
    return out


def load_atlas(png_path: Path) -> Optional[dict]:
    """讀同名的 atlas JSON（每格位置、時長）。沒有就回 None，走等分切割。"""
    j = png_path.with_suffix(".json")
    if not j.exists():
        return None
    try:
        return json.loads(j.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _atlas_frame_size(atlas: dict) -> Optional[Tuple[int, int]]:
    for key in ("frame_size", "size", "cell"):
        v = atlas.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return int(v[0]), int(v[1])
    if atlas.get("frame_w") and atlas.get("frame_h"):
        return int(atlas["frame_w"]), int(atlas["frame_h"])
    return None


def slice_sheet(png_path: Path, spec: Optional[AnimSpec]
                ) -> Tuple[List[np.ndarray], List[int], dict]:
    """把 spritesheet 切成影格。回傳 (影格, 每格 ms, 讀取資訊)。

    支援三種來源，由精確到寬鬆：
      1. 同名的 atlas JSON，逐格給 x/y/w/h 與 ms
      2. atlas 只給格子大小 → 依格線等分
      3. 沒有 atlas → 用動畫定義的影格數等分，再和 64×56 對帳

    bake.py 還沒寫，atlas 的欄位名還沒定案，所以這裡刻意寫得寬容，
    但每一種判斷都會回報在 `info["layout"]` 裡，不會默默猜。
    """
    img = Image.open(png_path).convert("RGBA")
    sheet = np.array(img)
    sh, sw = sheet.shape[:2]

    atlas = load_atlas(png_path)
    info = {"sheet": png_path, "sheet_size": (sw, sh), "atlas": atlas is not None,
            "layout": "", "notes": []}

    frames: List[np.ndarray] = []
    durations: List[int] = []

    # ---- 1. atlas 逐格 ----
    if atlas and isinstance(atlas.get("frames"), list) and atlas["frames"]:
        entries = atlas["frames"]
        default_ms = atlas.get("frame_ms")
        fw_fh = _atlas_frame_size(atlas)
        for e in entries:
            if isinstance(e, dict) and "x" in e:
                x, y = int(e["x"]), int(e["y"])
                w = int(e.get("w", (fw_fh or (SPRITE_W, SPRITE_H))[0]))
                h = int(e.get("h", (fw_fh or (SPRITE_W, SPRITE_H))[1]))
            else:
                # 只給時長不給位置：退回等分
                idx = len(frames)
                w, h = fw_fh or (SPRITE_W, SPRITE_H)
                x, y = (idx * w) % max(1, sw), ((idx * w) // max(1, sw)) * h
            frames.append(sheet[y:y + h, x:x + w].copy())
            ms = None
            if isinstance(e, dict):
                ms = e.get("ms", e.get("duration", e.get("frame_ms")))
            if ms is None:
                ms = default_ms
            durations.append(int(round(float(ms))) if ms is not None else -1)
        info["layout"] = "atlas"

    # ---- 2 / 3. 等分切割 ----
    else:
        fw_fh = _atlas_frame_size(atlas) if atlas else None
        n = None
        if atlas and isinstance(atlas.get("frames"), int):
            n = int(atlas["frames"])
        elif spec is not None:
            n = spec.frames

        if fw_fh:
            fw, fh = fw_fh
            info["layout"] = "atlas-grid"
        elif n and n > 0 and sw % n == 0 and sh > 0:
            fw, fh = sw // n, sh
            info["layout"] = "strip-by-spec"
        elif n and n > 0 and sh % n == 0:
            fw, fh = sw, sh // n
            info["layout"] = "vstrip-by-spec"
        else:
            fw, fh = SPRITE_W, SPRITE_H
            info["layout"] = "grid-64x56"

        cols = max(1, sw // max(1, fw))
        rows = max(1, sh // max(1, fh))
        total = cols * rows
        if n is None or n <= 0:
            n = total
        n = min(n, total)

        for i in range(n):
            r, c = divmod(i, cols)
            frames.append(sheet[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw].copy())
        durations = [-1] * n

        if total > n:
            info["notes"].append(
                "sheet 放得下 %d 格但只取了 %d 格（依動畫定義）" % (total, n))

    # 沒給時長的用動畫定義補上
    fallback = spec.frame_ms if spec is not None else DEFAULT_FRAME_MS
    durations = [fallback if d is None or d < 0 else d for d in durations]

    if frames:
        fh, fw = frames[0].shape[:2]
        info["frame_size"] = (fw, fh)
        if (fw, fh) != (SPRITE_W, SPRITE_H):
            info["notes"].append(
                "影格是 %d×%d，契約寫的是 %d×%d" % (fw, fh, SPRITE_W, SPRITE_H))

    # atlas 是 bake 當時的快照，動畫定義是現在的真相。兩者不合代表 sheet 過期了，
    # 那是最容易看走眼的錯誤——動畫看起來「幾乎對」，但改過的 JSON 根本沒生效。
    if atlas and spec is not None:
        if atlas.get("loop") is not None and bool(atlas["loop"]) != spec.loop:
            info["notes"].append(
                "atlas 的 loop=%s 與動畫定義的 %s 不合，sheet 可能過期"
                % (atlas["loop"], spec.loop))
        if atlas.get("frame_ms") is not None and \
                int(atlas["frame_ms"]) != spec.frame_ms:
            info["notes"].append(
                "atlas 的 frame_ms=%s 與動畫定義的 %d 不合，sheet 可能過期"
                % (atlas["frame_ms"], spec.frame_ms))

    return frames, durations, info


# --------------------------------------------------------------------------
# 沒有 spritesheet 時的替代來源：master sprite + 整數位移
# --------------------------------------------------------------------------

def frames_from_master(master: np.ndarray, spec: AnimSpec
                       ) -> Tuple[List[np.ndarray], List[int], dict]:
    """用 master sprite 加動畫 track 的整數位移合成影格。

    這是 bake.py 就位之前的暫代來源，**只做整數平移與水平翻轉**，
    和階段 B 允許的操作完全一致（docs/05 鐵律 3），所以看到的節奏是真的。
    但它當然不會有部件動作——PLACEHOLDER_NEEDS_RIG 的動畫看起來會像在呼吸。

    master 若不是 64×56 會補到 64×56（底部對齊，腳與接地陰影在下緣）。
    這是整數位移，不是縮放。
    """
    mh, mw = master.shape[:2]
    canvas0 = np.zeros((SPRITE_H, SPRITE_W, 4), np.uint8)
    pad_x = (SPRITE_W - mw) // 2
    pad_y = SPRITE_H - mh
    blit_rgba(canvas0, master, pad_x, pad_y)

    keys = {}
    for k in spec.track:
        f = k.get("f")
        if f is not None:
            keys[int(f)] = k

    frames = []
    last = {"x": 0, "y": 0, "flip": 0}
    for i in range(max(1, spec.frames)):
        k = keys.get(i)
        if k is not None:
            last = {"x": int(k.get("x", 0)), "y": int(k.get("y", 0)),
                    "flip": int(k.get("flip", 0))}
        src = canvas0[:, ::-1] if last["flip"] else canvas0
        out = np.zeros_like(canvas0)
        blit_rgba(out, src, last["x"], last["y"])
        frames.append(out)

    info = {"sheet": None, "sheet_size": (SPRITE_W, SPRITE_H), "atlas": False,
            "layout": "from-master", "frame_size": (SPRITE_W, SPRITE_H),
            "notes": ["由 master sprite + 整數位移合成，不是 bake.py 的產物"]}
    return frames, [spec.frame_ms] * len(frames), info


def blit_rgba(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    """RGBA 貼到 RGBA，覆蓋而非混色。超出畫布的部分裁掉。"""
    dh, dw = dst.shape[:2]
    sh, sw = src.shape[:2]
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(sw - sx0, dw - dx0)
    h = min(sh - sy0, dh - dy0)
    if w <= 0 or h <= 0:
        return
    patch = src[sy0:sy0 + h, sx0:sx0 + w]
    m = patch[..., 3] > 0
    dst[dy0:dy0 + h, dx0:dx0 + w][m] = patch[m]


# --------------------------------------------------------------------------
# GIF 輸出
# --------------------------------------------------------------------------

def pack_rgb(fr: np.ndarray) -> np.ndarray:
    """把 RGB 打包成單一 24 bit 整數，讓「找出用了哪些顏色」變成一維問題。

    打包保持排序：(r,g,b) 的字典序等同 packed 的數值序，
    所以排序過的調色盤可以直接用 searchsorted 反查索引。
    """
    rgb = fr[..., :3].astype(np.int32)
    return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]


def build_indexed(frames: Sequence[np.ndarray], transparent: bool
                  ) -> Tuple[List[Image.Image], int]:
    """把影格轉成共用同一組調色盤的 P 模式圖。回傳 (圖清單, 色數)。

    共用調色盤有兩個好處：
      · 檔案小（GIF 只寫一次全域調色盤）
      · 不會有色彩閃爍（各格自適應量化會挑到略微不同的色階，
        那正是 docs/05 鐵律 1 要防的事）
    """
    if not frames:
        return [], 0

    packed_frames = []
    masks = []
    used = set()
    for fr in frames:
        p = pack_rgb(fr)
        m = opaque_mask(fr) if fr.shape[-1] == 4 else np.ones(p.shape, bool)
        packed_frames.append(p)
        masks.append(m)
        if m.any():
            used.update(int(v) for v in np.unique(p[m]))

    reserved = 1 if transparent else 0
    if len(used) + reserved > 256:
        raise SystemExit(
            "影格用了 %d 色，超過 GIF 的 256 色上限。"
            "這通常代表來源被重新採樣過（產生了調色盤外的中間色）。" % len(used))

    keys = np.array(sorted(used), dtype=np.int32)
    palette = ([GIF_TRANSPARENT_RGB] if transparent else []) + \
              [((int(k) >> 16) & 0xFF, (int(k) >> 8) & 0xFF, int(k) & 0xFF)
               for k in keys]

    flat = []
    for c in palette:
        flat.extend(c)
    flat.extend([0] * (768 - len(flat)))

    out = []
    for p, m in zip(packed_frames, masks):
        h, w = p.shape
        pos = np.searchsorted(keys, p)
        np.clip(pos, 0, max(0, len(keys) - 1), out=pos)
        idx = (pos + reserved).astype(np.uint8)
        idx[~m] = 0
        im = Image.frombytes("P", (w, h), idx.tobytes())
        im.putpalette(flat)
        out.append(im)

    return out, len(palette)


def write_gif(path: Path, frames: Sequence[np.ndarray], durations: Sequence[int],
              transparent: bool = True) -> dict:
    """存 GIF。durations 單位 ms，會先對齊到 10ms 的倍數。

    透明版（單一動畫）用 disposal=2，每格前先還原成背景，杜絕殘影。
    不透明版（實機模擬）用 disposal=1 保留前一格，讓 Pillow 只寫差異區域。
    """
    if not frames:
        raise ValueError("沒有影格可以存")

    imgs, ncolours = build_indexed(frames, transparent)
    gif_ms = [to_gif_ms(d) for d in durations]

    kwargs = dict(
        save_all=True,
        append_images=imgs[1:],
        duration=gif_ms,
        loop=0,                    # 一律無限循環；loop=false 靠尾格延長來表達
        optimize=False,
    )
    if transparent:
        kwargs["transparency"] = 0
        kwargs["disposal"] = 2     # 還原成背景 → 沒有殘影
    else:
        kwargs["disposal"] = 1     # 保留前一格 → 只需寫差異區域，檔案小

    path.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(str(path), **kwargs)

    return {
        "path": path,
        "frames": len(frames),
        "colors": ncolours,
        "duration_ms": sum(gif_ms),
        "gif_ms": gif_ms,
        "requested_ms": [int(d) for d in durations],
        "bytes": path.stat().st_size,
    }


def anim_durations(spec: AnimSpec, durations: Sequence[int]) -> List[int]:
    """套用「loop=false 播完停在最後一格 1 秒」的規則。"""
    out = [int(d) for d in durations]
    if not spec.loop and out:
        out[-1] += TAIL_HOLD_MS
    return out


# --------------------------------------------------------------------------
# 接觸表
# --------------------------------------------------------------------------

def contact_layout(n: int, frame_w: int, frame_h: int, scale: int,
                   cols: Optional[int] = None) -> dict:
    """算接觸表的版面。自我測試會用同一個函式對帳輸出尺寸。"""
    n = max(1, int(n))
    if cols is None:
        cols = min(CONTACT_MAX_COLS, max(1, int(math.ceil(math.sqrt(n)))))
    cols = max(1, min(cols, n))
    rows = int(math.ceil(n / float(cols)))

    cell_w = frame_w * scale + CONTACT_PAD * 2
    cell_h = frame_h * scale + CONTACT_PAD * 2 + CONTACT_LABEL_H
    return {
        "cols": cols, "rows": rows,
        "cell_w": cell_w, "cell_h": cell_h,
        "width": cols * cell_w + CONTACT_MARGIN * 2,
        "height": rows * cell_h + CONTACT_MARGIN * 2 + CONTACT_HEADER_H,
    }


def render_contact_sheet(path: Path, character: str,
                         items: Sequence[Tuple[AnimSpec, List[np.ndarray], dict]],
                         scale: int, cols: Optional[int] = None) -> dict:
    """每格一個動畫的首格，加上動畫名、影格數、frame_ms、tier。

    標籤一律 ASCII——預設字型沒有中文字。
    """
    if not items:
        raise ValueError("沒有動畫可以排接觸表")

    # 影格尺寸理論上全部一樣（64×56）。取最大值是為了在 bake 出錯、
    # 某個動畫尺寸不對時仍然畫得出來，而不是直接爆掉——那樣才看得到問題。
    fw = max(frames[0].shape[1] for _s, frames, _i in items)
    fh = max(frames[0].shape[0] for _s, frames, _i in items)
    lay = contact_layout(len(items), fw, fh, scale, cols)

    canvas = np.empty((lay["height"], lay["width"], 3), np.uint8)
    canvas[:] = CONTACT_BG

    tile_w, tile_h = fw * scale, fh * scale
    for i, (spec, frames, _info) in enumerate(items):
        r, c = divmod(i, lay["cols"])
        cx = CONTACT_MARGIN + c * lay["cell_w"]
        cy = CONTACT_MARGIN + CONTACT_HEADER_H + r * lay["cell_h"]

        big = scale_nearest(frames[0], scale)
        tile = checkerboard(tile_w, tile_h)
        blit(tile, big, (tile_w - big.shape[1]) // 2, (tile_h - big.shape[0]) // 2)
        canvas[cy + CONTACT_PAD:cy + CONTACT_PAD + tile_h,
               cx + CONTACT_PAD:cx + CONTACT_PAD + tile_w] = tile

    img = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)
    f_head = get_font(15)
    f_name = get_font(14)
    f_meta = get_font(12)

    draw.text((CONTACT_MARGIN, CONTACT_MARGIN - 4),
              "%s  --  %d anims  --  contact sheet (frame 0)  x%d"
              % (character, len(items), scale),
              font=f_head, fill=CONTACT_FG)

    for i, (spec, frames, _info) in enumerate(items):
        r, c = divmod(i, lay["cols"])
        cx = CONTACT_MARGIN + c * lay["cell_w"]
        cy = CONTACT_MARGIN + CONTACT_HEADER_H + r * lay["cell_h"]
        ty = cy + CONTACT_PAD + tile_h + 5

        placeholder = spec.status and spec.status != "IMPLEMENTED"
        draw.text((cx + CONTACT_PAD, ty), spec.id, font=f_name,
                  fill=CONTACT_WARN if placeholder else CONTACT_FG)
        meta = "%df  %dms  T%d  %s" % (
            len(frames), spec.frame_ms, spec.tier,
            "loop" if spec.loop else "once")
        if placeholder:
            meta += "  *placeholder"
        draw.text((cx + CONTACT_PAD, ty + 16), meta, font=f_meta, fill=CONTACT_DIM)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))
    return {"path": path, "size": (lay["width"], lay["height"]), "layout": lay,
            "bytes": path.stat().st_size}


# --------------------------------------------------------------------------
# 實機模擬
# --------------------------------------------------------------------------

def screen_at(track, frame_index):
    """取某一格的 screen_offset。步進取值，不做補間。

    screen_offset 與 track 的差別：track 的位移會改變影格內的像素，
    screen_offset 不會——它只影響「這一格畫在螢幕的哪裡」。
    用於跳躍這種會超出影格畫布的位移。
    """
    if not track:
        return 0, 0
    cur = {}
    for k in track:
        if k.get("f", 0) <= frame_index:
            cur = k
        else:
            break
    return int(cur.get("x", 0)), int(cur.get("y", 0))


def make_device_background(shadow_w: int, shadow_cx: int,
                           draw_shadow: bool = True) -> np.ndarray:
    """320×240 的場景底圖：淺色牆面、淺色地板、接地陰影。

    刻意用淺色。深色背景會讓深棕色的狗看起來比在真實裝置上更清楚，
    那就失去驗收的意義了——這張圖存在的目的是「不要自己騙自己」。
    """
    bg = np.empty((DEVICE_H, DEVICE_W, 3), np.uint8)
    bg[:MOCK_FLOOR_Y] = MOCK_WALL
    bg[MOCK_FLOOR_Y:] = MOCK_FLOOR
    bg[MOCK_FLOOR_Y - 1:MOCK_FLOOR_Y] = MOCK_HORIZON

    if draw_shadow and shadow_w > 0:
        img = Image.fromarray(bg)
        d = ImageDraw.Draw(img)
        half = shadow_w // 2 + 3
        d.ellipse([shadow_cx - half, MOCK_FLOOR_Y - 4,
                   shadow_cx + half, MOCK_FLOOR_Y + 4], fill=MOCK_SHADOW)
        bg = np.array(img)
    return bg


def render_device_mock(path: Path,
                       items: Sequence[Tuple[AnimSpec, List[np.ndarray], dict]],
                       cycles: int = MOCK_CYCLES,
                       draw_shadow: bool = True) -> dict:
    """依序播放全部動畫的 320×240 模擬。1× 縮放，因為那才是實機的樣子。"""
    if not items:
        raise ValueError("沒有動畫可以做實機模擬")

    all_frames = [fr for _s, frs, _i in items for fr in frs]
    bbox = union_bbox(all_frames)
    fh, fw = all_frames[0].shape[:2]
    if bbox is None:
        bx0, by0, bx1, by1 = 0, 0, fw, fh
    else:
        bx0, by0, bx1, by1 = bbox

    # 內容水平置中、腳底貼在地平線上。**所有動畫共用同一個貼圖位置**——
    # 逐格重新對位會把我們正要檢查的位移動作抵銷掉。
    blit_x = DEVICE_W // 2 - (bx0 + bx1) // 2
    blit_y = MOCK_FLOOR_Y - by1

    bg = make_device_background(bx1 - bx0, DEVICE_W // 2, draw_shadow)
    font = get_font(13)

    out_frames: List[np.ndarray] = []
    out_ms: List[int] = []

    for spec, frames, _info in items:
        if not frames:
            continue
        reps = max(1, cycles) if spec.loop else 1
        for _ in range(reps):
            for i, fr in enumerate(frames):
                canvas = bg.copy()
                # screen_offset 是繪製時的偏移，不在影格像素裡——
                # 預覽必須自己套用，否則跳躍看不出來。渲染層也要做同樣的事。
                sx, sy = screen_at(spec.screen_offset, i)
                blit(canvas, fr, blit_x + sx, blit_y + sy)
                img = Image.fromarray(canvas)
                d = ImageDraw.Draw(img)
                d.text((6, 5), "%s  t%d  %d/%d"
                       % (spec.id, spec.tier, i + 1, len(frames)),
                       font=font, fill=MOCK_TEXT)
                out_frames.append(np.array(img))
                out_ms.append(spec.frame_ms)
        out_ms[-1] += MOCK_GAP_MS      # 動畫之間停一下，看得出來換了

    if not out_frames:
        raise ValueError("沒有影格可以做實機模擬")

    res = write_gif(path, out_frames, out_ms, transparent=False)
    res["anims"] = len(items)
    res["size"] = (DEVICE_W, DEVICE_H)
    return res


# --------------------------------------------------------------------------
# 健檢
# --------------------------------------------------------------------------

def duplicate_groups(frames: Sequence[np.ndarray]) -> List[List[int]]:
    """找出內容完全相同的影格。相同的影格是浪費——bake 可以指向同一份像素。"""
    seen = OrderedDict()
    for i, fr in enumerate(frames):
        seen.setdefault(frame_key(fr), []).append(i)
    return [v for v in seen.values() if len(v) > 1]


def duplicate_groups_from_track(spec: AnimSpec) -> List[List[int]]:
    """沒有 sheet 時退而求其次：比對 track 的變換值。

    transform 型動畫的影格完全由 (x, y, flip) 決定，
    所以這個判斷和比對像素等價——而且 bake.py 還沒寫就能先用。
    """
    keys = {}
    for k in spec.track:
        f = k.get("f")
        if f is not None:
            keys[int(f)] = k

    seen = OrderedDict()
    cur = (0, 0, 0)
    for i in range(max(1, spec.frames)):
        k = keys.get(i)
        if k is not None:
            cur = (int(k.get("x", 0)), int(k.get("y", 0)), int(k.get("flip", 0)))
        seen.setdefault(cur, []).append(i)
    return [v for v in seen.values() if len(v) > 1]


def check_report(specs: "OrderedDict[str, AnimSpec]",
                 loaded: Dict[str, Tuple[List[np.ndarray], List[int], dict]],
                 palette: Optional[set],
                 full_set: bool = True) -> Tuple[List[str], int, int]:
    """組出健檢報告。回傳 (行清單, 錯誤數, 警告數)。

    full_set=False 表示只檢查了部分動畫（--anim），此時不對影格總預算下判斷。
    """
    lines: List[str] = []
    errors = warns = 0

    header = ("%-18s %5s %8s %8s %9s  %s"
              % ("動畫", "影格", "frame_ms", "循環秒", "次/分", "備註"))
    lines.append(header)
    lines.append("-" * len(header))

    total_frames = 0
    for aid, spec in specs.items():
        entry = loaded.get(aid)
        notes: List[str] = []

        if entry is not None:
            frames, durations, info = entry
            n = len(frames)
            dups = duplicate_groups(frames)
            src = info.get("layout", "?")
            if info.get("notes"):
                notes.extend(info["notes"])
            if n != spec.frames:
                notes.append("sheet 有 %d 格，定義說 %d 格" % (n, spec.frames))
                errors += 1
            cycle_ms = sum(durations)

            # 部分透明度 = 有人重新採樣過。階段 B 不該產生這種像素。
            partial = 0
            stray = set()
            for fr in frames:
                a = fr[..., 3]
                partial += int(((a > 0) & (a < 255)).sum())
                if palette:
                    m = a > 0
                    if m.any():
                        for c in np.unique(fr[..., :3][m].reshape(-1, 3), axis=0):
                            t = tuple(int(v) for v in c)
                            if t not in palette:
                                stray.add(t)
            if partial:
                notes.append("有 %d 個半透明像素（代表被重新採樣過）" % partial)
                errors += 1
            if stray:
                notes.append("調色盤外的顏色 %d 種：%s"
                             % (len(stray),
                                " ".join(hexstr(c) for c in sorted(stray)[:4])))
                errors += 1
        else:
            n = spec.frames
            dups = duplicate_groups_from_track(spec)
            src = "track"
            cycle_ms = n * spec.frame_ms
            notes.append("無 sheet，數據來自動畫定義")

        total_frames += n
        cycle_s = cycle_ms / 1000.0
        per_min = (60.0 / cycle_s) if cycle_s > 0 else 0.0

        if dups:
            groups = ", ".join("f" + "=".join(str(i) for i in g) for g in dups)
            notes.append("重複影格 %s（浪費）" % groups)
            warns += 1

        if aid in BREATH_ANIMS:
            if BREATH_MIN <= per_min <= BREATH_MAX:
                notes.append("呼吸 %.1f 次/分，落在犬隻休息值 %g–%g" %
                             (per_min, BREATH_MIN, BREATH_MAX))
            else:
                notes.append("⚠️ 呼吸 %.1f 次/分，超出犬隻休息值 %g–%g" %
                             (per_min, BREATH_MIN, BREATH_MAX))
                warns += 1

        gif_ms = to_gif_ms(spec.frame_ms)
        if gif_ms != spec.frame_ms:
            notes.append("GIF 只能表達 10ms 的倍數，實播 %dms（差 %+dms/格）"
                         % (gif_ms, gif_ms - spec.frame_ms))

        if not spec.loop:
            notes.append("單次播放，GIF 尾格加 %dms" % TAIL_HOLD_MS)

        lines.append("%-18s %5d %8d %8.2f %9.1f  [%s] %s"
                     % (aid, n, spec.frame_ms, cycle_s, per_min, src,
                        "；".join(notes)))

    lines.append("-" * len(header))
    if full_set:
        over = total_frames > FRAME_BUDGET_TOTAL
        if over:
            errors += 1
        lines.append("合計 %d 格 / 預算 %d 格（docs/07 第三節）%s"
                     % (total_frames, FRAME_BUDGET_TOTAL,
                        "⚠️ 超過預算" if over else "在預算內"))
    else:
        lines.append("合計 %d 格（只檢查了部分動畫，不對總預算下判斷）"
                     % total_frames)
    return lines, errors, warns


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def collect(character: str, sheets_dir: Path, specs: "OrderedDict[str, AnimSpec]",
            only: Optional[str], from_master: Optional[Path]
            ) -> "OrderedDict[str, Tuple[List[np.ndarray], List[int], dict]]":
    """載入每個動畫的影格。sheet 優先，沒有才看 --from-master。"""
    wanted = [only] if only else list(specs.keys())
    sheets = find_sheets(sheets_dir, character, list(specs.keys()))

    master = None
    if from_master is not None:
        if not from_master.exists():
            raise SystemExit("找不到 master sprite：%s" % from_master)
        master = np.array(Image.open(from_master).convert("RGBA"))

    out = OrderedDict()
    for aid in wanted:
        spec = specs.get(aid)
        if spec is None:
            raise SystemExit("動畫定義裡沒有 %r。可用的是：%s"
                             % (aid, ", ".join(specs)))
        png = sheets.get(aid)
        if png is not None:
            out[aid] = slice_sheet(png, spec)
        elif master is not None:
            out[aid] = frames_from_master(master, spec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="把烘焙好的 spritesheet 變成可以在電腦上看的動畫（驗收工具）")
    ap.add_argument("--character", "-c", default="brown_mixed", help="角色 id")
    ap.add_argument("--sheets-dir", default="build/sheets",
                    help="spritesheet 目錄（預設 build/sheets）")
    ap.add_argument("--anim", help="只做這一個動畫，省略則全部")
    ap.add_argument("--anim-file",
                    help="動畫定義檔（預設 specs/animations/<char>.anim.json）。"
                         "多人同時改同一份定義時，各自先寫成片段檔、用這個旗標"
                         "單獨預覽自己的動畫，就不必為了看一眼而動到共用檔")
    ap.add_argument("--scale", type=int, default=6,
                    help="放大倍率，一律 NEAREST（預設 6）")
    ap.add_argument("--out-dir", default="build/preview",
                    help="輸出目錄（預設 build/preview）")
    ap.add_argument("--check", action="store_true",
                    help="只印健檢報告，不產圖")
    ap.add_argument("--from-master", nargs="?", const="AUTO", default=None,
                    help="沒有 spritesheet 時，用 master sprite + 動畫的整數位移"
                         "合成預覽。可指定 PNG 路徑，省略則用 art/approved 的 master")
    ap.add_argument("--contact-cols", type=int,
                    help="接觸表的欄數（預設依動畫數自動決定）")
    ap.add_argument("--contact-scale", type=int,
                    help="接觸表的放大倍率（預設同 --scale）")
    ap.add_argument("--mock-cycles", type=int, default=MOCK_CYCLES,
                    help="實機模擬裡循環型動畫播幾遍（預設 2）")
    ap.add_argument("--no-shadow", action="store_true",
                    help="實機模擬不畫接地陰影")
    ap.add_argument("--no-mock", action="store_true", help="不產生實機模擬")
    ap.add_argument("--no-contact", action="store_true", help="不產生接觸表")
    args = ap.parse_args()

    if args.scale < 1:
        ap.error("--scale 必須 >= 1")

    cid = args.character
    anim_path = resolve_path(args.anim_file) if args.anim_file \
        else resolve_path("specs/animations/%s.anim.json" % cid)
    if not anim_path.exists():
        sys.exit("找不到動畫定義：%s" % anim_path)
    specs = load_anim_specs(anim_path)
    palette = load_palette(resolve_path("specs/palettes/%s.json" % cid))

    sheets_dir = resolve_path(args.sheets_dir)
    out_dir = resolve_path(args.out_dir)

    master_path = None
    if args.from_master is not None:
        master_path = (resolve_path(
            "art/approved/%s/master_stand_r_64px.png" % cid)
            if args.from_master == "AUTO" else resolve_path(args.from_master))

    loaded = collect(cid, sheets_dir, specs, args.anim, master_path)

    wanted = [args.anim] if args.anim else list(specs.keys())
    missing = [a for a in wanted if a not in loaded]

    print("角色 %s ｜ 動畫定義 %d 個 ｜ sheets %s"
          % (cid, len(specs), sheets_dir))
    if args.anim_file:
        # 片段檔的動畫數本來就不齊，一定要說出來，否則「合計 N 格在預算內」
        # 會被誤讀成整隻狗都在預算內
        print("  動畫定義來自 %s（不是預設的 specs/animations/%s.anim.json）"
              % (anim_path, cid))
    if loaded:
        srcs = sorted({v[2].get("layout", "?") for v in loaded.values()})
        print("  影格來源：%s，共 %d 個動畫" % ("/".join(srcs), len(loaded)))
    if missing:
        print("  ⚠️  %d 個動畫沒有 spritesheet：%s"
              % (len(missing), ", ".join(missing[:8])
                 + ("…" if len(missing) > 8 else "")))
        if master_path is None:
            print("      先跑 bake.py，或加 --from-master 用 master sprite "
                  "與整數位移合成暫代預覽。")

    # ---------------- 健檢 ----------------
    if args.check:
        sel = OrderedDict((a, specs[a]) for a in wanted if a in specs)
        # 影格預算是「整隻角色 86 格」，只有在看完整的那份定義時才能下判斷。
        # 指定了 --anim 或 --anim-file 就是只看了一部分，不對總預算表態。
        lines, errors, warns = check_report(
            sel, loaded, palette,
            full_set=(args.anim is None and not args.anim_file))
        print()
        for ln in lines:
            print(ln)
        print("\n錯誤 %d ／ 警告 %d" % (errors, warns))
        sys.exit(1 if errors else 0)

    if not loaded:
        sys.exit("沒有任何影格可以預覽。先跑 bake.py 產生 build/sheets，"
                 "或加 --from-master。")

    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 單一動畫 GIF ----------------
    items = []
    for aid, (frames, durations, info) in loaded.items():
        spec = specs[aid]
        items.append((spec, frames, info))
        big = [scale_nearest(fr, args.scale) for fr in frames]
        res = write_gif(out_dir / ("%s.gif" % aid), big,
                        anim_durations(spec, durations), transparent=True)
        tail = "  +%dms 尾格" % TAIL_HOLD_MS if not spec.loop else "  loop"
        print("  %-18s %d 格  %5dms/格  %5.2fs%s  %d 色  %s"
              % (aid, res["frames"], spec.frame_ms,
                 res["duration_ms"] / 1000.0, tail, res["colors"],
                 res["path"].name))

    # ---------------- 接觸表 ----------------
    if not args.no_contact:
        cs = render_contact_sheet(
            out_dir / "_contact_sheet.png", cid, items,
            args.contact_scale or args.scale, args.contact_cols)
        print("  接觸表  %d×%d（%d 欄 × %d 列）  %s"
              % (cs["size"][0], cs["size"][1], cs["layout"]["cols"],
                 cs["layout"]["rows"], cs["path"].name))

    # ---------------- 實機模擬 ----------------
    if not args.no_mock:
        dm = render_device_mock(out_dir / "_device_mock.gif", items,
                                args.mock_cycles, not args.no_shadow)
        print("  實機模擬 %d×%d  %d 格  %.1fs  %s"
              % (DEVICE_W, DEVICE_H, dm["frames"],
                 dm["duration_ms"] / 1000.0, dm["path"].name))

    print("→ %s" % out_dir)


if __name__ == "__main__":
    main()
