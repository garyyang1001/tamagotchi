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

/* 迴圈裡逐次檢查用：不印每一次，只累積失敗次數，最後用一行 CHECK 收。
   幾千次的迴圈如果每次都印，真正失敗的那一行會被淹掉。 */
static int quiet_fail = 0;
#define CHECK_QUIET(cond) do { if (!(cond)) quiet_fail++; } while (0)

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

/* 完整開機：init → 回報存檔載入完 → 走完 UI_BOOT 的最短停留，停在主畫面。
   多數測試關心的是主畫面之後的行為，所以 helper 一次做完。
   要驗開機畫面本身的請看 test_boot_screen()。 */
static void boot(game_t *g, uint64_t now)
{
    save_blob_t s;
    save_set_defaults(&s, now);
    s.saved_at_unix = now;
    game_init(g, &s, now);
    game_boot_done(g);
    while (g->ui == UI_BOOT) game_tick(g, 100);
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

static void test_menu_actions(void)
{
    printf("互動選單：固定 5 個（公主 4 個）\n");

    game_t g; boot(&g, MORNING);

    /* 狗：吃飯 / 玩 / 摸摸 / 洗澡 / 上廁所，順序固定 */
    action_t out[MENU_MAX + 2] = {0};
    uint8_t k = game_menu_actions(&g, CHAR_BROWN_MIXED, out, MENU_MAX + 2);
    CHECK(k == 5, "狗的互動選單是 5 個（實際 %u）", k);
    CHECK(out[0] == ACT_FEED && out[1] == ACT_PLAY && out[2] == ACT_PET &&
          out[3] == ACT_BATH && out[4] == ACT_TOILET,
          "順序固定：吃飯 / 玩 / 摸摸 / 洗澡 / 上廁所");

    /* 公主沒有 bladder，所以少一格；**不是**顯示成灰色的第五格 */
    action_t po[MENU_MAX + 2] = {0};
    uint8_t pk = game_menu_actions(&g, CHAR_ICE_PRINCESS, po, MENU_MAX + 2);
    CHECK(pk == 4, "公主的互動選單是 4 個（實際 %u）", pk);
    bool p_has_toilet = false;
    for (uint8_t i = 0; i < pk; i++) if (po[i] == ACT_TOILET) p_has_toilet = true;
    CHECK(!p_has_toilet, "公主的選單裡沒有上廁所");

    /* 需求排序**不會**改變選單內容——位置固定，小孩才記得住 */
    uint8_t *n = (uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;
    n[0] = 5; n[1] = 5; n[2] = 5; n[3] = 5; n[4] = 5;
    action_t out2[MENU_MAX] = {0};
    game_menu_actions(&g, CHAR_BROWN_MIXED, out2, MENU_MAX);
    bool same = true;
    for (int i = 0; i < 5; i++) if (out2[i] != out[i]) same = false;
    CHECK(same, "需求全部掉到最低，選單的順序一個都沒有變");

    /* 休息 = 關燈，不是動作；換衣服走衣櫃，不是動作 */
    bool has_sleep = false, has_dress = false;
    for (int i = 0; i < 5; i++) {
        if (out[i] == ACT_SLEEP) has_sleep = true;
        if (out[i] == ACT_DRESS) has_dress = true;
    }
    CHECK(!has_sleep, "選單裡沒有「睡覺」——休息就是關燈（SLOT_LIGHT）");
    CHECK(!has_dress, "選單裡沒有「換衣服」——換裝走衣櫃（UI_DRESS）");

    CHECK(!game_do_action(&g, CHAR_ICE_PRINCESS, ACT_TOILET), "公主不受理廁所");
    CHECK(!game_do_action(&g, CHAR_BROWN_MIXED, ACT_DRESS), "狗不受理換衣服");
}

static void test_suggest_action(void)
{
    printf("最需要的那一個（只給提示用，不決定選單）\n");

    game_t g; boot(&g, MORNING);
    uint8_t *n = (uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;

    n[0] = 90; n[1] = 90; n[2] = 90; n[3] = 90; n[4] = 90;
    CHECK(game_suggest_action(&g, CHAR_BROWN_MIXED) == ACT_NONE,
          "什麼都不缺的時候不提示任何東西");

    n[0] = 15;
    CHECK(game_suggest_action(&g, CHAR_BROWN_MIXED) == ACT_FEED,
          "hunger 最低 → 提示吃飯");
    n[3] = 5;
    CHECK(game_suggest_action(&g, CHAR_BROWN_MIXED) == ACT_TOILET,
          "bladder 更低 → 改提示上廁所");
    n[3] = 90; n[0] = 90; n[4] = 12;
    CHECK(game_suggest_action(&g, CHAR_BROWN_MIXED) == ACT_BATH,
          "tidy 最低 → 提示洗澡（不是換衣服）");

    /* energy 不在提示範圍：休息不是選單裡的動作 */
    n[4] = 90; n[1] = 1;
    action_t a = game_suggest_action(&g, CHAR_BROWN_MIXED);
    CHECK(a == ACT_NONE, "energy 最低時不提示任何選單動作（休息 = 關燈，實際 %d）", a);

    /* 提示只是提示：它不會改變選單，也不會改變任何需求值 */
    uint8_t before[5];
    memcpy(before, &g.save.chars[CHAR_BROWN_MIXED].needs, 5);
    (void)game_suggest_action(&g, CHAR_BROWN_MIXED);
    CHECK(memcmp(before, &g.save.chars[CHAR_BROWN_MIXED].needs, 5) == 0,
          "查詢提示不改變任何需求值");
}

static void test_need_bars(void)
{
    printf("進度條\n");

    game_t g; boot(&g, MORNING);
    uint8_t *n = (uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;
    n[0] = 11; n[1] = 22; n[2] = 33; n[3] = 44; n[4] = 55;

    uint8_t bars[8] = {0};
    uint8_t k = game_need_bars(&g, CHAR_BROWN_MIXED, bars, 8);
    CHECK(k == 4, "狗有 4 條（實際 %u）", k);
    CHECK(bars[0] == 11 && bars[1] == 22 && bars[2] == 33 && bars[3] == 44,
          "狗的順序是 hunger / energy / mood / bladder");

    uint8_t *p = (uint8_t *)&g.save.chars[CHAR_ICE_PRINCESS].needs;
    p[0] = 60; p[1] = 61; p[2] = 62; p[3] = 63; p[4] = 64;
    uint8_t pb[8] = {0};
    uint8_t pk = game_need_bars(&g, CHAR_ICE_PRINCESS, pb, 8);
    CHECK(pk == 4, "公主也是 4 條，版面不會跳（實際 %u）", pk);
    CHECK(pb[0] == 60 && pb[1] == 61 && pb[2] == 62 && pb[3] == 64,
          "公主的第 4 條是 tidy 不是 bladder（她沒有 bladder）");

    /* 值域 0..100，而且**低的時候不回傳任何旗標**——
       這裡只有數字，渲染層拿不到「危險」這個概念（CLAUDE.md 規則 1） */
    for (int i = 0; i < 5; i++) n[i] = 0;
    k = game_need_bars(&g, CHAR_BROWN_MIXED, bars, 8);
    CHECK(k == 4, "需求全零時仍然是 4 條，不會少畫也不會多畫");
    bool in_range = true;
    for (uint8_t i = 0; i < k; i++) if (bars[i] > 100) in_range = false;
    CHECK(in_range, "全零時每一條都在 0..100");

    for (int i = 0; i < 5; i++) n[i] = 100;
    game_need_bars(&g, CHAR_BROWN_MIXED, bars, 8);
    in_range = true;
    for (int i = 0; i < 4; i++) if (bars[i] != 100) in_range = false;
    CHECK(in_range, "滿的時候每一條都是 100");

    /* max 小於條數時只填得下的部分，不會踩到呼叫端的陣列外 */
    uint8_t two[2] = { 9, 9 };
    CHECK(game_need_bars(&g, CHAR_BROWN_MIXED, two, 2) == 2, "max=2 只回 2 條");
    CHECK(game_need_bars(&g, CHAR_COUNT, bars, 8) == 0, "不存在的角色回 0 條");
}

static void test_bath(void)
{
    printf("洗澡\n");

    /* 洗澡四個角色都能做，而且**不需要新的角色動畫**——
       畫面上的敘事由場景物件負責（狗站在盆裡、公主在關起來的淋浴間裡）。 */
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        game_t g; boot(&g, MORNING);
        uint8_t *n = (uint8_t *)&g.save.chars[c].needs;
        n[4] = 20;
        CHECK(game_do_action(&g, c, ACT_BATH), "角色 %u 受理洗澡", c);
        CHECK(n[4] > 20, "角色 %u 洗完澡 tidy 上升（%u）", c, n[4]);
        CHECK(n[4] <= 100, "角色 %u 的 tidy 沒有超過 100（%u）", c, n[4]);
        bool only_known = true;
        for (uint8_t i = 0; i < g.rt[c].seq.len; i++)
            if (g.rt[c].seq.anim[i] >= ANIM_COUNT) only_known = false;
        CHECK(only_known, "角色 %u 的洗澡序列只用既有動畫", c);
    }

    /* 洗澡不會把任何需求扣掉——沒有失敗狀態（CLAUDE.md 規則 1） */
    game_t g; boot(&g, MORNING);
    uint8_t before[5], after[5];
    memcpy(before, &g.save.chars[CHAR_CHIHUAHUA].needs, 5);
    game_do_action(&g, CHAR_CHIHUAHUA, ACT_BATH);
    memcpy(after, &g.save.chars[CHAR_CHIHUAHUA].needs, 5);
    for (int i = 0; i < 5; i++)
        CHECK(after[i] >= before[i], "洗澡沒有讓需求 %d 下降（%u → %u）", i, before[i], after[i]);
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

static void test_post_input_quiet(void)
{
    printf("按鍵後的安靜期\n");

    game_t g; boot(&g, MORNING);
    run(&g, 30);
    uint8_t c = CHAR_BROWN_MIXED;
    g.rt[c].state = CSTATE_IDLE;

    game_select(&g, c);                      /* 點選角色 = 按鍵 */
    anim_id_t before = game_current_anim(&g, c);

    g.rt[c].idle_next_ms = g.now_ms;         /* 讓閒置排程立刻到期 */
    uint32_t sched_before = g.rt[c].idle_next_ms;
    game_tick(&g, 100);

    CHECK((g.now_ms - g.last_input_ms) < POST_INPUT_QUIET_MS,
          "確實在安靜期內（距按鍵 %u ms < %u）",
          g.now_ms - g.last_input_ms, POST_INPUT_QUIET_MS);
    CHECK(game_current_anim(&g, c) == before,
          "安靜期內自發行為沒有觸發（3 歲會把它誤認為自己造成的）");
    CHECK(g.rt[c].idle_next_ms == sched_before,
          "安靜期內排程也沒有被重抽");

    run(&g, 3);                              /* 過了安靜期 */
    g.rt[c].idle_next_ms = g.now_ms;
    game_tick(&g, 100);
    CHECK((g.now_ms - g.last_input_ms) >= POST_INPUT_QUIET_MS, "已離開安靜期");
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
        g.last_input_ms = 0;                       /* 排除安靜期干擾 */
        g.now_ms += POST_INPUT_QUIET_MS;
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

/* 六個里程碑分兩個軸：前四個給服裝、後兩個給配件。
   守的是「不要解鎖不存在的東西」——原本六個全給服裝會解到 bit 5、6，
   而 tools/outfits.py 量出來只做得出 5 套（七套彼此最短距離只剩 27.3）。 */
static void test_milestone_split(void)
{
    printf("里程碑分成服裝與配件兩個軸\n");

    save_blob_t s; save_set_defaults(&s, MORNING);
    for (uint8_t c = 0; c < CHAR_COUNT; c++) s.chars[c].affection = 1000;
    game_t g;
    game_init(&g, &s, MORNING);
    game_boot_done(&g);
    while (g.ui == UI_BOOT) game_tick(&g, 100);
    run(&g, 8);

    uint8_t o = g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits;
    uint8_t a = game_accessory_mask(&g, CHAR_ICE_PRINCESS);
    CHECK((o & 0x1Fu) == 0x1Fu, "五套服裝全解鎖（0x%02X）", o);
    CHECK((o & 0xE0u) == 0u,
          "**沒有解鎖不存在的第 6、7 套**（高位應為 0，實際 0x%02X）", o);
    CHECK((a & 0x18u) == 0x18u,
          "第 5、6 個里程碑解的是配件 bit 3/4（0x%02X）", a);
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].affection >= 1000,
          "規則 1：解鎖不扣任何東西，affection 只增不減");
}

static void test_button_ui(void)
{
    printf("按鍵 UI\n");

    game_t g; boot(&g, MORNING);
    CHECK(g.ui == UI_MAIN, "開機在主畫面");
    CHECK(g.cursor == SLOT_PRINCESS, "開機游標停在公主身上（實際 %u）", g.cursor);
    CHECK(g.selected == CHAR_ICE_PRINCESS, "所以一開機就看得到公主的進度條");

    /* 主畫面：左右移動游標，會循環。沒有狗在場時只有 4 格。 */
    uint8_t n_slot = game_main_slot_count(&g);
    uint8_t c0 = g.cursor;
    game_button(&g, BTN_NEXT);
    CHECK(g.cursor == (c0 + 1) % n_slot, "下一個把游標往後移");
    for (uint8_t i = 0; i < n_slot; i++) game_button(&g, BTN_NEXT);
    CHECK(g.cursor == (c0 + 1) % n_slot, "走完一圈回到原位（游標會循環）");
    game_button(&g, BTN_PREV);
    CHECK(g.cursor == c0, "上一個把游標往前移");

    /* 游標停在角色上就有 selected，停在家具上就沒有 */
    game_button(&g, BTN_PREV);
    CHECK(g.cursor == SLOT_LIGHT, "往前一格是電燈開關");
    CHECK(g.selected == CHAR_COUNT, "停在家具上時沒有選取任何角色");
    game_button(&g, BTN_NEXT);
    CHECK(g.selected == CHAR_ICE_PRINCESS, "回到公主身上就又有進度條了");

    /* 確認進互動選單 */
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_MENU, "在角色上按確認進互動選單");
    CHECK(g.menu_n == 4, "公主的選單是 4 個（實際 %u）", g.menu_n);
    CHECK(g.menu[0] == ACT_FEED, "第一個永遠是吃飯");
    CHECK(g.cursor == 0, "進選單時游標歸零");

    /* 選單裡的快照不會被需求變化影響 */
    action_t snap[MENU_MAX];
    for (int i = 0; i < g.menu_n; i++) snap[i] = g.menu[i];
    uint8_t *n = (uint8_t *)&g.save.chars[g.selected].needs;
    n[0] = 1;                                    /* 讓 hunger 掉到最低 */
    game_tick(&g, 200);                          /* run() 的單位是秒，這裡只要 200 ms */
    for (int i = 0; i < g.menu_n; i++)
        CHECK(g.menu[i] == snap[i], "選單開著時圖示不會在小孩眼前跳掉（第 %d 個）", i);

    /* 確認執行動作並退回主畫面，游標回到剛剛那一格 */
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_MAIN, "執行完退回主畫面");
    CHECK(g.cursor == SLOT_PRINCESS, "游標回到進選單之前那一格（實際 %u）", g.cursor);

    /* 選單逾時自動回主畫面——沒有取消鍵是刻意的 */
    game_t t; boot(&t, MORNING);
    game_button(&t, BTN_OK);
    CHECK(t.ui == UI_MENU, "進選單");
    for (uint32_t i = 0; i < (MENU_TIMEOUT_MS - 500) / 100; i++) game_tick(&t, 100);
    CHECK(t.ui == UI_MENU, "還沒逾時就不會跑掉（已過 %u ms）", MENU_TIMEOUT_MS - 500);
    game_tick(&t, 1000);
    CHECK(t.ui == UI_MAIN, "%u ms 沒動作自動回主畫面", MENU_TIMEOUT_MS);
    CHECK(t.cursor == SLOT_PRINCESS, "逾時回來游標也在原來那一格");

    /* 任何一顆按鍵都要開安靜期 */
    game_t q; boot(&q, MORNING);
    run(&q, 5);
    game_button(&q, BTN_NEXT);
    CHECK((q.now_ms - q.last_input_ms) < POST_INPUT_QUIET_MS,
          "按鍵之後在安靜期內");

    /* 被拒絕的動作也要退回主畫面——不能讓小孩停在按了沒反應的選單上 */
    game_t r2; boot(&r2, MORNING);
    game_do_action(&r2, CHAR_ICE_PRINCESS, ACT_SLEEP);   /* 先進入睡眠 */
    run(&r2, 4);
    game_button(&r2, BTN_OK);
    if (r2.ui == UI_MENU) {
        game_button(&r2, BTN_OK);
        CHECK(r2.ui == UI_MAIN, "動作被拒絕也退回主畫面");
    }
}

/* ---- 新的互動模型（2026-08-02）----------------------------------- */

static void test_present_pet(void)
{
    printf("在場的狗：呼叫與換狗\n");

    game_t g; boot(&g, MORNING);
    CHECK(g.present == CHAR_COUNT, "開機預設沒有狗（實際 %u）", g.present);
    CHECK(game_main_slot_count(&g) == 4,
          "沒有狗時主畫面只有 4 格（實際 %u）", game_main_slot_count(&g));

    /* 呼叫選單永遠是三隻狗，位置固定 */
    uint8_t list[4] = {0};
    uint8_t nl = game_call_list(&g, list, 4);
    CHECK(nl == 3, "呼叫選單有 3 隻狗（實際 %u）", nl);
    CHECK(list[0] == CHAR_CHIHUAHUA && list[1] == CHAR_BROWN_MIXED &&
          list[2] == CHAR_BRINDLE_GUARD, "順序固定，公主不在裡面");

    /* 走到門口，按確認開呼叫選單 */
    while (g.cursor != SLOT_DOOR) game_button(&g, BTN_PREV);
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_CALL, "在門上按確認開呼叫選單");
    CHECK(g.selected == CHAR_COUNT, "門不是角色，沒有進度條");

    game_button(&g, BTN_NEXT);                 /* 選第二隻：巧克力 */
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_MAIN, "選完回主畫面");
    CHECK(g.present == CHAR_BROWN_MIXED, "叫進來的是巧克力（實際 %u）", g.present);
    CHECK(game_current_anim(&g, CHAR_BROWN_MIXED) == ANIM_WALK,
          "到場的狗播 walk（實際 %s）",
          ANIM_NAME[game_current_anim(&g, CHAR_BROWN_MIXED)]);
    CHECK(game_pet_transition(&g), "正在過場");
    CHECK(!game_do_action(&g, CHAR_BROWN_MIXED, ACT_FEED), "過場期間不接受互動");

    run(&g, 2);
    CHECK(!game_pet_transition(&g), "%u ms 之後過場結束", ARRIVE_MS);
    CHECK(g.rt[CHAR_BROWN_MIXED].state == CSTATE_IDLE, "到場之後回到正常待機");
    CHECK(game_char_x_offset(&g, CHAR_BROWN_MIXED) == 0, "到場之後站回自己的位置");
    CHECK(game_main_slot_count(&g) == 5,
          "有狗時主畫面 5 格（實際 %u）", game_main_slot_count(&g));
    CHECK(game_do_action(&g, CHAR_BROWN_MIXED, ACT_FEED), "過場結束就可以互動了");

    /* 狗那一格是最後一格，而且前四格的位置沒有變 */
    while (g.cursor != SLOT_DOG) game_button(&g, BTN_NEXT);
    CHECK(g.selected == CHAR_BROWN_MIXED, "狗那一格選到的是在場的那一隻");
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_MENU && g.menu_n == 5, "狗的互動選單是 5 個（實際 %u）", g.menu_n);
    game_button(&g, BTN_OK);
    run(&g, 6);

    /* **換狗不需要先送回去。** 直接叫下一隻。 */
    game_call_pet(&g, CHAR_BRINDLE_GUARD);
    CHECK(g.present == CHAR_BRINDLE_GUARD, "新的那一隻立刻就位（不必先送回去）");
    CHECK(g.leaving == CHAR_BROWN_MIXED, "舊的那一隻自己走回去");
    CHECK(game_current_anim(&g, CHAR_BROWN_MIXED) == ANIM_WALK,
          "**離場的狗播 walk，不是 sad_wait**（實際 %s）",
          ANIM_NAME[game_current_anim(&g, CHAR_BROWN_MIXED)]);
    CHECK(game_current_anim(&g, CHAR_BRINDLE_GUARD) == ANIM_WALK, "到場的也播 walk");

    /* 規則 1：換狗不可以有任何負面語意，也不可以扣任何東西 */
    uint8_t left_needs[5];
    memcpy(left_needs, &g.save.chars[CHAR_BROWN_MIXED].needs, 5);
    uint16_t left_aff = g.save.chars[CHAR_BROWN_MIXED].affection;
    run(&g, 2);
    CHECK(memcmp(left_needs, &g.save.chars[CHAR_BROWN_MIXED].needs, 5) == 0,
          "走回去的那一隻一個需求值都沒有被扣");
    CHECK(g.save.chars[CHAR_BROWN_MIXED].affection == left_aff,
          "走回去的那一隻 affection 沒有變");
    CHECK(g.leaving == CHAR_COUNT && !game_pet_transition(&g), "兩段過場都走完了");
    CHECK(g.rt[CHAR_BROWN_MIXED].anim != ANIM_SAD_WAIT,
          "離場之後也不是停在難過的動畫上");

    /* 叫已經在場的那一隻是 no-op，不會重播過場 */
    game_call_pet(&g, CHAR_BRINDLE_GUARD);
    CHECK(!game_pet_transition(&g), "叫已經在場的那一隻不會重播過場");

    /* 按鍵可以中斷過場，而且不會壞掉 */
    game_t i2; boot(&i2, MORNING);
    game_call_pet(&i2, CHAR_CHIHUAHUA);
    game_tick(&i2, 100);
    game_button(&i2, BTN_NEXT);
    CHECK(!game_pet_transition(&i2), "按鍵把過場立刻走完");
    CHECK(i2.present == CHAR_CHIHUAHUA, "中斷之後狗還是在場的");
    CHECK(i2.rt[CHAR_CHIHUAHUA].state == CSTATE_IDLE, "中斷之後狀態是乾淨的待機");
    CHECK(game_char_x_offset(&i2, CHAR_CHIHUAHUA) == 0, "中斷之後位移歸零");
    CHECK(i2.ui == UI_MAIN, "中斷過場不會跑到別的畫面去");
    run(&i2, 3);
    CHECK(i2.ui == UI_MAIN && i2.present == CHAR_CHIHUAHUA, "之後也沒有壞掉");

    /* 換狗的過場中再按鍵中斷，兩隻都要收乾淨 */
    game_call_pet(&i2, CHAR_BROWN_MIXED);
    game_tick(&i2, 100);
    game_button(&i2, BTN_OK);
    CHECK(!game_pet_transition(&i2) && i2.leaving == CHAR_COUNT,
          "換狗的過場也中斷得乾淨");
    CHECK(i2.rt[CHAR_CHIHUAHUA].state == CSTATE_IDLE &&
          i2.rt[CHAR_BROWN_MIXED].state == CSTATE_IDLE, "兩隻都回到待機");
}

