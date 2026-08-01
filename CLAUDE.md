# ICEPET — 女兒的電子雞

軟硬體整合的桌上型電子寵物。角色為一位冰雪公主與三隻依照真實照片設計的狗。
目標使用者：**3–5 歲兒童**。第一版**純單機**，不連網、不需要帳號。

進度與已驗證的結論見 `docs/08_進度總表.md`。角色辨識設計見 `docs/09_角色辨識設計.md`。

---

## 給 AI 助理的核心規則

以下規則優先於一般慣例。多數是實測踩出來才寫下的，違反的產出會直接被退回。

### 1. 這是給幼兒的裝置 — 沒有失敗狀態

- **絕對不可以有死亡、生病、逃跑、消失等機制。**
- 需求降到 0 只會讓角色播「想被關心」的動畫，不扣分、不警告、不倒數。
- 離線衰減有下限 30。小孩隔一週回來，不可以看到全部歸零的角色。
- 難過的表現上限是「耳朵下垂 + 眼神向下 + 動作變慢」。不做哭泣、發抖、蜷縮躲藏。
- 需求歸零時**不可以整天只播難過動畫**。上限六成，其餘仍播正常閒置動作。
- UI 全圖示，同一畫面最多 3 個可點選項，觸控目標 ≥48px。

`firmware/test/test_game.c` 有一組「放置一個月」的測試在守這些規則，不要繞過它。

### 2. AI 負責設計，工具負責資產

影像模型的輸出**永遠不能直接當資產用**。實測 gpt-image-2 的「像素風」產出：
27,990 色、色塊長度 2/3/4/5 混雜、「純洋紅」背景是十幾種雜訊值。

分工是固定的：

| 階段 | 負責 | 工具 |
|---|---|---|
| 造型、體型、剪影、大面積配色 | AI 影像模型 | codex `image_gen` |
| 網格對齊、降採樣 | 眾數降採樣 | `tools/pixelate.py` |
| 材質的精確顏色 | 語意重映射 | `pixelate.py --remap` |
| 5 像素以下的細節（眼睛、鼻子） | 手工逐像素 | `tools/pixedit.py` |
| 部件切分 | 連通區域標記 | `tools/cutparts.py` |

**不要用重生圖去解決顏色不準或眼睛太小。** 那兩件事模型控制不了，在下游修。

### 3. 一個角色只生一個姿勢

實測：以已核可的 master 為參考圖、明確要求「同一角色的不同姿勢、不要重新設計」，
生成坐姿／趴姿／正面三張——**三張全部朝同一方向漂移**（更圓更幼、灰白吻部退回暖褐），
正面那張是徹底的重新設計。系統性的，再迭代也是擲骰子。

**只生站姿的部件分解圖，其餘姿態全部由部件推導。**

### 4. Tier 0 只允許整數像素位移與水平翻轉

**不可以用旋轉。** 旋轉像素藝術會破壞像素網格，64px 上轉 4 度只會產生歪斜鋸齒。

**不可以用非等比縮放。** 實測把 `sit_down` 寫成 `scale_y: 86%`，
讀起來是狗被壓扁，不是狗坐下。**坐姿是不同的剪影，不是同一個剪影變矮。**

只有 5 個動畫符合 Tier 0：`idle_breathe` `walk` `turn` `happy` `sad_wait`。
其餘 16 個標為 `PLACEHOLDER_NEEDS_RIG`，等 `bake.py` 從部件合成真影格。

### 5. 角色是真實存在的狗

`specs/characters/*.json` 的 `identity_features` 是**不可協商的辨識特徵**，
每張生成圖都要逐項檢查才能進 `art/approved/`。

不對稱特徵不可用水平翻轉產生（見各角色的 `mirror_policy`）。

虎斑護衛犬**不露牙、不皺鼻、不做撲咬姿態**。守護感來自站姿的穩定與視線的專注。

### 6. 開工前先查有沒有現成的

`tools/` 底下七支工具有四支重疊了 Aseprite 的功能，是專案做到一半才發現的。
評估結果見 `docs/10_開源工具評估.md`——結論是**保留現有管線**，
因為免費的替代品實測都不合用（LibreSprite 缺 `--batch`／`--data`／Lua，
只能當 GUI 編輯器；Pixelorama 無 CLI）。

新增任何工具之前，先問：這件事有沒有人做過了？
特別是 `pack.py` **不要自己寫**，先評估 `lv_img_conv`。

手動修圖用已安裝的 `/Applications/libresprite.app`。

### 7. 冰雪公主的 IP 界線

設計靈感來自商業動畫角色。本專案為**自用單件**，這樣沒有問題。
若日後有販售、公開展示、開源發布的打算，必須先改成原創設計。
程式碼、檔名、提示詞中不得出現任何商業作品的角色名稱或專有名詞。

---

## 角色何時算做完

判準不是主觀的，是這行跑得過：

```bash
python tools/validate.py --character <id> --rebuild
```

完整契約見 `docs/07_角色定義契約.md`，核心是三件事：

1. **可重現**：重跑 `art/approved/<id>/REBUILD.sh` 產出必須**逐位元相同**。
   代表沒有任何一步是手工在影像編輯器裡做的——所有修改都存在版控的 JSON 裡。
