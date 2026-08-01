#!/usr/bin/env python3
"""
test_bake.py — bake.py 的自我測試

用合成的假圖層驗證階段 B 的四個契約：

    1. 整數平移        位移 (dx, dy) 之後，像素叢集必須是「原封不動地搬家」，
                      一個像素都不能多、不能少、不能變色
    2. z 疊合          七個圖層群的疊合順序由 rig.json 的 z 推導，上層蓋下層
    3. 調色盤檢查      出現調色盤外的顏色必須中止，不是修一修放過
    4. 逐位元決定性    同樣的輸入跑兩次，產出的每一個 byte 都相同

第 4 條是整個管線的地基（見 docs/07 第一節）。它成立，才能說「改動畫改 JSON
重新烘焙」不會有人偷偷在影像編輯器裡動過手。

    .venv/bin/python tools/test_bake.py
"""

import hashlib
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bake  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
PALETTE = ROOT / "specs/palettes/brown_mixed.json"
RIG = ROOT / "art/rigs/brown_mixed/rig.json"

FW, FH = 64, 56
CHAR = "testdog"

# 從真實調色盤挑的顏色。用真檔案而不是假調色盤，
# 這樣測試同時證明了工具和 specs/palettes/brown_mixed.json 是相容的。
C_OUTLINE = (0x1E, 0x14, 0x10)      # outline
C_COAT = (0x4A, 0x32, 0x25)         # coat_mid
C_TAN = (0xB0, 0x80, 0x50)          # tan_mid
C_IRIS = (0xA8, 0x7A, 0x3C)         # eye_iris
C_TONGUE = (0xC0, 0x6A, 0x6A)       # tongue_pink
C_SHADOW = (0x24, 0x1A, 0x14)       # contact_shadow
C_COLLAR = (0x3C, 0x7F, 0xC4)       # collar_accent
C_OFFPAL = (0x12, 0x34, 0x56)       # 故意不在調色盤裡

# 每個假圖層畫一個矩形：group -> (x, y, w, h, 顏色)
FAKE_RECTS = {
    "core": (20, 30, 24, 16, C_COAT),
    "head": (40, 18, 14, 14, C_TAN),
    "ear_far": (38, 12, 6, 10, C_SHADOW),
    "ear_near": (48, 12, 6, 10, C_COLLAR),
    "tail": (12, 26, 8, 6, C_OUTLINE),
    "eyelid": (44, 22, 6, 3, C_IRIS),
    "jaw": (44, 30, 10, 4, C_TONGUE),
}

# 蓄意安排的重疊，用來驗證 z 疊合：
#   head(62) 蓋過 core(41)          -> (40,30) 這格必須是 head 的顏色
#   ear_near(70) 蓋過 head          -> (48,18) 這格必須是 ear_near 的顏色
#   core(41) 蓋過 tail(10)          -> (20,30) 這格必須是 core 的顏色
OVERLAP_CASES = [
    ((40, 30), C_TAN, "head 蓋過 core"),
    ((48, 18), C_COLLAR, "ear_near 蓋過 head"),
    ((20, 30), C_COAT, "core 蓋過 tail"),
]


# --------------------------------------------------------------------------
# 測試素材
# --------------------------------------------------------------------------

def make_layer(rect, w=FW, h=FH):
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    x, y, rw, rh, colour = rect
    arr[y:y + rh, x:x + rw, :3] = colour
    arr[y:y + rh, x:x + rw, 3] = 255
    return arr


def write_png(arr, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)


def build_parts(parts_dir, poses=("stand", "sit"), corrupt=None):
    """產生假的像素領域圖層。corrupt=(pose, group) 的那張會被塞進調色盤外的顏色。"""
    for pose in poses:
        for group, rect in FAKE_RECTS.items():
            arr = make_layer(rect)
            if pose == "sit":
                # 坐姿把每個矩形往下挪 4 px，模擬「不同姿勢是不同剪影」
                arr = np.roll(arr, 4, axis=0)
                arr[:4] = 0
            if corrupt == (pose, group):
                arr[2, 2, :3] = C_OFFPAL
                arr[2, 2, 3] = 255
            write_png(arr, parts_dir / ("%s_%s_%s.png" % (CHAR, pose, group)))


