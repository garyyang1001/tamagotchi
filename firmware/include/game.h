/*
 * game.h — 遊戲邏輯核心
 *
 * 純邏輯，不依賴任何硬體。可以在主機端完整編譯與測試（見 test/test_game.c）。
 * 渲染、觸控、音效都由外層呼叫這一層，這一層不反過來呼叫它們。
 *
 * 設計依據見 docs/02_遊戲設計.md。其中一條是硬性規則：
 *
 *     沒有失敗狀態。需求歸零只影響「播哪個動畫」，
 *     不扣分、不生病、不死亡、不觸發警告，也不會讓任何已解鎖的東西失去。
 *
 * 這台機器的使用者是 3 歲小孩。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "save.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* 動畫                                                                */
/* ------------------------------------------------------------------ */

/* 順序必須與 specs/animations/<char>.anim.json 一致。
   資產打包時以名稱雜湊對應，但執行期用這個 enum 索引。 */
typedef enum {
    ANIM_IDLE_BREATHE = 0,
    ANIM_IDLE_BLINK,
    ANIM_IDLE_LOOK,
    ANIM_IDLE_EAR_TWITCH,
    ANIM_TAIL_WAG,

    ANIM_SIT_DOWN,
    ANIM_STAND_UP,
    ANIM_LIE_DOWN,
    ANIM_WALK,
    ANIM_TURN,

    ANIM_EAT,
    ANIM_EAT_HAPPY,

    ANIM_SLEEP_BREATHE,
    ANIM_YAWN,
    ANIM_WAKE_UP,

    ANIM_PLAY_BOW,
    ANIM_CHASE_BALL,
    ANIM_HAPPY,
    ANIM_SAD_WAIT,
    ANIM_PET_REACT,
    ANIM_TOILET,

    ANIM_COUNT
} anim_id_t;

/* 角色的身體姿態。動畫序列要從目前姿態接到目標姿態，
   不能從站姿直接跳到 sleep_breathe，中間要補 lie_down。 */
typedef enum {
    POSE_STAND = 0,
    POSE_SIT,
    POSE_LIE,
    POSE_COUNT
} pose_t;

/* ------------------------------------------------------------------ */
/* 幼兒相關的時間常數                                                   */
/* ------------------------------------------------------------------ */

/* 觸控之後的「安靜期」：這段時間內不觸發任何自發行為。
 *
 * Vatavu et al. 2015（n=89，3–6 歲，文獻中最大的兒童觸控資料集）觀察到
 * 學齡前兒童有魔法思維，會把同時發生的事件關聯成因果關係。
 * 如果小孩剛摸完狗、狗就自己播了 sad_wait，她會認為是自己弄的。
 *
 * 這不是延遲問題是排程問題——自發行為必須避開觸控後的窗口。 */
#define POST_TOUCH_QUIET_MS 2500u

/* 閒置行為的切換間隔。實際出貨的電子雞韌體用 8–15 秒，
 * 遠短於 3A 遊戲的 30–60 秒——因為角色就是整個畫面內容。
 * 每次觸發後重新抽，不要用固定間隔。 */
#define IDLE_MIN_MS 8000u
#define IDLE_MAX_MS 15000u

/* 同一個閒置行為的最短重複間隔（次）。連續播到同一個會讓角色看起來卡住。 */
#define IDLE_NO_REPEAT 3

/* ------------------------------------------------------------------ */
/* 互動                                                                */
/* ------------------------------------------------------------------ */

typedef enum {
    ACT_NONE = 0,
    ACT_FEED,
    ACT_PET,
    ACT_PLAY,
    ACT_SLEEP,
    ACT_TOILET,
    ACT_DRESS,      /* 僅公主 */
    ACT_COUNT
} action_t;

/* 一次互動最多由這麼多段動畫組成 */
#define SEQ_MAX 6

typedef struct {
    uint8_t anim[SEQ_MAX];
    uint8_t repeat[SEQ_MAX];   /* 該段重複幾次，0 = 無限循環直到外部結束 */
    uint8_t len;
} anim_seq_t;

/* ------------------------------------------------------------------ */
/* 執行期狀態                                                          */
/* ------------------------------------------------------------------ */

typedef enum {
    CSTATE_IDLE = 0,   /* 自主待機，會隨機播閒置動畫 */
    CSTATE_BUSY,       /* 正在播互動序列 */
    CSTATE_SLEEPING,   /* 夜間或主動睡覺 */
} char_state_t;

