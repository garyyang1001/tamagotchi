/*
 * game.h — 遊戲邏輯核心
 *
 * 純邏輯，不依賴任何硬體。可以在主機端完整編譯與測試（見 test/test_game.c）。
 * 渲染、按鍵、音效都由外層呼叫這一層，這一層不反過來呼叫它們。
 *
 * 設計依據見 docs/02_遊戲設計.md。其中一條是硬性規則：
 *
 *     沒有失敗狀態。需求歸零只影響「播哪個動畫」，
 *     不扣分、不生病、不死亡、不觸發警告，也不會讓任何已解鎖的東西失去。
 *
 * 這台機器的使用者是四歲半的小孩。
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

/* 三顆實體按鍵。**沒有取消鍵**——四歲半不該被要求學會「返回」，
   選單改成 MENU_TIMEOUT_MS 沒動作就自動回主畫面。 */
typedef enum {
    BTN_PREV = 0,   /* 上一個 */
    BTN_OK,         /* 確認 */
    BTN_NEXT,       /* 下一個 */
    BTN_COUNT
} button_t;

typedef enum {
    UI_MAIN = 0,    /* 主畫面：游標在房間裡看得見的實體上（見 main_slot_t） */
    UI_MENU,        /* 互動選單：固定 5 個（狗）／4 個（公主） */
    UI_CALL,        /* 呼叫選單：三隻狗三選一 */
    UI_DRESS,       /* 換裝：已解鎖的服裝，未解鎖的不顯示 */
    UI_BOOT,        /* 開機：至少停留 BOOT_MIN_MS，且存檔載入完才走 */
    UI_CELEBRATE,   /* 里程碑慶祝：CELEBRATE_MS 後自動回主畫面 */
    UI_POWEROFF     /* 關機：大家睡著，POWEROFF_MS 之後外層才真的斷電 */
} ui_screen_t;

/* ------------------------------------------------------------------ */
/* 主畫面的游標                                                        */
/* ------------------------------------------------------------------ */

/* 2026-08-02 定案的互動模型。舊版是「四個角色同時在畫面上，游標在四隻之間移動」，
 * 新版是「公主常駐 + 最多一隻狗」，游標停在**房間裡看得見的實體**上。
 *
 * 為什麼改：四隻同框時，小孩要先在四隻裡找到她想找的那一隻，
 * 而「叫一隻狗過來陪我」本身就是一個她做得到、而且有意義的決定。
 * 一次一隻也讓每一隻的存在感變強——她是在跟這一隻玩，不是在管理四隻。
 *
 * 順序是固定的，**不會因為有沒有狗而重排**：狗那一格永遠是最後一格，
 * 前四格的位置在任何時候都一樣。四歲半是靠位置記東西的，位置不可以浮動。 */
typedef enum {
    SLOT_DOOR = 0,   /* 門：沒狗時呼叫、有狗時換狗，都是同一個呼叫選單 */
    SLOT_WARDROBE,   /* 衣櫃：進 UI_DRESS 換裝 */
    SLOT_LIGHT,      /* 電燈開關：直接切燈。**「休息」不是動作，就是關燈** */
    SLOT_PRINCESS,   /* 公主，常駐 */
    SLOT_DOG,        /* 在場的狗。**只在有狗在場時才是一格** */
    SLOT_MAX
} main_slot_t;

/* 沒有狗在場時只有前四格。用 game_main_slot_count() 取，不要寫死。 */
#define MAIN_SLOT_NO_DOG 4

/* 選單沒有任何按鍵這麼久就自動回主畫面。UI_MENU / UI_CALL / UI_DRESS 共用。
   給得寬鬆：3-6 歲按一次鍵要 267-312 ms，最長按住可達 5.1 秒
   （Vatavu et al. 2015），8 秒讓她有時間想清楚要按哪個。 */
#define MENU_TIMEOUT_MS 8000u

