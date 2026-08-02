/*
 * game.c — 遊戲邏輯核心實作
 *
 * 沒有失敗狀態。這份檔案裡找不到任何「扣分」「生病」「死亡」「逾時懲罰」，
 * 因為那些東西不存在。需求歸零唯一的後果是 pick_idle_anim() 會挑 ANIM_SAD_WAIT。
 */

#include "game.h"

#include <string.h>

/* ------------------------------------------------------------------ */
/* 參數（見 docs/02_遊戲設計.md）                                       */
/* ------------------------------------------------------------------ */

#define DECAY_INTERVAL_MS   (5u * 60u * 1000u)   /* 每 5 分鐘結算一次 */
#define DECAY_PER_INTERVAL_NUM  1                /* 每小時衰減量 ÷ 12 */

/* 需求索引，對應 needs_t 的欄位順序 */
enum { NEED_HUNGER = 0, NEED_ENERGY, NEED_MOOD, NEED_BLADDER, NEED_TIDY, NEED_COUNT };

/* 每小時衰減量 ×12（避免整數除法丟失精度，實際每 5 分鐘扣 1/12） */
static const uint8_t DECAY_HOURLY[NEED_COUNT] = { 4, 3, 3, 5, 2 };

/* 睡眠時 energy 每小時回補 */
#define ENERGY_RECOVER_HOURLY 12

/* 互動回補量 */
#define FEED_HUNGER   35
#define FEED_MOOD      5
#define PLAY_MOOD     25
#define PLAY_ENERGY   -8      /* 玩會累，但不會累到低於 0 */
#define TOILET_BLADDER 60
#define DRESS_TIDY    40
#define BATH_TIDY     70   /* 洗澡比換衣服有效得多 */
#define BATH_MOOD     10
#define PET_MOOD_PER_2S 3

/* 需求低於此值時角色開始「等待關心」 */
#define NEED_LOW      40

/* 閒置時抽到「在房間裡走幾步」的機率（%）。只有公主會走動。 */
#define ROAM_CHANCE_PCT 25

/* 走動時每移動一像素要多久。48 px 走到底約 5.8 秒。 */
#define WALK_STEP_MS 120u

/* 狗從哪一側進出房間。+1 = 門在站位的右邊。
   實際的門畫在哪由 specs/scene.json 決定，這裡只決定走進來的方向。 */
#define DOOR_SIDE (+1)

static const uint16_t MILESTONES[MILESTONE_COUNT] = { 100, 300, 600, 1000, 1500, 2500 };

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */

/* xorshift32。用確定性 PRNG 是為了讓測試可重現——
   隨機的閒置行為如果不可重現，「為什麼這隻狗一直在轉圈」就查不出來。 */
static uint32_t rnd(game_t *g)
{
    uint32_t x = g->rng ? g->rng : 0x2545F491u;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    g->rng = x;
    return x;
}

static uint32_t rnd_range(game_t *g, uint32_t lo, uint32_t hi)
{
    return (hi <= lo) ? lo : lo + rnd(g) % (hi - lo);
}

static uint8_t add_clamped(uint8_t cur, int delta)
{
    int v = (int)cur + delta;
    if (v < 0)   v = 0;
    if (v > 100) v = 100;
    return (uint8_t)v;
}

static uint8_t *needs_of(game_t *g, uint8_t c)
{
    return (uint8_t *)&g->save.chars[c].needs;
}

bool game_is_dog(uint8_t c)
{
    return c != CHAR_ICE_PRINCESS && c < CHAR_COUNT;
}

/* 這隻狗現在不在房間裡。公主常駐，永遠不會是 away。
   不在場不是「被丟掉」，是**在別的房間過自己的生活**——
   所以牠的需求照樣走，只是走得比較慢（見 apply_decay）。 */
static bool char_is_away(const game_t *g, uint8_t c)
{
    return game_is_dog(c) && c != g->present;
}

/* 該角色會用到哪些需求。公主沒有 bladder，狗沒有 tidy。 */
static bool need_applies(uint8_t c, int need)
{
    if (need == NEED_BLADDER) return game_is_dog(c);
    /* tidy（整潔）四個角色都有——狗也要洗澡。
       2026-08-02 之前這裡是「僅公主」，那時候整潔的唯一來源是換衣服。
       加了 ACT_BATH 之後就沒有理由把狗排除在外。
       存檔不用遷移：save.c 本來就把四個角色的 tidy 都初始化成 80。 */
    (void)need;
    return true;
}

/* ------------------------------------------------------------------ */
/* 動畫序列                                                            */
/* ------------------------------------------------------------------ */

static void seq_clear(anim_seq_t *s) { memset(s, 0, sizeof(*s)); }

static void return_to_main(game_t *g);   /* game_tick 的子畫面逾時要用 */
static uint8_t slot_character(const game_t *g, uint8_t slot);

static void seq_push(anim_seq_t *s, anim_id_t a, uint8_t repeat)
{
    if (s->len >= SEQ_MAX) return;
    s->anim[s->len]   = (uint8_t)a;
    s->repeat[s->len] = repeat;
    s->len++;
}

/* 從目前姿態接到目標姿態需要先插入哪些過渡動畫。
   這是狀態機推導出來的需求：不能從站姿直接跳到 sleep_breathe。 */
static void seq_push_pose_change(anim_seq_t *s, pose_t from, pose_t to)
{
    if (from == to) return;

    /* 姿態是線性的 STAND <-> SIT <-> LIE，不能跳級 */
    while (from < to) {
        seq_push(s, (from == POSE_STAND) ? ANIM_SIT_DOWN : ANIM_LIE_DOWN, 1);
        from++;
    }
    while (from > to) {
        seq_push(s, (from == POSE_LIE) ? ANIM_WAKE_UP : ANIM_STAND_UP, 1);
        from--;
    }
}

static void start_seq(game_t *g, uint8_t c, const anim_seq_t *s, pose_t end_pose)
{
    char_runtime_t *r = &g->rt[c];
    r->seq       = *s;
    r->seq_step  = 0;
    r->seq_loop  = 0;
    r->anim      = s->len ? s->anim[0] : ANIM_IDLE_BREATHE;
    r->frame     = 0;
    r->frame_accum_ms = 0;
    r->state     = s->len ? CSTATE_BUSY : CSTATE_IDLE;
    r->pose      = end_pose;
}

/* ------------------------------------------------------------------ */
/* 閒置行為                                                            */
/* ------------------------------------------------------------------ */

uint8_t game_lowest_need(const game_t *g, uint8_t c, uint8_t *which)
{
    const uint8_t *n = (const uint8_t *)&g->save.chars[c].needs;
    uint8_t best = 100, idx = 0;
    for (int i = 0; i < NEED_COUNT; i++) {
        if (!need_applies(c, i)) continue;
        if (n[i] < best) { best = n[i]; idx = (uint8_t)i; }
    }
    if (which) *which = idx;
    return best;
}

/* 挑一個閒置動畫。需求低時偏向 sad_wait，但**不是懲罰**——
   角色只是安靜地等待被關心，沒有任何數值或進度上的後果。 */
static anim_id_t pick_idle_anim(game_t *g, uint8_t c)
{
    uint8_t low = game_lowest_need(g, c, NULL);

    if (low < NEED_LOW) {
        /* 越低越常播等待動畫，但上限壓在六成。
           需求全零時若九成時間都在垂頭喪氣，對這個年齡的小孩太沉重——
           角色應該是「有點想你」，不是「快撐不住了」。 */
        uint32_t p = 40u + (uint32_t)(NEED_LOW - low) / 2u;   /* 40..60 */
        if (rnd(g) % 100u < p) {
            return ANIM_SAD_WAIT;
        }
    }

    static const anim_id_t POOL[] = {
        ANIM_IDLE_BREATHE, ANIM_IDLE_BLINK, ANIM_IDLE_LOOK,
        ANIM_IDLE_EAR_TWITCH, ANIM_TAIL_WAG,
    };
    const int N = (int)(sizeof(POOL) / sizeof(POOL[0]));
    char_runtime_t *r = &g->rt[c];

    /* 避開最近播過的，否則同一個動作連續出現會讓角色看起來卡住。
       最多試 N 次就放棄，免得候選池比 IDLE_NO_REPEAT 小時無限迴圈。 */
    for (int attempt = 0; attempt < N; attempt++) {
        anim_id_t pick = POOL[rnd(g) % N];
        bool recent = false;
        for (uint8_t i = 0; i < r->idle_recent_n; i++) {
            if (r->idle_recent[i] == (uint8_t)pick) { recent = true; break; }
        }
        if (!recent) return pick;
    }
    return POOL[rnd(g) % N];
}

