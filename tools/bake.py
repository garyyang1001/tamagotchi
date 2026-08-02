#!/usr/bin/env python3
"""
bake.py — 像素領域的決定性烘焙

這支程式做的事只有三件：**整數像素平移、水平翻轉、疊圖**。
它的唯一價值就是「不做別的」——不重新採樣、不旋轉、不縮放、不做 alpha 混合。

為什麼要這麼嚴
--------------
降採樣比是 21:1。在高解析度「把後腿轉 25 度」在輸出端的全部意義，
是把腿下段的 3 個像素往旁邊挪 2 格——而挪哪幾格是由降採樣格線與旋轉角度的
交互作用決定的，不受我們控制。那就是閃爍。
Dead Cells 的美術總監至今說他們沒找到閃爍的解法，原因就在這裡（docs/05 第三節）。

兩種動畫型別
------------
    frames     AI 一次生成一張含全部影格的圖 -> cutstrip 切開對齊
               -> pixelate 降採樣 -> 這裡串成 spritesheet
    transform  整張 master sprite 做整數位移與水平翻轉（呼吸、轉身、跳躍）

**曾經有第三種 `rig`**：把角色拆成七個像素領域圖層，逐格做整數平移再疊起來。
那條路線於 2026-08-01 移除——單段式部件繞肩關節旋轉會讓腳掌畫弧離開地面，
實測 walk 與 idle_breathe 產出完全相同的影格。程式碼於 2026-08-02 一併清掉：
四個角色定稿後沒有任何動畫是 rig 型，留著只會讓人以為那條路還在。
還原點 git commit `04f5ed2`。**不要再走回去**（CLAUDE.md 規則 4）。

硬性檢查（違反就中止，不會默默通過）
------------------------------------
1. 每一格輸出只能用 specs/palettes/<character>.json 裡的顏色
2. 所有位移必須是 JSON 整數字面值，小數（含 2.0）一律報錯
3. alpha 只能是 0 或 255（4bpp 索引色沒有半透明）
4. 同一個動畫的所有影格尺寸一致

用法
----
    .venv/bin/python tools/bake.py --character brown_mixed
"""

import argparse
import hashlib
import json
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cell import cell_for   # noqa: E402

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")


ROOT = Path(__file__).resolve().parent.parent

# 畫布尺寸。1344x1176 的來源除以 21 得到這個數字——選 1344/1176 就是為了讓
# 降採樣比是整數，每個來源像素都確定性地映射到一個目標格。
FRAME_W_DEFAULT = 64
FRAME_H_DEFAULT = 56
# 影格格的權威來源是角色規格檔的 render.sprite_cell（見 tools/cell.py）。
# 上面兩個常數只在完全沒有規格檔時才用得到。

# transform 型動畫的來源是整張 master sprite，在內部當成單一圖層處理，
# 好讓兩種型別共用同一條合成管線。
MASTER_LAYER = "__master__"

TRACK_IGNORED_KEYS = ("f", "desc", "note", "_comment")


class SkipAnimation(Exception):
    """這個動畫該被跳過，不是錯誤。"""


class BakeError(Exception):
    """資料違反烘焙的契約。一律中止，不做任何猜測性的修補。"""


# --------------------------------------------------------------------------
# 型別檢查：只認整數
# --------------------------------------------------------------------------

def as_int(value, where):
    """取整數像素位移。小數一律報錯——包含 2.0 這種數值上是整數的寫法。

    為什麼連 2.0 都要擋：這支程式的全部價值就是「保證沒有重新採樣」。
    只要位移欄位可以是浮點數，就有人會寫 2.5，而 2.5 會在下游被某個
    round() 悄悄吸掉，變成看不出來的格線漂移。在 JSON 裡就要求整數字面值，
    是唯一能在最上游擋住的地方。
    """
    if isinstance(value, bool):
        raise BakeError("%s 是布林值 %r，這裡要的是整數像素" % (where, value))
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise BakeError(
            "%s = %r 是小數。烘焙只做整數像素平移，非整數位移會破壞像素網格；"
            "數值上是整數也請寫成整數字面值（%d 而不是 %r）"
            % (where, value, int(value), value))
    raise BakeError("%s = %r 不是數字" % (where, value))


def as_flip(value, where):
    """水平翻轉旗標。接受 0/1 與 true/false。"""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int) and value in (0, 1):
        return value
    raise BakeError("%s = %r 不是有效的 flip（要 0 / 1 / true / false）" % (where, value))


