#!/usr/bin/env python3
"""
test_pack.py — pack.py 的自我測試

驗證資產包的五個契約：

    1. 可還原      bin 裡的**每一張** blob 解回索引平面，和來源 PNG 逐像素比對。
                  不抽樣，351 格全部比、1,486,925 個像素全部比。
    2. 可重現      同樣的輸入跑兩次，每一個 byte 都相同（sha256）
    3. 可直接定址   header / asset / frame 的每個欄位都自然對齊，
                  沒有隱含 padding，spi_flash_mmap 之後可以當 C 陣列用
    4. 雜湊唯一     FNV-1a 32 撞到必須中止。這裡用一組**真的會撞的**名字測，
                  不是用 mock（`"d/_h"` 與 `"x8ea"` 都是 0x6FEDED3B）
    5. 日夜同一張   room 的 blob 解出來要同時等於 room_day 與 room_night 的索引平面

**解碼器是這裡自己寫的，和 pack.py 的編碼器沒有共用任何程式碼。**
「來源 PNG → 索引平面」也另外寫了一份（`png_to_plane`）。
共用實作的話，編碼器把索引 3 和 4 寫反這種錯會兩邊一起錯、測試照樣通過。

    .venv/bin/python tools/test_pack.py
"""

import json
import shutil
import struct
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
SHEET_DIR = ROOT / "build/sheets"
SCENE_DIR = ROOT / "art/approved/_scene"


# --------------------------------------------------------------------------
# 獨立的解碼器
# --------------------------------------------------------------------------

class DecodeError(Exception):
    pass


def rle_decode(blob, w, h, strict=True):
    """docs/04 D 節的逃脫版 RLE → h×w 的索引平面。

    刻意寫成逐 token 的直白迴圈（不用 numpy），因為它同時是「韌體端要怎麼寫」
    的參考實作——ESP32 上就是這樣一個 while 迴圈。

    strict=True 會順便檢查兩件編碼器該保證的事：
      * run 不跨列（跨了就是格式壞了，逐列索引與局部重繪都會失效）
      * 逃脫 token 的 count 一定 ≥ 8（< 8 該用單位元組版，多寫一個 byte）
    """
    plane = np.zeros((h, w), dtype=np.uint8)
    i = 0
    n = len(blob)
    row = col = 0
    while i < n:
        if row >= h:
            raise DecodeError("token 比 %d 列還多" % h)
        b = blob[i]
        i += 1
        if b & 0x80:
            idx = b & 0x0F
            cnt = (b >> 4) & 0x07
            if cnt == 0:                      # 1000iiii + <count>
                if i >= n:
                    raise DecodeError("逃脫 token 少了 count 位元組")
                cnt = blob[i]
                i += 1
                if strict and cnt < 8:
                    raise DecodeError("逃脫 token 的 count=%d < 8，不是最短編碼"
                                      % cnt)
            if col + cnt > w:
                raise DecodeError("索引 run 跨列（col=%d cnt=%d w=%d）"
                                  % (col, cnt, w))
            plane[row, col:col + cnt] = idx
        else:                                  # 0nnnnnnn
            cnt = b & 0x7F
            if cnt == 0:
                raise DecodeError("長度 0 的透明 token")
            if col + cnt > w:
                raise DecodeError("透明 run 跨列（col=%d cnt=%d w=%d）"
                                  % (col, cnt, w))
            # plane 預設就是 0，不必寫
        col += cnt
        if col == w:
            row += 1
            col = 0
    if row != h or col != 0:
        raise DecodeError("解完只填到第 %d 列第 %d 行，應該是 %d 列" % (row, col, h))
    return plane


def png_to_plane(path, palette_json):
    """來源 PNG → 索引平面。獨立於 pack.index_plane 的第二份實作。"""
    entries = json.loads(Path(palette_json).read_text(encoding="utf-8"))["colors"]
    lut = {}
    for c in entries:
        if c.get("transparent"):
            continue
        s = c["hex"].lstrip("#")
        key = (int(s[0:2], 16) << 16) | (int(s[2:4], 16) << 8) | int(s[4:6], 16)
        lut.setdefault(key, int(c["index"]))
    arr = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
    h, w = arr.shape[:2]
    r, g, b, a = (arr[..., k].astype(np.uint32) for k in range(4))
    key = (r << 16) | (g << 8) | b
    uniq, inv = np.unique(key, return_inverse=True)
    table = np.zeros(uniq.shape[0], dtype=np.uint8)
    for i, k in enumerate(uniq):
        table[i] = lut.get(int(k), 0)
    plane = table[inv].reshape(h, w)
    plane[a == 0] = 0
    return plane


# --------------------------------------------------------------------------
# 讀回 bin
# --------------------------------------------------------------------------

