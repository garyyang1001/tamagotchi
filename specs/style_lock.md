# STYLE LOCK

**這個檔案的內容要一字不改地貼進每一次影像生成的提示詞。**

它是跨角色風格一致的唯一保證。改了它，四個角色就不會像是同一個世界裡的。
要調整風格時，改這個檔案，然後**把所有角色全部重生一次**——不要只改一個角色的提示詞。

---

## 區塊 1：解析度與細節密度

```
Pixel art game character sprite, in the style of a 16-bit SNES or Game Boy Color role-playing game.

CRITICAL - RESOLUTION AND DETAIL LEVEL:
This must look like a sprite that was originally drawn at only 64 x 56 pixels, then enlarged with nearest-neighbor scaling so that each original pixel becomes a large visible square block. Very low detail. Large flat areas of a single solid color. Fine detail is impossible at this size and everything must be aggressively simplified into big readable shapes. This is NOT a detailed illustration with a pixelation filter applied afterwards. This is a genuinely low-resolution sprite drawn pixel by pixel.
```

> **為什麼這段最重要**：實測發現，只寫 "pixel art" 的話，模型會理解成
> 「畫一張高細節的圖，然後套像素化濾鏡」。產出的等效解析度會到 150×160，
> 細節全部糊在一起，完全不能用。必須明確給出目標像素尺寸並強調
> "genuinely low-resolution"，模型才會真的用低資訊量的方式構圖。

## 區塊 2：風格規則

```
STYLE, strictly enforced:
- Exactly three tones per material: dark, mid, light. No more.
- Large uninterrupted regions of flat solid color
- A single dark outline, exactly one pixel thick, around the whole silhouette
- NO anti-aliasing, NO blur, NO gradients, NO dithering, NO noise, NO texture
- NO individual fur strands, NO realistic shading, NO photorealism
- Total palette: 12 colors maximum
- Readable at small size: the silhouette alone should identify the character
```

## 區塊 2b：可愛度基準（四角色統一）

決策依據：體型辨識度是這個專案的全部意義——小孩要認得出「這是我們家那隻」。
所以體型不能讓步。但眼睛大小幾乎不影響體型辨識，卻直接決定 3 歲小孩的情感連結。
**該讓步的是眼睛，不是身體。**

```
PROPORTIONS AND APPEAL:
Keep the animal's real adult build - the body proportions must read as the actual
breed and age, not as a generic cute puppy. Do NOT compress the body, do NOT
shorten the legs, do NOT inflate the head into chibi proportions.

Within that constraint, push appeal in two specific places only:
- The head is slightly larger than life, a little over one quarter of total height
- The eyes are noticeably larger than life, big enough that the iris and a single
  highlight pixel both read clearly at the final sprite size
```

> **實測提醒**：眼睛這一條**提示詞打不準**。在 64px sprite 上眼睛只有 2–3 像素，
> 低於模型的有效控制解析度。實測要求「眼睛放大 20%」得到的仍是 3×2 的純暗色塊，
> 沒有虹膜也沒有高光。
> **眼睛一律用 `tools/pixedit.py` 手工畫**，不要靠重生。

## 區塊 3：負面約束

```
BACKGROUND: flat pure magenta #FF00FF, completely uniform. No shadow, no ground, no scenery, no text, no border, no watermark, no signature, no labels, no grid lines, no color swatches.

Single character, centered, one pose only.
```

> **為什麼用洋紅背景**：gpt-image-2 **不支援透明背景**
> （要透明得退回 gpt-image-1.5，但那個模型的像素風格表現較差）。
> 所以統一用純洋紅，再由 `tools/pixelate.py` 以色度距離去背。
> 洋紅和四個角色的配色（棕、白、金、冰藍）在色相上都離得很遠，去背很乾淨。

---

## 生成參數

| 項目 | 值 | 說明 |
|---|---|---|
| 模型 | `gpt-image-2` | |
| 尺寸 | `1024×1024` | 必須是 16 的倍數、總像素 ≥655,360、長短邊比 ≤3:1 |
| 參考影像 | 真實照片 + 已核可的同角色設定圖 | gpt-image-2 的影像輸入永遠是 high fidelity |
| 後處理 | **不要在生成端做** | 一律交給 `tools/pixelate.py`，才能控制網格與調色盤 |

---

## 已知的模型行為（實測）

| 現象 | 對策 |
|---|---|
| 實際輸出尺寸不等於指定尺寸（要 1024×1024 得到 1341×1173） | 不要依賴輸出尺寸，`pixelate.py` 會裁到內容範圍再縮放 |
| 原始輸出有 ~28,000 色，「純洋紅」背景是 10 幾種相近雜訊值 | 一律經過 `pixelate.py`，不要直接用原始輸出 |
| 色塊長度 2/3/4/5 混雜，網格不一致 | 同上。網格由 `pixelate.py` 的眾數降採樣重建 |
| 一張圖裡放多個視角時，各視角比例會互相漂移 | **一次只生成一個姿勢**。設定圖也分開生，再自己拼版 |
| 強調「可愛」會讓中大型犬變成幼犬比例 | 在提示詞裡明確給出頭身比、腿長、體長與肩高的關係 |
| **5 像素以下的特徵無法用提示詞控制**（眼睛、鼻孔、牙齒） | 用 `tools/pixedit.py` 手工畫。這是分工不是妥協 |
| **指定顏色打不準**（要去飽和灰白，實得飽和度 80 的暖褐） | 用 `tools/pixelate.py --remap` 校正。實測可把飽和度 80 修成 26 |
| 同一個來源色在不同部位有不同語意（吻部與腳同色） | remap 用 `bbox` 區域限定，見 `docs/05` |

---

## 一句話總結

**AI 負責「設計」，`pixelate.py` 負責「資產」。**
不要期待模型直接產出可用的檔案，也不要在管線裡手工修圖。