static void test_away_decay(void)
{
    printf("不在場的狗：衰減減半、下限 30\n");

    game_t g; boot(&g, MORNING);
    game_call_pet(&g, CHAR_CHIHUAHUA);
    run(&g, 2);                                  /* 走完過場 */

    uint8_t before_in[5], before_out[5];
    memcpy(before_in,  &g.save.chars[CHAR_CHIHUAHUA].needs, 5);
    memcpy(before_out, &g.save.chars[CHAR_BROWN_MIXED].needs, 5);

    run(&g, 10 * 3600);                          /* 10 小時，還沒到夜間 */

    const uint8_t *in  = (const uint8_t *)&g.save.chars[CHAR_CHIHUAHUA].needs;
    const uint8_t *out = (const uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs;

    for (int i = 0; i < 5; i++) {
        int d_in  = (int)before_in[i]  - (int)in[i];
        int d_out = (int)before_out[i] - (int)out[i];
        CHECK(d_out * 2 == d_in,
              "需求 %d：在場掉 %d、不在場掉 %d（剛好一半）", i, d_in, d_out);
    }
    CHECK(g.present == CHAR_CHIHUAHUA, "10 小時後在場的還是同一隻（沒有走失）");

    /* 不凍結：不在場也會掉，否則小孩沒有理由去叫另外兩隻 */
    int moved = 0;
    for (int i = 0; i < 5; i++) if (out[i] < before_out[i]) moved++;
    CHECK(moved > 0, "不在場的狗**沒有被凍結**（%d 項有變化）", moved);

    /* 下限：不在場的衰減和離線一樣停在 OFFLINE_DECAY_FLOOR */
    game_t f; boot(&f, MORNING);
    game_call_pet(&f, CHAR_CHIHUAHUA);
    run(&f, 2);
    uint8_t *fn = (uint8_t *)&f.save.chars[CHAR_BRINDLE_GUARD].needs;
    for (int i = 0; i < 5; i++) fn[i] = 32;
    run(&f, 10 * 3600);
    for (int i = 0; i < 5; i++)
        CHECK(fn[i] >= OFFLINE_DECAY_FLOOR,
              "不在場的需求 %d = %u >= 下限 %d", i, fn[i], OFFLINE_DECAY_FLOOR);
}

static void test_dress_screen(void)
{
    printf("衣櫃（UI_DRESS）\n");

    game_t g; boot(&g, MORNING);
    uint8_t list[8] = {0};
    CHECK(game_outfit_list(&g, list, 8) == 1, "一開始只有預設服裝一套");
    CHECK(list[0] == 0, "預設服裝的 id 是 0");
    CHECK(game_outfit(&g) == 0, "身上穿的是預設服裝");

    /* 解鎖兩套：bit1 與 bit3 */
    g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits = (uint8_t)(0x01 | 0x02 | 0x08);
    uint8_t n = game_outfit_list(&g, list, 8);
    CHECK(n == 3, "解鎖兩套之後清單有 3 套（實際 %u）", n);
    CHECK(list[0] == 0 && list[1] == 1 && list[2] == 3,
          "**未解鎖的完全不出現在清單裡**（不是畫成鎖頭）");

    /* 走到衣櫃按確認 */
    while (g.cursor != SLOT_WARDROBE) game_button(&g, BTN_NEXT);
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_DRESS, "在衣櫃上按確認進換裝畫面");
    CHECK(g.cursor == 0, "游標停在現在穿的那一套上");

    game_button(&g, BTN_NEXT);
    game_button(&g, BTN_NEXT);                 /* 第三套 = id 3 */
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_MAIN, "選完回主畫面");
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].outfit_id == 3,
          "換上第三套，存進 outfit_id（實際 %u）",
          g.save.chars[CHAR_ICE_PRINCESS].outfit_id);
    CHECK(g.save_dirty, "換裝要寫回存檔");
    CHECK(g.cursor == SLOT_WARDROBE, "游標回到衣櫃那一格");

    /* 再進去一次，游標要停在現在穿的那一套 */
    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_DRESS && g.cursor == 2,
          "再打開衣櫃時游標停在正在穿的那一套（實際 %u）", g.cursor);

    /* 換裝畫面也有 8 秒逾時 */
    for (uint32_t i = 0; i < MENU_TIMEOUT_MS / 100 + 2; i++) game_tick(&g, 100);
    CHECK(g.ui == UI_MAIN, "換裝畫面 %u ms 沒動作自動回主畫面", MENU_TIMEOUT_MS);

    /* 未解鎖的直接拒絕，而且不會留下半套狀態 */
    CHECK(!game_set_outfit(&g, 5), "沒解鎖的服裝設不上去");
    CHECK(game_outfit(&g) == 3, "被拒絕之後身上還是原來那一套");
    CHECK(!game_set_outfit(&g, 99), "超出範圍的 id 也設不上去");

    /* 換裝不扣任何東西——規則 1 */
    uint8_t nb[5];
    memcpy(nb, &g.save.chars[CHAR_ICE_PRINCESS].needs, 5);
    game_set_outfit(&g, 1);
    for (int i = 0; i < 5; i++)
        CHECK(((uint8_t *)&g.save.chars[CHAR_ICE_PRINCESS].needs)[i] >= nb[i],
              "換裝沒有扣掉需求 %d", i);
}