class Bin:
    """把 assets.bin 讀成 Python 物件。韌體是 mmap 之後直接指標存取，
    這裡只是把同一份版面用 struct 拆開。"""

    def __init__(self, raw):
        self.raw = raw
        (magic, ver, hbytes, na, nf, npal, flags,
         a_off, f_off, p_off, b_off, b_bytes, total, crc, rsv) = \
            struct.unpack_from(pack.HEADER_FMT, raw, 0)
        self.magic, self.version, self.header_bytes = magic, ver, hbytes
        self.asset_count, self.frame_count, self.palette_count = na, nf, npal
        self.flags = flags
        self.asset_offset, self.frame_offset = a_off, f_off
        self.palette_offset, self.blob_offset = p_off, b_off
        self.blob_bytes, self.total_bytes, self.crc32, self.reserved = \
            b_bytes, total, crc, rsv

        self.assets = []
        for i in range(na):
            (h, fi, fc, w, hh, pd, pn, t, af, r) = struct.unpack_from(
                pack.ASSET_FMT, raw, a_off + i * pack.ASSET_BYTES)
            self.assets.append(dict(
                name_hash=h, frame_index=fi, frame_count=fc, w=w, h=hh,
                palette_day=pd, palette_night=pn, type=t, flags=af, reserved=r))
        self.frames = []
        for i in range(nf):
            (bo, bb, ms, dx, dy, flip, r0, r1, r2) = struct.unpack_from(
                pack.FRAME_FMT, raw, f_off + i * pack.FRAME_BYTES)
            self.frames.append(dict(
                blob_offset=bo, blob_bytes=bb, ms=ms, screen_dx=dx,
                screen_dy=dy, flip=flip, reserved=(r0, r1, r2)))
        self.palettes = [
            struct.unpack_from("<16H", raw, p_off + i * pack.PALETTE_BYTES)
            for i in range(npal)]

    def blob(self, frame):
        return self.raw[frame["blob_offset"]:
                        frame["blob_offset"] + frame["blob_bytes"]]


# --------------------------------------------------------------------------
# 迷你測試框架（沿用 test_bake.py 的形狀）
# --------------------------------------------------------------------------

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def expect_error(fn, needle, label):
    try:
        fn()
    except pack.PackError as e:
        assert needle in str(e), "%s：錯誤訊息沒提到 %r，實際是 %s" % (label, needle, e)
        return str(e)
    raise AssertionError("%s：預期要報錯，結果沒有" % label)


# 整個測試共用一次打包結果，不要每項都重跑（打包一次約 0.5 秒）
CTX = {}


def ctx():
    return CTX


# --------------------------------------------------------------------------
# 1. RLE：合成資料的往返
# --------------------------------------------------------------------------

@case
def test_RLE_往返_合成邊界情況(tmp):
    """挑的都是 token 版面的邊界：7/8（單位元組 vs 逃脫）、127/128（透明上限）、
    255/256（逃脫 count 上限）、以及每一列都不同的最壞情況。"""
    rng = np.random.default_rng(20260802)
    planes = {
        "全透明": np.zeros((4, 300), np.uint8),
        "全單色": np.full((4, 300), 7, np.uint8),
        "run7": np.tile(np.repeat(np.arange(1, 16, dtype=np.uint8), 7), (3, 1)),
        "run8": np.tile(np.repeat(np.arange(1, 16, dtype=np.uint8), 8), (3, 1)),
        "run255": np.full((2, 255), 5, np.uint8),
        "run256": np.full((2, 256), 5, np.uint8),
        "透明127": np.pad(np.zeros((2, 127), np.uint8), ((0, 0), (0, 1)),
                          constant_values=3),
        "透明128": np.zeros((2, 128), np.uint8),
        "最壞_逐像素換色": rng.integers(0, 16, (8, 64), dtype=np.uint8),
        "隨機_有透明": (rng.integers(0, 16, (56, 64)) *
                        (rng.random((56, 64)) > 0.6)).astype(np.uint8),
        "單像素寬": rng.integers(0, 16, (20, 1), dtype=np.uint8),
    }
    worst = 0
    for name, p in planes.items():
        blob = pack.rle_encode(p)
        back = rle_decode(blob, p.shape[1], p.shape[0])
        assert np.array_equal(back, p), "%s 往返不相同" % name
        worst = max(worst, len(blob) / p.size)
    return "11 種版面全部往返相同，最壞膨脹 %.2f B/px" % worst


