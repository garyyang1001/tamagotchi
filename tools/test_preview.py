#!/usr/bin/env python3
"""
test_preview.py — preview.py 的自我測試

用**合成的假 spritesheet** 驗證，不依賴任何已烘焙的資產。
理由：preview.py 是驗收工具，它自己一定要能在資產還沒好之前就被驗過，
否則「工具沒問題」和「資產沒問題」會混在一起，出事時分不出是誰壞的。

跑法
----
    /Users/gary/Documents/女兒電子雞/.venv/bin/python tools/test_preview.py

檢查的東西
----------
  · GIF 真的產生了，而且格數與延遲對得上（讀回檔案驗，不是驗記憶體）
  · frame_ms 是權威欄位（fps 存在時仍以 frame_ms 為準）
  · loop=false 的尾格延長 1 秒；loop=true 的沒有
  · GIF 的 10ms 量化有被處理（Pillow 是無條件捨去，我們要四捨五入）
  · 透明背景：index 0 是透明、disposal=2、沒有殘影
  · 接觸表尺寸與版面公式一致
  · 實機模擬恰好 320×240
  · 檔名最長匹配（eat 不會撈到 eat_happy）
  · --check 的循環秒數／次每分／重複影格
  · 放大只用 NEAREST（放大後的每個色塊都是純色，沒有插值出來的中間色）
"""

import json
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preview as P                                          # noqa: E402


# --------------------------------------------------------------------------
# 假資產
# --------------------------------------------------------------------------

CHAR = "test_dog"

# 刻意選幾個好認的顏色，方便在放大測試裡確認沒有插值出中間色
C_BODY = (0x4A, 0x32, 0x25)
C_MARK = (0xB0, 0x80, 0x50)
C_EYE = (0xF2, 0xED, 0xE4)


def make_frame(i: int, n: int, distinct: bool = True) -> np.ndarray:
    """畫一格假 sprite：一個方塊 + 一個會移動的標記像素。

    方塊固定，標記逐格移動 —— 這樣「影格是否相同」是可控的。
    """
    fr = np.zeros((P.SPRITE_H, P.SPRITE_W, 4), np.uint8)
    fr[30:50, 20:44] = (*C_BODY, 255)          # 身體
    fr[32:36, 38:42] = (*C_MARK, 255)          # 標記
    k = i if distinct else 0
    fr[10 + k, 30] = (*C_EYE, 255)             # 逐格移動的一個像素
    return fr


def write_sheet(path: Path, n: int, distinct: bool = True) -> None:
    """橫向排列的 spritesheet，n 格 × 64×56。"""
    sheet = np.zeros((P.SPRITE_H, P.SPRITE_W * n, 4), np.uint8)
    for i in range(n):
        sheet[:, i * P.SPRITE_W:(i + 1) * P.SPRITE_W] = make_frame(i, n, distinct)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(sheet).save(str(path))


ANIM_JSON = {
    "character_id": CHAR,
    "format_version": 2,
    "defaults": {"fps": 6, "loop": False, "type": "transform"},
    "animations": [
        {
            "id": "idle_breathe", "tier": 0, "loop": True,
            "fps": 1.33, "frames": 4, "frame_ms": 750,
            "status": "IMPLEMENTED",
            "track": [{"f": 0, "y": 0}, {"f": 1, "y": -1},
                      {"f": 2, "y": -1}, {"f": 3, "y": 0}],
        },
        {
            "id": "happy", "tier": 0, "fps": 10, "frames": 3,
            "status": "IMPLEMENTED",
            "track": [{"f": 0, "y": 0}, {"f": 1, "y": -5}, {"f": 2, "y": 0}],
        },
        {
            "id": "eat", "tier": 1, "loop": True, "fps": 6, "frames": 2,
            "status": "PLACEHOLDER_NEEDS_RIG",
            "track": [{"f": 0, "y": -1}, {"f": 1, "y": 0}],
        },
        {
            "id": "eat_happy", "tier": 1, "fps": 8, "frames": 2,
            "status": "PLACEHOLDER_NEEDS_RIG",
            "track": [{"f": 0, "y": -1}, {"f": 1, "y": 0}],
        },
    ],
}

# 每個動畫的影格數
FRAMES = {a["id"]: a["frames"] for a in ANIM_JSON["animations"]}


