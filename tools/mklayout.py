#!/usr/bin/env python3
"""mklayout.py — 把 specs 的版面資料編成 C 標頭給渲染層用。

為什麼要有這支
--------------
渲染層要知道：角色站哪裡、物件的錨點與接地列、UI 條的三格在哪、
哪個動畫要出現哪個物件。這些全部定義在 `specs/scene.json`——
但韌體讀不了 JSON（沒有解析器、也不該在 ESP32 上花那個記憶體）。

所以**編譯期產生**：`specs/*.json` 仍然是單一真相來源，
這支把它翻成 `firmware/include/layout.h`。
和 `tools/cell.py` 同一個原則——**同一個數字只能有一個地方定義**。
`brown_mixed` 的 sprite_cell 曾經寫錯很久沒被發現，就是因為那時候沒有任何程式讀它。

**不要手改 layout.h。** 改 `specs/scene.json` 再重跑這支。

用法
----
    .venv/bin/python tools/mklayout.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "firmware/include/layout.h"


def load(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def cname(s):
    return s.upper().replace("/", "_").replace("-", "_")


def main():
    sc = load("specs/scene.json")
    L = sc["layout"]
    U = L["ui"]
    slots = sc["characters"]["slots"]
    objs = {k: v for k, v in sc["objects"].items()
            if isinstance(v, dict) and "size" in v}
    cues = {k: v for k, v in sc["object_cues"].items() if not k.startswith("_")}
    vis = load("specs/visitors.json")["visitors"]
    acc = load("specs/accessories/ice_princess.json")["accessories"]
    outfits = load("specs/outfits/ice_princess.json")["outfits"]

    o_names = sorted(objs)
    a_names = [a["id"] for a in acc if not a["id"].startswith("_")]

    w = []
    p = w.append
    p("/* 由 tools/mklayout.py 從 specs/ 產生。**不要手改。**")
    p("   改 specs/scene.json / visitors.json / accessories/ / outfits/ 再重跑那支工具。")
    p("")
    p("   為什麼要編譯期產生：韌體讀不了 JSON，但同一個數字不能有兩個定義。")
    p("   tools/cell.py 那次的教訓——brown_mixed 的 sprite_cell 寫錯很久沒被發現，")
    p("   就是因為當時沒有任何程式讀它。 */")
    p("#ifndef ICEPET_LAYOUT_H")
    p("#define ICEPET_LAYOUT_H")
    p("")
    p("#include <stdint.h>")
    p("")
    # scene.json 的 size 是**房間**的尺寸（320×176），不是螢幕的。
    # 螢幕 = 房間 + UI 條，兩者不能混——第一版寫成 SCR_H 176，
    # UI 條會整條畫到畫布外。
    p("#define ROOM_W %d" % sc["size"][0])
    p("#define ROOM_H %d   /* 房間底圖的高度，不含 UI 條 */" % sc["size"][1])
    p("#define SCR_W %d" % sc["size"][0])
    p("#define SCR_H %d   /* 房間 %d + UI 條 %d */"
      % (L["ui_bar_y"] + L["ui_bar_h"], sc["size"][1], L["ui_bar_h"]))
    p("#define FLOOR_Y %d" % L["floor_y"])
    p("#define GROUND_Y %d   /* 角色腳底的基準線 */" % L["ground_y"])
    p("#define UI_BAR_Y %d" % L["ui_bar_y"])
    p("#define UI_BAR_H %d" % L["ui_bar_h"])
    p("#define UI_ICON_SIZE %d" % U["icon_size"])
    p("#define UI_ICON_Y %d" % U["icon_y"])
    p("#define UI_BOX_PAD %d" % U["select_box"]["pad"])
    p("#define UI_BOX_THICK %d" % U["select_box"]["thickness"])
    p("static const int16_t UI_ICON_X[3] = { %s };"
      % ", ".join(str(x) for x in U["icon_x"]))
    p("")
    p("/* 站位。狗那一格三隻共用，由 game_t.present 決定畫誰。 */")
    for s in slots:
        p("#define SLOT_%s_X %d" % (cname(s["id"]), s["x"]))
        p("#define SLOT_%s_DY %d" % (cname(s["id"]), s["dy"]))
    p("")
    p("/* 物件。mode 決定 x/y 怎麼算——判準是「有沒有接地」：")
    p("   ground 的 y 推算得出來（ground_y - dy - base_row），float 的沒有基準只能填。 */")
    p("typedef enum { OBJ_GROUND = 0, OBJ_FLOAT, OBJ_FIXED, OBJ_TRACK } obj_mode_t;")
    p("")
    p("typedef enum {")
    for n in o_names:
        p("    OBJ_%s," % cname(n))
    p("    OBJ_COUNT")
    p("} obj_id_t;")
    p("")
    p("typedef struct {")
    p("    uint8_t  mode;          /* obj_mode_t */")
    p("    uint8_t  w, h;")
    p("    uint8_t  frames;")
    p("    uint16_t ms;")
    p("    uint8_t  base_row;      /* ground：哪一列踩在地面線上 */")
    p("    int16_t  at_x, at_y;    /* fixed 才有意義 */")
    p("    int16_t  ax[4];         /* ground：每個角色的 anchor_x，-1 = 沒有 */")
    p("    int16_t  fx[4], fy[4];  /* float：每個角色相對影格左上角的偏移 */")
    p("} obj_def_t;")
    p("")
    CH = ["ice_princess", "chihuahua", "brown_mixed", "brindle_guard"]
    p("/* 角色索引和 save.h 的 character_id_t 一致：%s */" % ", ".join(CH))
    p("static const obj_def_t OBJ_DEF[OBJ_COUNT] = {")
    for n in o_names:
        o = objs[n]
        mode = {"ground": "OBJ_GROUND", "float": "OBJ_FLOAT",
                "fixed": "OBJ_FIXED", "track": "OBJ_TRACK"}[o.get("mode", "ground")]
        at = o.get("at") or [0, 0]
        axs = o.get("anchor_x") or {}
        anc = o.get("anchor") or {}
        ax = []
        for c in CH:
            v = axs.get(c, axs.get("all"))
            ax.append(int(v) if isinstance(v, (int, float)) else -1)
        fx, fy = [], []
        for c in CH:
            v = anc.get(c)
            fx.append(int(v[0]) if v else 0)
            fy.append(int(v[1]) if v else 0)
        p("    { %-11s %2d, %2d, %d, %4d, %2d, %3d, %3d, {%s}, {%s}, {%s} },  /* %s */"
          % (mode + ",", o["size"][0], o["size"][1], o.get("frame_count", 1),
             o.get("frame_ms", 0) or 0, o.get("base_row", o["size"][1] - 1),
             at[0], at[1],
             ",".join("%3d" % v for v in ax),
             ",".join("%3d" % v for v in fx),
             ",".join("%3d" % v for v in fy), n))
    p("};")
    p("")
    p("static const char *const OBJ_NAME[OBJ_COUNT] = {")
    for n in o_names:
        p('    "obj/%s",' % n)
    p("};")
    p("")
    p("/* 動畫 → 物件。渲染層查這張表，不必在程式裡寫死動畫名稱。")
    p("   z=1 代表先畫物件再畫角色（睡墊墊在身下）。 */")
    p("typedef struct {")
    p("    uint8_t anim;           /* anim_id_t */")
    p("    int8_t  obj;            /* obj_id_t，-1 = 無 */")
    p("    int8_t  obj_princess;   /* per_character 覆寫，-1 = 同上 */")
    p("    uint8_t under;          /* 1 = 畫在角色下方 */")
    p("} obj_cue_t;")
    p("")
    anims = load("specs/animations/%s.anim.json" % "brown_mixed")
    order = [a["id"] for a in anims["animations"]]
    p("static const obj_cue_t OBJ_CUE[] = {")
    for aid, c in sorted(cues.items()):
        if aid not in order:
            continue
        oid = c.get("object")
        pc = (c.get("per_character") or {}).get("ice_princess")
        p("    { %2d, %2d, %2d, %d },  /* %s */"
          % (order.index(aid),
             o_names.index(oid) if oid in o_names else -1,
             o_names.index(pc) if pc in o_names else -1,
             1 if c.get("z") == "under" else 0, aid))
    p("};")
    p("#define OBJ_CUE_COUNT %d" % sum(1 for a in cues if a in order))
    p("")
    p("/* 閒置訪客。牠們走 AI 管線、有自己的調色盤，不是 scene 的幾何物件。 */")
    p("typedef struct { uint8_t w, h, frames, base_row; uint16_t ms; } visitor_def_t;")
    p("static const visitor_def_t VISITOR_DEF[] = {")
    for v in vis:
        p("    { %2d, %2d, %d, %2d, %3d },  /* %s */"
          % (v["size"][0], v["size"][1], v["frame_count"],
             v["base_row"], v["frame_ms"], v["id"]))
    p("};")
    p("static const char *const VISITOR_NAME[] = {")
    for v in vis:
        p('    "visitor/%s",' % v["id"])
    p("};")
    p("")
    p("/* 公主的配件。掛在 specs/anchors/ 的逐格頭部錨點上。 */")
    p("#define ACC_COUNT %d" % len(a_names))
    p("typedef struct { uint8_t w, h; int8_t dx, dy; } acc_def_t;")
    p("static const acc_def_t ACC_DEF[ACC_COUNT] = {")
    for a in acc:
        if a["id"].startswith("_"):
            continue
        p("    { %2d, %2d, %3d, %3d },  /* %s */"
          % (a["size"][0], a["size"][1], a["dx"], a["dy"], a["id"]))
    p("};")
    p("static const char *const ACC_NAME[ACC_COUNT] = {")
    for n in a_names:
        p('    "acc/ice_princess/%s",' % n)
    p("};")
    p("")
    p("/* 服裝：只換調色盤的五個槽，零張新圖。索引就是 outfit_id。 */")
    slots5 = load("specs/palettes/ice_princess.json")["outfit_slots"]
    pal = {c["role"]: c["index"] for c in load("specs/palettes/ice_princess.json")["colors"]}
    p("#define OUTFIT_COUNT_GEN %d" % len(outfits))
    p("#define OUTFIT_SLOTS %d" % len(slots5))
    p("static const uint8_t OUTFIT_SLOT_IDX[OUTFIT_SLOTS] = { %s };"
      % ", ".join(str(pal[r]) for r in slots5))

    def rgb565(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

    p("static const uint16_t OUTFIT_RGB565[OUTFIT_COUNT_GEN][OUTFIT_SLOTS] = {")
    for o in outfits:
        p("    { %s },  /* %s */"
          % (", ".join("0x%04X" % rgb565(o["colors"][r]) for r in slots5), o["name"]))
    p("};")
    p("")
    p("/* 資產名字是 \"<角色>/<動畫>\"。渲染層用這兩張表組出來，不要在程式裡寫死字串。")
    p("   順序必須和 save.h 的 character_id_t、game.h 的 anim_id_t 完全一致。 */")
    p("static const char *const CHAR_ID[4] = { %s };"
      % ", ".join('"%s"' % c for c in CH))
    p("static const char *const ANIM_ID[%d] = {" % len(order))
    for a in order:
        p('    "%s",' % a)
    p("};")
    p("")
    p("/* 公主的逐格頭部錨點（tools/anchors.py 從髮色像素**量**出來的，不是填的）。")
    p("   配件掛在這上面，所以 lie_down 頭降 32 px 時帽子跟著降。")
    p("   索引 [anim_id][frame]，值是 {中心 x, 頭頂 y}，相對影格左上角。 */")
    anch = load("specs/anchors/ice_princess.json")["animations"]
    maxf = max(len(v) for v in anch.values())
    p("#define HEAD_MAX_FRAMES %d" % maxf)
    p("static const int8_t HEAD_ANCHOR[%d][HEAD_MAX_FRAMES][2] = {" % len(order))
    for a in order:
        pts = anch.get(a, [])
        cells = []
        for k in range(maxf):
            q = pts[k] if k < len(pts) else (pts[-1] if pts else [0, 0])
            cells.append("{%2d,%2d}" % (q[0], q[1]))
        p("    { %s },  /* %s */" % (", ".join(cells), a))
    p("};")
    p("")
    p("#endif /* ICEPET_LAYOUT_H */")

    OUT.write_text("\n".join(w) + "\n", encoding="utf-8")
    print("→ %s（%d 行）" % (OUT.relative_to(ROOT), len(w)))
    print("  物件 %d、訪客 %d、配件 %d、服裝 %d、cue %d"
          % (len(o_names), len(vis), len(a_names), len(outfits),
             sum(1 for a in cues if a in order)))


if __name__ == "__main__":
    main()
