/*
 * test_save.c — 存檔層的主機端測試
 *
 *   cc -I include -I test/stubs -o /tmp/test_save src/save.c test/test_save.c && /tmp/test_save
 *
 * 重點驗證：
 *   1. 離線衰減永遠不會跌破 OFFLINE_DECAY_FLOOR（不管離開多久）
 *   2. RTC 回跳時不會因為 uint64 回繞把角色打到下限
 *   3. A/B 雙槽在寫入中途掉電時，舊存檔仍可讀
 *   4. 版本比韌體新的存檔會被拒絕而不是誤讀
 */

#include "save.h"

#include "esp_partition.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- 假 flash ---------------------------------------------------- */

uint8_t fake_flash[8192];
int     fake_fail_after_bytes = -1;

static const esp_partition_t s_fake = { .label = "savegame", .size = sizeof(fake_flash) };

const esp_partition_t *esp_partition_find_first(esp_partition_type_t t,
                                                esp_partition_subtype_t s,
                                                const char *label)
{
    (void)t; (void)s;
    return (label && strcmp(label, "savegame") == 0) ? &s_fake : NULL;
}

esp_err_t esp_partition_read(const esp_partition_t *p, size_t off, void *dst, size_t n)
{
    (void)p;
    if (off + n > sizeof(fake_flash)) return ESP_FAIL;
    memcpy(dst, fake_flash + off, n);
    return ESP_OK;
}

esp_err_t esp_partition_write(const esp_partition_t *p, size_t off, const void *src, size_t n)
{
    (void)p;
    if (off + n > sizeof(fake_flash)) return ESP_FAIL;
    /* 模擬掉電：只寫入一部分就中斷 */
    size_t writable = n;
    if (fake_fail_after_bytes >= 0 && (size_t)fake_fail_after_bytes < n) {
        writable = (size_t)fake_fail_after_bytes;
    }
    memcpy(fake_flash + off, src, writable);
    return (writable == n) ? ESP_OK : ESP_FAIL;
}

esp_err_t esp_partition_erase_range(const esp_partition_t *p, size_t off, size_t n)
{
    (void)p;
    if (off + n > sizeof(fake_flash)) return ESP_FAIL;
    memset(fake_flash + off, 0xFF, n);
    return ESP_OK;
}

/* ---- 測試框架 ---------------------------------------------------- */

static int g_fail = 0;

#define CHECK(cond, ...)                                            \
    do {                                                            \
        if (!(cond)) {                                              \
            printf("  ✗ "); printf(__VA_ARGS__); printf("\n");      \
            g_fail++;                                               \
        } else {                                                    \
            printf("  ✓ "); printf(__VA_ARGS__); printf("\n");      \
        }                                                           \
    } while (0)

#define HOUR 3600ull
#define DAY  (24ull * HOUR)

/* ---- 測試 -------------------------------------------------------- */

static void test_decay_floor(void)
{
    printf("離線衰減下限\n");

    /* 離開一年 */
    save_blob_t b;
    save_set_defaults(&b, 1000);
    b.saved_at_unix = 1000;
    save_apply_offline_decay(&b, 1000 + 365 * DAY);

    for (int i = 0; i < CHAR_COUNT; i++) {
        const uint8_t *n = (const uint8_t *)&b.chars[i].needs;
        for (int k = 0; k < 5; k++) {
            CHECK(n[k] >= OFFLINE_DECAY_FLOOR,
                  "角色%d 需求%d = %u >= 下限 %d（離開一年）",
                  i, k, n[k], OFFLINE_DECAY_FLOOR);
        }
    }

    /* affection 不可因離線而減少 */
    save_blob_t c;
    save_set_defaults(&c, 1000);
    c.chars[1].affection = 500;
    c.saved_at_unix = 1000;
    save_apply_offline_decay(&c, 1000 + 30 * DAY);
    CHECK(c.chars[1].affection == 500, "affection 離線 30 天後仍為 500");
}

static void test_decay_short(void)
{
    printf("短期衰減\n");

    save_blob_t b;
    save_set_defaults(&b, 1000);
    b.saved_at_unix = 1000;
    uint8_t before = b.chars[0].needs.hunger;   /* 80 */
    save_apply_offline_decay(&b, 1000 + 5 * HOUR);

    /* 5 小時 × 每小時 4 = 20 → 80 - 20 = 60 */
    CHECK(b.chars[0].needs.hunger == 60,
          "5 小時後 hunger = %u（預期 60，起始 %u）",
          b.chars[0].needs.hunger, before);

    /* 未滿一小時不衰減 */
    save_blob_t c;
    save_set_defaults(&c, 1000);
    c.saved_at_unix = 1000;
    save_apply_offline_decay(&c, 1000 + 1800);
    CHECK(c.chars[0].needs.hunger == 80, "30 分鐘後 hunger 不變");
}