def build_master(path, w=64, h=54):
    """假的 master sprite：比畫布矮 2 px，重現真實情況（實測 master 是 64x54）。"""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[0:h, 10:30, :3] = C_COAT
    arr[0:h, 10:30, 3] = 255
    arr[0:4, 10:14, :3] = C_TAN          # 左上角做記號，翻轉測試要用
    write_png(arr, path)


def anim_doc(animations, defaults=None):
    return {
        "character_id": CHAR,
        "defaults": defaults or {"fps": 8, "loop": False, "type": "transform"},
        "animations": animations,
    }


def write_anim(path, animations, defaults=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(anim_doc(animations, defaults),
                               indent=2, ensure_ascii=False))
    return path


def run_bake(tmp, anim_path, out_dir, parts_dir=None, **kw):
    """呼叫 bake.run，並把「單一動畫失敗」也升級成例外。

    bake.run 的設計是一次列出所有動畫的問題再回報（離開碼非零），
    測試要的則是「這一項有沒有被擋下來」，所以在這裡轉成 BakeError。
    """
    kw.setdefault("scale", 0)
    result = bake.run(
        character=CHAR,
        parts_dir=parts_dir if parts_dir is not None else tmp / "pixparts",
        anim_path=anim_path,
        out_dir=out_dir,
        palette_path=PALETTE,
        master_path=tmp / "master.png",
        rig_path=RIG,
        frame_w=FW, frame_h=FH,
        quiet=True,
        **kw
    )
    if result["failures"]:
        raise bake.BakeError(result["failures"][0]["error"])
    return result


def bake_it(tmp, animations, out_name=None, defaults=None, **kw):
    """跑一次 bake，回傳 (result, 影格陣列的字典)。

    每個測試各用一個輸出目錄，避免上一個測試的殘檔混進來。
    """
    out_name = out_name or animations[0]["id"]
    anim_path = write_anim(tmp / (out_name + "_anim.json"), animations, defaults)
    out_dir = tmp / out_name
    result = run_bake(tmp, anim_path, out_dir, **kw)
    sheets = {}
    for entry in result["index"]["animations"]:
        sheet = np.array(Image.open(out_dir / entry["sheet"]).convert("RGBA"))
        sheets[entry["id"]] = [sheet[:, i * FW:(i + 1) * FW]
                               for i in range(entry["frame_count"])]
    return result, sheets


def rect_of(frame, colour):
    """回傳該顏色的所有像素座標集合。"""
    m = (frame[..., 3] > 0) & np.all(frame[..., :3] == np.array(colour, np.uint8), -1)
    ys, xs = np.where(m)
    return set(zip(xs.tolist(), ys.tolist()))


def colours_of(frame):
    """畫面上出現過的顏色集合（只算不透明像素）。"""
    return set(map(tuple, frame[..., :3][frame[..., 3] > 0].tolist()))


def hash_dir(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(d).iterdir()) if p.is_file()}


# --------------------------------------------------------------------------
# 迷你測試框架
# --------------------------------------------------------------------------

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def expect_error(fn, needle, label):
    try:
        fn()
    except bake.BakeError as e:
        assert needle in str(e), "%s：錯誤訊息沒提到 %r，實際是 %s" % (label, needle, e)
        return str(e)
    raise AssertionError("%s：預期要報錯，結果沒有" % label)


# --------------------------------------------------------------------------
# 1. 整數平移
# --------------------------------------------------------------------------

@case
def test_整數平移是原封不動的搬家(tmp):
    """tail 在 f1 位移 (+3, -2)：像素數量、顏色、相對形狀都必須完全不變。"""
    _r, sheets = bake_it(tmp, [{
        "id": "shift", "type": "rig", "pose": "stand",
        "frames": 3, "frame_ms": 100,
        "layers": {"tail": [{"f": 1, "dx": 3, "dy": -2}]},
    }])
    f = sheets["shift"]
    a, b = rect_of(f[0], C_OUTLINE), rect_of(f[1], C_OUTLINE)
    assert a, "第 0 格找不到 tail"
    assert len(a) == len(b) == 48, "tail 像素數變了：%d -> %d" % (len(a), len(b))
    moved = set((x + 3, y - 2) for x, y in a)
    assert b == moved, "位移後的像素座標不等於原座標 +(3,-2)"
    # 位移不可以動到任何顏色值
    assert colours_of(f[1]) == colours_of(f[0]), "位移改變了畫面上的顏色集合"