def build_fixture(root: Path) -> dict:
    """搭出一個最小但完整的假專案。"""
    (root / "specs" / "animations").mkdir(parents=True, exist_ok=True)
    (root / "specs" / "palettes").mkdir(parents=True, exist_ok=True)
    sheets = root / "build" / "sheets"
    sheets.mkdir(parents=True, exist_ok=True)

    (root / "specs" / "animations" / ("%s.anim.json" % CHAR)).write_text(
        json.dumps(ANIM_JSON, indent=2), encoding="utf-8")

    # 調色盤：故意只放 sheet 用到的三色 + 透明，讓 --check 的調色盤檢查有意義
    palette = {
        "character_id": CHAR,
        "colors": [
            {"index": 0, "role": "transparent", "hex": "#000000",
             "transparent": True},
            {"index": 1, "role": "coat_mid", "hex": "#%02X%02X%02X" % C_BODY},
            {"index": 2, "role": "tan_mid", "hex": "#%02X%02X%02X" % C_MARK},
            {"index": 3, "role": "eye_highlight", "hex": "#%02X%02X%02X" % C_EYE},
        ],
    }
    (root / "specs" / "palettes" / ("%s.json" % CHAR)).write_text(
        json.dumps(palette, indent=2), encoding="utf-8")

    # idle_breathe 用 f1==f2 的重複影格，模擬真實的呼吸曲線
    write_sheet(sheets / ("%s_idle_breathe_r.png" % CHAR), 4)
    dup = np.zeros((P.SPRITE_H, P.SPRITE_W * 4, 4), np.uint8)
    for i, k in enumerate([0, 1, 1, 0]):
        dup[:, i * P.SPRITE_W:(i + 1) * P.SPRITE_W] = make_frame(k, 4)
    Image.fromarray(dup).save(
        str(sheets / ("%s_idle_breathe_r.png" % CHAR)))

    write_sheet(sheets / ("%s_happy_r.png" % CHAR), 3)
    write_sheet(sheets / ("%s_eat_r.png" % CHAR), 2)
    write_sheet(sheets / ("%s_eat_happy_r.png" % CHAR), 2)

    return {"root": root, "sheets": sheets,
            "anim": root / "specs" / "animations" / ("%s.anim.json" % CHAR)}


def gif_durations(path: Path):
    """讀回 GIF 的每格延遲（ms）。驗檔案，不驗記憶體裡的東西。"""
    im = Image.open(str(path))
    out = []
    for i in range(im.n_frames):
        im.seek(i)
        out.append(int(im.info.get("duration", 0)))
    return out, im


# --------------------------------------------------------------------------
# 測試
# --------------------------------------------------------------------------

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def t_frame_ms_是權威欄位(fx):
    """idle_breathe 同時有 fps 1.33 與 frame_ms 750，必須取 750。"""
    specs = P.load_anim_specs(fx["anim"])
    assert specs["idle_breathe"].frame_ms == 750, specs["idle_breathe"].frame_ms
    assert specs["idle_breathe"].frame_ms_source == "anim.frame_ms"
    # happy 只有 fps 10 → 100ms
    assert specs["happy"].frame_ms == 100, specs["happy"].frame_ms
    # loop 預設值要吃到 defaults
    assert specs["idle_breathe"].loop is True
    assert specs["happy"].loop is False


@test
def t_檔名最長匹配(fx):
    """`eat` 的 glob 會撈到 `eat_happy`，必須取最長匹配。"""
    ids = list(FRAMES.keys())
    assert P.match_anim_from_filename(
        "%s_eat_happy_r" % CHAR, CHAR, ids) == "eat_happy"
    assert P.match_anim_from_filename("%s_eat_r" % CHAR, CHAR, ids) == "eat"
    assert P.match_anim_from_filename("%s_eat" % CHAR, CHAR, ids) == "eat"
    assert P.match_anim_from_filename("other_eat_r", CHAR, ids) is None

    found = P.find_sheets(fx["sheets"], CHAR, ids)
    assert set(found) == set(ids), sorted(found)
    assert found["eat"].name == "%s_eat_r.png" % CHAR, found["eat"].name
    assert found["eat_happy"].name == "%s_eat_happy_r.png" % CHAR