@case
def test_RLE_是最短編碼(tmp):
    """同樣的平面，不可以有比編碼器更短的合法編碼。

    檢查的是兩個實際會犯的錯：把 8 個同色像素拆成 7+1（多一個 byte）、
    以及逃脫 token 用在 < 8 的長度上。解碼器的 strict 模式在守第二條，
    這裡守第一條：相鄰的兩個 token 不可以是同一個索引。
    """
    rng = np.random.default_rng(7)
    p = rng.integers(0, 16, (30, 200), dtype=np.uint8)
    p[10:20, 30:180] = 4                       # 一段很長的同色，逼出逃脫 token
    blob = pack.rle_encode(p)
    rle_decode(blob, p.shape[1], p.shape[0])   # strict：會擋 count < 8

    i = 0
    prev = None
    merges = 0
    col = 0
    while i < len(blob):
        b = blob[i]
        i += 1
        if b & 0x80:
            idx, cnt = b & 0x0F, (b >> 4) & 0x07
            if cnt == 0:
                cnt = blob[i]
                i += 1
        else:
            idx, cnt = 0, b & 0x7F
        if prev is not None and prev[0] == idx and prev[1] + cnt <= (
                127 if idx == 0 else 255):
            merges += 1
        prev = (idx, cnt)
        col += cnt
        if col == p.shape[1]:
            col, prev = 0, None                # 換列，run 本來就不接
    assert merges == 0, "有 %d 對相鄰 token 可以合併，編碼不是最短的" % merges
    return "沒有可合併的相鄰 token"


# --------------------------------------------------------------------------
# 2. 名稱雜湊
# --------------------------------------------------------------------------

@case
def test_FNV1a_對得上標準向量(tmp):
    """FNV-1a 32 的官方測試向量。韌體端會自己寫一份，值必須完全一樣。"""
    vectors = {
        "": 0x811C9DC5,
        "a": 0xE40C292C,
        "foobar": 0xBF9CF968,
    }
    for s, want in vectors.items():
        got = pack.fnv1a_32(s)
        assert got == want, "fnv1a_32(%r) = 0x%08X，應為 0x%08X" % (s, got, want)
    return "3 組向量相符"


@case
def test_雜湊碰撞會中止(tmp):
    """用一組**真的會撞**的名字。

    `"d/_h"` 與 `"x8ea"` 都是 0x6FEDED3B——是掃過的 136 萬個字串裡的第一組碰撞。
    用真碰撞而不是 monkeypatch，是因為要證明檢查本身有效，
    不是證明「假裝撞了會報錯」。
    """
    a, b = "d/_h", "x8ea"
    assert pack.fnv1a_32(a) == pack.fnv1a_32(b) == 0x6FEDED3B
    expect_error(lambda: pack.check_collisions(["scene/room", a, b]),
                 "碰撞", "碰撞的名字")
    pack.check_collisions(["scene/room", a])          # 不撞的要放行
    return "0x6FEDED3B 這組真碰撞被擋下"


@case
def test_實際資產沒有雜湊碰撞(tmp):
    names = [a.name for a in ctx()["assets"]]
    assert len(names) == len(set(names)), "資產名重複"
    h = pack.check_collisions(names)
    assert len(h) == len(names)
    return "%d 個名字、%d 個雜湊" % (len(names), len(h))


# --------------------------------------------------------------------------
# 3. 檔案格式
# --------------------------------------------------------------------------

@case
def test_檔頭欄位正確(tmp):
    b = ctx()["bin"]
    assert b.magic == b"IPA1", "magic 是 %r" % b.magic
    assert b.version == pack.FORMAT_VERSION
    assert b.header_bytes == pack.HEADER_BYTES == 48
    assert b.total_bytes == len(b.raw), \
        "total_bytes=%d，實際檔案 %d B" % (b.total_bytes, len(b.raw))
    assert b.flags & pack.HF_BLOB_PER_FRAME, "預設應該是每格影格一個 blob"
    assert b.flags & pack.HF_BLOB_DEDUP
    assert b.reserved == 0
    import zlib
    want = zlib.crc32(b.raw[pack.HEADER_BYTES:]) & 0xFFFFFFFF
    assert b.crc32 == want, "crc32 對不上（0x%08X vs 0x%08X）" % (b.crc32, want)
    return "magic/version/total/crc32 都對"


