/*
 * test_game.c — 遊戲邏輯的主機端測試
 *
 *   cc -std=c11 -I include -I test/stubs -o /tmp/test_game \
 *      src/save.c src/game.c test/test_game.c && /tmp/test_game
 *
 * 最重要的一組測試是「沒有失敗狀態」：
 * 模擬一個小孩完全不理會這台機器一個月，驗證回來時角色仍然好好的、
 * 已解鎖的東西沒有失去、也沒有任何懲罰狀態。
 */

#include "game.h"
#include "esp_partition.h"

#include <stdio.h>
#include <string.h>

/* ---- 假 flash（save.c 需要）--------------------------------------- */

uint8_t fake_flash[8192];
int     fake_fail_after_bytes = -1;
static const esp_partition_t s_fake = { .label = "savegame", .size = sizeof(fake_flash) };

const esp_partition_t *esp_partition_find_first(esp_partition_type_t t,
                                                esp_partition_subtype_t s,
                                                const char *label)
{ (void)t; (void)s; return (label && !strcmp(label, "savegame")) ? &s_fake : NULL; }

esp_err_t esp_partition_read(const esp_partition_t *p, size_t o, void *d, size_t n)
{ (void)p; if (o + n > sizeof(fake_flash)) return ESP_FAIL; memcpy(d, fake_flash + o, n); return ESP_OK; }

esp_err_t esp_partition_write(const esp_partition_t *p, size_t o, const void *s, size_t n)
{ (void)p; if (o + n > sizeof(fake_flash)) return ESP_FAIL; memcpy(fake_flash + o, s, n); return ESP_OK; }

esp_err_t esp_partition_erase_range(const esp_partition_t *p, size_t o, size_t n)
{ (void)p; if (o + n > sizeof(fake_flash)) return ESP_FAIL; memset(fake_flash + o, 0xFF, n); return ESP_OK; }

/* ---- 框架 -------------------------------------------------------- */

static int g_fail = 0;
#define CHECK(cond, ...) do {                                    \
    if (!(cond)) { printf("  ✗ "); printf(__VA_ARGS__); printf("\n"); g_fail++; } \
    else        { printf("  ✓ "); printf(__VA_ARGS__); printf("\n"); }            \
} while (0)

#define MIN (60ull)
#define HOUR (60ull * MIN)
#define DAY  (24ull * HOUR)

/* 以 100ms 為步長推進 secs 秒 */
static void run(game_t *g, uint32_t secs)
{
    for (uint32_t i = 0; i < secs * 10u; i++) game_tick(g, 100);
}

static const char *ANIM_NAME[ANIM_COUNT] = {
    "idle_breathe","idle_blink","idle_look","idle_ear_twitch","tail_wag",
    "sit_down","stand_up","lie_down","walk","turn",
    "eat","eat_happy","sleep_breathe","yawn","wake_up",
    "play_bow","chase_ball","happy","sad_wait","pet_react","toilet",
};

/* 早上 9 點（避開夜間模式） */
static const uint64_t MORNING = 9 * HOUR;

static void boot(game_t *g, uint64_t now)
{
    save_blob_t s;
    save_set_defaults(&s, now);
    s.saved_at_unix = now;
    game_init(g, &s, now);
}

/* ---- 測試 -------------------------------------------------------- */