/* 記下剛播的閒置動畫，維持一個長度 IDLE_NO_REPEAT 的環形歷史 */
static void remember_idle(char_runtime_t *r, anim_id_t a)
{
    if (r->idle_recent_n < IDLE_NO_REPEAT) {
        r->idle_recent[r->idle_recent_n++] = (uint8_t)a;
        return;
    }
    for (int i = 1; i < IDLE_NO_REPEAT; i++) r->idle_recent[i - 1] = r->idle_recent[i];
    r->idle_recent[IDLE_NO_REPEAT - 1] = (uint8_t)a;
}

/* ---- 閒置走動 ----------------------------------------------------- */

/* 誰會在房間裡走來走去。目前只有公主：她是常駐角色，
   一直站在同一個點上會讓房間看起來像一張靜態圖。
   狗的移動另外走進出房間的過場，不走這裡。 */
static bool char_can_roam(const game_t *g, uint8_t c)
{
    return (c == CHAR_ICE_PRINCESS) && !g->night && !game_pet_transition(g);
}

/* 抽一個新的落腳點並開始走。位移是繪製時偏移，動畫還是共用的那一段 walk。 */
static void start_roam(game_t *g, uint8_t c)
{
    char_runtime_t *r = &g->rt[c];
    int t = (int)rnd_range(g, 0, 2u * CHAR_X_RANGE + 1u) - CHAR_X_RANGE;
    /* 不要原地踏步：抽到自己現在的位置就走到對稱的另一邊
       （站在正中央時 -0 還是 0，所以那一格要另外指一個目標）。 */
    if (t == r->x_ofs) t = (t == 0) ? CHAR_X_RANGE : -t;

    r->x_target      = (int8_t)t;
    r->walk_accum_ms = 0;
    r->anim          = ANIM_WALK;
    r->frame         = 0;
    r->frame_accum_ms = 0;
}

/* 把位移一格一格推向目標，到了就自己回待機。
   走 48 px 最長約 5.8 秒，遠短於 WCAG 2.2.2 的 5 秒門檻所針對的「無法中斷」，
   而且任何互動都會把它蓋掉。 */
static void tick_roam(game_t *g, uint8_t c, uint32_t dt_ms)
{
    char_runtime_t *r = &g->rt[c];
    if (r->state != CSTATE_IDLE || r->anim != ANIM_WALK) return;

    r->walk_accum_ms += dt_ms;
    while (r->walk_accum_ms >= WALK_STEP_MS && r->x_ofs != r->x_target) {
        r->walk_accum_ms -= WALK_STEP_MS;
        r->x_ofs = (int8_t)(r->x_ofs + ((r->x_ofs < r->x_target) ? 1 : -1));
    }
    if (r->x_ofs == r->x_target) {
        r->walk_accum_ms  = 0;
        r->anim           = ANIM_IDLE_BREATHE;
        r->frame          = 0;
        r->frame_accum_ms = 0;
        r->idle_next_ms   = g->now_ms + rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
    }
}

/* ------------------------------------------------------------------ */
/* 初始化                                                              */
/* ------------------------------------------------------------------ */

bool game_light_on(const game_t *g)
{
    return g ? (g->save.reserved[SAVE_RSV_LIGHT_OFF] == 0) : true;
}

/* 夜間 = 時鐘夜間 **或** 燈關著。
   兩者的畫面表現一樣（變暗 + 動畫 0.7 倍 + 角色睡著），
   差別只在誰決定的：一個是家長設的作息，一個是小孩自己按的開關。
   兩者都**沒有任何數值後果**，燈再按一次全部復原。 */
static void recompute_clock(game_t *g)
{
    g->hour  = (uint8_t)((g->now_unix / 3600ull) % 24ull);
    uint8_t s = g->save.night_start_hour, e = g->save.night_end_hour;
    bool clock_night = (s <= e) ? (g->hour >= s && g->hour < e)
                                : (g->hour >= s || g->hour < e);
    g->night = clock_night || !game_light_on(g);
}

/* 開機時把「已經領到手」的里程碑填進 unlocked_mask。
   不做這件事的話 check_milestones 每次開機都會把舊進度重新算成新解鎖，
   小孩每次開機都要看一次同樣的慶祝畫面。
   對照關係與 check_milestones 一致：第 m 個里程碑對應服裝 bit m+1。 */
static void seed_unlocked_mask(game_t *g)
{
    uint8_t have = g->save.chars[CHAR_ICE_PRINCESS].unlocked_outfits;
    for (uint8_t m = 0; m < MILESTONE_COUNT; m++) {
        uint8_t bit_idx = (uint8_t)((m + 1 < 8) ? m + 1 : 7);
        if (have & (uint8_t)(1u << bit_idx)) {
            g->unlocked_mask |= (uint16_t)(1u << m);
        }
    }
}

/* outfit_id 指到一套沒解鎖的服裝時退回預設。
   會發生在降級燒錄或存檔被手動改過的時候。做法刻意是**安靜地退回**：
   小孩只會看到公主穿著預設的那一套，不會看到任何錯誤。 */
static void sanitize_outfit(game_t *g)
{
    character_save_t *p = &g->save.chars[CHAR_ICE_PRINCESS];
    p->unlocked_outfits = (uint8_t)(p->unlocked_outfits | 0x01u);  /* 預設服裝永遠在 */
    if (p->outfit_id >= 8 || !(p->unlocked_outfits & (uint8_t)(1u << p->outfit_id))) {
        p->outfit_id = 0;
    }
}

void game_init(game_t *g, const save_blob_t *loaded, uint64_t now_unix)
{
    memset(g, 0, sizeof(*g));
    g->save     = *loaded;
    g->now_unix = now_unix;
    g->rng      = (uint32_t)(now_unix ^ 0x9E3779B9u) | 1u;

    /* **開機預設沒有狗在場。** memset 之後 present 會是 0（＝公主），
       所以這兩行一定要寫，不可以靠歸零。
       沒有狗不是缺陷狀態：房間裡有公主，小孩想要狗就自己去門口叫。 */
    g->present = CHAR_COUNT;
    g->leaving = CHAR_COUNT;

    /* 游標開機停在公主身上——畫面上唯一常駐的角色，
       而且停在角色上就會顯示進度條，一開機就有東西可以看。 */
    g->cursor      = SLOT_PRINCESS;
    g->main_cursor = SLOT_PRINCESS;
    g->selected    = CHAR_ICE_PRINCESS;

    sanitize_outfit(g);

    /* 開機先停在開機畫面，等外層回報存檔載入完 + 湊滿 BOOT_MIN_MS。 */
    g->ui          = UI_BOOT;
    g->ui_timer_ms = 0;
    g->boot_loaded = false;

    /* 電量還沒被回報之前當作滿的。預設 0 的話開機瞬間會顯示 PWR_CRITICAL，
       那是一個不存在的壞消息。 */
    g->battery_pct = 100;
    g->charging    = false;

    uint32_t away = save_apply_offline_decay(&g->save, now_unix);
    recompute_clock(g);

    /* 離開很久之後回來，開場是開心不是難過（docs/02）。
       這裡先把動畫擺好讓外層在 BOOT 期間就拿得到正確的角色圖，
       離開 BOOT 時再從第 0 格重播一次，整段才會落在主畫面上。 */
    g->greet_happy = (away >= 24u * 3600u);

    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *r = &g->rt[c];
        r->pose  = POSE_STAND;
        r->state = CSTATE_IDLE;

        if (g->night) {
            r->pose  = POSE_LIE;
            r->state = CSTATE_SLEEPING;
            r->anim  = ANIM_SLEEP_BREATHE;
        } else if (g->greet_happy) {
            anim_seq_t s; seq_clear(&s);
            seq_push(&s, ANIM_HAPPY, 1);
            seq_push(&s, ANIM_TAIL_WAG, 2);
            start_seq(g, c, &s, POSE_STAND);
        } else {
            r->anim = ANIM_IDLE_BREATHE;
        }
        r->idle_next_ms = rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
    }

    seed_unlocked_mask(g);
    g->save_dirty = (away > 0);
}