/* 互動選單的上限。2026-08-02 從 3 放寬到 5，理由見 game_menu_actions()。 */
#define MENU_MAX 5

/* 開機畫面的最短停留。存檔載入通常只要幾十毫秒，不設下限的話
   開機圖會一閃而過，對小孩那是「機器閃了一下」不是「機器開起來了」。 */
#define BOOT_MIN_MS 1200u

/* 關機動畫的長度。外層要等這麼久才真的斷電——
   關機不可以看起來像壞掉或消失，要看起來像「大家睡著了」。 */
#define POWEROFF_MS 2000u

/* 里程碑慶祝畫面的長度。剛好等於 ANIM_HAPPY 播兩輪。 */
#define CELEBRATE_MS 3000u

/* ------------------------------------------------------------------ */
/* 在場的狗與過場                                                       */
/* ------------------------------------------------------------------ */

/* 狗走進房間／走回門裡的過場長度。兩隻都播 ANIM_WALK。
 *
 * **不可以用 ANIM_SAD_WAIT。** 狗走回門裡不是「牠不理你了」，
 * 是牠回自己的房間去過自己的生活。這條是 CLAUDE.md 規則 1 的直接後果：
 * 換一隻狗不可以帶任何負面語意，否則小孩會學到「換掉＝丟掉」。 */
#define ARRIVE_MS 1200u
#define LEAVE_MS  1200u

/* 角色的水平位移上限（像素，相對自己的站位）。
   位移是**繪製時偏移**，不是烘進動畫的——和 screen_dx 同一個道理。 */
#define CHAR_X_RANGE 24

/* ------------------------------------------------------------------ */
/* 幼兒相關的時間常數                                                   */
/* ------------------------------------------------------------------ */

/* 按鍵之後的「安靜期」：這段時間內不觸發任何自發行為。
 *
 * Vatavu et al. 2015（n=89，3–6 歲）觀察到
 * 學齡前兒童有魔法思維，會把同時發生的事件關聯成因果關係。
 * 如果小孩剛摸完狗、狗就自己播了 sad_wait，她會認為是自己弄的。
 *
 * 這不是延遲問題是排程問題——自發行為必須避開輸入後的窗口。
 *
 * **這條和輸入方式無關。** 原本叫 POST_TOUCH_QUIET_MS，2026-08-02 改成按鍵之後
 * 只換了名字：小孩剛按完按鈕、狗就自己播 sad_wait，她一樣會認為是自己弄的。 */
#define POST_INPUT_QUIET_MS 2500u

/* 閒置行為的切換間隔。實際出貨的電子雞韌體用 8–15 秒，
 * 遠短於 3A 遊戲的 30–60 秒——因為角色就是整個畫面內容。
 * 每次觸發後重新抽，不要用固定間隔。 */
#define IDLE_MIN_MS 8000u
#define IDLE_MAX_MS 15000u

/* 同一個閒置行為的最短重複間隔（次）。連續播到同一個會讓角色看起來卡住。 */
#define IDLE_NO_REPEAT 3

/* ------------------------------------------------------------------ */
/* 待機（背光）                                                         */
/* ------------------------------------------------------------------ */

/* 沒有按鍵這麼久就把背光降到一半；再久就關掉背光並要求外層進 light sleep。
 *
 * docs/01 原本寫的是 30 秒／3 分鐘，那是**觸控時代**的政策：
 * 觸控機器沒有「只是在看」的使用情境，手離開螢幕就等於沒在用。
 * 按鍵機器不一樣——小孩會純粹看著角色自己動，那是這台機器的主要內容之一。
 * 30 秒就變暗會把「在看」誤判成「沒在用」，所以放寬到 60 秒／10 分鐘。
 *
 * **降到一半時動畫照跑。** 變暗只是省電，不是暫停，看狗不算閒置。 */
#define DIM_AFTER_MS   60000u
#define SLEEP_AFTER_MS 600000u

#define BACKLIGHT_FULL 100
#define BACKLIGHT_DIM  50
#define BACKLIGHT_OFF  0