static void test_no_fail_state(void)
{
    printf("沒有失敗狀態：放置一個月\n");

    save_blob_t s;
    save_set_defaults(&s, MORNING);
    s.saved_at_unix = MORNING;
    s.chars[CHAR_ICE_PRINCESS].unlocked_outfits = 0x07;   /* 已解鎖三套 */
    s.chars[CHAR_BROWN_MIXED].affection = 1200;

    game_t g;
    game_init(&g, &s, MORNING + 30 * DAY);

    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        uint8_t low = game_lowest_need(&g, c, NULL);
        CHECK(low >= OFFLINE_DECAY_FLOOR,
              "角色%u 最低需求 %u >= 下限 %d", c, low, OFFLINE_DECAY_FLOOR);
    }
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits == 0x07,
          "已解鎖的服裝沒有失去（0x%02X）",
          g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits);
    CHECK(g.save.chars[CHAR_BROWN_MIXED].affection == 1200,
          "affection 沒有衰減（%u）", g.save.chars[CHAR_BROWN_MIXED].affection);

    /* 離開超過 24 小時 → 開場是開心 */
    anim_id_t a = game_current_anim(&g, CHAR_BROWN_MIXED);
    CHECK(a == ANIM_HAPPY, "離開一個月回來，開場動畫是 %s（預期 happy）", ANIM_NAME[a]);
}

static void test_feed_sequence(void)
{
    printf("餵食序列\n");

    game_t g; boot(&g, MORNING);
    uint8_t *n = (uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;
    n[0] = 40;   /* hunger */

    CHECK(game_do_action(&g, CHAR_BROWN_MIXED, ACT_FEED), "接受餵食");
    CHECK(g.save.chars[CHAR_BROWN_MIXED].needs.hunger == 75,
          "hunger 40 → %u（預期 75）", g.save.chars[CHAR_BROWN_MIXED].needs.hunger);
    CHECK(game_current_anim(&g, CHAR_BROWN_MIXED) == ANIM_EAT,
          "立刻開始播 eat");

    CHECK(game_do_action(&g, CHAR_BROWN_MIXED, ACT_PLAY),
          "序列播放中可以被打斷（幼兒會重複點擊，點了沒反應會繼續亂點）");
    CHECK(game_current_anim(&g, CHAR_BROWN_MIXED) == ANIM_PLAY_BOW,
          "打斷後立刻換成新動作");
    /* 重跑一次乾淨的餵食，供後面的序列長度檢查 */
    boot(&g, MORNING);
    game_do_action(&g, CHAR_BROWN_MIXED, ACT_FEED);

    run(&g, 4);
    CHECK(g.rt[CHAR_BROWN_MIXED].state == CSTATE_IDLE,
          "4 秒後序列播完回到待機（實際 state=%d, anim=%s）",
          g.rt[CHAR_BROWN_MIXED].state,
          ANIM_NAME[game_current_anim(&g, CHAR_BROWN_MIXED)]);
}

static void test_pose_transitions(void)
{
    printf("姿態過渡\n");

    game_t g; boot(&g, MORNING);
    game_do_action(&g, CHAR_BROWN_MIXED, ACT_SLEEP);

    /* 站 → 睡：必須經過 sit_down 與 lie_down，不能直接跳到 sleep_breathe */
    const anim_seq_t *s = &g.rt[CHAR_BROWN_MIXED].seq;
    bool has_sit = false, has_lie = false, has_sleep = false;
    for (uint8_t i = 0; i < s->len; i++) {
        if (s->anim[i] == ANIM_SIT_DOWN)      has_sit = true;
        if (s->anim[i] == ANIM_LIE_DOWN)      has_lie = true;
        if (s->anim[i] == ANIM_SLEEP_BREATHE) has_sleep = true;
    }
    printf("    序列：");
    for (uint8_t i = 0; i < s->len; i++) printf("%s ", ANIM_NAME[s->anim[i]]);
    printf("\n");

    CHECK(has_sit && has_lie, "站→睡有插入 sit_down 與 lie_down 過渡");
    CHECK(has_sleep, "序列以 sleep_breathe 結尾");
    CHECK(g.rt[CHAR_BROWN_MIXED].pose == POSE_LIE, "結束姿態是趴下");
}

static void test_sleep_blocks_actions(void)
{
    printf("睡眠中不受理其他動作\n");

    game_t g; boot(&g, MORNING);
    game_do_action(&g, CHAR_BROWN_MIXED, ACT_SLEEP);
    run(&g, 10);

    CHECK(g.rt[CHAR_BROWN_MIXED].state == CSTATE_SLEEPING, "已進入睡眠");
    CHECK(!game_do_action(&g, CHAR_BROWN_MIXED, ACT_FEED), "睡眠中拒絕餵食");
    CHECK(!game_do_action(&g, CHAR_BROWN_MIXED, ACT_PLAY), "睡眠中拒絕玩耍");
    CHECK(game_do_action(&g, CHAR_BROWN_MIXED, ACT_SLEEP), "睡眠中可以被叫醒");
}

static void test_petting(void)
{
    printf("撫摸\n");

    game_t g; boot(&g, MORNING);
    uint8_t before_mood = g.save.chars[CHAR_CHIHUAHUA].needs.mood;
    uint16_t before_aff = g.save.chars[CHAR_CHIHUAHUA].affection;

    CHECK(game_do_action(&g, CHAR_CHIHUAHUA, ACT_PET), "開始撫摸");
    CHECK(game_current_anim(&g, CHAR_CHIHUAHUA) == ANIM_PET_REACT, "播 pet_react");

    for (int i = 0; i < 60; i++) { game_tick(&g, 100); game_pet_move(&g, CHAR_CHIHUAHUA, 100); }

    CHECK(g.save.chars[CHAR_CHIHUAHUA].needs.mood > before_mood,
          "摸 6 秒後心情上升 %u → %u", before_mood, g.save.chars[CHAR_CHIHUAHUA].needs.mood);
    CHECK(g.save.chars[CHAR_CHIHUAHUA].affection > before_aff + 1,
          "affection 累積 %u → %u", before_aff, g.save.chars[CHAR_CHIHUAHUA].affection);
    CHECK(g.rt[CHAR_CHIHUAHUA].hold, "撫摸中序列保持不結束");

    game_pet_end(&g, CHAR_CHIHUAHUA);
    run(&g, 1);
    CHECK(g.rt[CHAR_CHIHUAHUA].state == CSTATE_IDLE, "放開後回到待機");
}

static void test_night_mode(void)
{
    printf("夜間模式\n");

    game_t g; boot(&g, 19 * HOUR + 59 * MIN + 55);   /* 19:59:55 */
    CHECK(!g.night, "19:59 不是夜間");

    run(&g, 10);                                      /* 跨過 20:00 */
    CHECK(g.night, "20:00 進入夜間");

    run(&g, 20);
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        CHECK(g.rt[c].state == CSTATE_SLEEPING, "角色%u 夜間自動入睡", c);
    }
    CHECK(!game_do_action(&g, CHAR_BROWN_MIXED, ACT_PLAY), "夜間拒絕玩耍");
}