@case
def test_結構自然對齊且沒有隱含padding(tmp):
    """spi_flash_mmap 之後要能直接當 C 陣列存取，這是硬性條件。

    C 編譯器會把每個欄位對齊到自己的大小，並把結構補到最大成員的倍數。
    只要 Python 這邊算出來的位移和「C 會排出來的位移」一致，兩邊就是同一份版面。
    """
    layouts = {
        "ipa_header_t": (pack.HEADER_FMT, pack.HEADER_BYTES),
        "ipa_asset_t": (pack.ASSET_FMT, pack.ASSET_BYTES),
        "ipa_frame_t": (pack.FRAME_FMT, pack.FRAME_BYTES),
    }
    sizes = {"4s": 1, "B": 1, "b": 1, "H": 2, "h": 2, "I": 4, "i": 4}
    detail = []
    for name, (fmt, want) in layouts.items():
        assert struct.calcsize(fmt) == want, \
            "%s 是 %d B，應為 %d" % (name, struct.calcsize(fmt), want)
        off = 0
        biggest = 1
        for code in _fields(fmt):
            sz = sizes[code]
            width = 4 if code == "4s" else sz     # char[4] 的對齊是 1
            align = 1 if code == "4s" else sz
            assert off % align == 0, \
                "%s 的欄位 %s 在位移 %d，不是 %d 的倍數（C 會插 padding）" \
                % (name, code, off, align)
            off += width
            biggest = max(biggest, align)
        assert off == want
        assert want % biggest == 0, \
            "%s 大小 %d 不是最大成員 %d 的倍數，陣列的第二個元素會錯位" \
            % (name, want, biggest)
        detail.append("%s=%dB" % (name.replace("ipa_", "").replace("_t", ""),
                                  want))
    b = ctx()["bin"]
    for label, off, align in (("asset_offset", b.asset_offset, 4),
                              ("frame_offset", b.frame_offset, 4),
                              ("palette_offset", b.palette_offset, 4),
                              ("blob_offset", b.blob_offset, 4)):
        assert off % align == 0, "%s = %d 沒有對齊 %d" % (label, off, align)
    return "、".join(detail)


def _fields(fmt):
    """把 struct 格式字串拆成一個個欄位碼（不支援重複次數，本專案也沒用到）。"""
    out = []
    i = 1                                   # 跳過 "<"
    while i < len(fmt):
        if fmt[i].isdigit():
            j = i
            while fmt[j].isdigit():
                j += 1
            out.append(fmt[i:j + 1])
            i = j + 1
        else:
            out.append(fmt[i])
            i += 1
    return out


@case
def test_表格位移與長度自洽(tmp):
    b = ctx()["bin"]
    assert b.asset_offset == pack.HEADER_BYTES
    assert b.frame_offset == b.asset_offset + b.asset_count * pack.ASSET_BYTES
    assert b.palette_offset == b.frame_offset + b.frame_count * pack.FRAME_BYTES
    assert b.blob_offset >= b.palette_offset + b.palette_count * pack.PALETTE_BYTES
    assert b.blob_offset + b.blob_bytes == b.total_bytes
    seen = 0
    for a in b.assets:
        assert a["frame_index"] == seen, "影格表不是照資產順序連續排的"
        seen += a["frame_count"]
        assert 0 <= a["palette_day"] < b.palette_count
        assert 0 <= a["palette_night"] < b.palette_count
        assert a["reserved"] == 0
    assert seen == b.frame_count
    for f in b.frames:
        assert b.blob_offset <= f["blob_offset"], "blob 位移落在 blob 區之前"
        assert f["blob_offset"] + f["blob_bytes"] <= b.total_bytes
        assert f["reserved"] == (0, 0, 0)
    return "%d 個資產、%d 格、%d 份調色盤" % (b.asset_count, b.frame_count,
                                             b.palette_count)


@case
def test_資產表照name_hash排序(tmp):
    """韌體要二分搜尋，順序是規格的一部分。"""
    b = ctx()["bin"]
    hashes = [a["name_hash"] for a in b.assets]
    assert hashes == sorted(hashes), "資產表沒有照 name_hash 遞增排序"
    assert len(set(hashes)) == len(hashes)
    return "%d 個 name_hash 遞增且唯一" % len(hashes)


@case
def test_每個名字都查得到(tmp):
    """四個角色 × 21 個動畫 + 房間 + 物件，一個都不能少。"""
    b = ctx()["bin"]
    by_hash = {a["name_hash"]: a for a in b.assets}
    want = []
    for cid in ("brown_mixed", "brindle_guard", "chihuahua", "ice_princess"):
        atlas = json.loads(
            (SHEET_DIR / ("%s_atlas.json" % cid)).read_text(encoding="utf-8"))
        want += ["%s/%s" % (cid, e["id"]) for e in atlas["animations"]]
    want.append("scene/room")
    spec = json.loads((ROOT / "specs/scene.json").read_text(encoding="utf-8"))
    want += ["obj/%s" % k for k, v in spec["objects"].items()
             if isinstance(v, dict) and "size" in v]
    # 公主的配件。**這份清單刻意獨立列一次**，不從 pack.py 借——
    # 兩份實作各自從 specs/ 讀，才擋得住「打包時漏掉一件」這種錯。
    for ap in sorted((ROOT / "specs/accessories").glob("*.json")):
        acc = json.loads(ap.read_text(encoding="utf-8"))
        want += ["acc/%s/%s" % (ap.stem, a["id"])
                 for a in acc["accessories"] if not a["id"].startswith("_")]
    for n in want:
        assert pack.fnv1a_32(n) in by_hash, "查不到資產 %r" % n
    assert len(want) == b.asset_count, \
        "預期 %d 個資產，bin 裡有 %d 個" % (len(want), b.asset_count)
    return "%d 個名字全部查得到（含 84 個角色動畫、5 件配件）" % len(want)