static void test_old_save_defaults(void)
{
    printf("舊存檔升級（reserved 全 0）\n");

    save_blob_t old;
    save_set_defaults(&old, MORNING);
    old.saved_at_unix = MORNING;
    memset(old.reserved, 0, sizeof(old.reserved));
    for (int c = 0; c < CHAR_COUNT; c++) {
        memset(old.chars[c].reserved, 0, sizeof(old.chars[c].reserved));
        old.chars[c].outfit_id = 0;              /* 舊韌體寫過的值就是 0 */
    }

    game_t g;
    game_init(&g, &old, MORNING);
    game_boot_done(&g);
    while (g.ui == UI_BOOT) game_tick(&g, 100);

    CHECK(game_outfit(&g) == 0, "outfit_id 讀到 0 = 預設服裝（合理預設）");
    uint8_t list[8] = {0};
    CHECK(game_outfit_list(&g, list, 8) >= 1 && list[0] == 0,
          "預設服裝一定在清單裡，衣櫃不會是空的");
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        CHECK(game_accessory_mask(&g, c) == 0,
              "角色%u 的 accessory_mask 讀到 0 = 沒有配件（合理預設）", c);
    CHECK(game_light_on(&g), "燈是開的（既有契約沒有被改掉）");
    CHECK(g.present == CHAR_COUNT, "升級後開機一樣是沒有狗，不會憑空多一隻");
    CHECK(SAVE_VERSION == 1, "SAVE_VERSION 沒有動（新欄位全部放 reserved）");

    /* 配件的 API 掛鉤：寫得進去、讀得回來、會標記存檔要寫 */
    g.save_dirty = false;
    game_set_accessory_mask(&g, CHAR_ICE_PRINCESS, 0x05);
    CHECK(game_accessory_mask(&g, CHAR_ICE_PRINCESS) == 0x05, "配件寫得進去");
    CHECK(g.save_dirty, "配件改了要寫回存檔");
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].reserved[CHAR_RSV_ACCESSORY] == 0x05,
          "存在 character_save_t 的 reserved 裡，不是新欄位");

    /* outfit_id 指到沒解鎖的那一套時安靜地退回預設，不顯示任何錯誤 */
    save_blob_t bad;
    save_set_defaults(&bad, MORNING);
    bad.saved_at_unix = MORNING;
    bad.chars[CHAR_ICE_PRINCESS].outfit_id = 6;      /* 沒解鎖 */
    game_t b;
    game_init(&b, &bad, MORNING);
    CHECK(game_outfit(&b) == 0, "指到沒解鎖的服裝時退回預設（實際 %u）", game_outfit(&b));
}