static void test_decay_slowdown(void)
{
    printf("超過 8 小時後速率減半\n");

    save_blob_t b;
    save_set_defaults(&b, 1000);
    b.saved_at_unix = 1000;
    save_apply_offline_decay(&b, 1000 + 24 * HOUR);

    /* 有效時數 = 8 + (24-8)/2 = 16；mood 每小時 3 → 48
       起始 85，85-48 = 37，仍在下限之上 */
    CHECK(b.chars[0].needs.mood == 37,
          "24 小時後 mood = %u（預期 37）", b.chars[0].needs.mood);
}

static void test_rtc_backwards(void)
{
    printf("RTC 回跳\n");

    save_blob_t b;
    save_set_defaults(&b, 1000000);
    b.saved_at_unix = 1000000;

    /* 「現在」比存檔時間還早。uint64 相減會回繞成天文數字，
       若沒有防護，所有需求會被一次打到下限。 */
    uint32_t elapsed = save_apply_offline_decay(&b, 500000);

    CHECK(elapsed == 0, "回跳時 elapsed = %u（預期 0）", elapsed);
    CHECK(b.chars[0].needs.hunger == 80,
          "回跳時 hunger 不變 = %u（預期 80）", b.chars[0].needs.hunger);
}

static void test_ab_slots(void)
{
    printf("A/B 雙槽與掉電\n");

    memset(fake_flash, 0xFF, sizeof(fake_flash));
    fake_fail_after_bytes = -1;
    CHECK(save_init() == SAVE_OK, "save_init 成功");

    /* 首次載入：兩槽皆空 → 出廠預設 */
    save_blob_t b;
    bool fresh = false;
    CHECK(save_load(&b, &fresh) == SAVE_OK, "首次載入回傳 OK");
    CHECK(fresh == true, "首次載入標記為 fresh");
    CHECK(b.chars[0].needs.mood == 85, "出廠預設 mood = 85（角色是開心的）");

    /* 寫入三次，seq 遞增 */
    for (int i = 0; i < 3; i++) {
        b.chars[2].affection = (uint16_t)(100 * (i + 1));
        CHECK(save_commit(&b, 2000 + i) == SAVE_OK, "第 %d 次存檔成功", i + 1);
    }

    save_blob_t r;
    CHECK(save_load(&r, &fresh) == SAVE_OK, "重新載入成功");
    CHECK(fresh == false, "非首次開機");
    CHECK(r.chars[2].affection == 300,
          "讀到最新的 affection = %u（預期 300）", r.chars[2].affection);
    CHECK(r.seq == 3, "seq = %u（預期 3）", r.seq);

    /* 模擬寫入中途掉電 */
    uint16_t good_affection = r.chars[2].affection;
    r.chars[2].affection = 9999;
    fake_fail_after_bytes = 16;              /* 只寫進 16 bytes 就斷電 */
    save_err_t e = save_commit(&r, 3000);
    fake_fail_after_bytes = -1;
    CHECK(e != SAVE_OK, "掉電的寫入回報失敗（%d）", e);

    /* 關鍵：舊存檔必須還在 */
    save_blob_t after;
    CHECK(save_load(&after, &fresh) == SAVE_OK, "掉電後仍能載入");
    CHECK(after.chars[2].affection == good_affection,
          "掉電後讀回舊存檔 affection = %u（預期 %u，未被毀損）",
          after.chars[2].affection, good_affection);
}

static void test_future_version(void)
{
    printf("拒絕未來版本的存檔\n");

    memset(fake_flash, 0xFF, sizeof(fake_flash));
    save_init();

    save_blob_t b;
    save_set_defaults(&b, 1000);
    save_commit(&b, 1000);

    /* 手動把槽內的 version 改成未來版本並重算 CRC */
    save_blob_t *slot = (save_blob_t *)fake_flash;
    slot->version = SAVE_VERSION + 1;
    slot->crc32 = save_crc32(slot, sizeof(save_blob_t) - sizeof(uint32_t));

    save_blob_t out;
    CHECK(save_load(&out, NULL) == SAVE_ERR_FUTURE_VERSION,
          "未來版本的存檔被拒絕，不會誤讀");
}

static void test_struct_size(void)
{
    printf("結構大小\n");
    printf("    save_blob_t      = %zu bytes\n", sizeof(save_blob_t));
    printf("    character_save_t = %zu bytes\n", sizeof(character_save_t));
    CHECK(sizeof(save_blob_t) <= SAVE_SLOT_SZ,
          "save_blob_t (%zu) 放得進一個 sector (%d)",
          sizeof(save_blob_t), SAVE_SLOT_SZ);
}

int main(void)
{
    test_struct_size();
    test_decay_floor();
    test_decay_short();
    test_decay_slowdown();
    test_rtc_backwards();
    test_ab_slots();
    test_future_version();

    printf("\n%s（%d 項失敗）\n", g_fail ? "測試失敗" : "全部通過", g_fail);
    return g_fail ? 1 : 0;
}
