# 動畫影格圖的生成範本

> ### ⚠️ 這份文件現在是「為什麼」，不是「用什麼」
>
> 提示詞的**權威來源已經移到資料檔**：
>
> | 檔案 | 內容 |
> |---|---|
> | `specs/anim_prompt/_skeleton.txt` | 共用骨架（解析度宣告、動作規則、風格規則） |
> | `specs/anim_prompt/_frames.json` | 15 個動畫各自的 `THE FRAMES` 段落與版面 |
> | `specs/anim_prompt/<角色>.json` | 每隻角色的 SUBJECT／IDENTITY／STYLE／節奏／耳型 |
>
> 產生方式：
>
> ```bash
> python tools/mkprompt.py -c brindle_guard --all --out-dir build/prompt
> ```
>
> 為什麼要拆：一隻狗 15 個動畫，三隻狗就是 45 段幾乎相同的文字。
> 手抄一定會漂移，而且漂移的地方剛好是「IDENTITY FEATURES 每一格都要一樣」
> 這種最不能錯的條款。拆成骨架 + 角色填充 + 動作段落之後，改一次骨架三隻狗同步生效。
>
> 下面保留的是**這個結構為什麼長這樣**的理由，以及每一條防的是什麼問題。
> 要改提示詞請改資料檔，不要改這裡。


> **這個範本是驗證過的。** `B4_brown_mixed_walkcycle.png` 用它生出來的結果：
> 四格同一隻狗、同一尺度、腳掌貼地、腿在關節彎曲、色群一致。
>
> **照填，不要重寫結構。** 只換 `【動作】` 那一段。

---

## 生成參數

| 項目 | 值 |
|---|---|
| 模型 | `gpt-image-2` |
| 尺寸 | `1536x1024`（2×2 格）或 `1536x512`（4×1 橫排） |
| 參考影像 | `art/approved/<id>/master_stand_r_source.png` |
| 後處理 | **生成端不做任何後處理**，交給管線 |

---

## 提示詞（固定區塊，一字不改）

```
Pixel art walk cycle sprite sheet, in the style of a 16-bit SNES or Game Boy Color role-playing game. Flat pure magenta #FF00FF background, completely uniform.

SUBJECT: the exact same chocolate-brown mixed-breed dog shown in the attached reference image. Same build, same head size, same markings, same colors, same art style. Do not redesign it, do not make it cuter or younger or smaller.

CRITICAL - RESOLUTION AND DETAIL LEVEL:
Each frame must look like a sprite originally drawn at only 64 x 56 pixels, then enlarged with nearest-neighbor scaling so each original pixel is a large visible square block. Very low detail, large flat areas of a single solid color, aggressively simplified shapes. This is NOT a detailed illustration with a pixelation filter applied. It is a genuinely low-resolution sprite drawn pixel by pixel.

LAYOUT: a 2 by 2 grid of four frames, read left to right then top to bottom. The four frames sit in four equal quadrants with clear empty magenta space between them. No borders, no dividing lines, no numbers, no labels.

【動作】← 這一段每個動畫不同，見下方清單

MOTION RULES, strictly enforced:
- The dog's body stays at the SAME horizontal position and the SAME size in all frames. Only the parts described above change.
- Every paw that is on the ground must stay flat on the ground line. Paws must NOT swing up into the air in an arc.
- The legs bend at the knee and elbow. They are not stiff straight sticks rotating from the shoulder.
- All frames share the same ground line at the same height.

IDENTITY FEATURES, simplified into flat shapes, identical in all frames:
- Dark chocolate brown coat as the main body color
- A pale greyish-white grizzled muzzle patch around the nose and mouth, desaturated, almost silver-grey, NOT golden and NOT tan
- Two small tan dots above the eyes acting as eyebrows
- A tan stripe down the center of the chest
- Tan lower legs and paws. This tan is a WARM ORANGE-BROWN, clearly more saturated and more orange than the muzzle. The paws must NOT be white, cream, grey or pale - they are the same warm tan as the eyebrow dots and the chest stripe.
- Long floppy drop ears hanging beside the head
- Liver brown nose, NOT black
- Amber eyes with a single white highlight

STYLE, strictly enforced:
- Exactly three tones for the brown coat, two for the tan markings, two for the grey muzzle
- Large uninterrupted regions of flat solid color
- A single dark outline, one pixel thick, around the whole silhouette
- NO anti-aliasing, NO blur, NO gradients, NO dithering, NO noise, NO texture
- NO individual fur strands, NO realistic shading, NO photorealism
- Total palette: 12 colors maximum

No text, no numbers, no labels, no borders, no grid lines, no ground shadow, no scenery.
```