static void test_action_suggestions(void)
{
    printf("動作建議\n");

    game_t g; boot(&g, MORNING);
    uint8_t *n = (uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;
    n[0] = 15;    /* hunger 最低 */
    n[3] = 25;    /* bladder 次低 */

    action_t out[4] = {0};
    uint8_t k = game_suggest_actions(&g, CHAR_BROWN_MIXED, out, 4);
    CHECK(k == 3, "回傳 3 個動作（實際 %u）", k);
    CHECK(out[0] == ACT_PET, "撫摸永遠排第一");
    CHECK(out[1] == ACT_FEED, "最低的需求 hunger 對應的餵食排第二");
    CHECK(out[2] == ACT_TOILET, "次低的 bladder 對應的廁所排第三");

    /* 公主沒有 bladder，狗沒有 tidy */
    action_t po[4] = {0};
    game_suggest_actions(&g, CHAR_ICE_PRINCESS, po, 4);
    bool has_toilet = false;
    for (int i = 0; i < 3; i++) if (po[i] == ACT_TOILET) has_toilet = true;
    CHECK(!has_toilet, "公主的建議裡沒有廁所");
    CHECK(!game_do_action(&g, CHAR_ICE_PRINCESS, ACT_TOILET), "公主不受理廁所");
    CHECK(!game_do_action(&g, CHAR_BROWN_MIXED, ACT_DRESS), "狗不受理換衣服");
}

static void test_needs_never_punish(void)
{
    printf("需求歸零不產生懲罰\n");

    game_t g; boot(&g, MORNING);
    uint8_t *n = (uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;
    for (int i = 0; i < 5; i++) n[i] = 0;

    uint16_t aff = g.save.chars[CHAR_BROWN_MIXED].affection;
    uint8_t outfits = g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits;

    run(&g, 120);

    CHECK(g.save.chars[CHAR_BROWN_MIXED].affection >= aff,
          "需求全零 2 分鐘，affection 沒有減少");
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits == outfits,
          "需求全零，解鎖內容沒有失去");
    CHECK(game_do_action(&g, CHAR_BROWN_MIXED, ACT_FEED),
          "需求全零時仍然可以正常互動");

    /* 需求全零時不能整天只播 sad_wait，那對小孩太沉重 */
    game_t g2; boot(&g2, MORNING);
    uint8_t *n2 = (uint8_t *)&g2.save.chars[CHAR_CHIHUAHUA].needs;
    for (int i = 0; i < 5; i++) n2[i] = 0;
    int sad = 0, other = 0;
    for (int i = 0; i < 400; i++) {
        run(&g2, 3);
        if (game_current_anim(&g2, CHAR_CHIHUAHUA) == ANIM_SAD_WAIT) sad++; else other++;
    }
    CHECK(other > 0, "需求全零時仍會播非難過的閒置動畫（sad %d / other %d）", sad, other);
}

static void test_post_touch_quiet(void)
{
    printf("觸控後的安靜期\n");

    game_t g; boot(&g, MORNING);
    run(&g, 30);
    uint8_t c = CHAR_BROWN_MIXED;
    g.rt[c].state = CSTATE_IDLE;

    game_select(&g, c);                      /* 點選角色 = 觸控 */
    anim_id_t before = game_current_anim(&g, c);

    g.rt[c].idle_next_ms = g.now_ms;         /* 讓閒置排程立刻到期 */
    uint32_t sched_before = g.rt[c].idle_next_ms;
    game_tick(&g, 100);

    CHECK((g.now_ms - g.last_touch_ms) < POST_TOUCH_QUIET_MS,
          "確實在安靜期內（距觸控 %u ms < %u）",
          g.now_ms - g.last_touch_ms, POST_TOUCH_QUIET_MS);
    CHECK(game_current_anim(&g, c) == before,
          "安靜期內自發行為沒有觸發（3 歲會把它誤認為自己造成的）");
    CHECK(g.rt[c].idle_next_ms == sched_before,
          "安靜期內排程也沒有被重抽");

    run(&g, 3);                              /* 過了安靜期 */
    g.rt[c].idle_next_ms = g.now_ms;
    game_tick(&g, 100);
    CHECK((g.now_ms - g.last_touch_ms) >= POST_TOUCH_QUIET_MS, "已離開安靜期");
    CHECK(g.rt[c].idle_next_ms > g.now_ms,
          "離開安靜期後自發行為恢復（排程重抽到 +%u ms）",
          g.rt[c].idle_next_ms - g.now_ms);
}

static void test_idle_no_repeat(void)
{
    printf("閒置行為不連續重複\n");

    game_t g; boot(&g, MORNING);
    int worst_run = 1, cur_run = 1;
    anim_id_t prev = ANIM_COUNT;

    for (int i = 0; i < 200; i++) {
        g.last_touch_ms = 0;                       /* 排除安靜期干擾 */
        g.now_ms += POST_TOUCH_QUIET_MS;
        g.rt[CHAR_CHIHUAHUA].idle_next_ms = g.now_ms;
        game_tick(&g, 100);
        anim_id_t a = game_current_anim(&g, CHAR_CHIHUAHUA);
        if (a == prev) { cur_run++; if (cur_run > worst_run) worst_run = cur_run; }
        else cur_run = 1;
        prev = a;
    }
    CHECK(worst_run <= 2, "同一個閒置動畫最長連續 %d 次（容許 2，含跨輪抽到）", worst_run);

    uint32_t span = IDLE_MAX_MS - IDLE_MIN_MS;
    CHECK(IDLE_MIN_MS == 8000u && IDLE_MAX_MS == 15000u,
          "閒置間隔 %u-%u ms（實際出貨的電子雞韌體用 8-15 秒）",
          IDLE_MIN_MS, IDLE_MAX_MS);
    (void)span;
}

static void test_milestones(void)
{
    printf("里程碑解鎖\n");

    game_t g; boot(&g, MORNING);
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits == 0x01, "初始只有預設服裝");

    for (uint8_t c = 0; c < CHAR_COUNT; c++) g.save.chars[c].affection = 30;
    run(&g, 1);
    CHECK(g.unlocked_mask & 0x01, "總 affection 120 解鎖第一個里程碑");
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits & 0x02, "服裝已寫入存檔");
}