typedef struct {
    char_state_t state;
    pose_t       pose;

    anim_seq_t   seq;
    uint8_t      seq_step;      /* 目前播到序列第幾段 */
    uint8_t      seq_loop;      /* 該段已重複幾次 */

    uint8_t      anim;          /* 目前動畫 anim_id_t */
    uint16_t     frame;         /* 目前影格 */
    uint32_t     frame_accum_ms;

    uint32_t     idle_next_ms;  /* 下次挑閒置動畫的時間 */
    uint32_t     pet_accum_ms;  /* 撫摸累積時間，每滿 2 秒回補心情 */

    /* 最近播過的閒置動畫。連續播到同一個會讓角色看起來卡住，
       實際出貨的電子雞韌體都會設最短重複間隔。 */
    uint8_t      idle_recent[IDLE_NO_REPEAT];
    uint8_t      idle_recent_n;

    bool         hold;          /* 撫摸模式：序列最後一段無限循環 */
} char_runtime_t;

#define MILESTONE_COUNT 6

typedef struct {
    save_blob_t    save;
    char_runtime_t rt[CHAR_COUNT];

    uint64_t now_unix;
    uint32_t now_ms;            /* 開機以來的毫秒，動畫計時用 */
    uint32_t sec_accum_ms;      /* 累積未滿一秒的毫秒。不累積的話 dt=100ms
                                   時 dt/1000 恆為 0，時鐘會完全停住 */
    uint32_t last_decay_ms;
    uint8_t  decay_phase;       /* 0..11，把每小時的衰減攤到 12 次結算。
                                   放在結構裡而非函式靜態，否則多個 game
                                   實例會互相污染（測試會踩到） */

    uint8_t  hour;              /* 0..23，由 now_unix 換算 */
    bool     night;

    uint8_t  selected;          /* 目前被點選的角色，CHAR_COUNT = 無 */
    uint32_t rng;               /* 確定性 PRNG，測試可重現 */

    /* 最後一次觸控的時間。自發行為要避開它之後的 POST_TOUCH_QUIET_MS，
       否則 3 歲小孩會把自發行為誤認為是自己造成的。 */
    uint32_t last_touch_ms;

    /* 本 tick 內發生的事，供外層取用後清空 */
    uint16_t unlocked_mask;     /* 這次 tick 解鎖了哪些里程碑 */
    bool     save_dirty;
} game_t;

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */

/* 以載入好的存檔初始化。會套用離線衰減並決定開場動畫。
   離線超過 24 小時時開場是 ANIM_HAPPY 而不是 ANIM_SAD_WAIT——
   回來永遠是被歡迎的，見 docs/02。 */
void game_init(game_t *g, const save_blob_t *loaded, uint64_t now_unix);

/* 推進 dt_ms 毫秒。負責：需求衰減、動畫推進、序列轉換、
   閒置行為選擇、夜間模式切換、里程碑檢查。 */
void game_tick(game_t *g, uint32_t dt_ms);

/* 小孩點了某個角色。CHAR_COUNT 表示取消選取。 */
void game_select(game_t *g, uint8_t character);

/* 小孩按了某個動作。回傳是否被接受（睡眠中或忙碌中會拒絕）。 */
bool game_do_action(game_t *g, uint8_t character, action_t act);

/* 撫摸模式：手指在角色上滑動時持續呼叫，放開時呼叫 game_pet_end。 */
void game_pet_move(game_t *g, uint8_t character, uint32_t dt_ms);
void game_pet_end(game_t *g, uint8_t character);

/* 目前該顯示給小孩的三個動作圖示，依需求最低者優先。
   回傳實際填入的數量（最多 3）。 */
uint8_t game_suggest_actions(const game_t *g, uint8_t character,
                             action_t *out, uint8_t max);

/* 角色目前該播的動畫與影格，給渲染層用。 */
anim_id_t game_current_anim(const game_t *g, uint8_t character);

/* 該角色是否為狗（決定有無 bladder、有無 ACT_DRESS） */
bool game_is_dog(uint8_t character);

/* 需求的整體狀態，用於挑選閒置動畫。回傳 0..100 的最低需求值。 */
uint8_t game_lowest_need(const game_t *g, uint8_t character, uint8_t *which);

#ifdef __cplusplus
}
#endif