void game_boot_done(game_t *g)
{
    if (g) g->boot_loaded = true;
}

/* ------------------------------------------------------------------ */
/* 需求衰減                                                            */
/* ------------------------------------------------------------------ */

/* 不在場的狗掉到這裡就不再往下掉。用的是離線衰減的同一個下限——
   「在別的房間」和「機器關著」對小孩來說是同一件事：她不在的時候，
   角色不可以自己走到谷底。 */
static uint8_t decay_floored(uint8_t cur)
{
    return (cur > OFFLINE_DECAY_FLOOR) ? (uint8_t)(cur - 1) : cur;
}

/* 每 5 分鐘結算一次。以 12 個 interval 為一小時，用累積餘數的方式
   避免整數除法把小數量的衰減全部抹平。 */
static void apply_decay(game_t *g)
{
    g->decay_phase = (uint8_t)((g->decay_phase + 1) % 12);

    /* 不在場的狗用自己的相位，而且**每兩次結算才推進一次**，
       所以每一項需求的速率都剛好是在場的一半。
       不用 hourly/2 是因為那個對奇數不成立：mood 的 3 會變成 1（三分之一）
       或 2（三分之二），兩個都不是一半。相位減速對每一項都精確。 */
    bool away_due = false;
    g->away_div = (uint8_t)(g->away_div ^ 1u);
    if (g->away_div == 0) {
        g->away_phase = (uint8_t)((g->away_phase + 1) % 12);
        away_due = true;
    }

    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        bool away = char_is_away(g, c);
        if (away && !away_due) continue;

        uint8_t tick_count = away ? g->away_phase : g->decay_phase;
        uint8_t *n = needs_of(g, c);
        bool sleeping = (g->rt[c].state == CSTATE_SLEEPING);

        for (int i = 0; i < NEED_COUNT; i++) {
            if (!need_applies(c, i)) continue;

            if (i == NEED_ENERGY && sleeping) {
                if (tick_count == 0) {
                    n[i] = add_clamped(n[i], ENERGY_RECOVER_HOURLY);
                }
                continue;
            }
            /* 睡覺時其他需求衰減減半 */
            uint8_t hourly = DECAY_HOURLY[i];
            if (sleeping) hourly = (uint8_t)((hourly + 1) / 2);

            /* 把每小時的量攤到 12 個 interval：前 hourly 個 interval 各扣 1 */
            if (tick_count < hourly) {
                n[i] = away ? decay_floored(n[i]) : add_clamped(n[i], -1);
            }
        }
    }
    g->save_dirty = true;
}

/* ------------------------------------------------------------------ */
/* 里程碑                                                              */
/* ------------------------------------------------------------------ */

/* 回傳「這次呼叫**新**解鎖了哪些」，供慶祝畫面判斷要不要開。
   已經在 unlocked_mask 裡的不會再回報一次。 */
static uint16_t check_milestones(game_t *g)
{
    uint32_t total = 0;
    uint16_t newly = 0;
    for (uint8_t c = 0; c < CHAR_COUNT; c++) total += g->save.chars[c].affection;

    for (uint8_t m = 0; m < MILESTONE_COUNT; m++) {
        uint16_t bit = (uint16_t)(1u << m);
        if (g->unlocked_mask & bit) continue;
        if (total < MILESTONES[m]) continue;

        /* bit 0 是一開始就有的預設服裝，所以第 m 個里程碑解鎖 bit m+1。
           寫成 1<<m 的話第一個里程碑等於什麼都沒給。 */
        uint8_t bit_idx = (uint8_t)((m + 1 < 8) ? m + 1 : 7);
        uint8_t already = g->save.chars[CHAR_ICE_PRINCESS].unlocked_outfits;
        uint8_t want    = (uint8_t)(already | (1u << bit_idx));
        if (want != already) {
            g->save.chars[CHAR_ICE_PRINCESS].unlocked_outfits = want;
            g->save_dirty = true;
        }
        g->unlocked_mask |= bit;
        newly |= bit;
    }
    return newly;
}

/* ------------------------------------------------------------------ */
/* tick                                                                */
/* ------------------------------------------------------------------ */

/* 每個動畫的影格數與 fps。與 specs/animations 底下的 anim.json 對應，
   之後改由資產包載入，現在先寫死讓邏輯可以獨立測試。 */
typedef struct { uint8_t frames; uint8_t fps; bool loop; } anim_meta_t;

static const anim_meta_t ANIM_META[ANIM_COUNT] = {
    [ANIM_IDLE_BREATHE]   = {  8,  6, true  },
    [ANIM_IDLE_BLINK]     = {  4, 12, false },
    [ANIM_IDLE_LOOK]      = { 12,  6, false },
    [ANIM_IDLE_EAR_TWITCH]= {  6,  8, false },
    [ANIM_TAIL_WAG]       = {  6,  8, true  },
    [ANIM_SIT_DOWN]       = {  8,  8, false },
    [ANIM_STAND_UP]       = {  8,  8, false },
    [ANIM_LIE_DOWN]       = { 10,  8, false },
    [ANIM_WALK]           = {  8, 10, true  },
    [ANIM_TURN]           = {  6, 10, false },
    [ANIM_EAT]            = {  6,  8, true  },
    [ANIM_EAT_HAPPY]      = { 10,  6, false },
    [ANIM_SLEEP_BREATHE]  = { 16,  4, true  },
    [ANIM_YAWN]           = { 12,  6, false },
    [ANIM_WAKE_UP]        = { 10,  6, false },
    [ANIM_PLAY_BOW]       = {  8,  8, false },
    [ANIM_CHASE_BALL]     = {  8, 12, true  },
    [ANIM_HAPPY]          = { 12,  8, false },
    [ANIM_SAD_WAIT]       = { 12,  4, true  },
    [ANIM_PET_REACT]      = {  8,  8, true  },
    [ANIM_TOILET]         = { 14,  6, false },
};

/* 推進一個角色的動畫。回傳該段動畫是否播完一輪。 */
static bool advance_anim(game_t *g, uint8_t c, uint32_t dt_ms)
{
    char_runtime_t *r = &g->rt[c];
    const anim_meta_t *m = &ANIM_META[r->anim];
    uint8_t fps = m->fps ? m->fps : 6;
    uint32_t frame_ms = 1000u / fps;

    /* 夜間模式動畫放慢到 0.7 倍 */
    if (g->night) frame_ms = frame_ms * 10u / 7u;

    r->frame_accum_ms += dt_ms;
    bool wrapped = false;
    while (r->frame_accum_ms >= frame_ms) {
        r->frame_accum_ms -= frame_ms;
        r->frame++;
        if (r->frame >= m->frames) {
            r->frame = 0;
            wrapped = true;
        }
    }
    return wrapped;
}

static void advance_seq(game_t *g, uint8_t c)
{
    char_runtime_t *r = &g->rt[c];
    uint8_t rep = r->seq.repeat[r->seq_step];

    /* repeat = 0 代表無限循環，等外部呼叫（例如放開手指）才結束 */
    if (rep == 0 && r->hold) return;

    r->seq_loop++;
    if (rep != 0 && r->seq_loop < rep) return;

    r->seq_loop = 0;
    r->seq_step++;

    if (r->seq_step >= r->seq.len) {
        r->state = CSTATE_IDLE;
        r->hold  = false;
        r->anim  = ANIM_IDLE_BREATHE;
        r->frame = 0;
        r->idle_next_ms = g->now_ms + rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
        return;
    }
    r->anim  = r->seq.anim[r->seq_step];
    r->frame = 0;
    r->frame_accum_ms = 0;
}

