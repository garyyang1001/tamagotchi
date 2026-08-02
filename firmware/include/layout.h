/* 由 tools/mklayout.py 從 specs/ 產生。**不要手改。**
   改 specs/scene.json / visitors.json / accessories/ / outfits/ 再重跑那支工具。

   為什麼要編譯期產生：韌體讀不了 JSON，但同一個數字不能有兩個定義。
   tools/cell.py 那次的教訓——brown_mixed 的 sprite_cell 寫錯很久沒被發現，
   就是因為當時沒有任何程式讀它。 */
#ifndef ICEPET_LAYOUT_H
#define ICEPET_LAYOUT_H

#include <stdint.h>

#define ROOM_W 320
#define ROOM_H 240
#define SCR_W 320
#define SCR_H 240   /* 房間就是滿版，UI 條是浮層不佔高度 */
#define FLOOR_Y 132
#define GROUND_Y 170   /* 角色腳底的基準線 */
#define UI_BAR_Y 176
#define UI_BAR_H 64
#define UI_ICON_SIZE 48
#define UI_ICON_Y 184
#define UI_BOX_PAD 6
#define UI_BOX_THICK 3
#define UI_ICON_COUNT 5   /* game.h 的 MENU_MAX。原本寫死 3，後兩個動作根本畫不出來 */
static const int16_t UI_ICON_X[UI_ICON_COUNT] = { 8, 72, 136, 200, 264 };

/* 站位。狗那一格三隻共用，由 game_t.present 決定畫誰。 */
#define SLOT_DOG_X 40
#define SLOT_DOG_DY 4
#define SLOT_ICE_PRINCESS_X 120
#define SLOT_ICE_PRINCESS_DY 0

/* 物件。mode 決定 x/y 怎麼算——判準是「有沒有接地」：
   ground 的 y 推算得出來（ground_y - dy - base_row），float 的沒有基準只能填。
   ui 是介面圖示，不是房間裡的東西：位置由 UI 版面決定，
   **draw_fixed_objects 必須跳過它們**，render_init 也不把它們算成必要資產。 */
typedef enum { OBJ_GROUND = 0, OBJ_FLOAT, OBJ_FIXED, OBJ_TRACK,
               OBJ_UI } obj_mode_t;

typedef enum {
    OBJ_BALL,
    OBJ_BATTERY,
    OBJ_BED,
    OBJ_BED_PRINCESS,
    OBJ_BOWL,
    OBJ_CURSOR,
    OBJ_HEART,
    OBJ_ICON_BATH,
    OBJ_ICON_BLADDER,
    OBJ_ICON_CALL,
    OBJ_ICON_DRESS,
    OBJ_ICON_ENERGY,
    OBJ_ICON_FEED,
    OBJ_ICON_HUNGER,
    OBJ_ICON_LIGHT,
    OBJ_ICON_MOOD,
    OBJ_ICON_PET,
    OBJ_ICON_PLAY,
    OBJ_ICON_TIDY,
    OBJ_ICON_TOILET,
    OBJ_LAMP_SWITCH,
    OBJ_SHOWER,
    OBJ_SNOW,
    OBJ_STAR,
    OBJ_TUB,
    OBJ_COUNT
} obj_id_t;

typedef struct {
    uint8_t  mode;          /* obj_mode_t */
    uint8_t  w, h;
    uint8_t  frames;
    uint16_t ms;
    uint8_t  base_row;      /* ground：哪一列踩在地面線上 */
    int16_t  at_x, at_y;    /* fixed 才有意義 */
    int16_t  ax[4];         /* ground：每個角色的 anchor_x，-1 = 沒有 */
    int16_t  fx[4], fy[4];  /* float：每個角色相對影格左上角的偏移 */
} obj_def_t;