@test
def t_跳過_bake_的放大預覽(fx):
    """bake.py 會在同一個目錄放 `_x8` 的放大預覽，那不是 sheet。

    誤把它當 sheet 會切出 512×448 的「影格」，接觸表與 GIF 整個大一圈，
    而且畫面看起來還是對的——是最難發現的那種錯。
    """
    assert P.is_upscaled_preview("brown_mixed_walk_x8")
    assert P.is_upscaled_preview("brown_mixed_walk_x12")
    assert not P.is_upscaled_preview("brown_mixed_walk")
    assert not P.is_upscaled_preview("brown_mixed_walk_r")
    assert not P.is_upscaled_preview("brown_mixed_walk_xy")

    ids = list(FRAMES.keys())
    decoy = fx["sheets"] / ("%s_happy_x8.png" % CHAR)
    big = np.zeros((P.SPRITE_H * 8, P.SPRITE_W * 8 * 3, 4), np.uint8)
    Image.fromarray(big).save(str(decoy))
    try:
        found = P.find_sheets(fx["sheets"], CHAR, ids)
        assert found["happy"].name == "%s_happy_r.png" % CHAR, found["happy"].name
        # 就算基準檔不見了也不能退而求其次去挑放大版
        assert all(not P.is_upscaled_preview(p.stem) for p in found.values())
    finally:
        decoy.unlink()


@test
def t_atlas_與動畫定義不合會被指出(fx):
    """sheet 過期是最容易看走眼的錯：動畫「幾乎對」，但改過的 JSON 沒生效。"""
    specs = P.load_anim_specs(fx["anim"])
    png = fx["sheets"] / ("%s_happy_r.png" % CHAR)
    atlas = {
        "frame_w": 64, "frame_h": 56, "loop": True, "frame_ms": 999,
        "frames": [{"x": i * 64, "y": 0, "w": 64, "h": 56, "ms": 100}
                   for i in range(3)],
    }
    png.with_suffix(".json").write_text(json.dumps(atlas), encoding="utf-8")
    try:
        _frames, _d, info = P.slice_sheet(png, specs["happy"])
        text = " ".join(info["notes"])
        assert "loop" in text and "sheet 可能過期" in text, info["notes"]
        assert "frame_ms=999" in text, info["notes"]
    finally:
        png.with_suffix(".json").unlink()


@test
def t_切割_spritesheet(fx):
    """沒有 atlas 時，用動畫定義的影格數等分，每格必須是 64×56。"""
    specs = P.load_anim_specs(fx["anim"])
    png = fx["sheets"] / ("%s_happy_r.png" % CHAR)
    frames, durations, info = P.slice_sheet(png, specs["happy"])

    assert len(frames) == 3, len(frames)
    assert info["layout"] == "strip-by-spec", info["layout"]
    assert info["frame_size"] == (P.SPRITE_W, P.SPRITE_H), info["frame_size"]
    assert durations == [100, 100, 100], durations
    assert not info["notes"], info["notes"]

    # 切出來的內容要和原始影格逐位元相同
    for i, fr in enumerate(frames):
        assert np.array_equal(fr, make_frame(i, 3)), "第 %d 格切錯" % i


@test
def t_atlas_逐格時長(fx):
    """有 atlas 時，逐格的 ms 要蓋過動畫定義的 frame_ms。"""
    specs = P.load_anim_specs(fx["anim"])
    png = fx["sheets"] / ("%s_happy_r.png" % CHAR)
    atlas = {
        "character": CHAR, "anim": "happy", "frame_size": [64, 56],
        "frames": [
            {"x": 0, "y": 0, "w": 64, "h": 56, "ms": 80},
            {"x": 64, "y": 0, "w": 64, "h": 56, "ms": 120},
            {"x": 128, "y": 0, "w": 64, "h": 56, "ms": 200},
        ],
    }
    png.with_suffix(".json").write_text(json.dumps(atlas), encoding="utf-8")
    try:
        frames, durations, info = P.slice_sheet(png, specs["happy"])
        assert info["layout"] == "atlas", info["layout"]
        assert durations == [80, 120, 200], durations
        assert len(frames) == 3
    finally:
        png.with_suffix(".json").unlink()


