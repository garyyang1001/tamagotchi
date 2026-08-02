#!/usr/bin/env python3
"""組出動畫影格圖的生成提示詞。

為什麼要有這支：提示詞原本是手抄在 specs/anim_sheet_prompt.md 裡的。
一隻狗 15 個動畫，三隻狗就是 45 段幾乎相同的文字——手抄一定會漂移，
而且漂移的地方剛好是「IDENTITY FEATURES 每一格都要一樣」這種最不能錯的條款。
所以拆成骨架（共用）＋ 角色填充（每隻不同）＋ 動作段落（每個動畫不同），組合產生。

    python tools/mkprompt.py -c brindle_guard -a walk          # 印出提示詞
    python tools/mkprompt.py -c brindle_guard --all --out-dir build/prompt
    python tools/mkprompt.py -c brindle_guard --list
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cell import cell_for   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDIR = ROOT / "specs/anim_prompt"


def load(char):
    skel = (PDIR / "_skeleton.txt").read_text(encoding="utf-8")
    frames = json.loads((PDIR / "_frames.json").read_text(encoding="utf-8"))["animations"]
    fill = json.loads((PDIR / f"{char}.json").read_text(encoding="utf-8"))
    return skel, frames, fill


def skeleton_for(skel: str, spec: dict) -> str:
    """單張圖（master）要拿掉多影格才有意義的段落。

    骨架裡的 MOTION RULES 講的是「所有影格保持同一位置與大小」，
    IDENTITY 的抬頭寫的是「每一格都一樣」——單張基準圖沒有「每一格」，
    留著只是噪音。SUBJECT 的尾巴改由角色檔的 SUBJECT_OVERRIDE["master"] 負責——
    「不要畫得更小」那句對吉娃娃是反效果（牠本來就該很小），
    而公主根本沒有參考圖，連「attached reference image」都不能提。
    """
    if not spec.get("single"):
        return skel
    i = skel.index("MOTION RULES, strictly enforced:")
    j = skel.index("IDENTITY FEATURES", i)
    skel = skel[:i] + skel[j:]
    skel = skel.replace("IDENTITY FEATURES, simplified into flat shapes, identical in all frames:",
                        "IDENTITY FEATURES, simplified into flat shapes:")
    return skel


def build(char, anim, skel, frames, fill):
    if anim not in frames:
        raise SystemExit(f"沒有 {anim} 這個動畫。有的是：{', '.join(sorted(frames))}")
    spec = frames[anim]

    # 動作段落：角色可以整段覆寫。
    # 為什麼需要：21 個 anim_id_t 是照狗設計的（tail_wag / play_bow / toilet），
    # 韌體用同一個 enum 索引全部角色，所以公主也一定會播到它們。
    # 她沒有尾巴也沒有耳朵，那幾個動作必須換成人形的對應動作
    # （撥辮子／裙襬搖晃／屈膝禮／丟球給狗／蹲下）。
    # 不覆寫的動畫仍然吃共用段落，只做 token 代換。
    text = (fill.get("FRAMES_OVERRIDE") or {}).get(anim) or spec["text"]
    for k, v in fill.items():
        if k.startswith("_") or not isinstance(v, str):
            continue
        text = text.replace("{" + k + "}", v)
    if "{" in text:
        stray = text[text.index("{"):].split("}")[0] + "}"
        raise SystemExit(f"{char}/{anim}：角色檔缺 {stray}")

    skel = skeleton_for(skel, spec)
    cw, ch = cell_for(char)
    layout = (fill.get("LAYOUT_OVERRIDE") or {}).get(anim) or spec["layout"]
    subject = (fill.get("SUBJECT_OVERRIDE") or {}).get(anim) or fill["SUBJECT"]
    out = skel.replace("{CELL_W}", str(cw)).replace("{CELL_H}", str(ch))
    for k, v in (("SUBJECT", subject), ("LAYOUT", layout),
                 ("FRAMES", text), ("IDENTITY", fill["IDENTITY"]),
                 ("STYLE", fill["STYLE"]), ("NEGATIVE", fill["NEGATIVE"]),
                 ("MOTION_RULES", fill["MOTION_RULES"])):
        out = out.replace("{" + k + "}", v)
    return out.strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--char", required=True)
    ap.add_argument("-a", "--anim")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out-dir")
    args = ap.parse_args()

    skel, frames, fill = load(args.char)

    if args.list:
        for name in sorted(frames):
            s = frames[name]
            print(f"  {name:18s} {s['frames']} 格  {s['grid']}")
        return

    names = sorted(frames) if args.all else [args.anim]
    if not names or names == [None]:
        raise SystemExit("要 -a <動畫> 或 --all")

    for name in names:
        text = build(args.char, name, skel, frames, fill)
        if args.out_dir:
            d = pathlib.Path(args.out_dir)
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{args.char}_{name}.txt"
            p.write_text(text, encoding="utf-8")
            print(f"  → {p}  ({len(text)} 字元)")
        else:
            if len(names) > 1:
                print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
            sys.stdout.write(text)


if __name__ == "__main__":
    main()