/* 角色索引和 save.h 的 character_id_t 一致：ice_princess, chihuahua, brown_mixed, brindle_guard */
static const obj_def_t OBJ_DEF[OBJ_COUNT] = {
    { OBJ_TRACK,  10,  9, 1,    0,  8,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* ball */
    { OBJ_FIXED,  16, 10, 3,    0,  9, 298,   6, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* battery */
    { OBJ_GROUND, 66, 22, 1,    0, 13,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* bed */
    { OBJ_GROUND, 70, 30, 1,    0, 22,   0,   0, { -3, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* bed_princess */
    { OBJ_GROUND, 15,  9, 1,    0,  8,   0,   0, { -1, 42, 50, 47}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* bowl */
    { OBJ_GROUND, 32, 13, 1,    0,  0,   0,   0, { 16, 16, 16, 16}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* cursor */
    { OBJ_FLOAT,  10,  8, 4,  180,  7,   0,   0, { -1, -1, -1, -1}, { 24, 33, 39, 39}, { -5,  5,-11,-11} },  /* heart */
    { OBJ_UI,     40, 40, 1,    0, 39,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_bath */
    { OBJ_UI,     10, 10, 1,    0,  9,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_bladder */
    { OBJ_UI,     16, 16, 1,    0, 15,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_call */
    { OBJ_UI,     16, 16, 1,    0, 15,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_dress */
    { OBJ_UI,     10, 10, 1,    0,  9,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_energy */
    { OBJ_UI,     40, 40, 1,    0, 39,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_feed */
    { OBJ_UI,     10, 10, 1,    0,  9,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_hunger */
    { OBJ_UI,     16, 16, 2,    0, 15,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_light */
    { OBJ_UI,     10, 10, 1,    0,  9,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_mood */
    { OBJ_UI,     40, 40, 1,    0, 39,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_pet */
    { OBJ_UI,     40, 40, 1,    0, 39,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_play */
    { OBJ_UI,     10, 10, 1,    0,  9,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_tidy */
    { OBJ_UI,     40, 40, 1,    0, 39,   0,   0, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* icon_toilet */
    { OBJ_FIXED,  16, 16, 2,    0, 15, 210,  70, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* lamp_switch */
    { OBJ_GROUND, 46, 102, 3,  260, 101,   0,   0, {  9, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* shower */
    { OBJ_FIXED,  52, 36, 3,  420, 35, 135,  21, { -1, -1, -1, -1}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* snow */
    { OBJ_FLOAT,  10, 10, 4,  150,  9,   0,   0, { -1, -1, -1, -1}, { 24, 33, 39, 39}, { -7,  3,-13,-13} },  /* star */
    { OBJ_GROUND, 56, 24, 3,  300, 23,   0,   0, {  4,  4,  4,  4}, {  0,  0,  0,  0}, {  0,  0,  0,  0} },  /* tub */
};

static const char *const OBJ_NAME[OBJ_COUNT] = {
    "obj/ball",
    "obj/battery",
    "obj/bed",
    "obj/bed_princess",
    "obj/bowl",
    "obj/cursor",
    "obj/heart",
    "obj/icon_bath",
    "obj/icon_bladder",
    "obj/icon_call",
    "obj/icon_dress",
    "obj/icon_energy",
    "obj/icon_feed",
    "obj/icon_hunger",
    "obj/icon_light",
    "obj/icon_mood",
    "obj/icon_pet",
    "obj/icon_play",
    "obj/icon_tidy",
    "obj/icon_toilet",
    "obj/lamp_switch",
    "obj/shower",
    "obj/snow",
    "obj/star",
    "obj/tub",
};

/* ---- UI 圖示 ----
   紅框只說「選到了」，說不出「這是什麼」：四歲半看五個一樣的灰框，
   分不出哪個是吃飯哪個是洗澡。圖示是**唯一**能表達語意的東西——
   這個年齡不讀字，也不會去記「左邊數來第三個」。

   值是 OBJ_* 的索引，**-1 = 資產包裡還沒有那張圖**，
   渲染層畫退回版（空框／只有紅框），不是致命錯誤。 */

/* 動作 → 圖示。索引是 game.h 的 action_t，順序是**讀 game.h 讀來的**，
   不是抄的——抄一份就多一個定義。render.c 有 _Static_assert 在對齊兩邊。 */
#define UI_ACTION_ICON_COUNT 8
static const int8_t UI_ACTION_ICON[UI_ACTION_ICON_COUNT] = {
     -1,  /* ACT_NONE   （選單裡沒有這一項） */
     12,  /* ACT_FEED   icon_feed */
     16,  /* ACT_PET    icon_pet */
     17,  /* ACT_PLAY   icon_play */
     -1,  /* ACT_SLEEP  （選單裡沒有這一項） */
     19,  /* ACT_TOILET icon_toilet */
     -1,  /* ACT_DRESS  （選單裡沒有這一項） */
      7,  /* ACT_BATH   icon_bath */
};

/* 進度條的圖示。**順序必須和 game_need_bars() 一致**：
   hunger / energy / mood / (狗 = bladder、公主 = tidy)。
   第四條依角色換，所以兩張表——公主沒有 bladder。 */
#define UI_BAR_ICON_COUNT 4
static const int8_t UI_BAR_ICON_DOG[UI_BAR_ICON_COUNT] = { 13, 11, 15, 8 };
static const int8_t UI_BAR_ICON_PRINCESS[UI_BAR_ICON_COUNT] = { 13, 11, 15, 18 };

/* 主畫面牆上那三格的「按下去會怎樣」。索引是 main_slot_t 的前三格：
   門 → 呼叫、衣櫃 → 換裝、開關 → 燈（那一個有兩格，見 render.c）。
   公主與狗不在這裡：它們接地，用地板箭頭。 */
#define UI_SLOT_HINT_COUNT 3
static const int8_t UI_SLOT_HINT[UI_SLOT_HINT_COUNT] = { 9, 10, 14 };

/* 球的逐格螢幕座標。**track 型只有球一個**——判準是「它會不會離開角色的身體」，
   公主捧在手上的碗就不拆（它跟著手動，只能留在影格裡）。
   座標是相對房間左上角的絕對值，不經過 anchor 也不經過 base_row。 */
#define BALL_TRACK_COUNT 12
#define BALL_TRACK_MS 80
static const int16_t BALL_TRACK[BALL_TRACK_COUNT][2] = {
    {  78, 108 },
    { 102,  96 },
    { 126,  92 },
    { 150,  96 },
    { 172, 108 },
    { 192, 126 },
    { 208, 141 },
    { 222, 132 },
    { 234, 141 },
    { 244, 137 },
    { 252, 141 },
    { 256, 141 },
};

/* 動畫 → 物件。渲染層查這張表，不必在程式裡寫死動畫名稱。
   z=1 代表先畫物件再畫角色（睡墊墊在身下）。 */
typedef struct {
    uint8_t anim;           /* anim_id_t */
    int8_t  obj;            /* obj_id_t，-1 = 無 */
    int8_t  obj_princess;   /* per_character 覆寫，-1 = 同上 */
    uint8_t under;          /* 1 = 畫在角色下方 */
} obj_cue_t;

static const obj_cue_t OBJ_CUE[] = {
    { 16,  0, -1, 0 },  /* chase_ball */
    { 11,  4, -1, 0 },  /* eat */
    { 12,  4, -1, 0 },  /* eat_happy */
    {  7,  2,  3, 1 },  /* lie_down */
    { 19,  6, -1, 0 },  /* pet_react */
    { 13,  2,  3, 1 },  /* sleep_breathe */
    {  8,  2,  3, 1 },  /* wake_up */
};
#define OBJ_CUE_COUNT 7

/* 閒置訪客。牠們走 AI 管線、有自己的調色盤，不是 scene 的幾何物件。 */
typedef struct { uint8_t w, h, frames, base_row; uint16_t ms; } visitor_def_t;
static const visitor_def_t VISITOR_DEF[] = {
    { 40, 40, 4, 39, 200 },  /* squirrel */
    { 28, 26, 4, 25, 240 },  /* bird */
};
static const char *const VISITOR_NAME[] = {
    "visitor/squirrel",
    "visitor/bird",
};

/* 公主的配件。掛在 specs/anchors/ 的逐格頭部錨點上。 */
#define ACC_COUNT 5
typedef struct { uint8_t w, h; int8_t dx, dy; } acc_def_t;
static const acc_def_t ACC_DEF[ACC_COUNT] = {
    { 28, 16, -14,  -4 },  /* beanie */
    { 15, 10, -16,   1 },  /* bow_red */
    { 30,  9, -15,   0 },  /* flower_crown */
    { 32, 12, -16,  -1 },  /* earmuffs */
    { 11, 13,   4,   1 },  /* gem_clip */
};
static const char *const ACC_NAME[ACC_COUNT] = {
    "acc/ice_princess/beanie",
    "acc/ice_princess/bow_red",
    "acc/ice_princess/flower_crown",
    "acc/ice_princess/earmuffs",
    "acc/ice_princess/gem_clip",
};

/* 服裝：只換調色盤的五個槽，零張新圖。索引就是 outfit_id。 */
#define OUTFIT_COUNT_GEN 5
#define OUTFIT_SLOTS 5
static const uint8_t OUTFIT_SLOT_IDX[OUTFIT_SLOTS] = { 2, 3, 4, 5, 6 };
static const uint16_t OUTFIT_RGB565[OUTFIT_COUNT_GEN][OUTFIT_SLOTS] = {
    { 0x1B15, 0x3C9C, 0x757D, 0xCF1E, 0xEF9F },  /* 冰藍 */
    { 0x6AA1, 0xA402, 0xD523, 0xF6F3, 0xFF9A },  /* 陽光黃 */
    { 0x3B81, 0x5D42, 0x7EA3, 0xCF94, 0xE7DA },  /* 嫩芽綠 */
    { 0x1BE6, 0x25C9, 0x4ECE, 0xBF99, 0xE7DD },  /* 森綠 */
    { 0xB8B2, 0xE217, 0xEBBA, 0xFE7D, 0xFF5E },  /* 莓果粉 */
};

/* 資產名字是 "<角色>/<動畫>"。渲染層用這兩張表組出來，不要在程式裡寫死字串。
   順序必須和 save.h 的 character_id_t、game.h 的 anim_id_t 完全一致。 */
static const char *const CHAR_ID[4] = { "ice_princess", "chihuahua", "brown_mixed", "brindle_guard" };
static const char *const ANIM_ID[21] = {
    "idle_breathe",
    "idle_blink",
    "idle_look",
    "idle_ear_twitch",
    "tail_wag",
    "sit_down",
    "stand_up",
    "lie_down",
    "wake_up",
    "walk",
    "turn",
    "eat",
    "eat_happy",
    "sleep_breathe",
    "yawn",
    "play_bow",
    "chase_ball",
    "happy",
    "sad_wait",
    "pet_react",
    "toilet",
};

/* 公主的逐格頭部錨點（tools/anchors.py 從髮色像素**量**出來的，不是填的）。
   配件掛在這上面，所以 lie_down 頭降 32 px 時帽子跟著降。
   索引 [anim_id][frame]，值是 {中心 x, 頭頂 y}，相對影格左上角。 */
#define HEAD_MAX_FRAMES 6
static const int8_t HEAD_ANCHOR[21][HEAD_MAX_FRAMES][2] = {
    { {28, 7}, {28, 6}, {28, 6}, {28, 7}, {28, 7}, {28, 7} },  /* idle_breathe */
    { {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7} },  /* idle_blink */
    { {28,13}, {28,14}, {28,12}, {30,13}, {30,13}, {30,13} },  /* idle_look */
    { {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7} },  /* idle_ear_twitch */
    { {28, 9}, {28, 9}, {28, 9}, {28, 9}, {28, 9}, {28, 9} },  /* tail_wag */
    { {28,12}, {28,17}, {28,21}, {29,27}, {29,27}, {29,27} },  /* sit_down */
    { {29,27}, {28,21}, {28,17}, {28,12}, {28,12}, {28,12} },  /* stand_up */
    { {28,23}, {31,41}, {32,51}, {33,55}, {33,55}, {33,55} },  /* lie_down */
    { {33,55}, {32,51}, {31,41}, {28,23}, {28,23}, {28,23} },  /* wake_up */
    { {28,10}, {28,10}, {28,10}, {28,10}, {28,10}, {28,10} },  /* walk */
    { {28, 7}, {35, 7}, {35, 7}, {35, 7}, {35, 7}, {35, 7} },  /* turn */
    { {27,15}, {28,17}, {28,17}, {27,15}, {27,15}, {27,15} },  /* eat */
    { {26,11}, {31,11}, {28,10}, {26,12}, {26,12}, {26,12} },  /* eat_happy */
    { {26,50}, {24,50}, {25,50}, {26,50}, {26,50}, {26,50} },  /* sleep_breathe */
    { {28,10}, {27,10}, {27,10}, {28,10}, {28,10}, {28,10} },  /* yawn */
    { {27, 9}, {27, 9}, {32,24}, {32,25}, {32,25}, {32,25} },  /* play_bow */
    { {28, 9}, {31, 9}, {24, 9}, {23, 9}, {23, 9}, {23, 9} },  /* chase_ball */
    { {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7} },  /* happy */
    { {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7}, {28, 7} },  /* sad_wait */
    { {28, 7}, {34, 7}, {34, 8}, {28, 8}, {28, 8}, {28, 8} },  /* pet_react */
    { {27, 9}, {27,22}, {28,31}, {27,31}, {27,31}, {27,31} },  /* toilet */
};

#endif /* ICEPET_LAYOUT_H */