2. **分層**：Tier 0（只需 master sprite）→ Tier 1（加部件）→ Tier 2（加姿態），
   每一層都是可跑的，不是全有全無。
3. **影格預算**：電子雞不需要精細動畫。每角色 86 格封頂，超過會被 validator 擋下。

---

## 專案結構

```
.
├── CLAUDE.md                  ← 本檔
├── docs/
│   ├── 00_專案總覽.md
│   ├── 01_硬體選型與BOM.md      含幼兒安全的七項硬性要求
│   ├── 02_遊戲設計.md           「沒有失敗狀態」的完整理由
│   ├── 03_角色動畫資產規格表.md
│   ├── 04_資料結構與存檔設計.md
│   ├── 05_資產管線.md           多數規則是實測踩出來的
│   ├── 06_開發路線圖.md
│   ├── 07_角色定義契約.md       ← 角色何時算做完
│   └── 08_進度總表.md           ← 目前狀態與已驗證的結論
├── specs/
│   ├── style_lock.md          生成提示詞的固定區塊，一字不改
│   ├── characters/*.json      身份特徵、比例、狀態
│   ├── palettes/*.json        16 色，手工制定
│   └── animations/*.anim.json 動畫定義，純資料
├── art/
│   ├── reference/             使用者提供的真實照片，不進版控
│   ├── generated/             AI 原始輸出
│   ├── approved/<id>/         通過 QA 的素材 + REBUILD.sh
│   └── rigs/<id>/             切好的部件 + rig.json
├── tools/                     Python 資產管線
├── firmware/                  ESP-IDF 專案（save / game 已完成並測試）
└── data/                      燒進 LittleFS 的資產包
```

---

## 常用指令

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r tools/requirements.txt
```

驗證角色是否符合契約（改完 `specs/` 或 `art/approved/` 都要跑）：

```bash
python tools/validate.py --character brown_mixed --rebuild
```

把 AI 產出轉成真像素資產：

```bash
python tools/pixelate.py art/generated/foo.png --width 64 --colors 12 --palette specs/palettes/brown_mixed.json --remap art/generated/foo.remap.json
```

切分部件分解圖：

```bash
python tools/cutparts.py art/generated/B3_brown_mixed_parts_stand.png --character brown_mixed --out-dir art/rigs/brown_mixed --tol 120
```

手工修補 5 像素以下的細節：

```bash
python tools/pixedit.py sprite.png --patch eyes.json --palette specs/palettes/brown_mixed.json --out fixed.png
```

### 兩階段烘焙（docs/05 第三節）

**階段 A** — 高解析部件組成姿勢，人眼核可，只跑一次。改了 `specs/poses/` 才要重跑：

```bash
python tools/assemble.py --rig art/rigs/brown_mixed/rig.json \
    --poses specs/poses/brown_mixed.poses.json --out-dir build/poses --preview \
    --report build/poses/_assemble_report.json

python tools/pixelate.py build/poses/brown_mixed_stand_*.png \
    build/poses/brown_mixed_sit_*.png build/poses/brown_mixed_lie_*.png \
    --width 64 --height 56 --no-crop --scale 6 \
    --palette specs/palettes/brown_mixed.json \
    --remap art/rigs/brown_mixed/remap.json --out-dir build/pixparts
```

> `--no-crop` 與 `--height 56` 缺一不可。逐格裁切會讓降採樣的格線漂移，播放時整隻角色抖動（鐵律 2）。

**階段 B** — 像素領域烘焙，全自動、逐位元決定性。改了 `specs/animations/` 只要跑這兩行：

```bash
python tools/bake.py --character brown_mixed --parts-dir build/pixparts \
    --anim specs/animations/brown_mixed.anim.json --out-dir build/sheets

python tools/preview.py --character brown_mixed --sheets-dir build/sheets \
    --out-dir build/preview --scale 6
```

驗收：`--check` 只印健檢表（呼吸次數、重複影格、影格預算）不產圖；
`--anim-file` 可以只預覽自己的動畫片段，不必動到共用的 anim.json：

```bash
python tools/preview.py --character brown_mixed --check
python tools/preview.py --character brown_mixed --anim-file build/anim_frag/idle.json
```

### 測試

三支管線工具各有自我測試，改了工具一定要跑（`test_bake.py` 會用真的
`build/pixparts` 跑一次完整烘焙，所以要先跑過階段 A）：

```bash
python tools/test_assemble.py && python tools/test_bake.py && python tools/test_preview.py
```

韌體測試（主機端，不需要硬體）：

```bash
cd firmware && cc -std=c11 -I include -I test/stubs -o /tmp/t src/save.c src/game.c test/test_game.c && /tmp/t
```

---

## 工作慣例

- **文件與註解用繁體中文**，程式碼識別字用英文。
- 角色 id 固定為 `ice_princess` / `chihuahua` / `brown_mixed` / `brindle_guard`，
  不可縮寫改名——這些字串同時是檔名、JSON key 和韌體 enum。
- 改動 `specs/` 之後一定要跑 `validate.py`。
- **不要修改 `art/reference/`**，那是使用者提供的原始照片。
- 存檔結構 `save_blob_t` 改欄位時必須遞增 `SAVE_VERSION` 並寫遷移函式，
  否則小孩既有的養成進度會遺失。這件事沒有商量餘地。
- 動畫調整改 JSON 重新烘焙，**永遠不要重新生圖**。