@case
def test_調色盤是RGB565(tmp):
    """10 份調色盤 × 16 色，逐色和 specs/palettes/*.json 比對。"""
    b = ctx()["bin"]
    pals = ctx()["palettes"]
    n = 0
    for pid in range(b.palette_count):
        src = json.loads(Path(pals.get(pid).path).read_text(encoding="utf-8"))
        assert len(src["colors"]) == 16
        for c in src["colors"]:
            s = c["hex"].lstrip("#")
            r, g, bl = (int(s[i:i + 2], 16) for i in (0, 2, 4))
            want = ((r >> 3) << 11) | ((g >> 2) << 5) | (bl >> 3)
            got = b.palettes[pid][int(c["index"])]
            assert got == want, "%s 的 index %d：0x%04X 應為 0x%04X" \
                % (pals.name_of(pid), c["index"], got, want)
            n += 1
    return "%d 份 × 16 色全部相符" % b.palette_count


@case
def test_每個角色都有日夜兩份調色盤(tmp):
    b = ctx()["bin"]
    pals = ctx()["palettes"]
    seen = set()
    for a in b.assets:
        if a["type"] != pack.TYPE_ANIM:
            continue
        d, n = pals.name_of(a["palette_day"]), pals.name_of(a["palette_night"])
        assert n == d + "_night", "%s 的夜間盤是 %s" % (d, n)
        seen.add(d)
    assert seen == {"brown_mixed", "brindle_guard", "chihuahua", "ice_princess"}
    return "、".join(sorted(seen))


# --------------------------------------------------------------------------
# 4. 可還原：全部 blob 對來源 PNG 逐像素比對
# --------------------------------------------------------------------------

def decode_all(b):
    """把 bin 裡每一格解回索引平面。回傳 {name_hash: [plane, ...]}。"""
    out = {}
    for a in b.assets:
        planes = []
        for i in range(a["frame_count"]):
            f = b.frames[a["frame_index"] + i]
            blob = b.blob(f)
            if b.flags & pack.HF_BLOB_PER_FRAME:
                planes.append(rle_decode(blob, a["w"], a["h"]))
            else:
                sheet = rle_decode(blob, a["w"] * a["frame_count"], a["h"])
                planes.append(sheet[:, i * a["w"]:(i + 1) * a["w"]])
        out[a["name_hash"]] = planes
    return out


def expected_planes():
    """來源 PNG → 索引平面。回傳 {name: (planes, 來源檔)}。"""
    want = {}
    for cid in ("brown_mixed", "brindle_guard", "chihuahua", "ice_princess"):
        pal = ROOT / ("specs/palettes/%s.json" % cid)
        atlas = json.loads(
            (SHEET_DIR / ("%s_atlas.json" % cid)).read_text(encoding="utf-8"))
        for e in atlas["animations"]:
            adef = json.loads(
                (SHEET_DIR / e["atlas"]).read_text(encoding="utf-8"))
            png = SHEET_DIR / e["sheet"]
            sheet = png_to_plane(png, pal)
            w, n = int(adef["frame_w"]), int(adef["frame_count"])
            want["%s/%s" % (cid, e["id"])] = (
                [sheet[:, i * w:(i + 1) * w] for i in range(n)], png)
    spal = ROOT / "specs/palettes/scene.json"
    want["scene/room"] = ([png_to_plane(SCENE_DIR / "room_day.png", spal)],
                          SCENE_DIR / "room_day.png")
    spec = json.loads((ROOT / "specs/scene.json").read_text(encoding="utf-8"))
    for k, v in spec["objects"].items():
        if not isinstance(v, dict) or "size" not in v:
            continue
        png = SCENE_DIR / ("obj_%s.png" % k)
        sheet = png_to_plane(png, spal)
        w = int(v["size"][0])
        n = int(v.get("frame_count", 1))
        want["obj/%s" % k] = ([sheet[:, i * w:(i + 1) * w] for i in range(n)],
                              png)
    for ap in sorted((ROOT / "specs/accessories").glob("*.json")):
        cid = ap.stem
        acc = json.loads(ap.read_text(encoding="utf-8"))
        cpal = ROOT / ("specs/palettes/%s.json" % cid)   # 配件用角色的調色盤，不是場景的
        for a in acc["accessories"]:
            if a["id"].startswith("_"):
                continue
            png = ROOT / ("art/approved/_dressup/acc_%s_%s.png" % (cid, a["id"]))
            want["acc/%s/%s" % (cid, a["id"])] = ([png_to_plane(png, cpal)], png)
    return want