/* ------------------------------------------------------------------ */
/* 電量                                                                */
/* ------------------------------------------------------------------ */

/* 電量**只是一個角落的圖示**。它不影響任何需求值、不影響動畫、
 * 不進存檔、不觸發任何倒數或警告。低電量在這台機器上不是失敗狀態，
 * 是「該找爸爸插電了」，而那是大人的事。 */
typedef enum {
    PWR_NONE = 0,    /* 電量正常，不顯示任何圖示 */
    PWR_CHARGING,    /* 充電中（優先於低電量，插著電就不必再提醒） */
    PWR_LOW,         /* <= PWR_LOW_PCT */
    PWR_CRITICAL     /* <= PWR_CRITICAL_PCT */
} power_hint_t;

#define PWR_LOW_PCT      20
#define PWR_CRITICAL_PCT 5

/* ------------------------------------------------------------------ */
/* 互動                                                                */
/* ------------------------------------------------------------------ */

typedef enum {
    ACT_NONE = 0,
    ACT_FEED,
    ACT_PET,
    ACT_PLAY,
    ACT_SLEEP,      /* **不在互動選單裡**。休息 = 關燈，見 SLOT_LIGHT */
    ACT_TOILET,
    ACT_DRESS,      /* 僅公主。**不在互動選單裡**，只從衣櫃（UI_DRESS）進來 */
    ACT_BATH,       /* 洗澡。四個角色都有，由 tidy 需求驅動 */
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

    /* 相對站位的水平偏移（像素，±CHAR_X_RANGE）與走動的目標。
       公主閒置時會在房間裡走動、狗進出房間時也用它。
       **不要把位移烘進動畫**——這是繪製時偏移。 */
    int8_t       x_ofs;
    int8_t       x_target;
    uint32_t     walk_accum_ms;

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
    uint8_t  away_phase;        /* 不在場的狗自己的衰減相位 */
    uint8_t  away_div;          /* 每兩次結算才推進 away_phase → 速率剛好一半 */

    uint8_t  hour;              /* 0..23，由 now_unix 換算 */
    bool     night;

    uint8_t  selected;          /* 目前選到的角色，CHAR_COUNT = 無 */
    uint32_t rng;               /* 確定性 PRNG，測試可重現 */

    /* ---- 在場的狗 ----
       CHAR_COUNT = 沒有狗。**開機預設沒有狗**，由小孩自己去門口叫。
       不在場的狗仍然在別的房間過自己的生活（需求照衰減，速率減半）。 */
    uint8_t  present;
    uint8_t  leaving;           /* 正在走回門裡的狗，CHAR_COUNT = 沒有 */
    uint32_t arrive_ms;         /* 到場過場的剩餘時間 */
    uint32_t leave_ms;          /* 離場過場的剩餘時間 */

    /* ---- 按鍵 UI 的狀態 ----
       觸控時代「選取」是直接點出來的，不需要狀態；按鍵時代它**是**狀態。 */
    uint8_t  ui;                /* ui_screen_t */
    uint8_t  cursor;            /* 主畫面：0..game_main_slot_count()-1；
                                   子畫面：0..該畫面的項目數-1 */
    uint8_t  main_cursor;       /* 進子畫面前停在主畫面的哪一格，回來時還原 */
    action_t menu[MENU_MAX];    /* 進選單時抓的動作快照 */
    uint8_t  menu_n;
    uint32_t menu_idle_ms;      /* 選單沒動作多久了 */

    /* UI_BOOT / UI_CELEBRATE / UI_POWEROFF 共用的計時器。
       三個狀態互斥，不會同時計時，所以一個就夠。 */
    uint32_t ui_timer_ms;
    bool     boot_loaded;       /* 外層已回報存檔載入完畢（game_boot_done） */
    bool     greet_happy;       /* 離線超過 24 小時，離開 BOOT 時要播歡迎動畫 */

    /* ---- 電量 ----
       硬體狀態，**不進存檔**，也不影響任何遊戲數值。 */
    uint8_t  battery_pct;       /* 0..100 */
    bool     charging;

    /* 最後一次按鍵的時間。自發行為要避開它之後的 POST_INPUT_QUIET_MS，
       否則小孩會把自發行為誤認為是自己造成的。 */
    uint32_t last_input_ms;

    /* 已經領到手的里程碑（累積，不是單一 tick）。
       game_init 會依存檔裡的 unlocked_outfits 先填好，
       否則每次開機都會把小孩早就拿到的東西重新慶祝一次。 */
    uint16_t unlocked_mask;
    bool     save_dirty;
} game_t;

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */

/* 以載入好的存檔初始化。會套用離線衰減並決定開場動畫，畫面停在 UI_BOOT。
   離線超過 24 小時時開場是 ANIM_HAPPY 而不是 ANIM_SAD_WAIT——
   回來永遠是被歡迎的，見 docs/02。
   那段歡迎動畫在**離開 UI_BOOT 時會從第 0 格重播一次**，
   確保它整段落在主畫面上，而不是被開機圖蓋掉。

   開機預設**沒有狗在場**（present = CHAR_COUNT）。 */
void game_init(game_t *g, const save_blob_t *loaded, uint64_t now_unix);

/* 外層回報「存檔已經載入完畢」。在此之前不管過了多久都不會離開 UI_BOOT；
   在此之後還要湊滿 BOOT_MIN_MS 才會進主畫面。 */
void game_boot_done(game_t *g);

/* 電源鍵長按之後由外層呼叫，進入 UI_POWEROFF。
   角色全部播 ANIM_SLEEP_BREATHE——關機是「大家睡著了」，
   不是壞掉、不是消失（CLAUDE.md 規則 1）。

   **長按的計時在外層做，這一層不做任何長按判定。**
   三顆操作鍵一律沒有長按自動重複。 */
void game_power_off(game_t *g);

/* 關機動畫是否已經走完。外層看到 true 才可以真的斷電。 */
bool game_power_off_done(const game_t *g);

/* 背光亮度 0..100。規則見 DIM_AFTER_MS / SLEEP_AFTER_MS。
   任何按鍵都會讓它立刻回到 BACKLIGHT_FULL。 */
uint8_t game_backlight(const game_t *g);

/* 是否該進 light sleep（背光已經全暗，且超過 SLEEP_AFTER_MS 沒有按鍵）。
   外層負責設定 GPIO 中斷喚醒。 */
bool game_should_sleep(const game_t *g);

/* ---- 電燈（主畫面的 SLOT_LIGHT）----

   存檔裡存的是 **light_off**（0 = 燈開），理由見 save.h 的 SAVE_RSV_LIGHT_OFF。
   關燈只是把 night 判定打開（變暗 + 動畫 0.7 倍 + 角色睡著），
   **不扣任何分、不觸發任何負面狀態**，而且再按一次就全部復原。

   **「休息」不是一個動作，就是關燈。** 所以 ACT_SLEEP 不在互動選單裡：
   要讓角色休息就把燈關掉，這件事在畫面上是看得見的因果，
   而「在選單裡選『睡覺』」對四歲半只是一個圖示。 */
bool game_light_on(const game_t *g);
void game_set_light(game_t *g, bool on);
void game_toggle_light(game_t *g);

/* ---- 電量 ----
   外層量到電量之後回報進來。**不進存檔**，也不影響任何需求值或動畫。 */
void game_set_power(game_t *g, uint8_t percent, bool charging);
power_hint_t game_power_hint(const game_t *g);

/* 推進 dt_ms 毫秒。負責：需求衰減、動畫推進、序列轉換、
   閒置行為選擇、進出房間的過場、夜間模式切換、里程碑檢查。 */
void game_tick(game_t *g, uint32_t dt_ms);