@case
def test_步進取值不補間(tmp):
    """f0 與 f2 有關鍵影格，f1 必須完全等於 f0（保持前值），不是兩者的中間值。"""
    # 往左移，避開 core 的矩形，才驗得到「整塊搬家」而不是被遮掉一半
    _r, sheets = bake_it(tmp, [{
        "id": "step", "type": "rig", "pose": "stand",
        "frames": 4, "frame_ms": 100,
        "layers": {"tail": [{"f": 0, "dx": 0}, {"f": 2, "dx": -4}]},
    }], out_name="step")
    f = sheets["step"]
    assert np.array_equal(f[0], f[1]), "f1 不等於 f0，補間了"
    assert np.array_equal(f[2], f[3]), "f3 不等於 f2，關鍵影格沒有延續到結尾"
    assert not np.array_equal(f[1], f[2]), "f2 應該已經位移"
    a = rect_of(f[0], C_OUTLINE)
    assert rect_of(f[2], C_OUTLINE) == set((x - 4, y) for x, y in a)


@case
def test_每個欄位各自延續(tmp):
    """{"f":2,"show":false} 不需要重寫 dx/dy，dx 應該延續 f1 的值。"""
    _r, sheets = bake_it(tmp, [{
        "id": "carry", "type": "rig", "pose": "stand",
        "frames": 4, "frame_ms": 100,
        "layers": {"tail": [{"f": 1, "dx": 5}, {"f": 2, "show": False},
                            {"f": 3, "show": True}]},
    }])
    f = sheets["carry"]
    assert not rect_of(f[2], C_OUTLINE), "show=false 沒有把圖層藏起來"
    assert rect_of(f[3], C_OUTLINE) == rect_of(f[1], C_OUTLINE), \
        "f3 的 dx 沒有延續 f1 的 5"


# --------------------------------------------------------------------------
# 2. z 疊合
# --------------------------------------------------------------------------

@case
def test_z疊合順序由rig推導(tmp):
    order, src = bake.layer_draw_order(RIG)
    assert order == ["tail", "core", "ear_far", "jaw", "head", "eyelid", "ear_near"], \
        "從 rig.json 推出的圖層順序不對：%s" % order
    assert "rig.json" in src
    fallback, _ = bake.layer_draw_order(None)
    assert fallback == order, "後備順序與 rig.json 推導的結果不一致"


@case
def test_接受階段A報告的疊合順序(tmp):
    """assemble.py --report 的 group_draw_order 是權威，bake.py 要照它畫。

    階段 A 給人核可的預覽圖是照那個順序疊的，階段 B 畫不一樣就等於
    核可過的姿勢和實際烘出來的不是同一張。
    """
    rev = list(reversed(bake.FALLBACK_LAYER_ORDER))
    rp = tmp / "assemble_report.json"
    rp.write_text(json.dumps({"group_draw_order": rev}))
    r = run_bake(tmp, write_anim(tmp / "rep.json", SOLO_ANIM), tmp / "rep",
                 assemble_report=rp)
    assert r["index"]["layer_order"] == rev, "沒有採用報告裡的順序"

    bad = tmp / "bad_report.json"
    bad.write_text(json.dumps({"group_draw_order": ["core", "head"]}))
    expect_error(
        lambda: run_bake(tmp, tmp / "rep.json", tmp / "rep2", assemble_report=bad),
        "對不起來", "報告的圖層群不齊")


@case
def test_上層蓋下層(tmp):
    _r, sheets = bake_it(tmp, [{
        "id": "zorder", "type": "rig", "pose": "stand",
        "frames": 1, "frame_ms": 100, "layers": {},
    }])
    f = sheets["zorder"][0]
    for (x, y), colour, label in OVERLAP_CASES:
        got = tuple(int(v) for v in f[y, x, :3])
        assert got == colour, "%s 失敗：(%d,%d) 是 %s，應該是 %s" % (
            label, x, y, bake.hex_of(got), bake.hex_of(colour))