@case
def test_全部blob都解得回來源PNG(tmp):
    """**不抽樣。** 每一張 blob、每一個像素都比。

    這一項是整個資產包的意義所在：bin 裡的東西如果和 PNG 不同，
    小孩在板子上看到的就不是我們核可過的那張圖。
    """
    b = ctx()["bin"]
    got = decode_all(b)
    want = expected_planes()
    assert len(want) == b.asset_count
    npx = nframes = 0
    for name, (planes, png) in sorted(want.items()):
        h = pack.fnv1a_32(name)
        assert h in got, "bin 裡沒有 %s" % name
        gp = got[h]
        assert len(gp) == len(planes), \
            "%s 有 %d 格，bin 裡是 %d 格" % (name, len(planes), len(gp))
        for i, (g, w) in enumerate(zip(gp, planes)):
            assert g.shape == w.shape, \
                "%s f%d 尺寸 %s，來源 %s" % (name, i, g.shape, w.shape)
            if not np.array_equal(g, w):
                bad = int((g != w).sum())
                ys, xs = np.nonzero(g != w)
                raise AssertionError(
                    "%s f%d 有 %d 個像素不同（例如 (%d,%d)：bin=%d PNG=%d，來源 %s）"
                    % (name, i, bad, ys[0], xs[0], g[ys[0], xs[0]],
                       w[ys[0], xs[0]], png))
            npx += g.size
            nframes += 1
    return "%d 格、%s 個像素 100%% 相同" % (nframes, "{:,}".format(npx))


@case
def test_房間日夜共用同一張點陣圖(tmp):
    """room 的 blob 要同時等於 room_day 與 room_night 的索引平面。

    這是 specs/palettes/scene.json 明寫的設計地基，也是 docs/10 第 4.2 節
    否決 lv_img_conv 的那一條——它的自適應量化讓 27% 的像素對不上。
    """
    b = ctx()["bin"]
    a = next(x for x in b.assets
             if x["name_hash"] == pack.fnv1a_32("scene/room"))
    plane = rle_decode(b.blob(b.frames[a["frame_index"]]), a["w"], a["h"])
    day = png_to_plane(SCENE_DIR / "room_day.png",
                       ROOT / "specs/palettes/scene.json")
    night = png_to_plane(SCENE_DIR / "room_night.png",
                         ROOT / "specs/palettes/scene_night.json")
    assert np.array_equal(plane, day), "room blob 和 room_day 不同"
    assert np.array_equal(plane, night), "room blob 和 room_night 不同"
    pals = ctx()["palettes"]
    assert pals.name_of(a["palette_day"]) == "scene"
    assert pals.name_of(a["palette_night"]) == "scene_night"
    return "%d×%d，一張點陣圖兩份調色盤" % (a["w"], a["h"])


@case
def test_影格的時間與位移和atlas一致(tmp):
    """ms / screen_dx / screen_dy / flip / loop / frame_count 全部逐格比對 bake.py 的 atlas。"""
    b = ctx()["bin"]
    by_hash = {a["name_hash"]: a for a in b.assets}
    n = 0
    for cid in ("brown_mixed", "brindle_guard", "chihuahua", "ice_princess"):
        atlas = json.loads(
            (SHEET_DIR / ("%s_atlas.json" % cid)).read_text(encoding="utf-8"))
        for e in atlas["animations"]:
            adef = json.loads(
                (SHEET_DIR / e["atlas"]).read_text(encoding="utf-8"))
            a = by_hash[pack.fnv1a_32("%s/%s" % (cid, e["id"]))]
            assert a["frame_count"] == adef["frame_count"]
            assert a["w"] == adef["frame_w"] and a["h"] == adef["frame_h"]
            assert bool(a["flags"] & pack.AF_LOOP) == bool(adef["loop"]), \
                "%s/%s 的 loop 不符" % (cid, e["id"])
            assert a["type"] == pack.TYPE_ANIM
            for i, fr in enumerate(adef["frames"]):
                f = b.frames[a["frame_index"] + i]
                assert f["ms"] == fr["ms"], "%s/%s f%d 的 ms" % (cid, e["id"], i)
                assert f["screen_dx"] == fr["screen_dx"]
                assert f["screen_dy"] == fr["screen_dy"]
                assert f["flip"] == (1 if fr["flip"] else 0)
                n += 1
    return "%d 格的 ms/screen_dx/screen_dy/flip 全部相符" % n