---

## 為什麼這個結構有效

| 條款 | 防的問題 |
|---|---|
| 「一張圖含全部影格」 | 分開多次生成會漂移（實測三個姿勢全部往同方向跑） |
| `SAME horizontal position and SAME size` | 影格之間角色跳動 |
| `paws must stay flat on the ground line` | 腳掌畫弧飛起來——部件旋轉路線就是死在這 |
| `legs bend at the knee and elbow` | 腿變成從肩膀甩的棍子 |
| `genuinely low-resolution` | 模型會理解成「高細節圖套像素化濾鏡」，等效解析度衝到 150×160 |
| `IDENTITY FEATURES ... identical in all frames` | 標記位置在影格之間移動——繪本研究顯示孩子會察覺 |
| tan 那條特別強調「WARM ORANGE-BROWN，不是白/奶油/灰」 | 實測 `tail_wag` 那張的腳掌被畫成灰白，導致整批沒有高飽和色群，`automap.py` 分不出 tan 與 muzzle_grey |

---

## 十六個動畫的【動作】段落

`stand_up` 與 `wake_up` **不生成**，直接反向播放 `sit_down` / `lie_down`。
這是 Digimon V-Pet 的做法（12 張 sprite 撐出 8 種行為）。

### 待機類

**idle_blink**（3 格，2×2 用左上/右上/左下）
```
THE FRAMES: frame 1 the eyes are fully open; frame 2 the eyes are half closed, the upper eyelid covering the top half; frame 3 the eyes are fully closed, a simple dark line. Nothing else in the body moves at all. The fourth quadrant is empty magenta.
```

**idle_look**（4 格）
```
THE FRAMES: the dog turns its head to look around. Frame 1 head facing forward. Frame 2 head turned slightly toward the viewer. Frame 3 head turned back to forward. Frame 4 head tilted slightly down. Only the head and ears move; the body and all four legs stay completely still.
```

**idle_ear_twitch**（4 格）
```
THE FRAMES: one ear flicks. Frame 1 both ears hang relaxed. Frame 2 the near ear lifts and angles outward. Frame 3 the near ear flicks further back. Frame 4 both ears relaxed again. Only the ears move; head, body and legs stay completely still.
```

**tail_wag**（4 格）
```
THE FRAMES: the tail swings. Frame 1 the tail is low and to the left. Frame 2 the tail is raised to the middle. Frame 3 the tail is high and to the right. Frame 4 the tail is back to the middle. This is a heavy calm dog, so the swing is wide but slow, not a fast flutter. Only the tail moves; head, body and legs stay completely still.
```

### 姿態類

**sit_down**（4 格）
```
THE FRAMES: the dog sits down. Frame 1 standing on all four legs. Frame 2 the rear legs begin to fold, the hindquarters lowering. Frame 3 the hindquarters are almost down, the front legs still straight. Frame 4 fully seated, rear legs folded under, front legs straight and vertical, back upright and leaning slightly back. The silhouette must clearly change between frame 1 and frame 4 - a sitting dog is a different shape, not the same shape made shorter.
```

**lie_down**（4 格）
```
THE FRAMES: the dog lies down from a sitting position. Frame 1 seated. Frame 2 the front legs slide forward and the chest lowers. Frame 3 the chest is almost on the ground. Frame 4 fully lying down, belly and chest on the ground, front legs stretched forward flat, rear legs folded alongside the body, head up and alert resting above the front paws.
```

### 動作類

**eat**（4 格，循環）
```
THE FRAMES: the dog eats from a bowl on the ground. Frame 1 the head lowers toward the ground. Frame 2 the head is down, the mouth open. Frame 3 the head is down, the mouth closed, chewing. Frame 4 the head lifts slightly. The body and all four legs stay planted and still; only the head, neck and jaw move.
```

**eat_happy**（4 格）
```
THE FRAMES: the dog is happy after eating. Frame 1 standing, mouth closed in a content curve, licking the muzzle. Frame 2 the tongue out licking the nose. Frame 3 head tilted, tail up. Frame 4 back to standing, tail still up. The four paws stay flat on the ground in all frames.
```

