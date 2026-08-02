#!/usr/bin/env python3
"""
test_bake.py — bake.py 的自我測試

用合成的假素材驗證烘焙的四個契約：

    1. 整數平移        位移 (dx, dy) 之後，像素叢集必須是「原封不動地搬家」，
                      一個像素都不能多、不能少、不能變色
    2. 調色盤檢查      出現調色盤外的顏色必須中止，不是修一修放過
    3. 半透明拒絕      alpha 只能是 0 或 255；4bpp 索引色沒有半透明，
                      而且混合會產生調色盤外的中間色
    4. 逐位元決定性    同樣的輸入跑兩次，產出的每一個 byte 都相同

第 4 條是整個管線的地基（見 docs/07 第一節）。它成立，才能說「改動畫改 JSON
重新烘焙」不會有人偷偷在影像編輯器裡動過手。

**這份測試在 2026-08-02 重寫過。** 原本 25 項全部寫在已移除的 rig 型上，
夾具是七張假的「像素領域圖層」。rig 型的程式碼刪掉之後那些測試一項都跑不動了，
但它們測的不變式對現存的兩種型別一樣成立，所以是把夾具換掉而不是把測試刪掉：

    transform 型  整張 master + track 的整數位移與水平翻轉
                  -> 平移、翻轉、步進取值、欄位延續 都在這裡測
    frames 型     一個目錄的影格 PNG 串成 spritesheet
                  -> 影格數一致、反向播放、欄位誤用 在這裡測

只有真正隨 rig 消失的才刪掉（多圖層的 z 疊合、上層蓋下層、缺圖層的後備、
逐格切換姿勢）——現在一格只有一張圖，那些沒有對應的程式碼路徑了。

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

FW, FH = 64, 56
CHAR = "testdog"

# 從真實調色盤挑的顏色。用真檔案而不是假調色盤，
# 這樣測試同時證明了工具和 specs/palettes/brown_mixed.json 是相容的。
C_COAT = (0x4A, 0x32, 0x25)         # coat_mid
C_TAN = (0xB0, 0x80, 0x50)          # tan_mid
C_OFFPAL = (0x12, 0x34, 0x56)       # 故意不在調色盤裡


# --------------------------------------------------------------------------
# 素材
# --------------------------------------------------------------------------

def write_png(arr, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path)


def build_master(path, w=64, h=54):
    """假的 master sprite：比畫布矮 2 px，重現真實情況（實測 master 是 64x54）。"""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[0:h, 10:30, :3] = C_COAT
    arr[0:h, 10:30, 3] = 255
    arr[0:4, 10:14, :3] = C_TAN          # 左上角做記號，翻轉測試要用
    write_png(arr, path)


def build_frames(fdir, n=4, corrupt=None, size=None, semi=False):
    """產生假的影格目錄。

    檔名要符合 bake.py 的 glob（`*_f<N>_64px.png`）——那個樣式是 pixelate.py
    的輸出檔名決定的，不是自己取的。

    corrupt=i   第 i 格塞一個調色盤外的顏色
    size=(w,h)  第 0 格用不同尺寸，測「影格尺寸一致」
    semi=True   第 0 格塞一個半透明像素
    """
    fdir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        w, h = (size if (size and i == 0) else (FW, FH))
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        # 每格的方塊往右挪 3 px，這樣「哪一格是哪一格」看得出來
        x0 = 8 + i * 3
        arr[20:36, x0:x0 + 12, :3] = C_COAT
        arr[20:36, x0:x0 + 12, 3] = 255
        arr[20:23, x0:x0 + 3, :3] = C_TAN          # 左上角記號
        if corrupt == i:
            arr[2, 2, :3] = C_OFFPAL
            arr[2, 2, 3] = 255
        if semi and i == 0:
            arr[3, 3, :3] = C_COAT
            arr[3, 3, 3] = 128
        write_png(arr, fdir / ("%s_f%d_64px.png" % (CHAR, i)))
    return fdir


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


def run_bake(tmp, anim_path, out_dir, **kw):
    """呼叫 bake.run，並把「單一動畫失敗」也升級成例外。

    bake.run 的設計是一次列出所有動畫的問題再回報（離開碼非零），
    測試要的則是「這一項有沒有被擋下來」，所以在這裡轉成 BakeError。
    """
    kw.setdefault("scale", 0)
    result = bake.run(
        character=CHAR,
        anim_path=anim_path,
        out_dir=out_dir,
        palette_path=PALETTE,
        master_path=tmp / "master.png",
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


def frames_anim(tmp, aid, n=4, **kw):
    """frames 型動畫的常用寫法。"""
    d = {"id": aid, "type": "frames", "frames": n,
         "frames_dir": str(tmp / ("fr_" + aid))}
    d.update(kw)
    return d


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
# 1. 整數平移（transform 型）
# --------------------------------------------------------------------------

@case
def test_整數平移是原封不動的搬家(tmp):
    """位移之後像素叢集必須完全相同，只是座標整體加了 (dx, dy)。"""
    _, sh = bake_it(tmp, [{
        "id": "shift", "type": "transform", "frames": 2,
        "track": [{"f": 0, "x": 0, "y": 0}, {"f": 1, "x": 5, "y": -3}],
    }])
    a, b = sh["shift"]
    ra, rb = rect_of(a, C_COAT), rect_of(b, C_COAT)
    moved = {(x + 5, y - 3) for x, y in ra if 0 <= x + 5 < FW and 0 <= y - 3 < FH}
    assert rb == moved, "位移後的像素集合不等於原集合平移 (5,-3)"
    assert colours_of(a) == colours_of(b), "位移改變了顏色"
    return "%d 個像素完整搬家" % len(rb)


@case
def test_步進取值不補間(tmp):
    """關鍵影格是步進的：f2 才指定的值，f1 必須還是舊值。"""
    _, sh = bake_it(tmp, [{
        "id": "step", "type": "transform", "frames": 4,
        "track": [{"f": 0, "x": 0}, {"f": 2, "x": 10}],
    }])
    f = sh["step"]
    assert np.array_equal(f[0], f[1]), "f1 被補間了，應該還等於 f0"
    assert np.array_equal(f[2], f[3]), "f2 的值沒有延續到 f3"
    assert not np.array_equal(f[1], f[2]), "f2 沒有套用新值"


@case
def test_每個欄位各自延續(tmp):
    """x 在 f1 指定、y 在 f2 指定，兩者要各自延續，不互相清掉。"""
    _, sh = bake_it(tmp, [{
        "id": "carry", "type": "transform", "frames": 3,
        "track": [{"f": 0, "x": 0, "y": 0}, {"f": 1, "x": 4}, {"f": 2, "y": 2}],
    }])
    f = sh["carry"]
    base = rect_of(f[0], C_COAT)
    got = rect_of(f[2], C_COAT)
    want = {(x + 4, y + 2) for x, y in base if 0 <= x + 4 < FW and 0 <= y + 2 < FH}
    assert got == want, "f2 的位移不是 (x=4 延續, y=2 新指定)"


# --------------------------------------------------------------------------
# 2. 水平翻轉
# --------------------------------------------------------------------------

@case
def test_水平翻轉以畫布中線為軸(tmp):
    """翻轉的軸是畫布中線，不是角色外接框——否則角色會左右平移。"""
    _, sh = bake_it(tmp, [{
        "id": "flip", "type": "transform", "frames": 2,
        "track": [{"f": 0}, {"f": 1, "flip": 1}],
    }])
    a, b = sh["flip"]
    assert np.array_equal(b, a[:, ::-1]), "翻轉結果不等於整張畫布左右鏡射"


@case
def test_翻轉後才位移(tmp):
    """順序必須是「先翻轉、再位移」，反過來會讓位移方向也被鏡射。"""
    _, sh = bake_it(tmp, [{
        "id": "fx", "type": "transform", "frames": 2,
        "track": [{"f": 0, "flip": 1, "x": 0}, {"f": 1, "flip": 1, "x": 6}],
    }])
    a, b = sh["fx"]
    ra, rb = rect_of(a, C_COAT), rect_of(b, C_COAT)
    want = {(x + 6, y) for x, y in ra if x + 6 < FW}
    assert rb == want, "翻轉後的位移方向不對（應該仍然是 +x 往右）"


# --------------------------------------------------------------------------
# 3. transform 型與 master
# --------------------------------------------------------------------------

@case
def test_transform用master並底部對齊(tmp):
    """master 比畫布矮時，放置規則必須是「水平置中、底部對齊」且唯一。"""
    _, sh = bake_it(tmp, [{"id": "idle", "type": "transform", "frames": 1}])
    f = sh["idle"][0]
    ys = np.where(f[..., 3].any(axis=1))[0]
    assert ys.max() == FH - 1, "master 沒有底部對齊（最下面一列是 %d）" % ys.max()
    assert ys.min() == FH - 54, "master 的高度或對齊不對（最上面一列是 %d）" % ys.min()


@case
def test_transform型帶rig欄位要中止(tmp):
    """layers / pose / pose_track 屬於已移除的 rig 路線，留著會誤導。"""
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "stale", "type": "transform", "frames": 1, "pose": "stand",
        }], out_name="stale"),
        "已移除的部件旋轉路線", "transform 帶 pose")


@case
def test_rig型明確被拒絕(tmp):
    """rig 型已經移除，要給出「改用 frames」的明確訊息，不是「不認得的 type」。"""
    msg = expect_error(
        lambda: bake_it(tmp, [{"id": "old", "type": "rig", "frames": 1}],
                        out_name="old"),
        "部件旋轉路線已於", "rig 型")
    assert "frames" in msg, "錯誤訊息沒有指出該改用 frames 型"


# --------------------------------------------------------------------------
# 4. frames 型
# --------------------------------------------------------------------------

@case
def test_frames型逐格串成spritesheet(tmp):
    """影格目錄裡的 PNG 依檔名排序，原封不動地成為 spritesheet 的每一格。"""
    build_frames(tmp / "fr_seq", 4)
    _, sh = bake_it(tmp, [frames_anim(tmp, "seq", 4)])
    xs = [min(x for x, _ in rect_of(fr, C_COAT)) for fr in sh["seq"]]
    assert xs == sorted(xs) and len(set(xs)) == 4, \
        "四格的方塊位置不是遞增的，排序或內容錯了：%s" % xs


@case
def test_反向播放是倒著讀同一個目錄(tmp):
    """stand_up 就是倒著播的 sit_down——省一次生成，而且保證兩個方向對稱。"""
    build_frames(tmp / "fr_fwd", 4)
    _, sh = bake_it(tmp, [
        frames_anim(tmp, "fwd", 4),
        dict(frames_anim(tmp, "rev", 4), frames_dir=str(tmp / "fr_fwd"), reverse=True),
    ], out_name="revpair")
    fwd, rev = sh["fwd"], sh["rev"]
    for i in range(4):
        assert np.array_equal(fwd[i], rev[3 - i]), "第 %d 格反向對不上" % i


@case
def test_frames型的影格數要和檔案數一致(tmp):
    """宣告 4 格但目錄裡只有 3 個檔，是資料錯誤，不能默默少一格。"""
    build_frames(tmp / "fr_short", 3)
    expect_error(
        lambda: bake_it(tmp, [frames_anim(tmp, "short", 4)], out_name="short"),
        "只有 3 個影格檔", "影格數不一致")


@case
def test_frames型帶track要中止(tmp):
    """影格已經是完成品，track 不會有作用——留著會讓人以為位移生效了。"""
    build_frames(tmp / "fr_tk", 2)
    expect_error(
        lambda: bake_it(tmp, [frames_anim(tmp, "tk", 2, track=[{"f": 0, "x": 3}])],
                        out_name="tk"),
        "不會有作用", "frames 帶 track")


@case
def test_frames型沒有frames_dir要中止(tmp):
    expect_error(
        lambda: bake_it(tmp, [{"id": "nodir", "type": "frames", "frames": 2}],
                        out_name="nodir"),
        "沒有 frames_dir", "缺 frames_dir")


# --------------------------------------------------------------------------
# 5. 硬性檢查
# --------------------------------------------------------------------------

@case
def test_調色盤外的顏色要中止(tmp):
    build_frames(tmp / "fr_bad", 2, corrupt=1)
    msg = expect_error(
        lambda: bake_it(tmp, [frames_anim(tmp, "bad", 2)], out_name="bad"),
        "調色盤", "調色盤外的顏色")
    assert "123456" in msg.replace("#", "").upper(), "錯誤訊息沒指出是哪個顏色"


@case
def test_半透明要中止(tmp):
    build_frames(tmp / "fr_semi", 2, semi=True)
    expect_error(
        lambda: bake_it(tmp, [frames_anim(tmp, "semi", 2)], out_name="semi"),
        "半透明", "半透明像素")


@case
def test_影格尺寸不是完整畫布要中止(tmp):
    """影格必須是完整畫布。

    尺寸不符不能放過：blit 會把它貼在左上角，角色整個偏掉，而畫面上看起來
    只是「動畫抖了一下」，很難回推原因。這個檢查原本在 rig 型的 LayerCache 裡，
    rig 移除時一併沒了——這一項測試在 2026-08-02 重寫時把洞抓出來，才補回去。
    """
    build_frames(tmp / "fr_sz", 2, size=(60, 50))
    msg = expect_error(
        lambda: bake_it(tmp, [frames_anim(tmp, "sz", 2)], out_name="sz"),
        "完整畫布", "影格尺寸不是完整畫布")
    assert "--no-crop" in msg, "錯誤訊息沒有指出正確的修法"


@case
def test_小數位移要中止(tmp):
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "frac", "type": "transform", "frames": 2,
            "track": [{"f": 1, "x": 2.5}],
        }], out_name="frac"),
        "小數", "小數位移")


@case
def test_連整數值的浮點寫法也要中止(tmp):
    """2.0 數值上是整數，但只要欄位可以是浮點數，就有人會寫 2.5。"""
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "twofloat", "type": "transform", "frames": 2,
            "track": [{"f": 1, "x": 2.0}],
        }], out_name="twofloat"),
        "小數", "2.0 的浮點寫法")


@case
def test_打錯字要中止(tmp):
    """track 裡多打一個不認得的欄位，必須報錯而不是默默忽略。"""
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "typo", "type": "transform", "frames": 2,
            "track": [{"f": 1, "xx": 3}],
        }], out_name="typo"),
        "xx", "未知欄位")


@case
def test_關鍵影格超出範圍要中止(tmp):
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "oob", "type": "transform", "frames": 2,
            "track": [{"f": 5, "x": 1}],
        }], out_name="oob"),
        "f=5", "關鍵影格越界")


# --------------------------------------------------------------------------
# 6. 輸出契約
# --------------------------------------------------------------------------

@case
def test_越界像素被計數(tmp):
    """位移把像素推出畫布時要記在 clipped_px，不能默默吃掉。"""
    r, _ = bake_it(tmp, [{
        "id": "clip", "type": "transform", "frames": 2,
        "track": [{"f": 1, "x": 40}],
    }])
    e = r["index"]["animations"][0]
    assert e["clipped_px"] > 0, "位移出界了卻沒有計數"
    return "%d px 被計數" % e["clipped_px"]


@case
def test_strict_clip把越界升級成錯誤(tmp):
    expect_error(
        lambda: bake_it(tmp, [{
            "id": "clip2", "type": "transform", "frames": 2,
            "track": [{"f": 1, "x": 40}],
        }], out_name="clip2", strict_clip=True),
        "裁掉", "strict_clip")


@case
def test_screen_offset不烘進影格(tmp):
    """screen_offset 是繪製時的偏移，影格本身必須乾淨——跳多高都不會被切。"""
    r, sh = bake_it(tmp, [{
        "id": "jump", "type": "transform", "frames": 2,
        "screen_offset": [{"f": 0, "y": 0}, {"f": 1, "y": -20}],
    }])
    a, b = sh["jump"]
    assert np.array_equal(a, b), "screen_offset 被烘進影格了"
    fr = r["index"]["animations"][0]
    assert fr["clipped_px"] == 0, "screen_offset 不該造成切邊"
    return "影格未受影響"


@case
def test_frame_ms是權威欄位(tmp):
    """fps 只是參考；frame_ms 有給就以它為準（呼吸速率需要小數 fps 表達不了）。"""
    r, _ = bake_it(tmp, [{
        "id": "slow", "type": "transform", "frames": 2,
        "fps": 8, "frame_ms": 750,
    }])
    e = r["index"]["animations"][0]
    assert e["frame_ms"] == 750, "frame_ms 沒有蓋過 fps，實際 %d" % e["frame_ms"]
    assert e["total_ms"] == 1500, "總長度算錯：%d" % e["total_ms"]


@case
def test_跑兩次逐位元相同(tmp):
    """整個管線的地基：同樣的輸入，產出的每一個 byte 都相同。"""
    build_frames(tmp / "fr_det", 3)
    anims = [
        {"id": "t", "type": "transform", "frames": 2,
         "track": [{"f": 1, "x": 3, "y": -2, "flip": 1}]},
        frames_anim(tmp, "det", 3),
    ]
    path = write_anim(tmp / "det_anim.json", anims)
    run_bake(tmp, path, tmp / "det1")
    run_bake(tmp, path, tmp / "det2")
    h1, h2 = hash_dir(tmp / "det1"), hash_dir(tmp / "det2")
    assert h1 == h2, "兩次產出不同：%s" % (set(h1.items()) ^ set(h2.items()))
    return "%d 個檔案全部相同" % len(h1)


@case
def test_索引涵蓋全部動畫(tmp):
    build_frames(tmp / "fr_two", 2)
    r, _ = bake_it(tmp, [
        {"id": "one", "type": "transform", "frames": 2},
        frames_anim(tmp, "two", 2),
    ], out_name="idx")
    idx = r["index"]
    assert idx["animation_count"] == 2, "索引少了動畫"
    assert idx["total_frames"] == 4, "總影格數算錯：%d" % idx["total_frames"]
    assert idx["frame_size"] == [FW, FH], "索引沒記畫布尺寸"
    ids = [a["id"] for a in idx["animations"]]
    assert ids == ["one", "two"], "索引順序和定義不一致：%s" % ids
    for a in idx["animations"]:
        assert a["sha256_12"], "索引沒有記 spritesheet 的雜湊"


@case
def test_失敗時不寫索引(tmp):
    """有動畫失敗就不寫索引，而且要刪掉上一次留下的舊索引。

    「索引存在」必須等於「上一次烘焙全部成功」，否則下游 pack.py 會拿到
    一份看起來完整、其實缺格的資產包——那比沒有索引更危險。
    """
    out = tmp / "failidx"
    good = write_anim(tmp / "good_anim.json",
                      [{"id": "ok", "type": "transform", "frames": 1}])
    run_bake(tmp, good, out)
    idx = out / ("%s_atlas.json" % CHAR)
    assert idx.exists(), "第一次應該要寫出索引"

    bad = write_anim(tmp / "bad_anim.json",
                     [{"id": "boom", "type": "transform", "frames": 2,
                       "track": [{"f": 1, "x": 1.5}]}])
    try:
        run_bake(tmp, bad, out)
    except bake.BakeError:
        pass
    assert not idx.exists(), "失敗之後舊索引還在，下游會拿到過期的資產包"


# --------------------------------------------------------------------------
# 7. 真實資料的煙霧測試
# --------------------------------------------------------------------------

@case
def test_四個角色的真實動畫定義跑得過(tmp):
    """用真檔案跑一次完整烘焙。四個角色都要零跳過、零失敗、零切邊。"""
    done = []
    for cid in ("brown_mixed", "brindle_guard", "chihuahua", "ice_princess"):
        anim = ROOT / ("specs/animations/%s.anim.json" % cid)
        master = ROOT / ("art/approved/%s/master_stand_r_64px.png" % cid)
        if not anim.exists() or not master.exists():
            continue
        r = bake.run(character=cid, anim_path=anim, out_dir=tmp / ("real_" + cid),
                     scale=0, quiet=True, keep_going=True)
        assert r["ok"], "%s 烘焙失敗：%s" % (cid, r["failures"])
        idx = r["index"]
        assert idx["animation_count"] == 21, \
            "%s 應該烘出 21 個，實際 %d" % (cid, idx["animation_count"])
        assert not r["skipped"], "%s 有動畫被跳過：%s" % (cid, r["skipped"])
        clipped = [(e["id"], e["clipped_px"]) for e in idx["animations"]
                   if e["clipped_px"]]
        assert not clipped, "%s 有動畫被畫布切邊：%s" % (cid, clipped)
        done.append("%s(%d格)" % (cid, idx["total_frames"]))
    assert len(done) == 4, "只跑到 %d 個角色" % len(done)
    return "、".join(done)


# --------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="bake_test_"))
    try:
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