def as_bool(value, where):
    if isinstance(value, bool):
        return value
    raise BakeError("%s = %r 不是布林值" % (where, value))


def as_positive_int(value, where):
    v = as_int(value, where)
    if v <= 0:
        raise BakeError("%s = %d 必須大於 0" % (where, v))
    return v


def parse_hex(s):
    s = s.lstrip("#")
    if len(s) != 6:
        raise BakeError("顏色 %r 不是 RRGGBB 格式" % s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def hex_of(rgb):
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)


# --------------------------------------------------------------------------
# 讀檔
# --------------------------------------------------------------------------

def load_palette(path):
    """回傳 (允許的 RGB 集合, role -> RGB)。透明色不算在允許集合裡——
    透明是靠 alpha=0 表示的，不是靠某個 RGB 值。"""
    data = json.loads(path.read_text())
    entries = data["colors"] if isinstance(data, dict) else data
    allowed, roles = set(), {}
    for c in entries:
        if isinstance(c, dict):
            if c.get("transparent"):
                continue
            rgb = parse_hex(c["hex"])
            allowed.add(rgb)
            roles[c["role"]] = rgb
        else:
            allowed.add(parse_hex(c))
    if not allowed:
        raise BakeError("調色盤 %s 沒有任何不透明色" % path)
    return allowed, roles


def load_rgba(path, where):
    """讀成 RGBA uint8 陣列，並強制 alpha 是二值。

    半透明像素在 4bpp 索引色裡無法表示，而且會逼合成階段做 alpha 混合，
    混合出來的中間色不在調色盤裡——那是絕對不能發生的事。
    """
    arr = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
    alpha = arr[..., 3]
    bad = np.unique(alpha[(alpha != 0) & (alpha != 255)])
    if bad.size:
        raise BakeError(
            "%s（%s）有 %d 個半透明像素（alpha=%s...）。"
            "烘焙不做 alpha 混合，混合會產生調色盤外的中間色"
            % (where, path, int(((alpha != 0) & (alpha != 255)).sum()),
               ", ".join(str(int(v)) for v in bad[:4])))
    # 透明處一律清成 (0,0,0,0)，讓輸出可逐位元重現
    arr[alpha == 0] = 0
    return arr


def expand_track(keys, frames, spec, where):
    """把關鍵影格展開成逐格狀態。**步進取值，絕不補間。**

    像素動畫的力量來自明確的姿勢對比。補間會產生「介於兩個姿勢之間」的
    半格位移，在 64px 上那既讀不出來也一定要 round，round 就是格線漂移。

    每個欄位各自延續自己最後一次出現的值（per-field step-hold）。
    所以 {"f":1,"show":false} 不需要重複寫 dx / dy。

    spec: {欄位名: (解析函式, 預設值)}
    """
    if keys is None:
        keys = []
    if not isinstance(keys, list):
        raise BakeError("%s 必須是關鍵影格陣列" % where)

    seen = {}
    for k in keys:
        if not isinstance(k, dict):
            raise BakeError("%s 的關鍵影格 %r 不是物件" % (where, k))
        f = k.get("f")
        if not isinstance(f, int) or isinstance(f, bool):
            raise BakeError("%s 有關鍵影格缺少整數 f：%r" % (where, k))
        if not (0 <= f < frames):
            raise BakeError("%s 的關鍵影格 f=%d 落在 0..%d 之外"
                            % (where, f, frames - 1))
        if f in seen:
            raise BakeError("%s 有重複的關鍵影格 f=%d" % (where, f))
        seen[f] = k

    state = dict((name, default) for name, (_, default) in spec.items())
    out = []
    for f in range(frames):
        k = seen.get(f)
        if k is not None:
            for key, value in k.items():
                if key in TRACK_IGNORED_KEYS:
                    continue
                if key not in spec:
                    raise BakeError("%s f=%d 有未知欄位 '%s'，可用的是 %s"
                                    % (where, f, key, ", ".join(sorted(spec))))
                state[key] = spec[key][0](value, "%s f=%d 的 %s" % (where, f, key))
        out.append(dict(state))
    return out


# --------------------------------------------------------------------------
# 合成：整數平移 + 疊圖，沒有第三件事
# --------------------------------------------------------------------------

