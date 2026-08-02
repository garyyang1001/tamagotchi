/*
 * save.h — 存檔層
 *
 * 掉電安全的 A/B 雙槽存檔。設計理由見 docs/04_資料結構與存檔設計.md。
 *
 * 核心保證：任何時間點斷電，最多損失自上次存檔以來的進度，
 * 絕不會出現「存檔毀損、養成進度歸零」。這台機器會被小孩隨手拔電。
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* 角色                                                                */
/* ------------------------------------------------------------------ */

typedef enum {
    CHAR_ICE_PRINCESS  = 0,
    CHAR_CHIHUAHUA     = 1,
    CHAR_BROWN_MIXED   = 2,
    CHAR_BRINDLE_GUARD = 3,
    CHAR_COUNT         = 4
} character_id_t;

/* 需求值一律 0..100 */
typedef struct __attribute__((packed)) {
    uint8_t hunger;   /* 飽足 */
    uint8_t energy;   /* 精力 */
    uint8_t mood;     /* 心情 */
    uint8_t bladder;  /* 如廁，僅狗使用 */
    uint8_t tidy;     /* 整潔。2026-08-02 加了洗澡之後四個角色都用 */
} needs_t;

typedef struct __attribute__((packed)) {
    needs_t  needs;
    uint16_t affection;        /* 只增不減。這是「我們相處了多久」，不是分數 */
    uint8_t  outfit_id;        /* 僅公主。id = unlocked_outfits 的 bit 位置 */
    uint8_t  unlocked_outfits; /* bitmask，bit 0 = 一開始就有的預設服裝 */
    uint8_t  reserved[4];
} character_save_t;

/* character_save_t.reserved[] 的欄位配置。
   契約與 save_blob_t.reserved[] 一樣：**每一格的 0 都必須是合理的預設**，
   舊存檔沒寫過這些 byte，新韌體一律讀到 0。 */

/* reserved[0] — 配件 bitmask。0 = 沒有配件，正好是「還沒挑過」的預設。
   配件清單尚未定案，韌體只留 API 掛鉤（game_accessory_mask）。 */
#define CHAR_RSV_ACCESSORY 0

/* ------------------------------------------------------------------ */
/* 存檔                                                                */
/* ------------------------------------------------------------------ */

#define SAVE_MAGIC   0x31544550u  /* "PET1" */
#define SAVE_VERSION 1
#define SAVE_SLOT_SZ 4096         /* 一個 flash sector */

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;              /* 本結構實際大小，供版本遷移判斷 */
    uint32_t seq;               /* 單調遞增，開機時取較大且 CRC 正確者 */
    uint64_t saved_at_unix;

    character_save_t chars[CHAR_COUNT];

    uint32_t total_feeds;
    uint32_t total_plays;
    uint32_t total_pets;
    uint32_t first_boot_unix;   /* 「我們認識多久了」的起算點 */

    uint8_t  brightness;
    uint8_t  volume;
    uint8_t  night_start_hour;  /* 預設 20 */
    uint8_t  night_end_hour;    /* 預設 7  */

    /* 加新欄位時從這裡切，不必遞增 SAVE_VERSION：
       舊韌體讀新檔會忽略，新韌體讀舊檔會得到 0。雙向相容。

       用法見下方 SAVE_RSV_*。**每一格的 0 都必須代表「舊韌體的行為」**，
       這是 reserved 免遷移的唯一前提。 */
    uint8_t  reserved[32];

    uint32_t crc32;             /* 涵蓋本結構前面所有位元組 */
} save_blob_t;

/* ------------------------------------------------------------------ */
/* reserved[] 的欄位配置                                                */
/* ------------------------------------------------------------------ */

/* reserved[0] — 電燈開關。**存的是 light_off，不是 light_on。**
 *
 * 這個方向不是風格問題，是相容性問題。reserved 的契約是
 * 「新韌體讀舊檔會得到 0」，所以 0 必須代表舊韌體的行為，
 * 而舊韌體沒有電燈這個概念、螢幕永遠是亮的 = 燈開著。
 *
 * 如果存成 light_on，既有機器升級之後讀到 0 就是「燈關」，
 * 小孩一開機看到的是全黑的房間，而且她不知道那是可以按回來的。
 * 存成 light_off 就沒有這個問題，**因此也不需要遞增 SAVE_VERSION**——
 * 舊檔在新韌體下的語意是正確的，不是「被容忍的」。
 *
 * 值：0 = 燈開（預設）、1 = 燈關。 */
#define SAVE_RSV_LIGHT_OFF 0

/* ------------------------------------------------------------------ */
/* 離線衰減                                                            */
/* ------------------------------------------------------------------ */

/* 不管離開多久，任何需求的離線衰減都不會低於這個值。
 *
 * 這不是平衡數值，是產品原則：小孩隔一週回來，看到的必須是
 * 「有點想你」的角色，不是奄奄一息的角色。改這個值前先讀
 * docs/02_遊戲設計.md 的「沒有失敗狀態」。 */
#define OFFLINE_DECAY_FLOOR 30

/* 離線超過這個時數後，衰減速率減半 */
#define OFFLINE_SLOWDOWN_HOURS 8

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */

typedef enum {
    SAVE_OK = 0,
    SAVE_ERR_NO_PARTITION,   /* 找不到 savegame 分區 */
    SAVE_ERR_IO,             /* flash 讀寫失敗 */
    SAVE_ERR_VERIFY,         /* 寫入後讀回驗證失敗 */
    SAVE_ERR_FUTURE_VERSION, /* 存檔版本比韌體新，拒絕載入以免誤讀 */
} save_err_t;

/* 掛載 savegame 分區。其餘 API 之前必須先呼叫。 */
save_err_t save_init(void);

/* 載入存檔。兩槽皆無效時填入出廠預設並回傳 SAVE_OK。
 * out_was_fresh 回報是否為首次開機（可為 NULL）。 */
save_err_t save_load(save_blob_t *out, bool *out_was_fresh);

/* 寫入較舊的那一槽，寫完讀回驗證。
 * blob 的 seq / crc32 / saved_at_unix 由本函式填寫，呼叫端不必處理。 */
save_err_t save_commit(save_blob_t *blob, uint64_t now_unix);

/* 出廠預設：四個角色狀態良好。不是全部歸零。 */
void save_set_defaults(save_blob_t *out, uint64_t now_unix);

/* 依離線時間套用衰減，含 OFFLINE_DECAY_FLOOR 下限。
 * now_unix 早於 saved_at_unix 時（RTC 亂掉）視為沒有經過時間。
 * 回傳實際計算的離線秒數。 */
uint32_t save_apply_offline_decay(save_blob_t *blob, uint64_t now_unix);

/* 供測試與事件記錄共用 */
uint32_t save_crc32(const void *data, size_t len);

#ifdef __cplusplus
}
#endif