/* ------------------------------------------------------------------ */
/* 畫面狀態                                                            */
/* ------------------------------------------------------------------ */

/* 全部躺下睡著。夜間切換與關機共用。 */
static void sleep_all(game_t *g, bool with_yawn)
{
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *r = &g->rt[c];
        anim_seq_t s; seq_clear(&s);
        if (with_yawn) {
            seq_push(&s, ANIM_YAWN, 1);
            seq_push_pose_change(&s, r->pose, POSE_LIE);
        }
        seq_push(&s, ANIM_SLEEP_BREATHE, 0);
        start_seq(g, c, &s, POSE_LIE);
        r->hold  = true;
        r->state = CSTATE_SLEEPING;
    }
}

/* 全部醒來。夜間結束與「取消關機」共用——
   取消關機一定要看得出來大家又醒了，不然小孩不知道自己救回來了。 */
static void wake_all(game_t *g)
{
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *r = &g->rt[c];
        anim_seq_t s; seq_clear(&s);
        seq_push(&s, ANIM_WAKE_UP, 1);
        seq_push_pose_change(&s, POSE_LIE, POSE_STAND);
        seq_push(&s, ANIM_HAPPY, 1);
        start_seq(g, c, &s, POSE_STAND);
        r->hold = false;
    }
}

static void apply_night_change(game_t *g)
{
    if (g->night) sleep_all(g, true);
    else          wake_all(g);
}

/* ------------------------------------------------------------------ */
/* 進出房間                                                            */
/* ------------------------------------------------------------------ */

/* 把一個角色收回它平常的狀態：夜間就繼續睡，白天就回待機。
   過場結束、被叫進來的狗要接到什麼狀態，全部走這裡。 */
static void settle_character(game_t *g, uint8_t c)
{
    char_runtime_t *r = &g->rt[c];

    if (g->night) {
        anim_seq_t s; seq_clear(&s);
        seq_push(&s, ANIM_SLEEP_BREATHE, 0);
        start_seq(g, c, &s, POSE_LIE);
        r->hold  = true;
        r->state = CSTATE_SLEEPING;
        return;
    }

    seq_clear(&r->seq);
    r->seq_step = 0;
    r->seq_loop = 0;
    r->hold     = false;
    r->state    = CSTATE_IDLE;
    r->anim     = ANIM_IDLE_BREATHE;
    r->frame    = 0;
    r->frame_accum_ms = 0;
    r->idle_next_ms   = g->now_ms + rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
}

/* 走路的過場。**刻意不插姿態過渡動畫**，理由和關機同一條：
   站→坐→趴要 2.25 秒，比 1.2 秒的過場還長，小孩會看到狗「正要站起來」
   的時候過場就結束了。直接播 walk，走路這件事本身就看得懂。 */
static void start_walk(game_t *g, uint8_t c)
{
    anim_seq_t s; seq_clear(&s);
    seq_push(&s, ANIM_WALK, 0);     /* 0 = 無限循環，由過場計時收尾 */
    start_seq(g, c, &s, POSE_STAND);
    g->rt[c].hold = true;
}

bool game_pet_transition(const game_t *g)
{
    return g && (g->arrive_ms > 0u || g->leave_ms > 0u);
}

static void end_arrive(game_t *g)
{
    g->arrive_ms = 0;
    if (g->present < CHAR_COUNT) {
        g->rt[g->present].x_ofs = 0;
        settle_character(g, g->present);
    }
}

static void end_leave(game_t *g)
{
    uint8_t d = g->leaving;
    g->leave_ms = 0;
    g->leaving  = CHAR_COUNT;
    if (d < CHAR_COUNT) {
        /* 走出畫面之後把位移歸零，下次被叫進來才會從門口重走一次。 */
        g->rt[d].x_ofs    = 0;
        g->rt[d].x_target = 0;
        settle_character(g, d);
    }
}

/* 過場立刻走完。按鍵中斷與 game_call_pet 的重入都走這裡，
   所以「中斷過場」永遠是把它演完，不會留下半途的狀態。 */
static void finish_transition(game_t *g)
{
    if (g->leave_ms)  end_leave(g);
    if (g->arrive_ms) end_arrive(g);
}

static void tick_transition(game_t *g, uint32_t dt_ms)
{
    if (g->leave_ms) {
        g->leave_ms = (dt_ms >= g->leave_ms) ? 0u : g->leave_ms - dt_ms;
        if (g->leave_ms == 0u) {
            end_leave(g);
        } else if (g->leaving < CHAR_COUNT) {
            /* 0 → 門口。線性推，計時歸零時剛好到位。 */
            uint32_t gone = LEAVE_MS - g->leave_ms;
            g->rt[g->leaving].x_ofs =
                (int8_t)(DOOR_SIDE * (int)((CHAR_X_RANGE * gone) / LEAVE_MS));
        }
    }

    if (g->arrive_ms) {
        g->arrive_ms = (dt_ms >= g->arrive_ms) ? 0u : g->arrive_ms - dt_ms;
        if (g->arrive_ms == 0u) {
            end_arrive(g);
        } else if (g->present < CHAR_COUNT) {
            /* 門口 → 0 */
            g->rt[g->present].x_ofs =
                (int8_t)(DOOR_SIDE * (int)((CHAR_X_RANGE * g->arrive_ms) / ARRIVE_MS));
        }
    }
}

void game_call_pet(game_t *g, uint8_t dog)
{
    if (!g) return;
    if (dog != CHAR_COUNT && !game_is_dog(dog)) return;   /* 公主不是被叫的對象 */
    if (dog == g->present) return;                        /* 已經在場，不重播過場 */

    /* 前一段過場還沒走完就先把它演完，不要疊兩層。 */
    finish_transition(g);

    uint8_t old = g->present;
    g->present = dog;

    /* **舊的那一隻自己走回去，不需要小孩先送。** 走的是 ANIM_WALK，
       不是任何難過的動畫——換一隻狗不可以有負面語意（CLAUDE.md 規則 1）。 */
    if (game_is_dog(old)) {
        g->leaving  = old;
        g->leave_ms = LEAVE_MS;
        g->rt[old].x_ofs = 0;
        start_walk(g, old);
    }

    if (game_is_dog(dog)) {
        g->arrive_ms = ARRIVE_MS;
        g->rt[dog].x_ofs = (int8_t)(DOOR_SIDE * CHAR_X_RANGE);
        start_walk(g, dog);
    }

    /* 狗那一格出現或消失時，主畫面的游標與選取要跟著對齊。 */
    if (g->ui == UI_MAIN) {
        uint8_t n = game_main_slot_count(g);
        if (g->cursor >= n) g->cursor = (uint8_t)(n - 1);
        g->selected = slot_character(g, g->cursor);
    }
}

uint8_t game_call_list(const game_t *g, uint8_t *out, uint8_t max)
{
    if (!g || !out) return 0;
    uint8_t k = 0;
    for (uint8_t c = 0; c < CHAR_COUNT && k < max; c++) {
        if (game_is_dog(c)) out[k++] = c;
    }
    return k;
}

uint8_t game_main_slot_count(const game_t *g)
{
    if (!g) return MAIN_SLOT_NO_DOG;
    return (g->present < CHAR_COUNT) ? SLOT_MAX : MAIN_SLOT_NO_DOG;
}

/* 離開開機畫面。歡迎動畫在這裡才從第 0 格起跑，
   整段就完整落在主畫面上而不是被開機圖蓋掉。 */
