# ICEPET — 女兒的電子雞

軟硬體整合的桌上型電子寵物。一位冰雪公主，和三隻依照家裡真實照片設計的狗。
目標使用者 **3–5 歲**，純單機，**沒有死亡機制**。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r tools/requirements.txt
```

## 文件

| | |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | 給 AI 助理的核心規則。動工前先讀 |
| [`docs/00`](docs/00_專案總覽.md) | 專案總覽與系統分層 |
| [`docs/01`](docs/01_硬體選型與BOM.md) | 硬體選型、BOM、**幼兒安全要求** |
| [`docs/02`](docs/02_遊戲設計.md) | 遊戲設計。「沒有失敗狀態」的完整理由 |
| [`docs/03`](docs/03_角色動畫資產規格表.md) | **角色動畫資產規格表**。所有角色圖檔的唯一依據 |
| [`docs/04`](docs/04_資料結構與存檔設計.md) | 資料結構與存檔設計 |
| [`docs/05`](docs/05_資產管線.md) | 資產管線。多數規則是實測踩出來的 |
| [`docs/06`](docs/06_開發路線圖.md) | 開發路線圖與風險登記 |
| [`docs/07`](docs/07_角色定義契約.md) | **角色定義契約**。角色何時算做完 |
| [`docs/08`](docs/08_進度總表.md) | **進度總表**。目前狀態與已驗證的結論 |
| [`docs/09`](docs/09_角色辨識設計.md) | **角色辨識設計**。怎麼讓 3 歲認出「這是我們家那隻」 |
| [`docs/10`](docs/10_開源工具評估.md) | 開源工具評估。七支自製工具有四支在重造輪子 |

## 三個關鍵決策

**1. 不逐張生成動畫影格。** 影像模型每次推理都是獨立取樣，走路循環的第 3 格和第 2 格
必定有像素差異，播放時角色會抖動（boiling）。改成 AI 只產設計圖、切成部件、
動畫寫 JSON、build time 烘焙成 spritesheet。動畫調整不需要重新生圖。

**2. AI 產出不能直接用。** 實測 gpt-image-2 的「像素風」輸出有 **27,990 色**、
色塊長度 2/3/4/5 混雜、「純洋紅」背景是十幾種雜訊值。一律經過
[`tools/pixelate.py`](tools/pixelate.py) 才是資產。

**3. 沒有死亡、沒有懲罰、沒有計時器。** 需求歸零只會讓角色播放「想被關心」的動畫。
離線衰減有下限 30——小孩隔一週回來，看到的是有點想你的角色，不是奄奄一息的角色。

## 常用指令

```bash
python tools/pixelate.py art/generated/foo.png --width 64 --colors 12
```

```bash
cd firmware && cc -std=c11 -I include -I test/stubs -o /tmp/test_save src/save.c test/test_save.c && /tmp/test_save
```

其餘見 [`CLAUDE.md`](CLAUDE.md)。