/* 小孩按了某顆實體按鍵。這是**唯一**的輸入入口。

   契約（外層的按鍵驅動必須遵守，理由見 docs/02 第九節）：
     1. **按下的當下呼叫，不是放開時。**
     2. **一次按壓只呼叫一次。** 不可以做長按自動重複——
        3-6 歲最長會按住 5.1 秒，那對她只是「一次」。
     3. 不要做雙擊。小孩會以為程式沒反應。

   各畫面的行為：
     UI_BOOT       忽略（只更新背光計時）
     UI_MAIN       PREV/NEXT 移游標；OK 依游標停的那一格分派（見 main_slot_t）
     UI_MENU       PREV/NEXT 移游標；OK 執行動作後回主畫面
     UI_CALL       PREV/NEXT 移游標；OK 呼叫那一隻狗後回主畫面
     UI_DRESS      PREV/NEXT 移游標；OK 換上那一套服裝後回主畫面
     UI_CELEBRATE  任何鍵提前結束慶祝，**不執行任何選單操作**
     UI_POWEROFF   任何鍵取消關機回主畫面（小孩很容易誤觸電源鍵）

   過場（狗正在進出房間）期間任何鍵都只做一件事：把過場**立刻走完**。
   那 1.2 秒不接受互動，但按下去一定看得到畫面有反應。 */
void game_button(game_t *g, button_t btn);

/* 直接選取某個角色。按鍵層會呼叫它，測試也可以直接用。
   CHAR_COUNT 表示取消選取。 */
void game_select(game_t *g, uint8_t character);

/* 小孩按了某個動作。回傳是否被接受（睡眠中、過場中會拒絕）。

   **這一層不檢查角色在不在場。** 在場與否是主畫面的概念，
   而按鍵層只可能選到畫面上看得見的角色。 */
bool game_do_action(game_t *g, uint8_t character, action_t act);

/* 撫摸模式：按著確認鍵時持續呼叫，放開時呼叫 game_pet_end。 */
void game_pet_move(game_t *g, uint8_t character, uint32_t dt_ms);
void game_pet_end(game_t *g, uint8_t character);

/* ---- 在場的狗 ---- */

/* 叫一隻狗進來。dog 必須是三隻狗之一，CHAR_COUNT 表示「沒有狗」。

   **換狗不需要先送回去。** 直接呼叫新的，原本在場的那一隻自己走回去，
   兩段過場同時進行（ARRIVE_MS / LEAVE_MS）。多一個「送回去」的步驟
   對四歲半就是多一個她學不會、而且帶著「你要先把牠處理掉」語氣的動作。

   離場與到場**都播 ANIM_WALK**。不可以用 ANIM_SAD_WAIT 或任何難過的表現：
   狗走回門裡不是「牠不理你了」（CLAUDE.md 規則 1）。

   已經在場的那一隻再叫一次是 no-op，不會重播過場。 */
void game_call_pet(game_t *g, uint8_t dog);

/* 是否正在過場（有狗在走進來或走回去）。過場期間不接受互動。 */
bool game_pet_transition(const game_t *g);

/* 呼叫選單的內容：**永遠是三隻狗，順序固定**，包含已經在場的那一隻。
   在場的那一隻也留在清單裡，是為了讓三個圖示的位置永遠不動——
   四歲半是靠位置記東西的。回傳填入的數量。 */
uint8_t game_call_list(const game_t *g, uint8_t *out, uint8_t max);

/* 主畫面現在有幾格游標：沒有狗 4 格，有狗 5 格。
   狗那一格永遠是最後一格（SLOT_DOG），前四格的位置不會浮動。 */
uint8_t game_main_slot_count(const game_t *g);

/* ---- 互動選單 ---- */

