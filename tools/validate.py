#!/usr/bin/env python3
"""
validate.py — 檢查一個角色是否符合 docs/07_角色定義契約.md

判準不是主觀的「看起來好了」，是這支程式跑得過。

    python tools/validate.py --character brown_mixed
    python tools/validate.py --all
    python tools/validate.py --character brown_mixed --rebuild
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("需要 numpy 與 Pillow：pip install -r tools/requirements.txt")

ROOT = Path(__file__).resolve().parent.parent

# 必須與 firmware/include/game.h 的 anim_id_t 一致
REQUIRED_ANIMS = [
    "idle_breathe", "idle_blink", "idle_look", "idle_ear_twitch", "tail_wag",
    "sit_down", "stand_up", "lie_down", "walk", "turn",
    "eat", "eat_happy",
    "sleep_breathe", "yawn", "wake_up",
    "play_bow", "chase_ball", "happy", "sad_wait", "pet_react", "toilet",
]

# docs/07 第三節的影格預算
FRAME_BUDGET = {
    "idle_breathe": 4, "idle_blink": 3, "idle_look": 4, "idle_ear_twitch": 4,
    "tail_wag": 4, "sit_down": 4, "stand_up": 4, "lie_down": 4, "walk": 4,
    "turn": 3, "eat": 4, "eat_happy": 6, "sleep_breathe": 4, "yawn": 4,
    "wake_up": 4, "play_bow": 4, "chase_ball": 4, "happy": 6, "sad_wait": 4,
    "pet_react": 4, "toilet": 4,
}

TIER0_FILES = [
    "specs/characters/{id}.json",
    "specs/palettes/{id}.json",
    "specs/animations/{id}.anim.json",
    "art/approved/{id}/master_stand_r_source.png",
    "art/approved/{id}/master_stand_r.remap.json",
    "art/approved/{id}/master_stand_r.patch.json",
    "art/approved/{id}/master_stand_r_64px.png",
    "art/approved/{id}/REBUILD.sh",
]

TIER1_FILES = []   # 部件旋轉路線已移除，動畫改走 frames 型


class Report:
    def __init__(self, cid):
        self.cid = cid
        self.errors = []
        self.warns = []
        self.notes = []

    def err(self, m):  self.errors.append(m)
    def warn(self, m): self.warns.append(m)
    def note(self, m): self.notes.append(m)

    @property
    def ok(self): return not self.errors


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def parse_hex(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ------------------------------------------------------------------ #

def check_files(cid, r):
    missing0 = [f.format(id=cid) for f in TIER0_FILES
                if not (ROOT / f.format(id=cid)).exists()]
    missing1 = [f.format(id=cid) for f in TIER1_FILES
                if not (ROOT / f.format(id=cid)).exists()]

    for m in missing0:
        r.err(f"Tier 0 缺檔：{m}")
    for m in missing1:
        r.note(f"Tier 1 缺檔：{m}")

    return len(missing0) == 0, len(missing1) == 0


def check_character_spec(cid, r):
    p = ROOT / f"specs/characters/{cid}.json"
    if not p.exists():
        return
    d = json.loads(p.read_text())

    raw = p.read_text()
    if "TBD_FROM_PHOTO" in raw:
        n = raw.count("TBD_FROM_PHOTO")
        r.err(f"規格檔仍有 {n} 處 TBD_FROM_PHOTO 未填")

    status = d.get("status", "")
    if status != "APPROVED":
        r.warn(f"status 是 {status!r}，定案後應改為 APPROVED")

    if d.get("open_questions"):
        for q in d["open_questions"]:
            r.note(f"未決問題：{q}")


def check_palette(cid, r):
    p = ROOT / f"specs/palettes/{cid}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    cols = d["colors"] if isinstance(d, dict) else d

    if len(cols) != 16:
        r.err(f"調色盤有 {len(cols)} 色，契約要求恰好 16（4bpp）")

    if not cols or not cols[0].get("transparent"):
        r.err("調色盤 index 0 必須是透明色")

    roles = [c["role"] for c in cols if not c.get("transparent")]
    dup = {x for x in roles if roles.count(x) > 1}
    if dup:
        r.err(f"調色盤 role 重複：{sorted(dup)}")

    return {parse_hex(c["hex"]) for c in cols if not c.get("transparent")}


def check_sprite(cid, r, palette):
    p = ROOT / f"art/approved/{cid}/master_stand_r_64px.png"
    if not p.exists():
        return
    im = np.array(Image.open(p).convert("RGBA"))
    op = im[..., 3] > 0
    used = {tuple(int(v) for v in c) for c in im[..., :3][op]}

    if len(used) > 15:
        r.err(f"master sprite 有 {len(used)} 色，4bpp 扣掉透明只剩 15 個色格")

    if palette:
        stray = used - palette
        if stray:
            r.err("master sprite 用了調色盤外的顏色："
                  + ", ".join("#%02X%02X%02X" % c for c in sorted(stray)[:6]))

    h, w = im.shape[:2]
    r.note(f"master sprite {w}×{h}，{len(used)} 色，{int(op.sum())} 個不透明像素")


def check_animations(cid, r):
    p = ROOT / f"specs/animations/{cid}.anim.json"
    if not p.exists():
        return 0
    d = json.loads(p.read_text())
    anims = {a["id"]: a for a in d.get("animations", [])}

    missing = [a for a in REQUIRED_ANIMS if a not in anims]
    extra   = [a for a in anims if a not in REQUIRED_ANIMS]
    if missing:
        r.err(f"缺少 {len(missing)} 個動畫：{', '.join(missing)}")
    if extra:
        r.warn(f"多出 game.h 沒有的動畫：{', '.join(extra)}")

    tier_counts = {0: 0, 1: 0, 2: 0}
    total_frames = 0
    pending = []

    for name, a in anims.items():
        budget = FRAME_BUDGET.get(name)
        frames = a.get("frames", 0)
        total_frames += frames
        if budget and frames > budget:
            r.err(f"{name} 有 {frames} 格，超過預算 {budget}"
                  "（電子雞不需要精細動畫，見 docs/07 第三節）")

        tier_counts[a.get("tier", 0)] = tier_counts.get(a.get("tier", 0), 0) + 1

        track = a.get("track", [])
        if len(track) > frames:
            r.err(f"{name} 的 track 有 {len(track)} 個關鍵影格，超過 frames={frames}")

        for k in track:
            for axis in ("x", "y"):
                v = k.get(axis)
                if v is not None and float(v) != int(v):
                    r.err(f"{name} f{k.get('f')} 的 {axis}={v} 不是整數，"
                          "非整數位移會破壞像素網格")
            f = k.get("f")
            if f is None or not (0 <= f < frames):
                r.err(f"{name} 有關鍵影格 f={f} 落在 0..{frames-1} 之外")

        st = a.get("status")
        if st == "PENDING_REGEN":
            pending.append(name)
        elif a.get("type") == "frames" and not a.get("frames_dir"):
            r.err(f"{name} 是 frames 型但沒有 frames_dir")

    r.note(f"動畫 {len(anims)}/{len(REQUIRED_ANIMS)} 個，共 {total_frames} 格")
    if pending:
        r.warn(f"{len(pending)} 個動畫待重生（部件旋轉路線已移除）："
               f"{', '.join(pending[:6])}{' …' if len(pending) > 6 else ''}")
    return total_frames


def check_reproducible(cid, r, do_rebuild):
    sh = ROOT / f"art/approved/{cid}/REBUILD.sh"
    out = ROOT / f"art/approved/{cid}/master_stand_r_64px.png"
    if not sh.exists() or not out.exists():
        return

    if not do_rebuild:
        r.note("可重現性未驗證（加 --rebuild 執行）")
        return

    before = sha(out)
    res = subprocess.run(["sh", str(sh)], capture_output=True, text=True, cwd=str(ROOT))
    if res.returncode != 0:
        r.err(f"REBUILD.sh 執行失敗：{res.stderr.strip().splitlines()[-1:] or res.stdout[-200:]}")
        return
    after = sha(out)

    if before == after:
        r.note(f"✅ 可重現性通過：重跑 REBUILD.sh 產出逐位元相同（{after}）")
    else:
        r.err(f"可重現性失敗：重跑後檔案改變（{before} → {after}）。"
              "代表某一步不是純函式，或有手工修改沒進版控")


# ------------------------------------------------------------------ #

def validate(cid, do_rebuild):
    r = Report(cid)
    have0, have1 = check_files(cid, r)
    check_character_spec(cid, r)
    palette = check_palette(cid, r)
    check_sprite(cid, r, palette)
    check_animations(cid, r)
    check_reproducible(cid, r, do_rebuild)

    tier = -1
    if have0 and not r.errors:
        tier = 1 if have1 else 0
    return r, tier


def main():
    ap = argparse.ArgumentParser(description="檢查角色是否符合定義契約")
    ap.add_argument("--character", "-c")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--rebuild", action="store_true",
                    help="實際重跑 REBUILD.sh 驗證可重現性")
    args = ap.parse_args()

    ids = []
    if args.all:
        ids = sorted(p.stem for p in (ROOT / "specs/characters").glob("*.json"))
    elif args.character:
        ids = [args.character]
    else:
        ap.error("要給 --character 或 --all")

    worst = 0
    for cid in ids:
        r, tier = validate(cid, args.rebuild)
        label = {-1: "未達 Tier 0", 0: "Tier 0（可跑）", 1: "Tier 1（有表情）"}[tier]
        print(f"\n{'='*60}\n{cid}  —  {label}\n{'='*60}")
        for m in r.notes:  print(f"  ·  {m}")
        for m in r.warns:  print(f"  ⚠️  {m}")
        for m in r.errors: print(f"  ✗  {m}")
        if r.ok and not r.warns:
            print("  ✓  全部通過")
        worst = max(worst, 1 if r.errors else 0)

    sys.exit(worst)


if __name__ == "__main__":
    main()