static void test_princess_roam(void)
{
    printf("公主閒置時會在房間裡走動\n");

    game_t g; boot(&g, MORNING);
    CHECK(game_char_x_offset(&g, CHAR_ICE_PRINCESS) == 0, "一開始站在自己的位置上");

    bool walked = false;
    int8_t seen_min = 0, seen_max = 0;
    quiet_fail = 0;
    for (int i = 0; i < 4000; i++) {
        game_tick(&g, 100);
        int8_t x = game_char_x_offset(&g, CHAR_ICE_PRINCESS);
        if (game_current_anim(&g, CHAR_ICE_PRINCESS) == ANIM_WALK) walked = true;
        if (x < seen_min) seen_min = x;
        if (x > seen_max) seen_max = x;
        CHECK_QUIET(x >= -CHAR_X_RANGE && x <= CHAR_X_RANGE);
    }
    CHECK(walked, "公主閒置時會播 walk");
    CHECK(seen_min != 0 || seen_max != 0,
          "而且真的移動了（走過 %d..%d）", seen_min, seen_max);
    CHECK(quiet_fail == 0, "位移全程沒有超出 ±%d（%d 次越界）",
          CHAR_X_RANGE, quiet_fail);

    /* 狗不走這條路：牠的移動只發生在進出房間的過場 */
    game_t d; boot(&d, MORNING);
    game_call_pet(&d, CHAR_CHIHUAHUA);
    run(&d, 2);
    bool dog_moved = false;
    for (int i = 0; i < 2000; i++) {
        game_tick(&d, 100);
        if (game_char_x_offset(&d, CHAR_CHIHUAHUA) != 0) dog_moved = true;
    }
    CHECK(!dog_moved, "在場的狗不會自己在房間裡飄");

    /* 位移**不烘進動畫**：同一段共用的 walk，只有 x 偏移在動 */
    game_t w; boot(&w, MORNING);
    int guard = 0;
    while (game_current_anim(&w, CHAR_ICE_PRINCESS) != ANIM_WALK && guard++ < 20000) {
        game_tick(&w, 100);
    }
    CHECK(guard < 20000, "等得到公主開始走路（%d tick）", guard);
    int8_t x0 = game_char_x_offset(&w, CHAR_ICE_PRINCESS);
    for (int i = 0; i < 10; i++) game_tick(&w, 100);
    CHECK(game_char_x_offset(&w, CHAR_ICE_PRINCESS) != x0,
          "走路的時候 x 偏移一直在變（%d → %d）",
          x0, game_char_x_offset(&w, CHAR_ICE_PRINCESS));
}