@test
def t_GIF_產生且幀延遲正確(fx):
    """讀回 GIF 檔驗每格延遲。這是本測試最核心的一項。"""
    out = fx["root"] / "build" / "preview"
    specs = P.load_anim_specs(fx["anim"])
    png = fx["sheets"] / ("%s_happy_r.png" % CHAR)
    frames, durations, _ = P.slice_sheet(png, specs["happy"])

    res = P.write_gif(out / "happy.gif", frames,
                      P.anim_durations(specs["happy"], durations))
    assert (out / "happy.gif").exists()

    got, im = gif_durations(out / "happy.gif")
    # happy 是 loop=false → 最後一格 100 + 1000
    assert got == [100, 100, 1100], got
    assert im.n_frames == 3, im.n_frames
    assert sum(got) == res["duration_ms"] == 1300, (got, res["duration_ms"])


@test
def t_loop_true_不加尾格(fx):
    """loop=true 的動畫每格延遲一致，總長 = 格數 × frame_ms。"""
    out = fx["root"] / "build" / "preview"
    specs = P.load_anim_specs(fx["anim"])
    png = fx["sheets"] / ("%s_eat_r.png" % CHAR)
    frames, durations, _ = P.slice_sheet(png, specs["eat"])

    P.write_gif(out / "eat.gif", frames, P.anim_durations(specs["eat"], durations))
    got, im = gif_durations(out / "eat.gif")
    assert got == [170, 170], got            # 6 fps = 167ms → 四捨五入到 170
    assert im.info.get("loop") == 0, im.info.get("loop")


@test
def t_GIF_延遲量化到_10ms(fx):
    """Pillow 是無條件捨去（167→160），我們必須先四捨五入（167→170）。"""
    assert P.to_gif_ms(167) == 170
    assert P.to_gif_ms(750) == 750
    assert P.to_gif_ms(125) == 130
    assert P.to_gif_ms(875) == 880
    assert P.to_gif_ms(0) == 0

    out = fx["root"] / "build" / "preview"
    frames = [make_frame(i, 2) for i in range(2)]
    P.write_gif(out / "q.gif", frames, [167, 167])
    got, _ = gif_durations(out / "q.gif")
    assert got == [170, 170], got            # 若是 160 代表沒處理到


@test
def t_重複影格會被合併但總長不變(fx):
    """f1==f2 時 Pillow 會併格。總時長必須守恆，否則節奏就跑掉了。"""
    out = fx["root"] / "build" / "preview"
    specs = P.load_anim_specs(fx["anim"])
    png = fx["sheets"] / ("%s_idle_breathe_r.png" % CHAR)
    frames, durations, _ = P.slice_sheet(png, specs["idle_breathe"])

    assert P.frame_key(frames[1]) == P.frame_key(frames[2])
    P.write_gif(out / "idle_breathe.gif", frames,
                P.anim_durations(specs["idle_breathe"], durations))
    got, _ = gif_durations(out / "idle_breathe.gif")
    assert sum(got) == 4 * 750, got          # 3.0 秒 = 20 次/分
    assert got == [750, 1500, 750], got


@test
def t_透明背景沒有黑框與殘影(fx):
    """三件事：index 0 是透明、透明色不是黑、每格只剩自己的像素。"""
    out = fx["root"] / "build" / "preview"
    # 三格，每格的亮點在不同位置，若有殘影就會累積
    frames = [make_frame(i, 3) for i in range(3)]
    P.write_gif(out / "trans.gif", frames, [100, 100, 100], transparent=True)

    im = Image.open(str(out / "trans.gif"))
    assert im.info.get("transparency") == 0, im.info.get("transparency")

    pal = im.getpalette()
    assert tuple(pal[0:3]) == P.GIF_TRANSPARENT_RGB, tuple(pal[0:3])
    assert tuple(pal[0:3]) != (0, 0, 0), "透明色是黑的，不支援透明的檢視器會看到黑框"

    expected_opaque = int((frames[0][..., 3] > 0).sum())
    for i in range(im.n_frames):
        im.seek(i)
        a = np.array(im.convert("RGBA"))
        n = int((a[..., 3] > 0).sum())
        assert n == expected_opaque, "第 %d 格有 %d 個不透明像素，應該是 %d（殘影）" \
                                     % (i, n, expected_opaque)
        # 而且要和來源逐位元相同
        src = P.normalise_rgba(frames[i])
        assert np.array_equal(a[..., :3][a[..., 3] > 0],
                              src[..., :3][src[..., 3] > 0]), "第 %d 格顏色跑掉" % i


