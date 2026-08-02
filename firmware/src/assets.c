/* 資產包存取。結構與解碼器在 assets.h（取自 docs/04 D.7，已驗證）。
   這裡只做「把指標記下來」和幾個查表包裝。

   **零複製是刻意的。** ESP32 上 assets.bin 是 spi_flash_mmap 之後的位址，
   索引、影格表、調色盤全部可以直接當結構陣列讀，不必搬進 RAM。
   桌機模擬器把整個檔讀進記憶體再交進來，行為一樣。 */

#include "assets.h"

static const uint8_t     *s_base;
static const ipa_header_t *s_hdr;
static const ipa_asset_t  *s_assets;
static const ipa_frame_t  *s_frames;
static const uint16_t     *s_pal;

_Bool ipa_open(const uint8_t *base, size_t size)
{
    if (!base || size < sizeof(ipa_header_t)) return 0;
    const ipa_header_t *h = (const ipa_header_t *)base;
    if (memcmp(h->magic, "IPA1", 4) != 0) return 0;
    if (h->total_bytes > size) return 0;

    s_base   = base;
    s_hdr    = h;
    s_assets = (const ipa_asset_t *)(base + h->asset_offset);
    s_frames = (const ipa_frame_t *)(base + h->frame_offset);
    s_pal    = (const uint16_t *)(base + h->palette_offset);
    return 1;
}

const ipa_asset_t *ipa_asset(const char *name)
{
    if (!s_hdr) return 0;
    return ipa_find(s_assets, s_hdr->asset_count, ipa_hash(name));
}

const ipa_frame_t *ipa_frame(const ipa_asset_t *a, uint16_t k)
{
    if (!a || a->frame_count == 0) return 0;
    return &s_frames[a->frame_index + (k % a->frame_count)];
}

int ipa_pixels(const ipa_asset_t *a, uint16_t k, uint8_t *out)
{
    const ipa_frame_t *f = ipa_frame(a, k);
    if (!f || !out) return 0;
    return ipa_rle_decode(s_base + f->blob_offset, f->blob_bytes, out, a->w, a->h);
}

const uint16_t *ipa_palette(uint8_t id)
{
    if (!s_hdr || id >= s_hdr->palette_count) return 0;
    return s_pal + (size_t)id * 16u;
}

uint16_t ipa_asset_count(void)          { return s_hdr ? s_hdr->asset_count : 0; }
const ipa_asset_t *ipa_asset_at(uint16_t i)
{
    return (s_hdr && i < s_hdr->asset_count) ? &s_assets[i] : 0;
}