@case
def test_不做alpha混合(tmp):
    """重疊處只能是上層的原色，不能出現任何混合出來的新顏色。"""
    _r, sheets = bake_it(tmp, [{
        "id": "noblend", "type": "rig", "pose": "stand",
        "frames": 1, "frame_ms": 100, "layers": {},
    }])
    used = colours_of(sheets["noblend"][0])
    expected = set(r[4] for r in FAKE_RECTS.values())
    assert used <= expected, "合成產生了來源沒有的顏色：%s" % [
        bake.hex_of(c) for c in sorted(used - expected)]


# --------------------------------------------------------------------------
# 3. 硬性檢查
# --------------------------------------------------------------------------

@case
def test_調色盤外的顏色要中止(tmp):
    # 壞素材放在自己的目錄，不污染其他測試共用的 pixparts
    bad_parts = tmp / "offpal_parts"
    build_parts(bad_parts, corrupt=("stand", "core"))
    msg = expect_error(
        lambda: run_bake(tmp,
                         write_anim(tmp / "offpal.json", [{
                             "id": "bad", "type": "rig", "pose": "stand",
                             "frames": 1, "frame_ms": 100, "layers": {}}]),
                         tmp / "offpal", parts_dir=bad_parts),
        "調色盤外", "調色盤檢查")
    assert "#123456" in msg, "錯誤訊息沒指出是哪個顏色：%s" % msg
    assert "(2,2)" in msg, "錯誤訊息沒指出座標：%s" % msg


@case
def test_小數位移要中止(tmp):
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "frac", "type": "rig", "pose": "stand",
            "frames": 2, "frame_ms": 100,
            "layers": {"tail": [{"f": 1, "dx": 1.5}]},
        }], out_name="frac"),
        "小數", "小數位移")
    # 數值上是整數的 2.0 一樣要擋——只要欄位可以是浮點數，遲早會有人寫 2.5
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "fl", "type": "rig", "pose": "stand",
            "frames": 2, "frame_ms": 100,
            "layers": {"tail": [{"f": 1, "dy": 2.0}]},
        }], out_name="fl"),
        "小數", "整數值的浮點數")
    # transform 的整體位移走同一條檢查
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "tf", "type": "transform", "frames": 2, "frame_ms": 100,
            "track": [{"f": 1, "y": -0.5}],
        }], out_name="tf"),
        "小數", "transform 的小數位移")


@case
def test_打錯字要中止(tmp):
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "typo", "type": "rig", "pose": "stand",
            "frames": 2, "frame_ms": 100,
            "layers": {"tail": [{"f": 1, "dxx": 2}]},
        }], out_name="typo"),
        "未知欄位", "欄位打錯字")
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "badlayer", "type": "rig", "pose": "stand",
            "frames": 1, "frame_ms": 100,
            "layers": {"ear": [{"f": 0, "dx": 1}]},
        }], out_name="badlayer"),
        "不存在的圖層群", "圖層名打錯字")
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "oob", "type": "rig", "pose": "stand",
            "frames": 2, "frame_ms": 100,
            "layers": {"tail": [{"f": 5, "dx": 1}]},
        }], out_name="oob"),
        "之外", "關鍵影格超出範圍")
    # transform 型帶著 layers 是最危險的一種寫法：看起來像部件在動，其實沒有
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "mixed", "type": "transform", "frames": 2, "frame_ms": 100,
            "layers": {"tail": [{"f": 1, "dx": 2}]},
        }], out_name="mixed"),
        "只有 rig 型看得懂", "transform 型誤放 layers")


@case
def test_影格尺寸一致(tmp):
    r, sheets = bake_it(tmp, [{
        "id": "sizes", "type": "rig", "pose": "stand",
        "frames": 4, "frame_ms": 100,
        "layers": {"head": [{"f": 2, "dx": 2, "dy": 2}]},
    }])
    frames = sheets["sizes"]
    assert len(set(f.shape for f in frames)) == 1, "影格尺寸不一致"
    assert frames[0].shape == (FH, FW, 4)
    entry = r["index"]["animations"][0]
    meta = json.loads((r["out_dir"] / entry["atlas"]).read_text())
    assert meta["sheet_size"] == [FW * 4, FH]
    for i, fr in enumerate(meta["frames"]):
        assert fr["x"] == i * FW and fr["y"] == 0
        assert fr["w"] == FW and fr["h"] == FH