/* ---- 六個畫面狀態（2026-08-02 新增）------------------------------- */

static void test_boot_screen(void)
{
    printf("開機畫面\n");

    save_blob_t s;
    save_set_defaults(&s, MORNING);
    s.saved_at_unix = MORNING;

    /* (1) 最短停留：存檔一開始就載好了，仍然要湊滿 BOOT_MIN_MS。
           不設下限的話開機圖會一閃而過，那不叫「機器開起來了」。 */
    game_t g;
    game_init(&g, &s, MORNING);
    CHECK(g.ui == UI_BOOT, "game_init 之後停在開機畫面");
    CHECK(game_backlight(&g) == BACKLIGHT_FULL, "開機畫面背光全亮");
    CHECK(!game_should_sleep(&g), "開機畫面不會要求 light sleep");

    game_boot_done(&g);
    uint32_t elapsed = 0;
    while (elapsed + 100 < BOOT_MIN_MS) { game_tick(&g, 100); elapsed += 100; }
    CHECK(g.ui == UI_BOOT, "存檔載好了也要停滿 %u ms（已過 %u ms）",
          BOOT_MIN_MS, elapsed);
    game_tick(&g, 100);
    CHECK(g.ui == UI_MAIN, "湊滿 %u ms 才進主畫面", BOOT_MIN_MS);

    /* (2) 存檔沒回報載入完就一直等，不管過了多久 */
    game_t w;
    game_init(&w, &s, MORNING);
    run(&w, 10);
    CHECK(w.ui == UI_BOOT, "存檔還沒載入完，10 秒後仍停在開機畫面");
    game_boot_done(&w);
    game_tick(&w, 100);
    CHECK(w.ui == UI_MAIN, "回報載入完之後才進主畫面");

    /* (3) 離線超過 24 小時的歡迎動畫仍然成立，而且整段落在主畫面上 */
    game_t h;
    game_init(&h, &s, MORNING + 30 * DAY);
    CHECK(game_current_anim(&h, CHAR_CHIHUAHUA) == ANIM_HAPPY,
          "開機畫面期間就已經擺好歡迎動畫");
    game_boot_done(&h);
    while (h.ui == UI_BOOT) game_tick(&h, 100);
    CHECK(game_current_anim(&h, CHAR_CHIHUAHUA) == ANIM_HAPPY,
          "離開開機畫面時是 happy（回來永遠是被歡迎的）");
    CHECK(h.rt[CHAR_CHIHUAHUA].frame == 0,
          "歡迎動畫從第 0 格重播，不會被開機圖吃掉前半段（實際 frame=%u）",
          h.rt[CHAR_CHIHUAHUA].frame);
}

