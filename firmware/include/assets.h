/* ICEPET 資產包存取。**本檔的結構與解碼器由 tools/mklayout.py 之外的來源產生：
   它們原封不動取自 docs/04 D.7 節**，那一段是經過驗證的——
   從 markdown 抽出來編譯、餵真正的 assets.bin，解出來的像素和 Python 解碼器
   以及來源 PNG 三方逐位元相同。改這裡等於改規格，兩邊要一起改。 */
#ifndef ICEPET_ASSETS_H
#define ICEPET_ASSETS_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include <stdint.h>

#define IPA_MAGIC          "IPA1"
#define IPA_VERSION        1

/* header flags */
#define IPA_HF_BLOB_PER_FRAME  (1u << 0)   /* blob 的單位是每格影格 */
#define IPA_HF_BLOB_DEDUP      (1u << 1)   /* 相同的 blob 已合併 */

/* asset flags */
#define IPA_AF_LOOP            (1u << 0)

typedef enum {
    IPA_TYPE_ANIM   = 0,        /* 角色動畫 */
    IPA_TYPE_SCENE  = 1,        /* 房間底圖 */
    IPA_TYPE_OBJECT = 2,        /* 場景物件 */
} ipa_type_t;

typedef struct {                /* 48 bytes */
    char     magic[4];          /* "IPA1" */
    uint16_t version;
    uint16_t header_bytes;
    uint16_t asset_count;
    uint16_t frame_count;
    uint16_t palette_count;
    uint16_t flags;
    uint32_t asset_offset;
    uint32_t frame_offset;
    uint32_t palette_offset;
    uint32_t blob_offset;
    uint32_t blob_bytes;
    uint32_t total_bytes;
    uint32_t content_crc32;     /* 涵蓋 [header_bytes, total_bytes) */
    uint32_t reserved;
} ipa_header_t;

typedef struct {                /* 20 bytes */
    uint32_t name_hash;         /* FNV-1a 32，表照它遞增排序 */
    uint32_t frame_index;
    uint16_t frame_count;
    uint16_t w;
    uint16_t h;
    uint8_t  palette_day;
    uint8_t  palette_night;
    uint8_t  type;              /* ipa_type_t */
    uint8_t  flags;             /* IPA_AF_* */
    uint16_t reserved;
} ipa_asset_t;

typedef struct {                /* 16 bytes */
    uint32_t blob_offset;       /* 相對檔案開頭 */
    uint32_t blob_bytes;
    uint16_t ms;
    int8_t   screen_dx;
    int8_t   screen_dy;
    uint8_t  flip;              /* 像素已鏡像，不要再翻 */
    uint8_t  reserved[3];
} ipa_frame_t;

typedef struct {                /* 32 bytes */
    uint16_t rgb565[16];        /* index 0 一律是透明 */
} ipa_palette_t;

_Static_assert(sizeof(ipa_header_t)  == 48, "檔頭版面和 docs/04 D 節不符");
_Static_assert(sizeof(ipa_asset_t)   == 20, "資產表版面和 docs/04 D 節不符");
_Static_assert(sizeof(ipa_frame_t)   == 16, "影格表版面和 docs/04 D 節不符");
_Static_assert(sizeof(ipa_palette_t) == 32, "調色盤版面和 docs/04 D 節不符");

/* 名稱雜湊。和 tools/pack.py 的 fnv1a_32 必須完全一致。 */
static inline uint32_t ipa_hash(const char *s)
{
    uint32_t h = 0x811C9DC5u;
    while (*s) { h ^= (uint8_t)*s++; h *= 0x01000193u; }
    return h;
}

/* 二分搜尋。assets 是照 name_hash 遞增排序的。 */
static inline const ipa_asset_t *ipa_find(const ipa_asset_t *a, uint16_t n,
                                          uint32_t hash)
{
    uint16_t lo = 0, hi = n;
    while (lo < hi) {
        uint16_t mid = (uint16_t)((lo + hi) >> 1);
        if (a[mid].name_hash < hash)      lo = (uint16_t)(mid + 1);
        else if (a[mid].name_hash > hash) hi = mid;
        else                              return &a[mid];
    }
    return NULL;
}

/* RLE 解碼成 8bpp 索引平面（一列一列，run 不跨列）。
   回傳實際填出來的列數，不等於 h 就是資料壞了。 */
static inline int ipa_rle_decode(const uint8_t *p, uint32_t n,
                                 uint8_t *dst, int w, int h)
{
    int row = 0, col = 0;
    uint32_t i = 0;
    while (i < n && row < h) {
        uint8_t b = p[i++];
        int cnt, idx;
        if (b & 0x80) {
            idx = b & 0x0F;
            cnt = (b >> 4) & 0x07;
            if (cnt == 0) {                 /* 1000iiii + <count> */
                if (i >= n) return -1;
                cnt = p[i++];
            }
        } else {                            /* 0nnnnnnn 透明 */
            idx = 0;
            cnt = b & 0x7F;
            if (cnt == 0) return -1;
        }
        if (col + cnt > w) return -1;       /* run 跨列 = 格式壞了 */
        for (int k = 0; k < cnt; k++) dst[row * w + col + k] = (uint8_t)idx;
        col += cnt;
        if (col == w) { col = 0; row++; }
    }
    return (col == 0) ? row : -1;
}

/* ---- 以下是給渲染層用的薄包裝，不在 docs/04 裡 ---- */

/* 把整個 assets.bin 交給存取層。回傳 false 代表 magic 或 CRC 不對。
   桌機模擬器讀檔進記憶體；ESP32 上是 spi_flash_mmap 之後的指標，零複製。 */
_Bool ipa_open(const uint8_t *base, size_t size);

/* 依名字查資產。查不到回 NULL。名字見 data/assets.json。 */
const ipa_asset_t *ipa_asset(const char *name);

/* 第 k 格。k 會對 frame_count 取模，呼叫端不必自己防。 */
const ipa_frame_t *ipa_frame(const ipa_asset_t *a, uint16_t k);

/* 解一格成 8bpp 索引平面。out 要有 w*h 個 byte。回傳解出的列數。 */
int ipa_pixels(const ipa_asset_t *a, uint16_t k, uint8_t *out);

/* 第 id 份調色盤，16 個 RGB565。 */
const uint16_t *ipa_palette(uint8_t id);

uint16_t ipa_asset_count(void);
const ipa_asset_t *ipa_asset_at(uint16_t i);

#endif /* ICEPET_ASSETS_H */