static void leave_boot(game_t *g)
{
    g->ui           = UI_MAIN;
    g->ui_timer_ms  = 0;
    g->menu_idle_ms = 0;

    if (g->greet_happy) {
        for (uint8_t c = 0; c < CHAR_COUNT; c++) {
            if (g->rt[c].state == CSTATE_SLEEPING) continue;   /* 夜間就別吵他們 */
            anim_seq_t s; seq_clear(&s);
            seq_push(&s, ANIM_HAPPY, 1);
            seq_push(&s, ANIM_TAIL_WAG, 2);
            start_seq(g, c, &s, POSE_STAND);
        }
        g->greet_happy = false;
    }
}

/* 子畫面被別的畫面蓋掉時，游標要收回主畫面的座標系
   （子畫面的 cursor 是 0..項目數-1，主畫面的是 0..slot_count-1），
   否則回到主畫面之後游標會停在一個不相干的位置上。
   還原用的是進來之前停的那一格，不是重新推算——
   四歲半找不回自己剛剛停的位置就會重新從頭數一次。 */
static void drop_subscreen(game_t *g)
{
    if (g->ui != UI_MENU && g->ui != UI_CALL && g->ui != UI_DRESS) return;
    uint8_t n = game_main_slot_count(g);
    g->cursor = (g->main_cursor < n) ? g->main_cursor : (uint8_t)(n - 1);
}

/* 里程碑慶祝。ANIM_HAPPY 兩輪剛好 CELEBRATE_MS。 */
static void enter_celebrate(game_t *g)
{
    drop_subscreen(g);
    g->ui           = UI_CELEBRATE;
    g->ui_timer_ms  = 0;
    g->menu_idle_ms = 0;

    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *r = &g->rt[c];
        if (r->state == CSTATE_SLEEPING) continue;
        anim_seq_t s; seq_clear(&s);
        seq_push(&s, ANIM_HAPPY, 2);
        start_seq(g, c, &s, r->pose);
        r->hold = false;
    }
}

static void leave_celebrate(game_t *g)
{
    g->ui           = UI_MAIN;
    g->ui_timer_ms  = 0;
    g->menu_idle_ms = 0;
}

void game_power_off(game_t *g)
{
    if (!g || g->ui == UI_POWEROFF) return;

    drop_subscreen(g);
    g->ui           = UI_POWEROFF;
    g->ui_timer_ms  = 0;
    g->menu_idle_ms = 0;
    g->save_dirty   = true;      /* 外層趁斷電前把進度寫下去 */

    /* **不插過渡動畫。** 站→坐→趴要 2.25 秒，比 POWEROFF_MS 還長，
       小孩會看到大家「正要躺下」的時候螢幕就黑掉，那看起來像壞掉。
       直接進 sleep_breathe：關機的畫面是「大家睡著了」。 */
    sleep_all(g, false);
}

bool game_power_off_done(const game_t *g)
{
    return g && g->ui == UI_POWEROFF && g->ui_timer_ms >= POWEROFF_MS;
}

/* 取消關機。小孩很容易誤觸電源鍵，一定要救得回來。 */
static void cancel_power_off(game_t *g)
{
    g->ui           = UI_MAIN;
    g->ui_timer_ms  = 0;
    g->menu_idle_ms = 0;
    /* 本來就在夜間（或燈是關的）就讓大家繼續睡，不然把大家叫起來。 */
    if (!g->night) wake_all(g);
}

/* ------------------------------------------------------------------ */

void game_tick(game_t *g, uint32_t dt_ms)
{
    g->now_ms += dt_ms;

    /* 累積後才進位。直接寫 now_unix += dt_ms / 1000 的話，
       以 100ms 為步長呼叫時整數除法恆為 0，時鐘會完全停住，
       夜間模式永遠不會觸發。 */
    g->sec_accum_ms += dt_ms;
    if (g->sec_accum_ms >= 1000u) {
        g->now_unix    += g->sec_accum_ms / 1000u;
        g->sec_accum_ms = g->sec_accum_ms % 1000u;
    }

    bool was_night = g->night;
    recompute_clock(g);

    /* 需求衰減 */
    if (g->now_ms - g->last_decay_ms >= DECAY_INTERVAL_MS) {
        g->last_decay_ms = g->now_ms;
        apply_decay(g);
    }

    /* ---- 畫面計時 ---- */
    switch (g->ui) {
    case UI_BOOT:
        /* 存檔還沒載完就一直等；載完了也要湊滿最短停留，
           免得開機圖一閃而過。 */
        g->ui_timer_ms += dt_ms;
        if (g->boot_loaded && g->ui_timer_ms >= BOOT_MIN_MS) leave_boot(g);
        break;

    case UI_POWEROFF:
        /* 只計時，不自己離開——外層看到 game_power_off_done() 才斷電。 */
        g->ui_timer_ms += dt_ms;
        break;

    case UI_CELEBRATE:
        g->ui_timer_ms += dt_ms;
        if (g->ui_timer_ms >= CELEBRATE_MS) leave_celebrate(g);
        break;

    case UI_MENU:
    case UI_CALL:
    case UI_DRESS:
        /* 三個子畫面共用同一條逾時。沒有取消鍵是刻意的：
           四歲半不該被要求學會「返回」，讓它自己回去比教她一個抽象概念可靠。 */
        g->menu_idle_ms += dt_ms;
        if (g->menu_idle_ms >= MENU_TIMEOUT_MS) return_to_main(g);
        break;

    default:
        break;
    }

    /* 狗進出房間的過場。開機／關機畫面上不推進——那兩個畫面不是房間。 */
    if (g->ui != UI_BOOT && g->ui != UI_POWEROFF) {
        tick_transition(g, dt_ms);
    }

    /* 夜間切換（含小孩自己關燈）。關機中不理會——
       關機畫面已經是全部睡著了，再切一次只會把序列打斷。 */
    if (g->night != was_night && g->ui != UI_POWEROFF) {
        apply_night_change(g);
    }

    /* 動畫推進。開機畫面上角色是靜止的，那時候螢幕根本不是房間。 */
    if (g->ui != UI_BOOT) {
        for (uint8_t c = 0; c < CHAR_COUNT; c++) {
            char_runtime_t *r = &g->rt[c];
            bool wrapped = advance_anim(g, c, dt_ms);

            if (r->state == CSTATE_BUSY || r->state == CSTATE_SLEEPING) {
                if (wrapped) advance_seq(g, c);
            } else if (r->state == CSTATE_IDLE) {
                /* 走到一半的話先把腳步走完，不要在半路換動作。 */
                tick_roam(g, c, dt_ms);

                /* 按鍵後的安靜期內不觸發自發行為。這個年齡會把同時發生的事
                   關聯成因果——剛摸完狗、狗就自己難過，她會以為是自己弄的。
                   慶祝期間也不插入自發行為，不然慶祝到一半會冒出 sad_wait。 */
                bool quiet = (g->now_ms - g->last_input_ms) < POST_INPUT_QUIET_MS;
                bool allow = !quiet && g->ui != UI_CELEBRATE;
                bool walking = (r->anim == ANIM_WALK);
                if (g->now_ms >= r->idle_next_ms && allow && !walking) {
                    if (char_can_roam(g, c) && rnd(g) % 100u < ROAM_CHANCE_PCT) {
                        /* 走動不進 idle_recent：它不在閒置池裡，
                           記進去只會把池子的歷史擠掉一格。 */
                        start_roam(g, c);
                    } else {
                        anim_id_t pick = pick_idle_anim(g, c);
                        remember_idle(r, pick);
                        r->anim  = (uint8_t)pick;
                        r->frame = 0;
                        r->frame_accum_ms = 0;
                    }
                    r->idle_next_ms = g->now_ms + rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
                }
            }
        }
    }

    /* 里程碑。開機／關機畫面上不算——那兩個狀態不接受互動，
       affection 不會變，而且慶祝畫面不該蓋掉開機或關機。 */
    if (g->ui == UI_MAIN || g->ui == UI_MENU || g->ui == UI_CELEBRATE) {
        uint16_t newly = check_milestones(g);
        if (newly && g->ui != UI_CELEBRATE) enter_celebrate(g);
    }
}