static void test_power_off(void)
{
    printf("關機\n");

    game_t g; boot(&g, MORNING);
    uint8_t before[CHAR_COUNT][5];
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        memcpy(before[c], &g.save.chars[c].needs, 5);

    game_power_off(&g);
    CHECK(g.ui == UI_POWEROFF, "game_power_off 進入關機畫面");
    bool all_sleep = true;
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        if (game_current_anim(&g, c) != ANIM_SLEEP_BREATHE) all_sleep = false;
    CHECK(all_sleep, "四個角色立刻播 sleep_breathe（關機是「大家睡著了」）");
    CHECK(game_backlight(&g) == BACKLIGHT_FULL,
          "關機那兩秒螢幕要亮著，否則看起來像壞掉");

    run(&g, 1);
    CHECK(!game_power_off_done(&g), "1 秒還不能斷電（POWEROFF_MS=%u）", POWEROFF_MS);
    run(&g, 2);
    CHECK(game_power_off_done(&g), "%u ms 之後外層才可以斷電", POWEROFF_MS);
    CHECK(g.ui == UI_POWEROFF, "時間走完仍停在關機畫面，等外層動手");

    /* 規則 1：關機不扣任何分、不改任何需求值 */
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        CHECK(memcmp(before[c], &g.save.chars[c].needs, 5) == 0,
              "角色%u 關機沒有改變任何需求值", c);
    }

    /* 誤觸電源鍵一定要救得回來——3 歲很容易按到 */
    game_t r; boot(&r, MORNING);
    game_power_off(&r);
    run(&r, 1);
    game_button(&r, BTN_OK);
    CHECK(r.ui == UI_MAIN, "關機中按操作鍵取消，回到主畫面");
    CHECK(!game_power_off_done(&r), "取消之後外層不會斷電");
    bool none_sleeping = true;
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        if (r.rt[c].state == CSTATE_SLEEPING) none_sleeping = false;
    CHECK(none_sleeping, "取消之後大家開始醒過來");
    run(&r, 8);
    bool back_idle = true;
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        if (r.rt[c].state != CSTATE_IDLE) back_idle = false;
    CHECK(back_idle, "醒來的序列走完之後回到正常待機");

    /* 三顆操作鍵都要能取消，不能只有其中一顆 */
    const button_t ALL[BTN_COUNT] = { BTN_PREV, BTN_OK, BTN_NEXT };
    for (int i = 0; i < BTN_COUNT; i++) {
        game_t t; boot(&t, MORNING);
        game_power_off(&t);
        game_button(&t, ALL[i]);
        CHECK(t.ui == UI_MAIN, "第 %d 顆鍵也能取消關機", i);
    }
}

static void test_backlight(void)
{
    printf("待機背光\n");

    game_t g; boot(&g, MORNING);
    run(&g, 700);                 /* 把 now_ms 拉開，下面才好直接設 last_input_ms */

    g.last_input_ms = g.now_ms;
    CHECK(game_backlight(&g) == BACKLIGHT_FULL, "剛按過鍵 → 100");

    g.last_input_ms = g.now_ms - (DIM_AFTER_MS - 1);
    CHECK(game_backlight(&g) == BACKLIGHT_FULL,
          "%u ms 沒按鍵 → 還是 100", DIM_AFTER_MS - 1);

    g.last_input_ms = g.now_ms - DIM_AFTER_MS;
    CHECK(game_backlight(&g) == BACKLIGHT_DIM, "整 %u ms（60 秒）→ 50", DIM_AFTER_MS);
    CHECK(!game_should_sleep(&g), "60 秒不進 light sleep");

    g.last_input_ms = g.now_ms - (SLEEP_AFTER_MS - 1);
    CHECK(game_backlight(&g) == BACKLIGHT_DIM, "%u ms → 仍是 50", SLEEP_AFTER_MS - 1);
    CHECK(!game_should_sleep(&g), "差 1 ms 還不進 light sleep");

    g.last_input_ms = g.now_ms - SLEEP_AFTER_MS;
    CHECK(game_backlight(&g) == BACKLIGHT_OFF,
          "整 %u ms（10 分鐘）→ 0", SLEEP_AFTER_MS);
    CHECK(game_should_sleep(&g), "10 分鐘之後要求外層進 light sleep");

    game_button(&g, BTN_NEXT);
    CHECK(game_backlight(&g) == BACKLIGHT_FULL, "任何按鍵立刻回到 100");
    CHECK(!game_should_sleep(&g), "按鍵之後不再要求 light sleep");

    /* **變暗不是暫停**：50% 的時候動畫必須繼續跑。看狗不算閒置。 */
    game_t d; boot(&d, MORNING);
    run(&d, 90);
    CHECK(game_backlight(&d) == BACKLIGHT_DIM, "90 秒沒按鍵 → 背光 50");
    uint16_t f0 = d.rt[CHAR_CHIHUAHUA].frame;
    bool moved = false;
    for (int i = 0; i < 30 && !moved; i++) {
        game_tick(&d, 100);
        if (d.rt[CHAR_CHIHUAHUA].frame != f0) moved = true;
    }
    CHECK(moved, "背光 50 時動畫仍在推進（看狗不算閒置）");
}