SOLO_ANIM = [{"id": "solo", "type": "rig", "pose": "stand",
              "frames": 1, "frame_ms": 100, "layers": {}}]


@case
def test_圖層尺寸不對要中止(tmp):
    """圖層必須是完整畫布。一旦允許裁切過的圖層就得記 offset，
    階段 B 也就不再只是整數平移了。"""
    bad = tmp / "badsize_parts"
    build_parts(bad)
    write_png(make_layer(FAKE_RECTS["core"], w=40, h=30),
              bad / ("%s_stand_core.png" % CHAR))
    expect_error(
        lambda: run_bake(tmp, write_anim(tmp / "badsize.json", SOLO_ANIM),
                         tmp / "badsize", parts_dir=bad),
        "完整畫布", "圖層尺寸檢查")


@case
def test_缺圖層要中止(tmp):
    miss = tmp / "miss_parts"
    build_parts(miss)
    (miss / ("%s_stand_eyelid.png" % CHAR)).unlink()
    anim = write_anim(tmp / "miss.json", SOLO_ANIM)

    expect_error(lambda: run_bake(tmp, anim, tmp / "miss0", parts_dir=miss),
                 "缺少", "缺圖層")
    # 明確允許時才當成全透明，而且要留下紀錄
    r = run_bake(tmp, anim, tmp / "miss1", parts_dir=miss,
                 allow_missing_layers=True)
    assert r["ok"] and r["index"]["missing_layers"] == ["stand/eyelid"]


@case
def test_半透明要中止(tmp):
    """半透明在 4bpp 索引色裡表達不了，而且會逼合成階段做 alpha 混合。"""
    semi = tmp / "semi_parts"
    build_parts(semi)
    arr = make_layer(FAKE_RECTS["core"])
    arr[35, 25, 3] = 128
    write_png(arr, semi / ("%s_stand_core.png" % CHAR))
    expect_error(
        lambda: run_bake(tmp, write_anim(tmp / "semi.json", SOLO_ANIM),
                         tmp / "semi", parts_dir=semi),
        "半透明", "半透明檢查")


# --------------------------------------------------------------------------
# 4. 翻轉、姿勢、transform
# --------------------------------------------------------------------------

@case
def test_水平翻轉以畫布中線為軸(tmp):
    _r, sheets = bake_it(tmp, [{
        "id": "flip", "type": "rig", "pose": "stand",
        "frames": 2, "frame_ms": 100,
        "track": [{"f": 1, "flip": 1}], "layers": {},
    }])
    a, b = sheets["flip"]
    assert np.array_equal(b, a[:, ::-1, :]), "翻轉不是單純的水平鏡射"
    assert colours_of(a) == colours_of(b), "翻轉改變了顏色集合"


@case
def test_翻轉後才位移(tmp):
    """先翻轉再位移，x 才永遠是「往畫面右邊」。"""
    _r, sheets = bake_it(tmp, [{
        "id": "fx", "type": "rig", "pose": "stand",
        "frames": 2, "frame_ms": 100,
        "track": [{"f": 0, "flip": 1}, {"f": 1, "flip": 1, "x": 2}],
        "layers": {},
    }])
    a, b = sheets["fx"]
    shifted = np.zeros_like(a)
    shifted[:, 2:] = a[:, :-2]
    assert np.array_equal(b, shifted), "flip 之後的 x 不是往右移"


@case
def test_逐格切換姿勢(tmp):
    """sit_down 這種動畫要能逐格指定 pose。"""
    _r, sheets = bake_it(tmp, [{
        "id": "sit_down", "type": "rig", "pose": "stand",
        "frames": 4, "frame_ms": 100,
        "pose_track": [{"f": 2, "pose": "sit"}],
        "layers": {},
    }])
    f = sheets["sit_down"]
    assert np.array_equal(f[0], f[1]), "pose_track 在 f2 才切換，f1 應該還是 stand"
    assert np.array_equal(f[2], f[3]), "pose 沒有延續到結尾"
    # sit 的假素材是把 stand 整體下移 4 px
    expect = np.zeros_like(f[0])
    expect[4:] = f[0][:-4]
    assert np.array_equal(f[2], expect), "f2 不是 sit 姿勢的圖層"