/* ------------------------------------------------------------------ */
/* 待機（背光）                                                         */
/* ------------------------------------------------------------------ */

uint8_t game_backlight(const game_t *g)
{
    if (!g) return BACKLIGHT_OFF;

    /* 開機圖與關機圖一定要看得見。關機那兩秒尤其重要——
       小孩要看到「大家睡著了」，而不是螢幕突然黑掉。 */
    if (g->ui == UI_BOOT || g->ui == UI_POWEROFF) return BACKLIGHT_FULL;

    uint32_t idle = g->now_ms - g->last_input_ms;
    if (idle < DIM_AFTER_MS)   return BACKLIGHT_FULL;
    if (idle < SLEEP_AFTER_MS) return BACKLIGHT_DIM;   /* 動畫照跑，看狗不算閒置 */
    return BACKLIGHT_OFF;
}

bool game_should_sleep(const game_t *g)
{
    if (!g) return false;
    if (g->ui == UI_BOOT || g->ui == UI_POWEROFF) return false;
    return (g->now_ms - g->last_input_ms) >= SLEEP_AFTER_MS;
}

/* ------------------------------------------------------------------ */
/* 電燈                                                                */
/* ------------------------------------------------------------------ */

void game_set_light(game_t *g, bool on)
{
    if (!g) return;

    uint8_t want = on ? 0u : 1u;
    if (g->save.reserved[SAVE_RSV_LIGHT_OFF] == want) return;

    g->save.reserved[SAVE_RSV_LIGHT_OFF] = want;
    g->save_dirty = true;

    /* 燈是 night 判定的一部分，改了要立刻反映，不能等下一個 tick——
       否則 game_tick 會拿已經更新過的 g->night 當 was_night，
       角色永遠不會切換睡／醒。 */
    bool was_night = g->night;
    recompute_clock(g);
    if (g->night != was_night && g->ui != UI_POWEROFF) apply_night_change(g);

    /* 關燈**不扣任何分、不改任何需求值**。這裡刻意什麼都不做。 */
}

void game_toggle_light(game_t *g)
{
    if (g) game_set_light(g, !game_light_on(g));
}

/* ------------------------------------------------------------------ */
/* 電量                                                                */
/* ------------------------------------------------------------------ */

void game_set_power(game_t *g, uint8_t percent, bool charging)
{
    if (!g) return;
    g->battery_pct = (percent > 100u) ? 100u : percent;
    g->charging    = charging;
    /* 刻意不碰 save、不碰 needs、不碰 rt。
       電量是硬體狀態，遊戲狀態完全不受影響（CLAUDE.md 規則 1）。 */
}

power_hint_t game_power_hint(const game_t *g)
{
    if (!g) return PWR_NONE;
    if (g->charging) return PWR_CHARGING;          /* 插著電就不必再提醒 */
    if (g->battery_pct <= PWR_CRITICAL_PCT) return PWR_CRITICAL;
    if (g->battery_pct <= PWR_LOW_PCT)      return PWR_LOW;
    return PWR_NONE;
}

/* ------------------------------------------------------------------ */
/* 互動                                                                */
/* ------------------------------------------------------------------ */
/* 按鍵 UI                                                             */
/* ------------------------------------------------------------------ */

/* 游標停在哪一格，就選到哪一個角色。門／衣櫃／開關那三格沒有角色。
   進度條是「游標停著就顯示」，所以 selected 必須在移動的當下就正確。 */
static uint8_t slot_character(const game_t *g, uint8_t slot)
{
    if (slot == SLOT_PRINCESS) return CHAR_ICE_PRINCESS;
    if (slot == SLOT_DOG)      return g->present;
    return CHAR_COUNT;
}

static void sync_selection(game_t *g)
{
    game_select(g, slot_character(g, g->cursor));
}

static void return_to_main(game_t *g)
{
    drop_subscreen(g);              /* 只有從子畫面回來時才還原游標 */
    g->ui = UI_MAIN;

    uint8_t n = game_main_slot_count(g);
    if (g->cursor >= n) g->cursor = (uint8_t)(n - 1);
    g->menu_idle_ms = 0;

    /* 這裡**不走 game_select**：逾時回主畫面不是輸入，
       不可以因此重開一次安靜期，否則放著不動也會一直壓住自發行為。 */
    g->selected = slot_character(g, g->cursor);
}

/* 進互動選單。內容是固定的（狗 5 個、公主 4 個），
   還是抓一份快照：這樣「選單開著的時候內容不會變」是結構保證的，
   不是靠「反正現在的內容不會變」這種會被下一次改動推翻的理由。 */
static void enter_menu(game_t *g)
{
    uint8_t c = slot_character(g, g->cursor);
    if (c >= CHAR_COUNT) return;

    g->menu_n = game_menu_actions(g, c, g->menu, MENU_MAX);
    if (g->menu_n == 0) return;

    g->main_cursor  = g->cursor;
    g->ui           = UI_MENU;
    g->cursor       = 0;
    g->menu_idle_ms = 0;
}

/* 呼叫選單。沒狗、有狗都是同一個畫面——「換狗」和「叫狗」對小孩是同一件事，
   分成兩個畫面只會讓她要先判斷自己現在是哪一種情況。 */
static void enter_call(game_t *g)
{
    g->main_cursor  = g->cursor;
    g->ui           = UI_CALL;
    g->cursor       = 0;
    g->menu_idle_ms = 0;
}

static void enter_dress(game_t *g)
{
    uint8_t list[8];
    uint8_t n = game_outfit_list(g, list, (uint8_t)sizeof(list));
    if (n == 0) return;                 /* 預設服裝一定在，這裡走不到 */

    g->main_cursor  = g->cursor;
    g->ui           = UI_DRESS;
    g->menu_idle_ms = 0;

    /* 游標停在現在穿的那一套上，不是第 0 個——
       小孩打開衣櫃看到的第一件事應該是「我現在穿的是這件」。 */
    g->cursor = 0;
    for (uint8_t i = 0; i < n; i++) {
        if (list[i] == game_outfit(g)) { g->cursor = i; break; }
    }
}

/* 子畫面共用的游標移動。n 是項目數。 */
static void cursor_step(game_t *g, button_t btn, uint8_t n)
{
    if (n == 0) return;
    if (btn == BTN_PREV) g->cursor = (uint8_t)((g->cursor + n - 1) % n);
    else if (btn == BTN_NEXT) g->cursor = (uint8_t)((g->cursor + 1) % n);
}

