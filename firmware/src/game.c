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
#define PET_MOOD_PER_2S 3

/* 需求低於此值時角色開始「等待關心」 */
#define NEED_LOW      40

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

/* 該角色會用到哪些需求。公主沒有 bladder，狗沒有 tidy。 */
static bool need_applies(uint8_t c, int need)
{
    if (need == NEED_BLADDER) return game_is_dog(c);
    if (need == NEED_TIDY)    return !game_is_dog(c);
    return true;
}

/* ------------------------------------------------------------------ */
/* 動畫序列                                                            */
/* ------------------------------------------------------------------ */

static void seq_clear(anim_seq_t *s) { memset(s, 0, sizeof(*s)); }

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
           需求全零時若九成時間都在垂頭喪氣，對 3 歲小孩太沉重——
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

/* ------------------------------------------------------------------ */
/* 初始化                                                              */
/* ------------------------------------------------------------------ */

static void recompute_clock(game_t *g)
{
    g->hour  = (uint8_t)((g->now_unix / 3600ull) % 24ull);
    uint8_t s = g->save.night_start_hour, e = g->save.night_end_hour;
    g->night = (s <= e) ? (g->hour >= s && g->hour < e)
                        : (g->hour >= s || g->hour < e);
}

void game_init(game_t *g, const save_blob_t *loaded, uint64_t now_unix)
{
    memset(g, 0, sizeof(*g));
    g->save     = *loaded;
    g->now_unix = now_unix;
    g->selected = CHAR_COUNT;
    g->rng      = (uint32_t)(now_unix ^ 0x9E3779B9u) | 1u;

    uint32_t away = save_apply_offline_decay(&g->save, now_unix);
    recompute_clock(g);

    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *r = &g->rt[c];
        r->pose  = POSE_STAND;
        r->state = CSTATE_IDLE;

        if (g->night) {
            r->pose  = POSE_LIE;
            r->state = CSTATE_SLEEPING;
            r->anim  = ANIM_SLEEP_BREATHE;
        } else if (away >= 24u * 3600u) {
            /* 離開很久之後回來，開場是開心不是難過。
               回來永遠是被歡迎的，見 docs/02_遊戲設計.md。 */
            anim_seq_t s; seq_clear(&s);
            seq_push(&s, ANIM_HAPPY, 1);
            seq_push(&s, ANIM_TAIL_WAG, 2);
            start_seq(g, c, &s, POSE_STAND);
        } else {
            r->anim = ANIM_IDLE_BREATHE;
        }
        r->idle_next_ms = rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
    }
    g->save_dirty = (away > 0);
}

/* ------------------------------------------------------------------ */
/* 需求衰減                                                            */
/* ------------------------------------------------------------------ */

/* 每 5 分鐘結算一次。以 12 個 interval 為一小時，用累積餘數的方式
   避免整數除法把小數量的衰減全部抹平。 */
static void apply_decay(game_t *g)
{
    g->decay_phase = (uint8_t)((g->decay_phase + 1) % 12);
    uint8_t tick_count = g->decay_phase;

    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
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
                n[i] = add_clamped(n[i], -1);
            }
        }
    }
    g->save_dirty = true;
}

/* ------------------------------------------------------------------ */
/* 里程碑                                                              */
/* ------------------------------------------------------------------ */