@test
def t_放大只用_NEAREST(fx):
    """放大後每個 scale×scale 區塊必須是純色，出現中間色就代表插值了。"""
    fr = make_frame(0, 1)
    big = P.scale_nearest(fr, 6)
    assert big.shape == (P.SPRITE_H * 6, P.SPRITE_W * 6, 4), big.shape

    src_colours = {tuple(int(v) for v in c)
                   for c in np.unique(fr.reshape(-1, 4), axis=0)}
    big_colours = {tuple(int(v) for v in c)
                   for c in np.unique(big.reshape(-1, 4), axis=0)}
    assert big_colours == src_colours, big_colours - src_colours

    for y in range(0, big.shape[0], 6):
        for x in range(0, big.shape[1], 6):
            block = big[y:y + 6, x:x + 6].reshape(-1, 4)
            assert len(np.unique(block, axis=0)) == 1, "區塊 (%d,%d) 不是純色" % (x, y)


@test
def t_接觸表尺寸(fx):
    """接觸表尺寸必須和版面公式一致（不是靠肉眼看）。"""
    out = fx["root"] / "build" / "preview"
    specs = P.load_anim_specs(fx["anim"])
    items = []
    for aid in FRAMES:
        png = fx["sheets"] / ("%s_%s_r.png" % (CHAR, aid))
        frames, _d, info = P.slice_sheet(png, specs[aid])
        items.append((specs[aid], frames, info))

    scale = 4
    res = P.render_contact_sheet(out / "_contact_sheet.png", CHAR, items, scale)
    lay = P.contact_layout(len(items), P.SPRITE_W, P.SPRITE_H, scale)

    assert lay["cols"] == 2 and lay["rows"] == 2, lay      # 4 個動畫 → 2×2
    assert res["size"] == (lay["width"], lay["height"]), res["size"]

    im = Image.open(str(out / "_contact_sheet.png"))
    assert im.size == (lay["width"], lay["height"]), im.size

    # 手算一次，確認公式沒有被偷偷改掉
    cell_w = P.SPRITE_W * scale + P.CONTACT_PAD * 2
    cell_h = P.SPRITE_H * scale + P.CONTACT_PAD * 2 + P.CONTACT_LABEL_H
    assert im.size == (2 * cell_w + P.CONTACT_MARGIN * 2,
                       2 * cell_h + P.CONTACT_MARGIN * 2 + P.CONTACT_HEADER_H), \
        im.size

    # 首格必須真的畫進去了：找得到身體色
    arr = np.array(im.convert("RGB"))
    assert (np.all(arr == np.array(C_BODY, np.uint8), axis=-1)).sum() > 0, \
        "接觸表裡找不到 sprite"


@test
def t_接觸表版面公式(fx):
    """欄數的自動決定要可預測：21 個動畫 → 5 欄 5 列。"""
    lay = P.contact_layout(21, 64, 56, 6)
    assert (lay["cols"], lay["rows"]) == (5, 5), lay
    assert P.contact_layout(1, 64, 56, 6)["cols"] == 1
    assert P.contact_layout(4, 64, 56, 6)["cols"] == 2
    assert P.contact_layout(9, 64, 56, 6)["cols"] == 3
    assert P.contact_layout(50, 64, 56, 6)["cols"] == P.CONTACT_MAX_COLS
    forced = P.contact_layout(21, 64, 56, 6, cols=3)
    assert (forced["cols"], forced["rows"]) == (3, 7), forced


@test
def t_實機模擬是_320x240(fx):
    out = fx["root"] / "build" / "preview"
    specs = P.load_anim_specs(fx["anim"])
    items = []
    for aid in FRAMES:
        png = fx["sheets"] / ("%s_%s_r.png" % (CHAR, aid))
        frames, _d, info = P.slice_sheet(png, specs[aid])
        items.append((specs[aid], frames, info))

    res = P.render_device_mock(out / "_device_mock.gif", items, cycles=2)
    im = Image.open(str(out / "_device_mock.gif"))
    assert im.size == (P.DEVICE_W, P.DEVICE_H) == (320, 240), im.size
    assert res["size"] == (320, 240)

    # 影格數 = 循環型播 2 遍 + 單次型播 1 遍
    expect = 0
    for spec, frames, _ in items:
        expect += len(frames) * (2 if spec.loop else 1)
    assert res["frames"] == expect, (res["frames"], expect)

    # 不透明畫布：不能宣告透明索引，否則地板會破洞
    assert "transparency" not in im.info, im.info.get("transparency")

    # 地板與牆面的分界要在 MOCK_FLOOR_Y
    a = np.array(im.convert("RGB"))
    assert tuple(a[10, 300]) == P.MOCK_WALL, tuple(a[10, 300])
    assert tuple(a[P.MOCK_FLOOR_Y + 20, 300]) == P.MOCK_FLOOR, \
        tuple(a[P.MOCK_FLOOR_Y + 20, 300])