**sleep_breathe**（4 格，循環）
```
THE FRAMES: the dog is asleep lying down. In all four frames the dog is lying on its belly, front legs stretched forward, head resting down on or beside the front paws, eyes closed as simple dark lines. The only difference between frames is a very small rise and fall of the ribcage: frame 1 and 4 are the lowest, frames 2 and 3 the ribcage is one pixel higher. Nothing else moves at all.
```

**yawn**（4 格）
```
THE FRAMES: the dog yawns. Frame 1 mouth closed, eyes open. Frame 2 the mouth begins to open, eyes narrowing. Frame 3 the mouth is wide open in a big yawn, eyes squeezed shut, tongue visible. Frame 4 the mouth closes again, eyes still narrow. The body and legs stay completely still.
```

**play_bow**（4 格）
```
THE FRAMES: the dog does a play bow inviting play. Frame 1 standing normally. Frame 2 the front legs begin to fold and the chest lowers. Frame 3 the chest and front legs are down on the ground while the hindquarters stay up high, the classic play bow. Frame 4 the same bow held, tail raised. The rear paws stay flat on the ground throughout.
```

**chase_ball**（4 格，循環）
```
THE FRAMES: the dog runs. Frame 1 both front legs reaching forward together and both rear legs extended back, the body stretched out, all four paws off the ground at the lowest point of the stride. Frame 2 the legs gathering under the body, paws near the ground. Frame 3 the legs bunched together under the body, the back arched, ready to push off. Frame 4 the legs extending again. This is a heavy calm dog, so the run is a steady bound, not a frantic sprint.
```

**pet_react**（4 格，循環）
```
THE FRAMES: the dog enjoys being petted. Frame 1 head level, eyes open. Frame 2 the head leans and presses upward and to the side, eyes narrowing. Frame 3 the head presses further into the touch, eyes closed happily in a curve. Frame 4 the head returns partway. Only the head and neck move; the body and all four legs stay planted.
```

**toilet**（4 格）
```
THE FRAMES: the dog squats. Frame 1 standing. Frame 2 the rear legs begin to bend and the hindquarters lower. Frame 3 the dog is in a low squat, rear legs bent, front legs straight, back slightly arched, head turned to look back. Frame 4 the same squat held. Show only the posture. Draw absolutely nothing on the ground.
```

**turn**（2 格）
```
THE FRAMES: the dog turns around. Only two frames are needed, in the top two quadrants; leave the bottom two quadrants empty magenta. Frame 1 the dog seen from the side facing right. Frame 2 the dog seen from the side facing left, the exact mirror image. Both frames identical apart from the direction.
```

---

## 產出後的處理

```bash
python tools/cutstrip.py art/generated/B4_<id>_<anim>.png \
    --grid 2x2 --out-dir build/strip/<anim> --name <id>_<anim>

# 先看色群，寫 remap 對照表
python tools/pixelate.py build/strip/<anim>/*.png --width 64 --height 56 --no-crop \
    --colors 12 --palette specs/palettes/<id>.json --dump-clusters --out-dir /tmp/c

# 正式產出
python tools/pixelate.py build/strip/<anim>/*.png --width 64 --height 56 --no-crop \
    --colors 12 --palette specs/palettes/<id>.json \
    --remap art/generated/B4_<id>_<anim>.remap.json --out-dir build/strip/<anim>_px
```

**`cutstrip.py` 會在切開之前先量化前景**，所以各格共用同一組色群，
一份 remap 對照表涵蓋全部影格。這一步漏掉的話，對照表只會中第一格。


---

## 追加的教訓（brindle_guard 那一輪）

| 條款 | 防的問題 |
|---|---|
| 背帶那條要明確禁止「thin straps」「繞腹」「碰到腿或腳掌」 | 只寫「solid flat block」不夠強。模型仍然畫成繞腹的細帶子，降到 64px 之後變成沿著前腿到腳掌的粉紅線——畫面上是狗穿粉襪。改寫之後 15 張裡 14 張乾淨 |
| 虎斑那條要寫「FEW and BOLD，像大貓條紋」並明確禁止細密紋路 | 照實畫真實虎斑，64px 下只會變成雜訊 |
| 立耳要寫「rotate, do not fold or droop」 | 耳朵動畫的預設寫法是照垂耳寫的，套到立耳上模型會把耳朵折下來 |

**提示詞改不動的，就改管線。** 背帶那條改寫過一輪之後仍有一半的圖會畫腿線，
最後是靠調色盤的 `protect.min_thickness` 擋掉的（見 docs/08 第 2.9 節）。
提示詞負責大方向，管線負責保證下限——兩邊都要有。