static void check_milestones(game_t *g)
{
    uint32_t total = 0;
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
    }
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

    /* 夜間切換 */
    if (g->night != was_night) {
        for (uint8_t c = 0; c < CHAR_COUNT; c++) {
            char_runtime_t *r = &g->rt[c];
            anim_seq_t s; seq_clear(&s);
            if (g->night) {
                seq_push(&s, ANIM_YAWN, 1);
                seq_push_pose_change(&s, r->pose, POSE_LIE);
                seq_push(&s, ANIM_SLEEP_BREATHE, 0);
                start_seq(g, c, &s, POSE_LIE);
                r->hold  = true;
                r->state = CSTATE_SLEEPING;
            } else {
                seq_push(&s, ANIM_WAKE_UP, 1);
                seq_push_pose_change(&s, POSE_LIE, POSE_STAND);
                seq_push(&s, ANIM_HAPPY, 1);
                start_seq(g, c, &s, POSE_STAND);
                r->hold = false;
            }
        }
    }

    /* 動畫推進 */
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *r = &g->rt[c];
        bool wrapped = advance_anim(g, c, dt_ms);

        if (r->state == CSTATE_BUSY || r->state == CSTATE_SLEEPING) {
            if (wrapped) advance_seq(g, c);
        } else if (r->state == CSTATE_IDLE) {
            /* 觸控後的安靜期內不觸發自發行為。3 歲小孩會把同時發生的事
               關聯成因果——剛摸完狗、狗就自己難過，她會以為是自己弄的。 */
            bool quiet = (g->now_ms - g->last_touch_ms) < POST_TOUCH_QUIET_MS;
            if (g->now_ms >= r->idle_next_ms && !quiet) {
                anim_id_t pick = pick_idle_anim(g, c);
                remember_idle(r, pick);
                r->anim  = (uint8_t)pick;
                r->frame = 0;
                r->frame_accum_ms = 0;
                r->idle_next_ms = g->now_ms + rnd_range(g, IDLE_MIN_MS, IDLE_MAX_MS);
            }
        }
    }

    check_milestones(g);
}

/* ------------------------------------------------------------------ */
/* 互動                                                                */
/* ------------------------------------------------------------------ */

void game_select(game_t *g, uint8_t c)
{
    /* 點選角色也是觸控，一樣要開安靜期。 */
    g->last_touch_ms = g->now_ms;
    g->selected = (c < CHAR_COUNT) ? c : CHAR_COUNT;
}

bool game_do_action(game_t *g, uint8_t c, action_t act)
{
    if (c >= CHAR_COUNT || act == ACT_NONE || act >= ACT_COUNT) return false;

    char_runtime_t *r = &g->rt[c];

    /* 每一次觸控都要記錄，不管動作有沒有被接受——安靜期看的是「有沒有被碰」。 */
    g->last_touch_ms = g->now_ms;

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

    g->last_touch_ms = g->now_ms;      /* 手指還在滑動，持續延長安靜期 */
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

uint8_t game_suggest_actions(const game_t *g, uint8_t c, action_t *out, uint8_t max)
{
    if (c >= CHAR_COUNT || !out || max == 0) return 0;

    /* 依需求由低到高排出對應動作，最多回 3 個。
       3 歲的孩子一次看 3 個以上的選項會呆住，見 docs/02。 */
    typedef struct { uint8_t need; action_t act; } cand_t;
    cand_t cand[NEED_COUNT];
    uint8_t nc = 0;
    const uint8_t *n = (const uint8_t *)&g->save.chars[c].needs;

    if (need_applies(c, NEED_HUNGER))  cand[nc++] = (cand_t){ n[NEED_HUNGER],  ACT_FEED };
    if (need_applies(c, NEED_MOOD))    cand[nc++] = (cand_t){ n[NEED_MOOD],    ACT_PLAY };
    if (need_applies(c, NEED_ENERGY))  cand[nc++] = (cand_t){ n[NEED_ENERGY],  ACT_SLEEP };
    if (need_applies(c, NEED_BLADDER)) cand[nc++] = (cand_t){ n[NEED_BLADDER], ACT_TOILET };
    if (need_applies(c, NEED_TIDY))    cand[nc++] = (cand_t){ n[NEED_TIDY],    ACT_DRESS };

    /* 插入排序，nc 最多 5 */
    for (uint8_t i = 1; i < nc; i++) {
        cand_t key = cand[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && cand[j].need > key.need) { cand[j + 1] = cand[j]; j--; }
        cand[j + 1] = key;
    }

    uint8_t k = 0;
    /* 撫摸永遠排第一。它是最重要的互動，而且沒有目標、沒有結束條件。 */
    out[k++] = ACT_PET;
    for (uint8_t i = 0; i < nc && k < max && k < 3; i++) {
        if (cand[i].act == ACT_PET) continue;
        out[k++] = cand[i].act;
    }
    return k;
}

anim_id_t game_current_anim(const game_t *g, uint8_t c)
{
    return (c < CHAR_COUNT) ? (anim_id_t)g->rt[c].anim : ANIM_IDLE_BREATHE;
}