@case
def test_物件的frame_ms來自specs(tmp):
    b = ctx()["bin"]
    by_hash = {a["name_hash"]: a for a in b.assets}
    spec = json.loads((ROOT / "specs/scene.json").read_text(encoding="utf-8"))
    n = 0
    for k, v in sorted(spec["objects"].items()):
        if not isinstance(v, dict) or "size" not in v:
            continue
        a = by_hash[pack.fnv1a_32("obj/%s" % k)]
        assert a["type"] == pack.TYPE_OBJECT
        assert a["frame_count"] == int(v.get("frame_count", 1))
        assert (a["w"], a["h"]) == (v["size"][0], v["size"][1])
        want_ms = int(v.get("frame_ms", 0))
        for i in range(a["frame_count"]):
            assert b.frames[a["frame_index"] + i]["ms"] == want_ms, \
                "obj/%s f%d 的 ms" % (k, i)
        assert bool(a["flags"] & pack.AF_LOOP) == (a["frame_count"] > 1)
        n += 1
    return "%d 個物件" % n


# --------------------------------------------------------------------------
# 5. 去重
# --------------------------------------------------------------------------

@case
def test_去重有真的發生而且解出來還是對的(tmp):
    """transform 型動畫的四格是同一張點陣圖（位移記在 screen_dy），
    去重之後多格會指向同一個 blob。指向同一個 blob 但解錯，
    上面那項全比對就會紅——所以這裡只需要證明「真的共用了」。"""
    b = ctx()["bin"]
    offsets = [(f["blob_offset"], f["blob_bytes"]) for f in b.frames]
    shared = len(offsets) - len(set(offsets))
    assert shared > 0, "一格都沒去重，transform 型動畫的假設不成立了"
    total = sum(bb for _, bb in set(offsets))
    assert total == b.blob_bytes, \
        "唯一 blob 加起來 %d B，blob 區是 %d B（有洞或重疊）" % (total, b.blob_bytes)
    return "%d/%d 格共用既有的 blob，blob 區沒有洞" % (shared, len(offsets))


# --------------------------------------------------------------------------
# 6. 可重現
# --------------------------------------------------------------------------

@case
def test_跑兩次逐位元相同(tmp):
    """docs/07 的硬性契約：沒有任何一步是手工的，所以重跑必須逐位元相同。"""
    a = pack.run(out_path=tmp / "a.bin", quiet=True, write_manifest=True)
    b = pack.run(out_path=tmp / "b.bin", quiet=True, write_manifest=True)
    ra, rb = (tmp / "a.bin").read_bytes(), (tmp / "b.bin").read_bytes()
    assert ra == rb, "兩次產出不同（%d vs %d B）" % (len(ra), len(rb))
    assert a["sha256"] == b["sha256"]
    ja = (tmp / "a.json").read_text(encoding="utf-8")
    jb = (tmp / "b.json").read_text(encoding="utf-8")
    assert ja == jb, "對照表兩次不同"
    assert a["sha256"] == ctx()["sha256"], \
        "和 data/assets.bin 的雜湊不同，表示輸出路徑會影響內容"
    return "sha256 %s" % a["sha256"][:16]


# --------------------------------------------------------------------------
# 7. per-sheet 那條路也要是活的
# --------------------------------------------------------------------------

@case
def test_per_sheet模式也解得回來(tmp):
    """對照組不是死程式碼（docs/08 第 2.10 節：死程式碼比沒有程式碼危險）。
    它要能產出合法的檔案、要解得回來，數字才有資格拿來做決策。"""
    r = pack.run(out_path=tmp / "sheet.bin", per_frame=False, dedup=False,
                 quiet=True, write_manifest=False)
    b = Bin((tmp / "sheet.bin").read_bytes())
    assert not (b.flags & pack.HF_BLOB_PER_FRAME)
    got = decode_all(b)
    want = expected_planes()
    for name, (planes, _) in want.items():
        gp = got[pack.fnv1a_32(name)]
        for i, (g, w) in enumerate(zip(gp, planes)):
            assert np.array_equal(g, w), "%s f%d per-sheet 解錯" % (name, i)
    per_frame = ctx()["bytes"]
    return "per-sheet %s B，比預設的每格影格（%s B）大 %.1f%%" % (
        "{:,}".format(r["bytes"]), "{:,}".format(per_frame),
        (r["bytes"] - per_frame) / per_frame * 100)


# --------------------------------------------------------------------------
# 8. 壞資料要中止，不要默默通過
# --------------------------------------------------------------------------

@case
def test_調色盤外的顏色會中止(tmp):
    """精確比對，不做最近距離猜測（docs/08 第 2.4 節）。"""
    pal = pack.Palette("scene", ROOT / "specs/palettes/scene.json")
    arr = np.zeros((4, 4, 4), np.uint8)
    arr[..., :3] = (0xEC, 0xE4, 0xD6)          # wall_light，合法
    arr[..., 3] = 255
    good = tmp / "good.png"
    Image.fromarray(arr).save(good)
    pack.index_plane(good, pal)                 # 不該報錯

    arr[1, 1, :3] = (0x12, 0x34, 0x56)          # 調色盤外
    bad = tmp / "bad.png"
    Image.fromarray(arr).save(bad)
    expect_error(lambda: pack.index_plane(bad, pal),
                 "#123456", "調色盤外的顏色")

    arr[1, 1, :3] = (0xEC, 0xE4, 0xD6)
    arr[2, 2, 3] = 128                          # 半透明
    semi = tmp / "semi.png"
    Image.fromarray(arr).save(semi)
    expect_error(lambda: pack.index_plane(semi, pal), "半透明", "半透明像素")
    return "調色盤外的顏色與半透明像素都會中止"