def blit(canvas, src, dx, dy):
    """把 src 整數平移 (dx, dy) 後蓋到 canvas 上。回傳被畫布邊界裁掉的不透明像素數。

    二值 alpha 直接覆蓋，**不做 alpha 混合**——混合會產生調色盤外的中間色。
    src 與 canvas 都不會被重新採樣，只是索引位移，所以結果逐位元決定。
    """
    ch, cw = canvas.shape[:2]
    sh, sw = src.shape[:2]

    total_opaque = int((src[..., 3] > 0).sum())

    sx0, sx1 = max(0, -dx), min(sw, cw - dx)
    sy0, sy1 = max(0, -dy), min(sh, ch - dy)
    if sx0 >= sx1 or sy0 >= sy1:
        return total_opaque

    sub = src[sy0:sy1, sx0:sx1]
    m = sub[..., 3] > 0
    canvas[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx][m] = sub[m]
    return total_opaque - int(m.sum())


def compose_frame(frame_w, frame_h, draw_list, flip, gx, gy):
    """合成一格。

    順序固定為：疊圖 -> 水平翻轉 -> 整體位移。

    先翻轉再位移，x 才永遠是「往畫面右邊移動」。翻轉是以畫布中線為軸的
    整體鏡射，所以圖層自己的 dx 也會跟著鏡射——那正是我們要的：
    「往右甩尾」在狗轉向後自然變成「往左甩尾」。

    回傳 (frame, 被裁掉的不透明像素數)。
    """
    canvas = np.zeros((frame_h, frame_w, 4), dtype=np.uint8)
    clipped = 0
    for _name, img, dx, dy in draw_list:
        if img is None:
            continue
        clipped += blit(canvas, img, dx, dy)

    if flip:
        canvas = np.ascontiguousarray(canvas[:, ::-1, :])

    if gx or gy:
        moved = np.zeros((frame_h, frame_w, 4), dtype=np.uint8)
        clipped += blit(moved, canvas, gx, gy)
        canvas = moved

    return canvas, clipped


def check_frame_palette(frame, allowed, where):
    """硬性檢查：每一格只能用調色盤裡的顏色。"""
    alpha = frame[..., 3]
    if not np.isin(alpha, (0, 255)).all():
        raise BakeError("%s 出現半透明像素" % where)
    opaque = alpha > 0
    if not opaque.any():
        return 0
    used = set(tuple(int(v) for v in c) for c in frame[..., :3][opaque])
    stray = sorted(used - allowed)
    if stray:
        ys, xs = np.where(opaque)
        first = {}
        for y, x in zip(ys, xs):
            c = tuple(int(v) for v in frame[y, x, :3])
            if c in stray and c not in first:
                first[c] = (int(x), int(y))
        raise BakeError(
            "%s 用了調色盤外的 %d 個顏色：%s。"
            "所有影格必須共用同一組固定調色盤，否則播放時整隻角色會色彩閃爍"
            % (where, len(stray),
               ", ".join("%s@(%d,%d)" % (hex_of(c), first[c][0], first[c][1])
                         for c in stray[:6])))
    return len(used)


# --------------------------------------------------------------------------
# 單一動畫的烘焙
# --------------------------------------------------------------------------

GLOBAL_TRACK_SPEC_BASE = {
    "x": (as_int, 0),
    "y": (as_int, 0),
    "flip": (as_flip, 0),
}


def resolve_base_ms(anim, defaults, where):
    """frame_ms 是權威欄位，fps 只是給人看的參考。

    理由見 specs/animations/*.anim.json 的 timing_note：真實犬隻休息呼吸
    10-30 次/分，4 格的循環要 2-6 秒，換算成 fps 是 0.67-2.0 的小數，
    整數 fps 表達不了。
    """
    if "frame_ms" in anim:
        return as_positive_int(anim["frame_ms"], "%s 的 frame_ms" % where)
    fps = anim.get("fps", defaults.get("fps"))
    if fps is None:
        raise BakeError("%s 既沒有 frame_ms 也沒有 fps" % where)
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise BakeError("%s 的 fps=%r 無效" % (where, fps))
    return int(round(1000.0 / float(fps)))