void game_button(game_t *g, button_t btn)
{
    if (!g || btn >= BTN_COUNT) return;

    /* 每一次按鍵都要記錄，不管它有沒有造成什麼——
       安靜期與背光看的都是「有沒有被操作」。 */
    g->last_input_ms = g->now_ms;
    g->menu_idle_ms = 0;

    /* 關機中：任何操作鍵都取消。小孩很容易誤觸電源鍵，必須救得回來。 */
    if (g->ui == UI_POWEROFF) {
        cancel_power_off(g);
        return;
    }

    /* 慶祝中：任何鍵提前結束，但**不執行任何選單操作**。
       慶祝畫面上的按鍵不該順手把某個動作也做掉。 */
    if (g->ui == UI_CELEBRATE) {
        leave_celebrate(g);
        return;
    }

    /* 開機中：只記錄按鍵（背光要回到全亮），畫面不受影響。 */
    if (g->ui == UI_BOOT) return;

    /* 過場中（有狗正在走進來或走回去）：任何鍵都只把過場**立刻走完**，
       不做別的。那 1.2 秒不接受互動，但按下去一定看得到畫面有反應——
       按了完全沒事發生，小孩就會一直按（Russo-Johnson et al. 2017）。 */
    if (game_pet_transition(g)) {
        finish_transition(g);
        if (g->ui == UI_MAIN) sync_selection(g);
        return;
    }

    if (g->ui == UI_MAIN) {
        if (btn == BTN_PREV || btn == BTN_NEXT) {
            cursor_step(g, btn, game_main_slot_count(g));
            sync_selection(g);
            return;
        }
        /* BTN_OK：依游標停的那一格分派。門／衣櫃／開關**都不是角色**，
           所以先取消選取，進度條才不會留在畫面上。 */
        switch (g->cursor) {
        case SLOT_DOOR:
            game_select(g, CHAR_COUNT);
            enter_call(g);
            break;
        case SLOT_WARDROBE:
            game_select(g, CHAR_COUNT);
            enter_dress(g);
            break;
        case SLOT_LIGHT:
            /* 電燈那一格**不進選單**，直接切開關。
               多一層選單對這個年齡就是多一個學不會的步驟。
               這一格就是「休息」——休息不是動作。 */
            game_select(g, CHAR_COUNT);
            game_toggle_light(g);
            break;
        default:
            sync_selection(g);
            enter_menu(g);
            break;
        }
        return;
    }

    if (g->ui == UI_CALL) {
        uint8_t list[CHAR_COUNT];
        uint8_t n = game_call_list(g, list, (uint8_t)sizeof(list));
        if (btn != BTN_OK) { cursor_step(g, btn, n); return; }

        /* 叫的是已經在場的那一隻時 game_call_pet 是 no-op，
           但畫面一樣要回主畫面——按下去要有結果。 */
        if (g->cursor < n) game_call_pet(g, list[g->cursor]);
        return_to_main(g);
        return;
    }

    if (g->ui == UI_DRESS) {
        uint8_t list[8];
        uint8_t n = game_outfit_list(g, list, (uint8_t)sizeof(list));
        if (btn != BTN_OK) { cursor_step(g, btn, n); return; }

        if (g->cursor < n && game_set_outfit(g, list[g->cursor])) {
            /* 換好衣服播一次 happy。ACT_DRESS 從互動選單移除之後，
               只剩衣櫃這一條路會用到它。 */
            game_do_action(g, CHAR_ICE_PRINCESS, ACT_DRESS);
        }
        return_to_main(g);
        return;
    }

    /* UI_MENU */
    if (btn != BTN_OK) { cursor_step(g, btn, g->menu_n); return; }

    /* 動作被拒絕（睡眠中）也一樣退回主畫面——
       讓小孩停在一個按了沒反應的選單上，她會一直按。 */
    game_do_action(g, g->selected, g->menu[g->cursor]);
    return_to_main(g);
}

/* ------------------------------------------------------------------ */

void game_select(game_t *g, uint8_t c)
{
    /* 選取角色也是輸入，一樣要開安靜期。 */
    g->last_input_ms = g->now_ms;
    g->selected = (c < CHAR_COUNT) ? c : CHAR_COUNT;
}

bool game_do_action(game_t *g, uint8_t c, action_t act)
{
    if (c >= CHAR_COUNT || act == ACT_NONE || act >= ACT_COUNT) return false;

    /* 開機圖與關機圖上沒有房間可以互動。
       慶祝畫面不擋——那是 game_button 的事，這裡擋會讓外層難寫。 */
    if (g->ui == UI_BOOT || g->ui == UI_POWEROFF) return false;

    /* 正在走進來／走回去的那一隻不受理互動——牠正在走路。
       只擋當事的那一隻，房間裡的其他人照常。 */
    if (game_pet_transition(g) && (c == g->present || c == g->leaving)) return false;

    char_runtime_t *r = &g->rt[c];

    /* 每一次輸入都要記錄，不管動作有沒有被接受——安靜期看的是「有沒有被操作」。 */
    g->last_input_ms = g->now_ms;

    /* 播放中「可以」被打斷。這是刻意的：
       Russo-Johnson et al. 2017（n=170，2–4 歲）測到學齡前兒童即使被明確
       告知不要點，仍會在非互動段落點擊 38–44 次。點了沒反應的後果是繼續亂點。
       需求值本來就夾在 100，重複觸發不會有任何負面效果。 */

    /* 睡覺時只能被叫醒，其他動作不受理。
       這是刻意的：夜間模式的目的是引導小孩去睡覺，不是繼續玩。 */
    if (r->state == CSTATE_SLEEPING && act != ACT_SLEEP) return false;

    if (act == ACT_DRESS && game_is_dog(c)) return false;
    if (act == ACT_TOILET && !game_is_dog(c)) return false;

    uint8_t *n = needs_of(g, c);
    anim_seq_t s; seq_clear(&s);
    pose_t end = r->pose;

    switch (act) {
    case ACT_FEED:
        seq_push_pose_change(&s, r->pose, POSE_STAND);
        seq_push(&s, ANIM_EAT, 2);
        seq_push(&s, ANIM_EAT_HAPPY, 1);
        end = POSE_STAND;
        n[NEED_HUNGER] = add_clamped(n[NEED_HUNGER], FEED_HUNGER);
        n[NEED_MOOD]   = add_clamped(n[NEED_MOOD],   FEED_MOOD);
        g->save.total_feeds++;
        break;

    case ACT_PET:
        seq_push(&s, ANIM_PET_REACT, 0);         /* 0 = 無限，放開手指才結束 */
        r->hold = true;
        g->save.total_pets++;
        break;

    case ACT_PLAY:
        seq_push_pose_change(&s, r->pose, POSE_STAND);
        seq_push(&s, ANIM_PLAY_BOW, 1);
        seq_push(&s, ANIM_CHASE_BALL, 2);
        seq_push(&s, ANIM_HAPPY, 1);
        end = POSE_STAND;
        n[NEED_MOOD]   = add_clamped(n[NEED_MOOD],   PLAY_MOOD);
        n[NEED_ENERGY] = add_clamped(n[NEED_ENERGY], PLAY_ENERGY);
        g->save.total_plays++;
        break;

    case ACT_SLEEP:
        if (r->state == CSTATE_SLEEPING) {
            seq_push(&s, ANIM_WAKE_UP, 1);
            seq_push_pose_change(&s, POSE_LIE, POSE_STAND);
            end = POSE_STAND;
            start_seq(g, c, &s, end);
            r->hold = false;
            g->save.chars[c].affection++;
            g->save_dirty = true;
            return true;
        }
        seq_push(&s, ANIM_YAWN, 1);
        seq_push_pose_change(&s, r->pose, POSE_LIE);
        seq_push(&s, ANIM_SLEEP_BREATHE, 0);
        end = POSE_LIE;
        r->hold = true;
        break;

    case ACT_TOILET:
        seq_push_pose_change(&s, r->pose, POSE_STAND);
        seq_push(&s, ANIM_WALK, 2);
        seq_push(&s, ANIM_TOILET, 1);
        seq_push(&s, ANIM_WALK, 2);
        end = POSE_STAND;
        n[NEED_BLADDER] = add_clamped(n[NEED_BLADDER], TOILET_BLADDER);
        break;

    case ACT_DRESS:
        seq_push_pose_change(&s, r->pose, POSE_STAND);
        seq_push(&s, ANIM_HAPPY, 1);
        end = POSE_STAND;
        n[NEED_TIDY] = add_clamped(n[NEED_TIDY], DRESS_TIDY);
        break;

    case ACT_BATH:
        /* 洗澡**沒有專屬的角色動畫**。狗站在洗澡盆裡、公主在關起來的淋浴間裡，
           畫面上的敘事全部由場景物件負責（specs/scene.json 的 object_cues.bath）。
           角色播待機，渲染層把物件畫在它之上。
           這樣一個新動作的資產成本是兩個物件，不是 4 個角色 × 4 格影格圖。 */
        seq_push_pose_change(&s, r->pose, POSE_STAND);
        seq_push(&s, ANIM_IDLE_BREATHE, 4);
        seq_push(&s, ANIM_HAPPY, 1);
        end = POSE_STAND;
        n[NEED_TIDY] = add_clamped(n[NEED_TIDY], BATH_TIDY);
        n[NEED_MOOD] = add_clamped(n[NEED_MOOD], BATH_MOOD);
        break;

    default:
        return false;
    }

    start_seq(g, c, &s, end);
    if (act == ACT_SLEEP) g->rt[c].state = CSTATE_SLEEPING;

    g->save.chars[c].affection++;
    g->save_dirty = true;
    return true;
}

