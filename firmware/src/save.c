/*
 * save.c — 存檔層實作
 *
 * A/B 雙槽 + CRC32。寫入永遠針對「較舊」的那一槽，
 * 所以寫到一半掉電時，較新的那一槽仍然完整。
 */

#include "save.h"

#include <string.h>

#include "esp_log.h"
#include "esp_partition.h"

static const char *TAG = "save";

static const esp_partition_t *s_part = NULL;

#define SLOT_A_OFF 0
#define SLOT_B_OFF SAVE_SLOT_SZ

_Static_assert(sizeof(save_blob_t) <= SAVE_SLOT_SZ,
               "save_blob_t 超過一個 sector，需要重新規劃分區");

/* ------------------------------------------------------------------ */
/* CRC32（IEEE 802.3，反射式，與 zlib 相同）                            */
/* ------------------------------------------------------------------ */

uint32_t save_crc32(const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFu;

    while (len--) {
        crc ^= *p++;
        for (int i = 0; i < 8; i++) {
            uint32_t mask = -(crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return ~crc;
}

/* crc32 欄位本身不參與計算 */
static size_t crc_span(void)
{
    return sizeof(save_blob_t) - sizeof(uint32_t);
}

static bool blob_valid(const save_blob_t *b)
{
    if (b->magic != SAVE_MAGIC) return false;
    if (b->size == 0 || b->size > sizeof(save_blob_t)) return false;
    return b->crc32 == save_crc32(b, crc_span());
}

/* ------------------------------------------------------------------ */
/* 初始化                                                              */
/* ------------------------------------------------------------------ */

save_err_t save_init(void)
{
    s_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                      ESP_PARTITION_SUBTYPE_ANY, "savegame");
    if (!s_part) {
        ESP_LOGE(TAG, "找不到 savegame 分區，檢查 partitions.csv");
        return SAVE_ERR_NO_PARTITION;
    }
    if (s_part->size < 2 * SAVE_SLOT_SZ) {
        ESP_LOGE(TAG, "savegame 分區太小：%" PRIu32 " < %d",
                 s_part->size, 2 * SAVE_SLOT_SZ);
        return SAVE_ERR_NO_PARTITION;
    }
    return SAVE_OK;
}

/* ------------------------------------------------------------------ */
/* 出廠預設                                                            */
/* ------------------------------------------------------------------ */

void save_set_defaults(save_blob_t *out, uint64_t now_unix)
{
    memset(out, 0, sizeof(*out));

    out->magic   = SAVE_MAGIC;
    out->version = SAVE_VERSION;
    out->size    = sizeof(save_blob_t);
    out->seq     = 1;
    out->saved_at_unix   = now_unix;
    out->first_boot_unix = (uint32_t)now_unix;

    /* 出廠是「四個角色都過得不錯」。
       不要改成 0 或 50——第一次開機看到的角色應該是開心的。 */
    for (int i = 0; i < CHAR_COUNT; i++) {
        out->chars[i].needs.hunger  = 80;
        out->chars[i].needs.energy  = 80;
        out->chars[i].needs.mood    = 85;
        out->chars[i].needs.bladder = 80;
        out->chars[i].needs.tidy    = 80;
        out->chars[i].affection     = 0;
        out->chars[i].outfit_id     = 0;
        out->chars[i].unlocked_outfits = 0x01;  /* 預設服裝一開始就有 */
    }

    out->brightness       = 200;
    out->volume           = 160;
    out->night_start_hour = 20;
    out->night_end_hour   = 7;
}

/* ------------------------------------------------------------------ */
/* 載入                                                                */
/* ------------------------------------------------------------------ */

static bool read_slot(size_t off, save_blob_t *out)
{
    if (esp_partition_read(s_part, off, out, sizeof(*out)) != ESP_OK) {
        return false;
    }
    return blob_valid(out);
}

/* 舊版存檔的欄位補齊。目前只有 v1，之後每加一版就在這裡接一段。 */
static void migrate(save_blob_t *b)
{
    if (b->size < sizeof(save_blob_t)) {
        /* 新韌體讀舊檔：舊檔沒有的尾端欄位補 0。
           reserved[] 的設計讓這件事在多數情況下不需要動 version。 */
        memset((uint8_t *)b + b->size, 0, sizeof(save_blob_t) - b->size);
        b->size = sizeof(save_blob_t);
        ESP_LOGI(TAG, "存檔已由舊版結構補齊");
    }
    b->version = SAVE_VERSION;
}

save_err_t save_load(save_blob_t *out, bool *out_was_fresh)
{
    if (!s_part) return SAVE_ERR_NO_PARTITION;

    save_blob_t a, b;
    bool ok_a = read_slot(SLOT_A_OFF, &a);
    bool ok_b = read_slot(SLOT_B_OFF, &b);

    const save_blob_t *pick = NULL;
    if (ok_a && ok_b) pick = (a.seq >= b.seq) ? &a : &b;
    else if (ok_a)    pick = &a;
    else if (ok_b)    pick = &b;

    if (!pick) {
        ESP_LOGW(TAG, "兩槽皆無效，套用出廠預設");
        save_set_defaults(out, 0);
        if (out_was_fresh) *out_was_fresh = true;
        return SAVE_OK;
    }

    /* 存檔比韌體新：拒絕載入而不是誤讀。
       降級燒錄時這一步保住小孩的進度。 */
    if (pick->version > SAVE_VERSION) {
        ESP_LOGE(TAG, "存檔版本 %u 比韌體 %u 新，拒絕載入",
                 pick->version, SAVE_VERSION);
        return SAVE_ERR_FUTURE_VERSION;
    }

    *out = *pick;
    migrate(out);
    if (out_was_fresh) *out_was_fresh = false;

    ESP_LOGI(TAG, "載入存檔 seq=%" PRIu32 " 槽=%c",
             out->seq, (pick == &a) ? 'A' : 'B');
    return SAVE_OK;
}

/* ------------------------------------------------------------------ */
/* 寫入                                                                */
/* ------------------------------------------------------------------ */

save_err_t save_commit(save_blob_t *blob, uint64_t now_unix)
{
    if (!s_part) return SAVE_ERR_NO_PARTITION;

    /* 決定寫哪一槽：永遠寫較舊的那個 */
    save_blob_t a, b;
    bool ok_a = read_slot(SLOT_A_OFF, &a);
    bool ok_b = read_slot(SLOT_B_OFF, &b);

    size_t   target;
    uint32_t newest_seq = 0;

    if (ok_a && ok_b) {
        target     = (a.seq >= b.seq) ? SLOT_B_OFF : SLOT_A_OFF;
        newest_seq = (a.seq >= b.seq) ? a.seq : b.seq;
    } else if (ok_a) {
        target = SLOT_B_OFF; newest_seq = a.seq;
    } else if (ok_b) {
        target = SLOT_A_OFF; newest_seq = b.seq;
    } else {
        target = SLOT_A_OFF; newest_seq = 0;
    }

    blob->magic         = SAVE_MAGIC;
    blob->version       = SAVE_VERSION;
    blob->size          = sizeof(save_blob_t);
    blob->seq           = newest_seq + 1;
    blob->saved_at_unix = now_unix;
    blob->crc32         = save_crc32(blob, crc_span());

    if (esp_partition_erase_range(s_part, target, SAVE_SLOT_SZ) != ESP_OK) {
        return SAVE_ERR_IO;
    }
    if (esp_partition_write(s_part, target, blob, sizeof(*blob)) != ESP_OK) {
        return SAVE_ERR_IO;
    }

    /* 讀回驗證。失敗時不算數，下次開機仍會讀到另一槽的舊存檔。 */
    save_blob_t back;
    if (!read_slot(target, &back) || back.seq != blob->seq) {
        ESP_LOGE(TAG, "寫入驗證失敗，槽=%c",
                 (target == SLOT_A_OFF) ? 'A' : 'B');
        return SAVE_ERR_VERIFY;
    }

    ESP_LOGD(TAG, "存檔 seq=%" PRIu32 " 槽=%c",
             blob->seq, (target == SLOT_A_OFF) ? 'A' : 'B');
    return SAVE_OK;
}

/* ------------------------------------------------------------------ */
/* 離線衰減                                                            */
/* ------------------------------------------------------------------ */

/* 每小時的衰減量，見 docs/02_遊戲設計.md */
static const uint8_t DECAY_PER_HOUR[5] = {
    4,  /* hunger  */
    3,  /* energy  */
    3,  /* mood    */
    5,  /* bladder */
    2,  /* tidy    */
};

static uint8_t decay_one(uint8_t cur, uint32_t amount)
{
    if (cur <= OFFLINE_DECAY_FLOOR) {
        return cur;                       /* 已經在下限之下就不再往下掉 */
    }
    if (amount >= (uint32_t)(cur - OFFLINE_DECAY_FLOOR)) {
        return OFFLINE_DECAY_FLOOR;       /* 最多掉到下限為止 */
    }
    return (uint8_t)(cur - amount);
}

uint32_t save_apply_offline_decay(save_blob_t *blob, uint64_t now_unix)
{
    /* RTC 亂掉導致「現在」早於存檔時間時，當作沒有經過時間。
       否則 uint64 相減會回繞成天文數字，把所有需求打到下限。 */
    if (now_unix <= blob->saved_at_unix) {
        return 0;
    }

    uint64_t elapsed = now_unix - blob->saved_at_unix;
    uint32_t hours   = (uint32_t)(elapsed / 3600u);
    if (hours == 0) {
        return (uint32_t)elapsed;
    }

    /* 超過 OFFLINE_SLOWDOWN_HOURS 之後速率減半 */
    uint32_t effective_hours = hours;
    if (hours > OFFLINE_SLOWDOWN_HOURS) {
        effective_hours = OFFLINE_SLOWDOWN_HOURS +
                          (hours - OFFLINE_SLOWDOWN_HOURS) / 2u;
    }

    for (int i = 0; i < CHAR_COUNT; i++) {
        uint8_t *n = (uint8_t *)&blob->chars[i].needs;
        for (int k = 0; k < 5; k++) {
            n[k] = decay_one(n[k], effective_hours * DECAY_PER_HOUR[k]);
        }
        /* energy 在離線期間如果跨越夜間會回補，但那需要時區與作息資訊，
           留給遊戲層在載入後依 night_start/end 補算，這裡只做單純衰減。 */
    }

    return (uint32_t)elapsed;
}