def bake_animation(anim, defaults, master, allowed, frame_w, frame_h):
    """烘焙一個動畫，回傳 (frames 陣列, meta 字典)。"""
    aid = anim.get("id")
    if not aid:
        raise BakeError("有動畫沒有 id")
    where = "動畫 %s" % aid

    if anim.get("status") == "PENDING_REGEN":
        # 半完成的專案要能繼續建置：跳過而不是報錯。
        raise SkipAnimation("標記為待重生")

    atype = anim.get("type", defaults.get("type", "transform"))
    if atype == "rig":
        raise BakeError("%s 是 rig 型。部件旋轉路線已於 2026-08-01 移除"
                        "（腳掌會畫弧離開地面），請改用 frames 型。"
                        "還原點 git commit 04f5ed2。" % where)
    if atype not in ("transform", "frames"):
        raise BakeError("%s 的 type='%s' 不認得（transform / frames）" % (where, atype))

    frames = as_positive_int(anim.get("frames"), "%s 的 frames" % where)
    base_ms = resolve_base_ms(anim, defaults, where)
    loop = bool(anim.get("loop", defaults.get("loop", False)))

    global_spec = dict(GLOBAL_TRACK_SPEC_BASE)
    global_spec["ms"] = (as_positive_int, base_ms)
    if "flip" in anim:
        global_spec["flip"] = (as_flip, as_flip(anim["flip"], "%s 的 flip" % where))
    gstates = expand_track(anim.get("track"), frames, global_spec, "%s 的 track" % where)

    # screen_offset：繪製時的偏移，**不烘進影格**。
    #
    # 為什麼要有這個：影格畫布是 64×56，master 是 64×54 底部對齊，頭上只剩 2 列。
    # happy 原本想跳 7 px，第 1-3 格共 141 px 的頭頂與耳尖被切掉。
    # 但角色跳起來本來就是在螢幕空間移動，不需要更高的影格——
    # 把位移交給渲染層，影格保持乾淨，跳多高都不會被切。
    #
    # 與 track 的 x/y 的差別：track 的位移會改變影格內的像素，
    # screen_offset 不會，它只影響「這一格畫在螢幕的哪裡」。
    screen = expand_track(anim.get("screen_offset"), frames, global_spec,
                          "%s 的 screen_offset" % where) if anim.get("screen_offset") else None

    # ---- 每格要畫哪些圖層 -------------------------------------------------
    if atype == "frames":
        # 影格已經是完成品：AI 一次生成一張含全部影格的圖，
        # 由 cutstrip.py 切開對齊、pixelate.py 降採樣，這裡只負責串成 spritesheet。
        #
        # 這條路線取代了 rig 型。實測部件旋轉做走路會讓腳掌畫弧離開地面
        # （整條腿繞肩關節轉，真實的腿是在膝肘彎曲、腳掌貼地推進），
        # 而 AI 一次畫四格的結果腳掌是貼地的。見 docs/05。
        misplaced = [k for k in ("layers", "pose", "pose_track", "track") if k in anim]
        if misplaced:
            raise BakeError(
                "%s 是 frames 型卻有 %s。影格已經是完成品，這些欄位不會有作用"
                % (where, "、".join(misplaced)))
        src = anim.get("frames_dir")
        if not src:
            raise BakeError("%s 是 frames 型但沒有 frames_dir" % where)
        fdir = pathlib.Path(src)
        files = sorted(fdir.glob("*_f*_64px.png"))
        if anim.get("reverse"):
            # 反向播放：stand_up 就是倒著播的 sit_down。
            # Digimon V-Pet 用 12 張 sprite 撐出 8 種行為就是靠這招——
            # 省一次生成，而且保證兩個方向完全對稱。
            files = list(reversed(files))
        if len(files) != frames:
            raise BakeError("%s 宣告 %d 格，但 %s 只有 %d 個影格檔"
                            % (where, frames, src, len(files)))
        per_frame_layers = []
        for f in files:
            im = load_rgba(f, "%s 的影格 %s" % (where, f.name))
            h, w = im.shape[:2]
            if (w, h) != (frame_w, frame_h):
                # 尺寸不符不能放過：blit 會把它貼在左上角，角色整個偏掉，
                # 而且畫面上看起來只是「動畫抖了一下」，很難回推原因。
                # 正確的來源是 pixelate.py --width/--height --no-crop，
                # 那組參數保證每一格都是完整畫布。
                raise BakeError(
                    "%s 的影格 %s 是 %dx%d，畫布是 %dx%d。"
                    "影格必須是完整畫布——請確認 pixelate.py 有加 --no-crop"
                    % (where, f.name, w, h, frame_w, frame_h))
            per_frame_layers.append([(f.stem, im, 0, 0)])
        layer_names = [f.stem for f in files]
    elif atype == "transform":
        misplaced = [k for k in ("layers", "pose", "pose_track") if k in anim]
        if misplaced:
            raise BakeError(
                "%s 是 transform 型卻有 %s。這幾個欄位屬於已移除的部件旋轉路線"
                "（rig 型），留著會讓人以為部件真的動了——請刪掉它們。"
                % (where, "、".join(misplaced)))
        if master is None:
            raise BakeError("%s 是 transform 型，但沒有載入 master sprite" % where)
        # 整隻 sprite 當成單一圖層，共用同一條合成管線
        per_frame_layers = [[(MASTER_LAYER, master, 0, 0)] for _ in range(frames)]
        layer_names = [MASTER_LAYER]

    # ---- 逐格合成 ---------------------------------------------------------
    out_frames, per_frame_clip, colours = [], [], set()
    for f in range(frames):
        g = gstates[f]
        frame, clipped = compose_frame(frame_w, frame_h, per_frame_layers[f],
                                       g["flip"], g["x"], g["y"])
        check_frame_palette(frame, allowed, "%s 第 %d 格" % (where, f))
        op = frame[..., 3] > 0
        if op.any():
            colours |= set(tuple(int(v) for v in c) for c in frame[..., :3][op])
        per_frame_clip.append(clipped)
        out_frames.append(frame)
    clipped_total = sum(per_frame_clip)

    # 硬性檢查：同一個動畫的所有影格尺寸一致
    sizes = set(fr.shape for fr in out_frames)
    if len(sizes) != 1:
        raise BakeError("%s 的影格尺寸不一致：%s" % (where, sorted(sizes)))

    meta = {
        "id": aid,
        "type": atype,
        "tier": anim.get("tier"),
        "status": anim.get("status"),
        "loop": loop,
        "frame_count": frames,
        "frame_w": frame_w,
        "frame_h": frame_h,
        "frame_ms": base_ms,
        "total_ms": sum(g["ms"] for g in gstates),
        "layers": layer_names,
        "colors": len(colours),
        "clipped_px": clipped_total,
        "frames": [
            {
                "index": f,
                "x": f * frame_w,
                "y": 0,
                "w": frame_w,
                "h": frame_h,
                "ms": gstates[f]["ms"],
                "flip": gstates[f]["flip"],
                "dx": gstates[f]["x"],
                "dy": gstates[f]["y"],
                "screen_dx": screen[f]["x"] if screen else 0,
                "screen_dy": screen[f]["y"] if screen else 0,
                "clipped_px": per_frame_clip[f],
            }
            for f in range(frames)
        ],
    }
    return out_frames, meta