@case
def test_transform用master並底部對齊(tmp):
    r, sheets = bake_it(tmp, [{
        "id": "idle", "type": "transform", "frames": 2, "frame_ms": 100,
        "track": [{"f": 0, "y": 0}, {"f": 1, "y": -1}],
    }])
    f = sheets["idle"]
    ys = np.where(f[0][..., 3] > 0)[0]
    assert ys.max() == FH - 1, "master 沒有底部對齊（最下面一列應該有像素）"
    assert ys.min() == 2, "64x54 放進 64x56 應該從第 2 列開始，實際是 %d" % ys.min()
    a = rect_of(f[0], C_COAT)
    assert rect_of(f[1], C_COAT) == set((x, y - 1) for x, y in a), "y=-1 沒有整體上移 1px"
    assert r["index"]["sources"]["master_offset"] == [0, 2]


@case
def test_越界像素被計數(tmp):
    r, sheets = bake_it(tmp, [{
        "id": "clip", "type": "rig", "pose": "stand",
        "frames": 2, "frame_ms": 100,
        "layers": {"tail": [{"f": 1, "dx": -20}]},
    }])
    entry = r["index"]["animations"][0]
    assert entry["clipped_px"] == 48, "被裁掉的像素數不對：%d" % entry["clipped_px"]
    assert not rect_of(sheets["clip"][1], C_OUTLINE), "越界的部分應該完全消失"
    # --strict-clip 要把它升級成錯誤
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "clip2", "type": "rig", "pose": "stand",
            "frames": 2, "frame_ms": 100,
            "layers": {"tail": [{"f": 1, "dx": -20}]},
        }], out_name="clip2", strict_clip=True),
        "裁掉", "strict-clip")


@case
def test_frame_ms是權威欄位(tmp):
    r, _s = bake_it(tmp, [
        {"id": "a", "type": "rig", "pose": "stand", "frames": 2,
         "fps": 1.33, "frame_ms": 750, "layers": {}},
        {"id": "b", "type": "rig", "pose": "stand", "frames": 2,
         "fps": 8, "layers": {}},
        {"id": "c", "type": "rig", "pose": "stand", "frames": 3,
         "frame_ms": 100, "track": [{"f": 1, "ms": 400}], "layers": {}},
    ])
    got = dict((e["id"], e["frame_ms"]) for e in r["index"]["animations"])
    assert got["a"] == 750, "有 frame_ms 時不該用 fps 反推"
    assert got["b"] == 125, "沒有 frame_ms 時要用 fps 反推，得到 %s" % got["b"]
    meta = json.loads((r["out_dir"] / ("%s_c.json" % CHAR)).read_text())
    assert [fr["ms"] for fr in meta["frames"]] == [100, 400, 400], \
        "逐格 ms 沒有步進延續"
    assert meta["total_ms"] == 900


# --------------------------------------------------------------------------
# 5. 逐位元決定性
# --------------------------------------------------------------------------

@case
def test_跑兩次逐位元相同(tmp):
    """同樣的輸入跑兩次，產出的每一個 byte 都必須相同。

    這是 docs/07 第一節的硬性判準。它成立才能說沒有任何一步是手工做的。
    """
    anims = [
        {"id": "tail_wag", "type": "rig", "pose": "stand", "loop": True,
         "frames": 4, "frame_ms": 125,
         "layers": {"tail": [{"f": 0, "dx": 0, "dy": 0}, {"f": 2, "dx": 2, "dy": -1}],
                    "ear_near": [{"f": 1, "dy": -1}]}},
        {"id": "sit_down", "type": "rig", "pose": "stand", "frames": 4,
         "frame_ms": 125, "pose_track": [{"f": 2, "pose": "sit"}],
         "layers": {"head": [{"f": 2, "dy": 2}]}},
        {"id": "turn", "type": "transform", "frames": 2, "frame_ms": 125,
         "track": [{"f": 0}, {"f": 1, "flip": 1}]},
    ]
    anim_path = write_anim(tmp / "det_anim.json", anims)

    r1 = run_bake(tmp, anim_path, tmp / "det", scale=8)
    h1 = hash_dir(r1["out_dir"])
    r2 = run_bake(tmp, anim_path, tmp / "det", scale=8)
    h2 = hash_dir(r2["out_dir"])
    assert h1 == h2, "重跑後這些檔案變了：%s" % sorted(
        k for k in h1 if h1.get(k) != h2.get(k))
    # 每個動畫 3 個檔（sheet / atlas / 放大預覽）+ 1 個索引
    assert len(h1) == 3 * 3 + 1, "產出的檔案數不對：%s" % sorted(h1)

    # 換一個輸出目錄也要一樣——atlas 裡不能記到會隨執行環境變動的路徑
    r3 = run_bake(tmp, anim_path, tmp / "det2", scale=8)
    h3 = hash_dir(r3["out_dir"])
    assert h1 == h3, "換輸出目錄後檔案內容變了：%s" % sorted(
        k for k in h1 if h1.get(k) != h3.get(k))