void game_pet_move(game_t *g, uint8_t c, uint32_t dt_ms)
{
    if (c >= CHAR_COUNT) return;
    char_runtime_t *r = &g->rt[c];
    if (!r->hold || r->anim != ANIM_PET_REACT) return;

    g->last_input_ms = g->now_ms;      /* 按鍵還按著，持續延長安靜期 */
    r->pet_accum_ms += dt_ms;
    while (r->pet_accum_ms >= 2000u) {
        r->pet_accum_ms -= 2000u;
        uint8_t *n = needs_of(g, c);
        n[NEED_MOOD] = add_clamped(n[NEED_MOOD], PET_MOOD_PER_2S);
        if (g->save.chars[c].affection < UINT16_MAX) g->save.chars[c].affection++;
        g->save_dirty = true;
    }
}

void game_pet_end(game_t *g, uint8_t c)
{
    if (c >= CHAR_COUNT) return;
    char_runtime_t *r = &g->rt[c];
    if (!r->hold) return;
    r->hold = false;
    r->pet_accum_ms = 0;
    advance_seq(g, c);
}

/* ------------------------------------------------------------------ */
/* 動作建議                                                            */
/* ------------------------------------------------------------------ */

/* 互動選單的固定內容。順序是**寫死的**，不隨需求變動。
 *
 * 2026-08-02 之前這裡是「需求最低的前三名」，因為 docs/02 第三節寫
 * 「同一畫面最多 3 個可選項」。使用者女兒現在四歲半，已明確同意放寬到 5。
 *
 * 順序固定比「把最需要的排前面」重要：排序會動的選單等於每次打開
 * 圖示都在不同的位置，小孩就得每次重新讀一遍。位置固定之後她可以用
 * 「第三個是摸摸」來記，而「哪一個最需要」改由 game_suggest_action()
 * 在圖示上加提示來表達——兩件事分開，變動的理由才不會綁在一起。 */
uint8_t game_menu_actions(const game_t *g, uint8_t c, action_t *out, uint8_t max)
{
    if (!g || c >= CHAR_COUNT || !out || max == 0) return 0;

    static const action_t FIXED[MENU_MAX] = {
        ACT_FEED,     /* 吃飯 */
        ACT_PLAY,     /* 玩 */
        ACT_PET,      /* 摸摸 */
        ACT_BATH,     /* 洗澡 */
        ACT_TOILET,   /* 上廁所 —— 只有狗有 */
    };

    uint8_t k = 0;
    for (uint8_t i = 0; i < MENU_MAX && k < max; i++) {
        /* 公主沒有 bladder，所以她的選單少一個，是 4 個。
           不顯示成灰色的第五格——那是「你不能用這個」的失敗語意。 */
        if (FIXED[i] == ACT_TOILET && !game_is_dog(c)) continue;
        out[k++] = FIXED[i];
    }
    return k;
}

/* 現在最需要的那一個動作，只是給渲染層加提示用。
   回傳 ACT_NONE 表示現在什麼都不缺——那時候畫面上不該有任何提示。 */
action_t game_suggest_action(const game_t *g, uint8_t c)
{
    if (!g || c >= CHAR_COUNT) return ACT_NONE;

    static const action_t BY_NEED[NEED_COUNT] = {
        [NEED_HUNGER]  = ACT_FEED,
        [NEED_ENERGY]  = ACT_NONE,   /* 休息 = 關燈，不是選單裡的動作 */
        [NEED_MOOD]    = ACT_PLAY,
        [NEED_BLADDER] = ACT_TOILET,
        [NEED_TIDY]    = ACT_BATH,
    };

    const uint8_t *n = (const uint8_t *)&g->save.chars[c].needs;
    action_t best_act = ACT_NONE;
    uint8_t  best_val = NEED_LOW;    /* 沒有低於門檻的就不提示 */

    for (int i = 0; i < NEED_COUNT; i++) {
        if (!need_applies(c, i) || BY_NEED[i] == ACT_NONE) continue;
        if (n[i] < best_val) { best_val = n[i]; best_act = BY_NEED[i]; }
    }
    return best_act;
}

/* 進度條。狗是 hunger / energy / mood / bladder，
   公主是 hunger / energy / mood / tidy——**兩邊都是 4 條**，
   游標在公主和狗之間移動時版面不會跳。
   狗的 tidy 沒有條（牠會洗澡，但那一項對小孩不是「要顧的東西」）。

   **刻意不回傳任何「危險」旗標。** 值低本身不是壞消息（CLAUDE.md 規則 1），
   給了旗標就等於邀請渲染層畫成紅色。 */
uint8_t game_need_bars(const game_t *g, uint8_t c, uint8_t *out, uint8_t max)
{
    if (!g || c >= CHAR_COUNT || !out || max == 0) return 0;

    const uint8_t *n = (const uint8_t *)&g->save.chars[c].needs;
    const int order[4] = {
        NEED_HUNGER, NEED_ENERGY, NEED_MOOD,
        game_is_dog(c) ? NEED_BLADDER : NEED_TIDY,
    };

    uint8_t k = 0;
    for (int i = 0; i < 4 && k < max; i++) {
        uint8_t v = n[order[i]];
        out[k++] = (v > 100u) ? 100u : v;
    }
    return k;
}

/* ------------------------------------------------------------------ */
/* 換裝與配件                                                          */
/* ------------------------------------------------------------------ */

uint8_t game_outfit_list(const game_t *g, uint8_t *out, uint8_t max)
{
    if (!g || !out) return 0;
    uint8_t have = (uint8_t)(g->save.chars[CHAR_ICE_PRINCESS].unlocked_outfits | 0x01u);
    uint8_t k = 0;
    for (uint8_t id = 0; id < 8 && k < max; id++) {
        /* 未解鎖的**不列出來**。畫成鎖頭就變成「你還沒有」的失敗語意，
           而這台機器上沒有任何東西是「還沒有」的——只有還沒發生的驚喜。 */
        if (have & (uint8_t)(1u << id)) out[k++] = id;
    }
    return k;
}

uint8_t game_outfit(const game_t *g)
{
    return g ? g->save.chars[CHAR_ICE_PRINCESS].outfit_id : 0;
}

bool game_set_outfit(game_t *g, uint8_t outfit_id)
{
    if (!g || outfit_id >= 8) return false;

    character_save_t *p = &g->save.chars[CHAR_ICE_PRINCESS];
    uint8_t have = (uint8_t)(p->unlocked_outfits | 0x01u);
    if (!(have & (uint8_t)(1u << outfit_id))) return false;

    if (p->outfit_id != outfit_id) {
        p->outfit_id  = outfit_id;
        g->save_dirty = true;
    }
    return true;
}

/* 配件：清單還沒定案，這裡只是把 byte 接出來，讓存檔格式先固定下來。
   0 = 沒有配件，所以舊存檔（reserved 全 0）讀到的就是正確的預設。 */
uint8_t game_accessory_mask(const game_t *g, uint8_t c)
{
    return (g && c < CHAR_COUNT) ? g->save.chars[c].reserved[CHAR_RSV_ACCESSORY] : 0;
}

void game_set_accessory_mask(game_t *g, uint8_t c, uint8_t mask)
{
    if (!g || c >= CHAR_COUNT) return;
    if (g->save.chars[c].reserved[CHAR_RSV_ACCESSORY] == mask) return;
    g->save.chars[c].reserved[CHAR_RSV_ACCESSORY] = mask;
    g->save_dirty = true;
}

/* ------------------------------------------------------------------ */

anim_id_t game_current_anim(const game_t *g, uint8_t c)
{
    return (c < CHAR_COUNT) ? (anim_id_t)g->rt[c].anim : ANIM_IDLE_BREATHE;
}

int8_t game_char_x_offset(const game_t *g, uint8_t c)
{
    return (g && c < CHAR_COUNT) ? g->rt[c].x_ofs : 0;
}