# --------------------------------------------------------------------------
# 輸出
# --------------------------------------------------------------------------

def write_sheet(frames, out_path, scale, preview_path):
    """水平排列成 spritesheet。"""
    h, w = frames[0].shape[:2]
    sheet = np.zeros((h, w * len(frames), 4), dtype=np.uint8)
    for i, fr in enumerate(frames):
        sheet[:, i * w:(i + 1) * w] = fr
    img = Image.fromarray(sheet)          # HxWx4 uint8 -> Pillow 自動判定 RGBA
    img.save(out_path)
    if scale and preview_path is not None:
        # 預覽一律用最近鄰放大。任何其他插值都會產生調色盤外的顏色。
        img.resize((sheet.shape[1] * scale, h * scale),
                   Image.Resampling.NEAREST).save(preview_path)
    return sheet


def dump_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def sha12(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def rel_to_root(path):
    """路徑一律相對於專案根目錄記錄，這樣不同機器產出的 atlas 也逐位元相同。"""
    p = Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def place_master(master_raw, frame_w, frame_h, log):
    """把 master sprite 放進畫布。水平置中、底部對齊（腳踩在同一條地平線上）。

    master 通常是 pixelate.py 裁到內容範圍的產物（實測 64x54），
    比 64x56 的畫布小。放置規則必須是決定性的而且只有一種，否則
    transform 型與 frames 型的影格會對不齊。
    """
    h, w = master_raw.shape[:2]
    if w > frame_w or h > frame_h:
        raise BakeError("master sprite 是 %dx%d，比畫布 %dx%d 大，"
                        "烘焙不做縮放" % (w, h, frame_w, frame_h))
    canvas = np.zeros((frame_h, frame_w, 4), dtype=np.uint8)
    ox, oy = (frame_w - w) // 2, frame_h - h
    blit(canvas, master_raw, ox, oy)
    if (ox, oy) != (0, 0):
        log("  master %dx%d 置入 %dx%d 畫布於 (%d, %d)（水平置中、底部對齊）"
            % (w, h, frame_w, frame_h, ox, oy))
    return canvas, (ox, oy)


def run(character, anim_path, out_dir,
        palette_path=None, master_path=None,
        frame_w=None, frame_h=None,
        only=None, scale=8, strict_clip=False, keep_going=False,
        quiet=False):
    """烘焙全部（或指定的）動畫。回傳摘要字典。ok=False 代表有動畫失敗。"""
    log = (lambda *_a: None) if quiet else (lambda m: print(m))

    if frame_w is None or frame_h is None:
        cw, ch = cell_for(character)
        frame_w = frame_w or cw
        frame_h = frame_h or ch

    anim_path = Path(anim_path)
    out_dir = Path(out_dir)
    palette_path = Path(palette_path) if palette_path else \
        ROOT / ("specs/palettes/%s.json" % character)
    master_path = Path(master_path) if master_path else \
        ROOT / ("art/approved/%s/master_stand_r_64px.png" % character)

    if not anim_path.exists():
        raise BakeError("找不到動畫定義：%s" % anim_path)
    if not palette_path.exists():
        raise BakeError("找不到調色盤：%s" % palette_path)

    allowed, _roles = load_palette(palette_path)
    doc = json.loads(anim_path.read_text())
    defaults = doc.get("defaults", {})
    anims = doc.get("animations", [])
    if not anims:
        raise BakeError("%s 沒有任何動畫" % anim_path)

    if only:
        unknown = sorted(set(only) - set(a.get("id") for a in anims))
        if unknown:
            raise BakeError("--only 指到不存在的動畫：%s" % ", ".join(unknown))

    log("角色 %s  畫布 %dx%d  調色盤 %d 色（%s）"
        % (character, frame_w, frame_h, len(allowed), rel_to_root(palette_path)))

    # 只有 transform 型需要 master；frames 型自帶影格
    needs_master = any(a.get("type", defaults.get("type", "transform")) == "transform"
                       for a in anims if not only or a.get("id") in only)
    master, master_off = None, None
    if needs_master:
        if not master_path.exists():
            raise BakeError("transform 型動畫需要 master sprite，但找不到 %s" % master_path)
        master, master_off = place_master(
            load_rgba(master_path, "master sprite"), frame_w, frame_h, log)
        check_frame_palette(master, allowed, "master sprite %s" % master_path.name)

    out_dir.mkdir(parents=True, exist_ok=True)
    entries, failures, skipped, total_frames = [], [], [], 0

    for anim in anims:
        aid = anim.get("id", "<無 id>")
        if only and aid not in only:
            continue
        try:
            frames, meta = bake_animation(anim, defaults, master,
                                          allowed, frame_w, frame_h)
            if strict_clip and meta["clipped_px"]:
                raise BakeError("動畫 %s 有 %d 個不透明像素被畫布邊界裁掉"
                                % (aid, meta["clipped_px"]))

            sheet_path = out_dir / ("%s_%s.png" % (character, aid))
            atlas_path = out_dir / ("%s_%s.json" % (character, aid))
            preview_path = out_dir / ("%s_%s_x%d.png" % (character, aid, scale)) \
                if scale else None
            write_sheet(frames, sheet_path, scale, preview_path)

            # 逐格的 frames 陣列最長，放在最後，讓人打開檔案先看到摘要
            meta_out = {"character_id": character}
            for key, value in meta.items():
                if key == "frames":
                    meta_out["sheet"] = sheet_path.name
                    meta_out["sheet_size"] = [frame_w * len(frames), frame_h]
                meta_out[key] = value
            dump_json(atlas_path, meta_out)

            total_frames += meta["frame_count"]
            entries.append({
                "id": aid,
                "type": meta["type"],
                "tier": meta["tier"],
                "status": meta["status"],
                "loop": meta["loop"],
                "frame_count": meta["frame_count"],
                "frame_ms": meta["frame_ms"],
                "total_ms": meta["total_ms"],
                "colors": meta["colors"],
                "clipped_px": meta["clipped_px"],
                "sheet": sheet_path.name,
                "atlas": atlas_path.name,
                "sha256_12": sha12(sheet_path),
            })
            flag = ""
            if meta["clipped_px"]:
                bad = ", ".join(str(fr["index"]) for fr in meta["frames"]
                                if fr["clipped_px"])
                flag = "   [警告] %d px 被畫布裁掉（第 %s 格）" % (meta["clipped_px"], bad)
            log("  [OK] %-16s %s  %d 格 x %dms  %d 色%s"
                % (aid, meta["type"], meta["frame_count"], meta["frame_ms"],
                   meta["colors"], flag))
        except SkipAnimation as e:
            skipped.append({"id": aid, "reason": str(e)})
            log("  [跳過] %-16s %s" % (aid, e))
        except BakeError as e:
            # 不在第一個錯誤就死掉：一次把所有問題列出來，改一輪就好。
            # 但結束時一定是非零離開碼，而且預設不寫索引。
            failures.append({"id": aid, "error": str(e)})
            log("  [失敗] %-16s %s" % (aid, e))

    index = {
        "character_id": character,
        "format_version": 1,
        "generator": "tools/bake.py",
        "frame_size": [frame_w, frame_h],
        "sources": {
            "anim": rel_to_root(anim_path),
            "palette": rel_to_root(palette_path),
            "master": rel_to_root(master_path) if needs_master else None,
            "master_offset": list(master_off) if master_off else None,
        },
        "animation_count": len(entries),
        "total_frames": total_frames,
        "animations": entries,
        "failed": failures,
        "skipped": skipped,
    }

    ok = not failures
    index_path = out_dir / ("%s_atlas.json" % character)
    if ok or keep_going:
        dump_json(index_path, index)
        log("  索引 %s（%d 個動畫，%d 格）"
            % (rel_to_root(index_path), len(entries), total_frames))
    else:
        # 有動畫失敗就不寫索引：下游 pack.py 會立刻找不到檔案而中止，
        # 不會拿到一份看起來完整、其實缺格的資產包。
        # 上一次成功留下的舊索引也要一併刪掉，否則「索引存在」就不再等於
        # 「上一次烘焙全部成功」——那比沒有索引更危險。
        if index_path.exists():
            index_path.unlink()
            log("  [錯誤] 已刪除上一次留下的舊索引 %s" % index_path.name)
        log("  [錯誤] %d 個動畫失敗，未寫出索引 %s（要保留請加 --keep-going）"
            % (len(failures), index_path.name))

    return {
        "ok": ok,
        "index": index,
        "index_path": index_path if (ok or keep_going) else None,
        "out_dir": out_dir,
        "failures": failures,
        "skipped": skipped,
        "total_frames": total_frames,
    }


def main():
    ap = argparse.ArgumentParser(
        description="把動畫定義烘焙成 spritesheet（純整數運算）")
    ap.add_argument("--character", "-c", required=True)
    ap.add_argument("--anim", help="動畫定義（預設 specs/animations/<char>.anim.json）")
    ap.add_argument("--out-dir", default="build/sheets")
    ap.add_argument("--palette", help="預設 specs/palettes/<char>.json")
    ap.add_argument("--master", help="transform 型的來源 sprite，"
                                     "預設 art/approved/<char>/master_stand_r_64px.png")
    ap.add_argument("--frame-w", type=int,
                    help="省略則讀 specs/characters/<char>.json 的 render.sprite_cell")
    ap.add_argument("--frame-h", type=int)
    ap.add_argument("--only", action="append", help="只烘焙指定動畫，可重複")
    ap.add_argument("--scale", type=int, default=8, help="另存放大預覽（0 = 不存）")
    ap.add_argument("--strict-clip", action="store_true",
                    help="有像素被畫布邊界裁掉就視為錯誤")
    ap.add_argument("--keep-going", action="store_true",
                    help="有動畫失敗仍寫出索引（索引內含 failed 清單）")
    args = ap.parse_args()

    anim = args.anim or (ROOT / ("specs/animations/%s.anim.json" % args.character))

    try:
        result = run(
            character=args.character,
            anim_path=anim,
            out_dir=args.out_dir,
            palette_path=args.palette,
            master_path=args.master,
            frame_w=args.frame_w,
            frame_h=args.frame_h,
            only=args.only,
            scale=args.scale,
            strict_clip=args.strict_clip,
            keep_going=args.keep_going,
        )
    except BakeError as e:
        sys.exit("[錯誤] %s" % e)

    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