@case
def test_影格尺寸不符會中止(tmp):
    """docs/08 第 2.10 節：frames 型從來沒有影格尺寸檢查，
    尺寸不符的影格會被默默貼在畫布左上角。同一個洞不要在打包這一層再開一次。"""
    sheet = np.zeros((56, 200), np.uint8)
    expect_error(lambda: pack.split_frames(sheet, 64, 56, 4, "假動畫"),
                 "應為", "sheet 寬度不是 4 × 64")
    ok = pack.split_frames(np.zeros((56, 256), np.uint8), 64, 56, 4, "假動畫")
    assert len(ok) == 4 and ok[0].shape == (56, 64)
    return "寬度對不上就中止"


@case
def test_透明色必須在index0(tmp):
    """RLE 的 `0nnnnnnn` token 建立在這條約定上，改了整個格式就壞了。"""
    src = json.loads((ROOT / "specs/palettes/scene.json").read_text(
        encoding="utf-8"))
    src["colors"][0]["transparent"] = False
    src["colors"][3]["transparent"] = True
    p = tmp / "moved.json"
    p.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    expect_error(lambda: pack.Palette("moved", p), "index 0", "透明色被搬走")
    return "透明色不在 index 0 就中止"


@case
def test_同色兩個索引不能拿來反推(tmp):
    """`brindle_guard_night` 的 index 2 與 11 都是 #202030——真實存在的情況。

    那份調色盤只拿來「換色」（角色的索引平面來自日間圖），所以存進 bin 沒問題；
    但如果有人拿它去建索引平面，反推會有歧義，必須擋下而不是靜靜選一個。
    """
    p = pack.Palette("brindle_guard_night",
                     ROOT / "specs/palettes/brindle_guard_night.json")
    assert p.dup, "brindle_guard_night 應該有同色的兩個索引"
    p.to_bytes()                                # 存進 bin 不受影響
    png = tmp / "x.png"
    arr = np.zeros((2, 2, 4), np.uint8)
    arr[..., 3] = 255
    Image.fromarray(arr).save(png)
    expect_error(lambda: pack.index_plane(png, p), "歧義", "同色兩個索引")

    day = pack.Palette("brindle_guard",
                       ROOT / "specs/palettes/brindle_guard.json")
    assert not day.dup, "日間調色盤不該有同色索引"
    return "index %s 同色會擋下反推" % "/".join(
        str(i) for i in list(p.dup.values())[0])


@case
def test_調色盤必須剛好16色(tmp):
    src = json.loads((ROOT / "specs/palettes/scene.json").read_text(
        encoding="utf-8"))
    src["colors"] = src["colors"][:12]
    p = tmp / "short.json"
    p.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    expect_error(lambda: pack.Palette("short", p), "4bpp", "只有 12 色")
    return "12 色會中止"


# --------------------------------------------------------------------------
# 9. 預算
# --------------------------------------------------------------------------

@case
def test_檔案塞得進assets分區(tmp):
    b = ctx()["bytes"]
    assert b < pack.ASSETS_PARTITION_BYTES, "超過 6 MB 的 assets 分區"
    return "%.1f KB，佔 16 MB flash 的 %.2f%%、6 MB 分區的 %.2f%%" % (
        b / 1024, b / pack.FLASH_BYTES * 100,
        b / pack.ASSETS_PARTITION_BYTES * 100)


# --------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="pack_test_"))
    try:
        r = pack.run(out_path=tmp / "ctx.bin", quiet=True, write_manifest=False)
        CTX.update(r)
        CTX["bin"] = Bin((tmp / "ctx.bin").read_bytes())

        print("pack.py 自我測試  素材：%s" % tmp)
        print("-" * 68)
        failed = 0
        for fn in CASES:
            name = fn.__name__.replace("test_", "")
            try:
                extra = fn(tmp)
            except Exception:
                failed += 1
                print("  [失敗] %s" % name)
                for line in traceback.format_exc().strip().splitlines()[-4:]:
                    print("         %s" % line)
            else:
                print("  [通過] %s%s" % (name, "   %s" % extra if extra else ""))
        print("-" * 68)
        print("%d 項通過，%d 項失敗" % (len(CASES) - failed, failed))
        return 1 if failed else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