static void test_light_switch(void)
{
    printf("電燈開關\n");

    game_t g; boot(&g, MORNING);
    CHECK(game_light_on(&g), "預設燈是開的");
    CHECK(!g.night, "早上 9 點 + 燈開著 → 不是夜間");

    while (g.cursor != SLOT_LIGHT) game_button(&g, BTN_NEXT);
    CHECK(g.cursor == SLOT_LIGHT,
          "游標第 %d 格是電燈（實際 %u）", SLOT_LIGHT, g.cursor);
    CHECK(g.selected == CHAR_COUNT, "站在電燈上時沒有選取任何角色");

    uint8_t need_before[5];
    memcpy(need_before, &g.save.chars[CHAR_BROWN_MIXED].needs, 5);
    uint16_t aff_before = g.save.chars[CHAR_BROWN_MIXED].affection;
    uint8_t  outfits_before = g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits;

    game_button(&g, BTN_OK);
    CHECK(g.ui == UI_MAIN, "在電燈上按確認**不進選單**，直接切開關");
    CHECK(!game_light_on(&g), "按下去把燈關掉");
    CHECK(g.night, "燈關 → night 成立（night = 時鐘夜間 || 燈關）");
    CHECK(g.save.reserved[SAVE_RSV_LIGHT_OFF] == 1,
          "存檔存的是 light_off = 1，不是 light_on");
    CHECK(g.save_dirty, "燈的狀態要寫回存檔");

    run(&g, 20);
    game_button(&g, BTN_OK);
    CHECK(game_light_on(&g), "再按一次把燈打開");
    CHECK(!g.night, "燈開回來就不再是夜間");
    run(&g, 20);

    /* 規則 1：關燈不扣任何分、不觸發任何負面狀態 */
    for (int i = 0; i < 5; i++)
        CHECK(((uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs)[i] >= need_before[i],
              "關燈再開燈沒有扣掉需求 %d（%u → %u）", i, need_before[i],
              ((uint8_t *)&g.save.chars[CHAR_BROWN_MIXED].needs)[i]);
    CHECK(g.save.chars[CHAR_BROWN_MIXED].affection >= aff_before, "關燈沒有扣 affection");
    CHECK(g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits == outfits_before,
          "關燈沒有讓任何已解鎖的東西失去");

    /* 舊存檔（reserved 全 0）升級之後，燈必須是**開**的。
       這就是為什麼存的是 light_off 而不是 light_on。 */
    save_blob_t old;
    save_set_defaults(&old, MORNING);
    old.saved_at_unix = MORNING;
    memset(old.reserved, 0, sizeof(old.reserved));   /* 舊韌體沒寫過這個欄位 */
    game_t o;
    game_init(&o, &old, MORNING);
    CHECK(game_light_on(&o), "舊存檔（reserved 全 0）升級之後燈是開的");
    CHECK(!o.night, "舊存檔升級之後畫面不會整片黑掉");
    game_boot_done(&o);
    while (o.ui == UI_BOOT) game_tick(&o, 100);
    CHECK(o.rt[CHAR_CHIHUAHUA].state != CSTATE_SLEEPING,
          "舊存檔升級之後角色不會一開機就全部睡著");

    /* 燈關時動畫放慢 0.7 倍——沿用既有的夜間邏輯，不是另外一套。
       把兩台機器擺成完全相同的 sleep_breathe，只差在燈的開關。 */
    game_t fast; boot(&fast, MORNING);
    game_t slow; boot(&slow, MORNING);
    game_set_light(&slow, false);
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        char_runtime_t *a = &fast.rt[c], *b = &slow.rt[c];
        a->state = b->state = CSTATE_SLEEPING;
        a->anim  = b->anim  = ANIM_SLEEP_BREATHE;
        a->frame = b->frame = 0;
        a->frame_accum_ms = b->frame_accum_ms = 0;
        a->hold = b->hold = true;
        a->seq.len = b->seq.len = 1;
        a->seq.anim[0]   = b->seq.anim[0]   = ANIM_SLEEP_BREATHE;
        a->seq.repeat[0] = b->seq.repeat[0] = 0;
        a->seq_step = b->seq_step = 0;
    }
    for (int i = 0; i < 20; i++) { game_tick(&fast, 100); game_tick(&slow, 100); }
    CHECK(slow.rt[0].frame < fast.rt[0].frame,
          "燈關時同一段動畫走得比較慢（%u 格 vs %u 格）",
          slow.rt[0].frame, fast.rt[0].frame);
}

static void test_battery(void)
{
    printf("電量\n");

    game_t g; boot(&g, MORNING);
    CHECK(game_power_hint(&g) == PWR_NONE, "還沒回報電量時不顯示任何圖示");

    game_set_power(&g, 100, false);
    CHECK(game_power_hint(&g) == PWR_NONE, "100%% → 沒有圖示");
    game_set_power(&g, PWR_LOW_PCT + 1, false);
    CHECK(game_power_hint(&g) == PWR_NONE, "%d%% → 沒有圖示", PWR_LOW_PCT + 1);
    game_set_power(&g, PWR_LOW_PCT, false);
    CHECK(game_power_hint(&g) == PWR_LOW, "%d%% → 低電量", PWR_LOW_PCT);
    game_set_power(&g, PWR_CRITICAL_PCT + 1, false);
    CHECK(game_power_hint(&g) == PWR_LOW, "%d%% → 仍是低電量", PWR_CRITICAL_PCT + 1);
    game_set_power(&g, PWR_CRITICAL_PCT, false);
    CHECK(game_power_hint(&g) == PWR_CRITICAL, "%d%% → 極低電量", PWR_CRITICAL_PCT);
    game_set_power(&g, 0, false);
    CHECK(game_power_hint(&g) == PWR_CRITICAL, "0%% → 極低電量");
    game_set_power(&g, 3, true);
    CHECK(game_power_hint(&g) == PWR_CHARGING, "充電中優先於低電量");
    game_set_power(&g, 200, false);
    CHECK(g.battery_pct == 100, "超過 100 的回報被夾住（實際 %u）", g.battery_pct);

    /* 規則 1：低電量**不影響任何遊戲狀態**。
       兩台機器同樣的種子、同樣的時間，只差在一台快沒電。 */
    game_t low, ctl;
    boot(&low, MORNING);
    boot(&ctl, MORNING);
    game_set_power(&low, 1, false);
    save_blob_t save_before = low.save;

    run(&low, 60);
    run(&ctl, 60);

    CHECK(memcmp(&low.save, &ctl.save, sizeof(save_blob_t)) == 0,
          "1%% 電量跑 60 秒，存檔與滿電那台逐 byte 相同（需求一個都沒被扣）");
    CHECK(memcmp(&low.save, &save_before, sizeof(save_blob_t)) == 0,
          "電量不進存檔（game_set_power 之後存檔一個 byte 都沒動）");
    bool same_anim = true;
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        if (game_current_anim(&low, c) != game_current_anim(&ctl, c)) same_anim = false;
    CHECK(same_anim, "1%% 電量不會改變任何角色的動畫");
    CHECK(low.ui == ctl.ui, "1%% 電量不會改變畫面狀態");
    CHECK(game_do_action(&low, CHAR_BROWN_MIXED, ACT_FEED),
          "1%% 電量時互動照常受理");
}