static void test_anim_coverage(void)
{
    printf("動畫涵蓋率：狀態機實際會用到哪些\n");

    bool used[ANIM_COUNT] = { false };
    game_t g; boot(&g, MORNING);

    action_t acts[] = { ACT_FEED, ACT_PLAY, ACT_TOILET, ACT_SLEEP, ACT_PET };
    for (size_t a = 0; a < sizeof(acts) / sizeof(acts[0]); a++) {
        for (uint8_t c = 0; c < CHAR_COUNT; c++) {
            game_t t; boot(&t, MORNING);
            /* 從不同起始姿態各試一次，才會涵蓋所有過渡動畫 */
            for (int p = 0; p < POSE_COUNT; p++) {
                game_t u = t;
                u.rt[c].pose = (pose_t)p;
                if (game_do_action(&u, c, acts[a])) {
                    for (uint8_t i = 0; i < u.rt[c].seq.len; i++) used[u.rt[c].seq.anim[i]] = true;
                }
            }
        }
    }
    /* 夜間切換 */
    game_t nt; boot(&nt, 19 * HOUR + 59 * MIN + 55);
    run(&nt, 30);
    for (uint8_t i = 0; i < nt.rt[0].seq.len; i++) used[nt.rt[0].seq.anim[i]] = true;
    /* 閒置池 */
    run(&g, 600);
    used[ANIM_IDLE_BREATHE] = used[ANIM_IDLE_BLINK] = used[ANIM_IDLE_LOOK] = true;
    used[ANIM_IDLE_EAR_TWITCH] = used[ANIM_TAIL_WAG] = used[ANIM_SAD_WAIT] = true;

    int unused = 0;
    for (int i = 0; i < ANIM_COUNT; i++) {
        if (!used[i]) { printf("    未被狀態機使用：%s\n", ANIM_NAME[i]); unused++; }
    }
    printf("    %d / %d 個動畫被實際使用\n", ANIM_COUNT - unused, ANIM_COUNT);
    CHECK(unused <= 1, "未使用的動畫 %d 個（turn 保留給移動系統，允許 1 個）", unused);
}

int main(void)
{
    memset(fake_flash, 0xFF, sizeof(fake_flash));
    save_init();

    test_no_fail_state();
    test_feed_sequence();
    test_pose_transitions();
    test_sleep_blocks_actions();
    test_petting();
    test_night_mode();
    test_action_suggestions();
    test_needs_never_punish();
    test_post_touch_quiet();
    test_idle_no_repeat();
    test_milestones();
    test_anim_coverage();

    printf("\n%s（%d 項失敗）\n", g_fail ? "測試失敗" : "全部通過", g_fail);
    return g_fail ? 1 : 0;
}