/* 互動選單的內容。**固定 5 個**：吃飯 / 玩 / 摸摸 / 洗澡 / 上廁所。
   公主沒有 bladder，所以她是 4 個（少了上廁所）。
   順序固定，不隨需求變動——圖示會不會在小孩眼前跳掉，比「排最需要的在前面」重要。

   2026-08-02 之前這裡只顯示需求最低的前三個，因為 docs/02 第三節寫
   「同一畫面最多 3 個可選項」。使用者女兒現在四歲半，已明確同意放寬到 5。
   放寬的是**認知難度**，不是「沒有失敗狀態」——那條一個字都沒有放寬。

   回傳實際填入的數量。 */
uint8_t game_menu_actions(const game_t *g, uint8_t character,
                          action_t *out, uint8_t max);

/* 現在最需要的那一個動作，給渲染層在那一個圖示上加一個柔和的提示用。
   沒有任何需求低於 NEED_LOW 時回傳 ACT_NONE（不要一直有東西在閃）。

   **這個函式的角色在 2026-08-02 變了。** 它以前叫 game_suggest_actions，
   負責決定「顯示哪三個」；選單改成固定 5 個之後那個工作沒有了，
   剩下的只有「哪一個最需要」。分開是因為兩件事的變動理由不同：
   選單的內容要**穩定**（位置不能跳），提示要**跟著需求走**。

   energy 不在回傳範圍內：休息 = 關燈，不是選單裡的動作。 */
action_t game_suggest_action(const game_t *g, uint8_t character);

/* 進度條。游標停在角色上時直接顯示，不必按確認。
   狗 4 條（hunger / energy / mood / bladder），
   公主 4 條（hunger / energy / mood / tidy）——兩者都是 4 條，版面不會跳。
   值域 0..100，回傳條數。

   **刻意只回數值，不回任何「危險」旗標。** 低的時候不可以有警告語意
   （CLAUDE.md 規則 1），提供旗標只會誘惑渲染層把它畫成紅色。 */
uint8_t game_need_bars(const game_t *g, uint8_t character,
                       uint8_t *out, uint8_t max);

/* ---- 換裝（UI_DRESS）---- */

/* 已解鎖的服裝 id 清單（id 就是 unlocked_outfits 的 bit 位置，0 = 預設服裝）。
   **未解鎖的不列出來，也不要畫成鎖頭**——那是「你還沒有」的失敗語意。 */
uint8_t game_outfit_list(const game_t *g, uint8_t *out, uint8_t max);

/* 目前穿的服裝 id。 */
uint8_t game_outfit(const game_t *g);

/* 換上某一套。未解鎖的會被拒絕（回傳 false），存進
   save.chars[CHAR_ICE_PRINCESS].outfit_id。 */
bool game_set_outfit(game_t *g, uint8_t outfit_id);

/* 配件：目前只有 API 掛鉤，配件清單還沒定案。
   存在 character_save_t.reserved[CHAR_RSV_ACCESSORY]，
   0 = 沒有配件，所以舊存檔讀到 0 就是正確的預設。 */
uint8_t game_accessory_mask(const game_t *g, uint8_t character);
void game_set_accessory_mask(game_t *g, uint8_t character, uint8_t mask);

/* ---- 渲染層要的東西 ---- */

/* 角色目前該播的動畫與影格，給渲染層用。 */
anim_id_t game_current_anim(const game_t *g, uint8_t character);

/* 相對站位的水平偏移（像素，±CHAR_X_RANGE）。
   公主閒置時會在房間裡走動，狗進出房間時也會移動。
   **位移不烘進動畫**——渲染層畫的時候把它加到 x 上就好，
   和 screen_dx 同一個道理。 */
int8_t game_char_x_offset(const game_t *g, uint8_t character);

/* 該角色是否為狗（決定有無 bladder、有無 ACT_DRESS、能不能被呼叫） */
bool game_is_dog(uint8_t character);

/* 需求的整體狀態，用於挑選閒置動畫。回傳 0..100 的最低需求值。 */
uint8_t game_lowest_need(const game_t *g, uint8_t character, uint8_t *which);

#ifdef __cplusplus
}
#endif