static void test_celebrate(void)
{
    printf("里程碑慶祝\n");

    game_t g; boot(&g, MORNING);
    uint8_t need_before[5];
    memcpy(need_before, &g.save.chars[CHAR_BROWN_MIXED].needs, 5);

    for (uint8_t c = 0; c < CHAR_COUNT; c++) g.save.chars[c].affection = 30; /* 總 120 */
    game_tick(&g, 100);
    CHECK(g.ui == UI_CELEBRATE, "解鎖里程碑時進入慶祝畫面");
    CHECK(g.unlocked_mask & 0x01, "第一個里程碑已記錄");
    bool all_happy = true;
    for (uint8_t c = 0; c < CHAR_COUNT; c++)
        if (game_current_anim(&g, c) != ANIM_HAPPY) all_happy = false;
    CHECK(all_happy, "慶祝時四個角色都播 happy");
    CHECK(memcmp(need_before, &g.save.chars[CHAR_BROWN_MIXED].needs, 5) == 0,
          "慶祝不改任何需求值");

    /* 慶祝期間不接受選單操作，但按任何鍵可以提前結束 */
    game_button(&g, BTN_OK);
    CHECK(g.ui != UI_MENU, "慶祝期間按確認不會開選單");
    CHECK(g.ui == UI_MAIN, "按任何鍵提前結束慶祝，回到主畫面");

    /* 不按也會自己結束——不可以要求 3 歲學會「關掉這個畫面」 */
    game_t a; boot(&a, MORNING);
    for (uint8_t c = 0; c < CHAR_COUNT; c++) a.save.chars[c].affection = 30;
    game_tick(&a, 100);
    CHECK(a.ui == UI_CELEBRATE, "進入慶祝");
    uint32_t elapsed = 0;
    while (elapsed + 100 < CELEBRATE_MS) { game_tick(&a, 100); elapsed += 100; }
    CHECK(a.ui == UI_CELEBRATE, "%u ms 之內不會提早結束（已過 %u ms）",
          CELEBRATE_MS, elapsed);
    game_tick(&a, 100);
    CHECK(a.ui == UI_MAIN, "%u ms 之後自動回主畫面", CELEBRATE_MS);

    /* 已經領過的里程碑不可以每次開機重新慶祝一次 */
    save_blob_t s;
    save_set_defaults(&s, MORNING);
    s.saved_at_unix = MORNING;
    for (uint8_t c = 0; c < CHAR_COUNT; c++) s.chars[c].affection = 30;
    s.chars[CHAR_ICE_PRINCESS].unlocked_outfits = 0x03;   /* 預設 + 第一個里程碑 */
    game_t b;
    game_init(&b, &s, MORNING);
    game_boot_done(&b);
    while (b.ui == UI_BOOT) game_tick(&b, 100);
    run(&b, 2);
    CHECK(b.ui == UI_MAIN, "已經領過的里程碑不會在下次開機重新慶祝");
}

/* 閒置訪客。**規則 1 的重點是「牠不是第五個要照顧的對象」**——
   出現與離開都不能動到任何需求值，也不能在小孩正在玩的時候搶注意力。 */
static void test_visitor(void)
{
    printf("閒置訪客：小松鼠與小鳥\n");

    game_t g; boot(&g, MORNING);
    CHECK(game_visitor(&g) == VISITOR_NONE, "開機沒有訪客");

    /* 對照組：完全相同的 tick 次數，但強制不讓訪客出現。
       **兩邊必須 tick 同樣次數**，否則比的是「跑了多久」不是「有沒有訪客」。 */
    game_t q; boot(&q, MORNING);

    visitor_t seen = VISITOR_NONE;
    int16_t xmin = 32767, xmax = -32768;
    int moved = 0;
    for (uint32_t i = 0; i < 3200; i++) {
        game_tick(&g, 100);
        game_tick(&q, 100);
        q.visitor = VISITOR_NONE;
        q.visitor_ms = 100000u;
        if (game_visitor(&g) != VISITOR_NONE) {
            if (seen == VISITOR_NONE) seen = game_visitor(&g);
            int16_t x = game_visitor_x(&g);
            if (x < xmin) xmin = x;
            if (x > xmax) xmax = x;
            moved = 1;
        }
    }
    CHECK(seen == VISITOR_SQUIRREL || seen == VISITOR_BIRD,
          "閒置夠久會有訪客（%d）", (int)seen);
    CHECK(moved && xmin >= VISITOR_X_MIN && xmax <= VISITOR_X_MAX,
          "走動範圍在 %d..%d 之內（實測 %d..%d）",
          VISITOR_X_MIN, VISITOR_X_MAX, xmin, xmax);
    CHECK(xmax > xmin, "真的有在動（%d → %d）", xmin, xmax);

    /* 規則 1：訪客不動任何遊戲數值。PRNG 會不同（訪客會抽亂數，
       那只影響播哪個閒置動畫），但存檔必須逐 byte 相同。 */
    CHECK(memcmp(&g.save.chars, &q.save.chars, sizeof g.save.chars) == 0,
          "**有訪客與沒訪客，四個角色的存檔逐 byte 相同**");

    /* 剛按過鍵的安靜期內不會冒出訪客來搶注意力 */
    game_t r; boot(&r, MORNING);
    r.visitor_ms = 0;
    game_button(&r, BTN_NEXT);
    game_tick(&r, 100);
    CHECK(game_visitor(&r) == VISITOR_NONE,
          "剛按過鍵的安靜期內不會冒出訪客");
}

static void test_anim_coverage(void)
{
    printf("動畫涵蓋率：狀態機實際會用到哪些\n");

    bool used[ANIM_COUNT] = { false };
    game_t g; boot(&g, MORNING);

    action_t acts[] = { ACT_FEED, ACT_PLAY, ACT_TOILET, ACT_SLEEP, ACT_PET,
                        ACT_DRESS, ACT_BATH };
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
    test_menu_actions();
    test_suggest_action();
    test_need_bars();
    test_bath();
    test_button_ui();
    test_present_pet();
    test_away_decay();
    test_dress_screen();
    test_old_save_defaults();
    test_princess_roam();
    test_boot_screen();
    test_power_off();
    test_backlight();
    test_light_switch();
    test_battery();
    test_celebrate();
    test_needs_never_punish();
    test_post_input_quiet();
    test_idle_no_repeat();
    test_milestones();
    test_milestone_split();
    test_visitor();
    test_anim_coverage();

    printf("\n%s（%d 項失敗）\n", g_fail ? "測試失敗" : "全部通過", g_fail);
    return g_fail ? 1 : 0;
}