@test
def t_健檢的數值(fx):
    """循環秒數、次/分、重複影格。"""
    specs = P.load_anim_specs(fx["anim"])
    loaded = OrderedDict()
    for aid in FRAMES:
        png = fx["sheets"] / ("%s_%s_r.png" % (CHAR, aid))
        loaded[aid] = P.slice_sheet(png, specs[aid])

    palette = P.load_palette(
        fx["root"] / "specs" / "palettes" / ("%s.json" % CHAR))
    lines, errors, warns = P.check_report(specs, loaded, palette)
    text = "\n".join(lines)

    # 4 格 × 750ms = 3.00 秒 = 20.0 次/分
    row = [ln for ln in lines if ln.startswith("idle_breathe")][0]
    assert "3.00" in row, row
    assert "20.0" in row, row
    assert "落在犬隻休息值" in row, row
    # sheet 是 [0,1,1,0] → f0 與 f3 相同、f1 與 f2 相同
    assert "重複影格 f0=3, f1=2" in row, row
    assert errors == 0, text
    assert warns >= 1, text

    # 重複影格的偵測
    frames = [make_frame(0, 3), make_frame(1, 3), make_frame(0, 3)]
    assert P.duplicate_groups(frames) == [[0, 2]]
    assert P.duplicate_groups([make_frame(i, 3) for i in range(3)]) == []


@test
def t_健檢會抓到呼吸太快(fx):
    """把 frame_ms 改成 100 → 2.5 次/秒，遠超生理值，必須被標出來。"""
    specs = P.load_anim_specs(fx["anim"])
    specs["idle_breathe"].frame_ms = 100
    lines, errors, warns = P.check_report(
        OrderedDict([("idle_breathe", specs["idle_breathe"])]), {}, None,
        full_set=False)
    row = [ln for ln in lines if ln.startswith("idle_breathe")][0]
    assert "超出犬隻休息值" in row, row
    assert "150.0" in row, row               # 4 × 100ms = 0.4s → 150 次/分
    assert warns >= 1


@test
def t_健檢沒有_sheet_時退回_track(fx):
    """bake.py 還沒跑也要能用：transform 型的重複影格從 track 就看得出來。"""
    specs = P.load_anim_specs(fx["anim"])
    lines, errors, warns = P.check_report(specs, {}, None)
    text = "\n".join(lines)
    assert "無 sheet，數據來自動畫定義" in text
    assert errors == 0, text
    # idle_breathe 的 track 是 y=0,-1,-1,0 → f0=3 與 f1=2 兩組重複
    row = [ln for ln in lines if ln.startswith("idle_breathe")][0]
    assert "f0=3" in row and "f1=2" in row, row


@test
def t_健檢會抓到半透明像素(fx):
    """半透明 = 被重新採樣過。階段 B 不可能產生這種像素，必須是錯誤。"""
    specs = P.load_anim_specs(fx["anim"])
    bad = make_frame(0, 1)
    bad[31, 21, 3] = 128                     # 偷偷放一個半透明像素
    loaded = {"happy": ([bad], [100], {"layout": "test", "notes": []})}
    sel = OrderedDict([("happy", specs["happy"])])
    sel["happy"].frames = 1
    lines, errors, warns = P.check_report(sel, loaded, None, full_set=False)
    assert errors >= 1, lines
    assert "半透明像素" in "\n".join(lines)