@case
def test_索引涵蓋全部動畫(tmp):
    r, _s = bake_it(tmp, [
        {"id": "one", "type": "rig", "pose": "stand", "frames": 2,
         "frame_ms": 100, "tier": 1, "layers": {}},
        {"id": "two", "type": "transform", "frames": 3, "frame_ms": 100,
         "tier": 0, "loop": True},
    ])
    idx = r["index"]
    assert idx["animation_count"] == 2 and idx["total_frames"] == 5
    assert idx["frame_size"] == [FW, FH]
    assert idx["layer_order"] == ["tail", "core", "ear_far", "jaw",
                                  "head", "eyelid", "ear_near"]
    assert not idx["failed"]
    for e in idx["animations"]:
        p = r["out_dir"] / e["sheet"]
        assert p.exists() and e["sha256_12"] == bake.sha12(p)
    assert (r["out_dir"] / ("%s_atlas.json" % CHAR)).exists()


@case
def test_失敗時不寫索引(tmp):
    """有動畫失敗就不寫索引，下游寧可立刻找不到檔案，也不要拿到缺格的資產包。"""
    out = tmp / "partial"
    r = bake.run(character=CHAR, parts_dir=tmp / "pixparts",
                 anim_path=write_anim(tmp / "a5.json", [
                     {"id": "good", "type": "rig", "pose": "stand",
                      "frames": 1, "frame_ms": 100, "layers": {}},
                     {"id": "bad", "type": "rig", "pose": "stand",
                      "frames": 1, "frame_ms": 100,
                      "layers": {"tail": [{"f": 0, "dx": 0.5}]}},
                 ]),
                 out_dir=out, palette_path=PALETTE, master_path=tmp / "master.png",
                 rig_path=RIG, frame_w=FW, frame_h=FH, scale=0, quiet=True)
    assert not r["ok"] and len(r["failures"]) == 1
    assert r["failures"][0]["id"] == "bad"
    assert not (out / ("%s_atlas.json" % CHAR)).exists(), "失敗時不該寫索引"

    # 先成功一次留下舊索引，再失敗一次：舊索引必須被刪掉，
    # 否則「索引存在」就不再等於「上一次烘焙全部成功」
    ok_anim = write_anim(tmp / "a6.json", SOLO_ANIM)
    run_bake(tmp, ok_anim, out)
    assert (out / ("%s_atlas.json" % CHAR)).exists()
    bake.run(character=CHAR, parts_dir=tmp / "pixparts", anim_path=tmp / "a5.json",
             out_dir=out, palette_path=PALETTE, master_path=tmp / "master.png",
             rig_path=RIG, frame_w=FW, frame_h=FH, scale=0, quiet=True)
    assert not (out / ("%s_atlas.json" % CHAR)).exists(), "失敗時沒有刪掉舊索引"

    r2 = bake.run(character=CHAR, parts_dir=tmp / "pixparts",
                  anim_path=tmp / "a5.json", out_dir=out, palette_path=PALETTE,
                  master_path=tmp / "master.png", rig_path=RIG,
                  frame_w=FW, frame_h=FH, scale=0, quiet=True, keep_going=True)
    assert not r2["ok"]
    idx = json.loads((out / ("%s_atlas.json" % CHAR)).read_text())
    assert [f["id"] for f in idx["failed"]] == ["bad"], "--keep-going 要把失敗記進索引"


# --------------------------------------------------------------------------
# 6. 階段 A 與階段 B 的接縫
# --------------------------------------------------------------------------