@test
def t_健檢會抓到調色盤外的顏色(fx):
    """鐵律 1：所有影格共用同一組固定調色盤。"""
    specs = P.load_anim_specs(fx["anim"])
    bad = make_frame(0, 1)
    bad[31, 21] = (1, 2, 3, 255)             # 調色盤裡沒有的顏色
    loaded = {"happy": ([bad], [100], {"layout": "test", "notes": []})}
    sel = OrderedDict([("happy", specs["happy"])])
    sel["happy"].frames = 1
    palette = P.load_palette(
        fx["root"] / "specs" / "palettes" / ("%s.json" % CHAR))
    lines, errors, warns = P.check_report(sel, loaded, palette, full_set=False)
    assert errors >= 1, lines
    assert "#010203" in "\n".join(lines), lines


@test
def t_from_master_只做整數位移(fx):
    """master 合成路徑只允許整數平移與水平翻轉，不可以有新顏色。"""
    specs = P.load_anim_specs(fx["anim"])
    master = make_frame(0, 1)[2:, :]          # 64×54，模擬現況的 master 尺寸
    frames, durations, info = P.frames_from_master(master, specs["happy"])

    assert len(frames) == 3
    assert frames[0].shape[:2] == (P.SPRITE_H, P.SPRITE_W), frames[0].shape
    assert durations == [100, 100, 100], durations
    assert info["layout"] == "from-master"

    src = {tuple(int(v) for v in c)
           for c in np.unique(master[master[..., 3] > 0][:, :3], axis=0)}
    for fr in frames:
        got = {tuple(int(v) for v in c)
               for c in np.unique(fr[fr[..., 3] > 0][:, :3], axis=0)}
        assert got <= src, got - src          # 沒有新顏色 = 沒有插值

    # happy 的 f1 是 y=-5，內容應該剛好往上 5 格
    a, b = P.normalise_rgba(frames[0]), P.normalise_rgba(frames[1])
    assert np.array_equal(a[5:], b[:-5]), "位移量不是 5 個像素"


@test
def t_命令列端到端(fx):
    """真的跑一次 CLI，確認參數接線與三個產物都出來了。"""
    out = fx["root"] / "build" / "preview_cli"
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "preview.py"),
         "--character", CHAR,
         "--sheets-dir", str(fx["sheets"]),
         "--out-dir", str(out),
         "--scale", "3"],
        capture_output=True, text=True, cwd=str(fx["root"]))
    assert r.returncode == 0, r.stdout + r.stderr

    for name in ["idle_breathe.gif", "happy.gif", "eat.gif", "eat_happy.gif",
                 "_contact_sheet.png", "_device_mock.gif"]:
        assert (out / name).exists(), "少了 %s\n%s" % (name, r.stdout)

    im = Image.open(str(out / "happy.gif"))
    assert im.size == (P.SPRITE_W * 3, P.SPRITE_H * 3), im.size
    assert Image.open(str(out / "_device_mock.gif")).size == (320, 240)

    # --check 模式
    r2 = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "preview.py"),
         "--character", CHAR, "--sheets-dir", str(fx["sheets"]), "--check"],
        capture_output=True, text=True, cwd=str(fx["root"]))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "次/分" in r2.stdout, r2.stdout
    assert "20.0" in r2.stdout, r2.stdout

    # --anim 只做一個
    out2 = fx["root"] / "build" / "preview_one"
    r3 = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "preview.py"),
         "--character", CHAR, "--sheets-dir", str(fx["sheets"]),
         "--out-dir", str(out2), "--anim", "eat", "--scale", "2"],
        capture_output=True, text=True, cwd=str(fx["root"]))
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert (out2 / "eat.gif").exists()
    assert not (out2 / "eat_happy.gif").exists(), "--anim 沒有生效"


# --------------------------------------------------------------------------

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="preview_test_"))
    try:
        fx = build_fixture(tmp)
        passed = failed = 0
        for fn in TESTS:
            name = fn.__name__[2:].replace("_", " ")
            try:
                fn(fx)
            except AssertionError as e:
                failed += 1
                print("  ✗  %s\n       %s" % (name, e))
            except Exception as e:                          # noqa: BLE001
                failed += 1
                print("  ✗  %s\n       %s: %s" % (name, type(e).__name__, e))
            else:
                passed += 1
                print("  ✓  %s" % name)

        print("\n%d 通過 / %d 失敗（共 %d 項）" % (passed, failed, len(TESTS)))
        return 1 if failed else 0
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