@case
def test_七層零位移合成等於原姿勢(tmp):
    """七張圖層在零位移下合成，必須逐位元等於階段 A 那張姿勢圖。

    這是階段 A 與階段 B 的接縫，也是「階段 B 不可能閃爍」的證據：
    合不回去就代表拆層時漏了像素、多了像素，或格線錨點漂了。

    這裡用真的 master sprite 當素材，並且刻意用「交錯」而不是「切區塊」的方式
    拆成七層——順便驗證上層的透明像素不會在下層打洞（合成是遮罩覆蓋，
    不是整塊搬運）。
    """
    master_path = ROOT / "art/approved/brown_mixed/master_stand_r_64px.png"
    if not master_path.exists():
        return "跳過（找不到 master sprite）"
    src = bake.load_rgba(master_path, "master")
    expect, _off = bake.place_master(src, FW, FH, lambda *_a: None)

    parts = tmp / "split_parts"
    groups = sorted(bake.LAYER_GROUPS)
    ys, xs = np.where(expect[..., 3] > 0)
    for i, g in enumerate(groups):
        layer = np.zeros_like(expect)
        sel = ((xs * 3 + ys * 5) % len(groups)) == i
        layer[ys[sel], xs[sel]] = expect[ys[sel], xs[sel]]
        write_png(layer, parts / ("%s_master_%s.png" % (CHAR, g)))

    r = run_bake(tmp, write_anim(tmp / "split.json", [{
        "id": "rejoin", "type": "rig", "pose": "master",
        "frames": 1, "frame_ms": 100, "layers": {}}]),
        tmp / "split", parts_dir=parts)
    got = np.array(Image.open(
        r["out_dir"] / ("%s_rejoin.png" % CHAR)).convert("RGBA"))
    assert np.array_equal(got, expect), "七層合成後與原始姿勢圖不一致"
    return "%d 個不透明像素完全復原" % len(ys)


# --------------------------------------------------------------------------
# 7. 真實資料的煙霧測試
# --------------------------------------------------------------------------

@case
def test_真實動畫定義跑得過(tmp):
    """用真檔案跑一次完整的階段 B。

    這裡的每一項輸入都是版控裡的真東西，不是造出來的假素材：
    specs/animations/brown_mixed.anim.json（5 個 transform + 16 個 rig）、
    build/pixparts 的 21 張像素領域圖層、真的 master、真的調色盤、真的 rig。

    **parts_dir 一定要指向真的 build/pixparts。** 其他測試共用的 tmp/pixparts
    是 build_parts() 造的假圖層，檔名前綴是 CHAR（"testdog"），character 一旦
    傳 "brown_mixed" 就一張都對不上。16 個動畫改成 rig 型之前這個錯誤看不出來，
    因為全是 transform 型、根本不讀圖層。
    """
    real_anim = ROOT / "specs/animations/brown_mixed.anim.json"
    master = ROOT / "art/approved/brown_mixed/master_stand_r_64px.png"
    parts = ROOT / "build/pixparts"
    if not real_anim.exists() or not master.exists():
        return "跳過（找不到真實資產）"
    if not list(parts.glob("brown_mixed_*.png")):
        return "跳過（build/pixparts 是空的，先跑 assemble.py + pixelate.py）"
    r = bake.run(character="brown_mixed", parts_dir=parts,
                 anim_path=real_anim, out_dir=tmp / "real",
                 palette_path=PALETTE, master_path=master, rig_path=RIG,
                 frame_w=FW, frame_h=FH, scale=0, quiet=True)
    assert r["ok"], "真實動畫定義烘焙失敗：%s" % r["failures"]
    assert r["index"]["animation_count"] == 21
    rig_n = sum(1 for e in r["index"]["animations"] if e.get("type") == "rig")
    clipped = [(e["id"], e["clipped_px"]) for e in r["index"]["animations"]
               if e["clipped_px"]]
    return "21 個動畫（rig %d）%d 格；越界：%s" % (
        rig_n, r["index"]["total_frames"],
        ", ".join("%s=%dpx" % c for c in clipped) or "無")


# --------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="bake_test_"))
    try:
        build_parts(tmp / "pixparts")
        build_master(tmp / "master.png")

        print("bake.py 自我測試  素材：%s" % tmp)
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
