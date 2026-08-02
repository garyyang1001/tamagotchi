/* 渲染層。見 render.h 的說明。
 *
 * 三個貫穿全檔的規則：
 *
 * 1. **座標一律推算，不寫死。** 接地物件的 y = GROUND_Y − slot.dy − base_row，
 *    這條規則和 tools/scene.py 的算法逐字相同。記 [x,y] 的版本對狗成立、
 *    對公主差 40 px，那個錯已經犯過一次。
 *
 * 2. **日夜、服裝都只換調色盤，不換點陣圖。** 每個資產帶 palette_day/palette_night
 *    兩個 id，公主再額外覆寫 outfit_slots 那五格。一套服裝的成本是 10 個 byte。
 *
 * 3. **繪製時偏移不烘進資產。** happy 的跳躍、公主閒置時的走動、訪客的位移
 *    全部是畫的時候加上去的。烘進去就永遠動不了。
 */

#include <string.h>

#include "render.h"
#include "assets.h"
#include "layout.h"

#define FB_PX (SCR_W * SCR_H)

/* 一格最大的像素數：房間 320×176。角色最大 64×96。 */
static uint8_t s_px[ROOM_W * ROOM_H];

static uint32_t s_decoded, s_sprites;

/* 每個角色一個動畫時鐘。**遊戲層只說播什麼，播到第幾格是這一層的事**——
   節奏來自資產包的 frame.ms。 */
static struct {
    uint8_t  anim;
    uint16_t frame;
    uint32_t acc_ms;
} s_clk[CHAR_COUNT];

static uint32_t s_obj_ms;      /* 多影格物件共用的時鐘 */
static uint32_t s_vis_ms;

static const ipa_asset_t *s_room;

/* ---- 基本繪圖 ---------------------------------------------------- */

static void fill(uint16_t *fb, int x0, int y0, int w, int h, uint16_t c)
{
    for (int y = y0; y < y0 + h; y++) {
        if (y < 0 || y >= SCR_H) continue;
        for (int x = x0; x < x0 + w; x++) {
            if (x < 0 || x >= SCR_W) continue;
            fb[y * SCR_W + x] = c;
        }
    }
}

static void box(uint16_t *fb, int x, int y, int w, int h, int t, uint16_t c)
{
    fill(fb, x, y, w, t, c);
    fill(fb, x, y + h - t, w, t, c);
    fill(fb, x, y, t, h, c);
    fill(fb, x + w - t, y, t, h, c);
}

/* 把一格資產畫上去。pal 為 NULL 就用資產自己的調色盤。
   index 0 是透明——**整個 RLE 格式建立在這條約定上**（見 docs/04 D 節）。 */
static void blit(uint16_t *fb, const ipa_asset_t *a, uint16_t k,
                 int dx, int dy, const uint16_t *pal)
{
    if (!a) return;
    if ((size_t)a->w * a->h > sizeof s_px) return;
    if (!ipa_pixels(a, k, s_px)) return;
    if (!pal) pal = ipa_palette(a->palette_day);
    if (!pal) return;

    s_decoded += (uint32_t)a->w * a->h;
    s_sprites++;

    for (int y = 0; y < a->h; y++) {
        int fy = dy + y;
        if (fy < 0 || fy >= SCR_H) continue;
        const uint8_t *row = s_px + (size_t)y * a->w;
        uint16_t *out = fb + (size_t)fy * SCR_W;
        for (int x = 0; x < a->w; x++) {
            uint8_t i = row[x];
            if (!i) continue;                 /* index 0 = 透明 */
            int fx = dx + x;
            if (fx < 0 || fx >= SCR_W) continue;
            out[fx] = pal[i];
        }
    }
}

/* ---- 調色盤 ------------------------------------------------------ */

/* 角色的調色盤。夜間換一份，公主再覆寫服裝那五格。
   回傳的是 static 緩衝區，呼叫端要在下一次呼叫前用完。 */
static const uint16_t *char_palette(const game_t *g, uint8_t c, const ipa_asset_t *a)
{
    static uint16_t buf[16];
    _Bool night = !game_light_on(g);
    const uint16_t *src = ipa_palette(night ? a->palette_night : a->palette_day);
    if (!src) return 0;
    if (c != CHAR_ICE_PRINCESS) return src;

    uint8_t o = game_outfit(g);
    if (o == 0 || o >= OUTFIT_COUNT_GEN) return src;   /* 0 = 預設，本來就是那些色 */

    memcpy(buf, src, sizeof buf);
    for (int i = 0; i < OUTFIT_SLOTS; i++)
        buf[OUTFIT_SLOT_IDX[i]] = OUTFIT_RGB565[o][i];
    /* 夜間 + 換裝：服裝色也要跟著壓暗，否則她在暗房間裡會發光。
       用和 tools/nightpal.py 同一條仿射變換：out = day*0.45 + tint*0.55。
       那條**保序**，所以壓暗之後五套服裝仍然分得開。 */
    if (night) {
        for (int i = 0; i < OUTFIT_SLOTS; i++) {
            uint16_t v = buf[OUTFIT_SLOT_IDX[i]];
            int r = (v >> 11) & 0x1F, gg = (v >> 5) & 0x3F, b = v & 0x1F;
            r = (r * 45 + (28 >> 3) * 55) / 100;
            gg = (gg * 45 + (36 >> 2) * 55) / 100;
            b = (b * 45 + (70 >> 3) * 55) / 100;
            buf[OUTFIT_SLOT_IDX[i]] = (uint16_t)((r << 11) | (gg << 5) | b);
        }
    }
    return buf;
}

/* ---- 動畫時鐘 ---------------------------------------------------- */

static const ipa_asset_t *char_anim(const game_t *g, uint8_t c, uint16_t *out_frame)
{
    char name[48];
    uint8_t aid = (uint8_t)game_current_anim(g, c);
    if (aid >= 21) aid = 0;
    /* 名字用 CHAR_ID/ANIM_ID 組出來，不寫死——layout.h 保證順序和 enum 一致 */
    size_t n = 0;
    for (const char *p = CHAR_ID[c]; *p; p++) name[n++] = *p;
    name[n++] = '/';
    for (const char *p = ANIM_ID[aid]; *p; p++) name[n++] = *p;
    name[n] = 0;

    const ipa_asset_t *a = ipa_asset(name);
    if (!a) return 0;
    if (s_clk[c].anim != aid) {                 /* 換動畫就從第 0 格重來 */
        s_clk[c].anim = aid;
        s_clk[c].frame = 0;
        s_clk[c].acc_ms = 0;
    }
    if (out_frame) *out_frame = s_clk[c].frame;
    return a;
}

static void advance_clocks(const game_t *g, uint32_t dt)
{
    for (uint8_t c = 0; c < CHAR_COUNT; c++) {
        uint16_t k;
        const ipa_asset_t *a = char_anim(g, c, &k);
        if (!a || a->frame_count <= 1) continue;
        const ipa_frame_t *f = ipa_frame(a, k);
        uint16_t ms = f && f->ms ? f->ms : 200;
        /* 夜間動畫放慢到 0.7 倍——和 game.c 的規則一致 */
        if (!game_light_on(g)) ms = (uint16_t)(ms * 10u / 7u);
        s_clk[c].acc_ms += dt;
        while (s_clk[c].acc_ms >= ms) {
            s_clk[c].acc_ms -= ms;
            s_clk[c].frame = (uint16_t)((s_clk[c].frame + 1) % a->frame_count);
            f = ipa_frame(a, s_clk[c].frame);
            ms = f && f->ms ? f->ms : 200;
            if (!game_light_on(g)) ms = (uint16_t)(ms * 10u / 7u);
        }
    }
    s_obj_ms += dt;
    s_vis_ms += dt;
}

/* ---- 物件 -------------------------------------------------------- */

static const obj_cue_t *cue_for(uint8_t anim)
{
    for (int i = 0; i < OBJ_CUE_COUNT; i++)
        if (OBJ_CUE[i].anim == anim) return &OBJ_CUE[i];
    return 0;
}

static void draw_cue_obj(const game_t *g, uint16_t *fb, uint8_t c,
                         int slot_x, int slot_dy, int frame_h, _Bool under)
{
    const obj_cue_t *q = cue_for((uint8_t)game_current_anim(g, c));
    if (!q || q->obj < 0) return;
    if ((_Bool)q->under != under) return;

    int oid = q->obj;
    if (c == CHAR_ICE_PRINCESS && q->obj_princess >= 0) oid = q->obj_princess;
    const obj_def_t *d = &OBJ_DEF[oid];
    const ipa_asset_t *a = ipa_asset(OBJ_NAME[oid]);
    if (!a) return;

    uint16_t k = d->frames > 1 && d->ms ? (uint16_t)((s_obj_ms / d->ms) % d->frames) : 0;
    const uint16_t *pal = ipa_palette(game_light_on(g) ? a->palette_day : a->palette_night);

    if (d->mode == OBJ_FLOAT) {
        /* 不接地，沒有可以推算的基準，x/y 由人填是對的 */
        int top = GROUND_Y - (frame_h - 1) - slot_dy;
        blit(fb, a, k, slot_x + d->fx[c], top + d->fy[c], pal);
    } else if (d->mode == OBJ_GROUND) {
        if (d->ax[c] < 0) return;
        /* **y 永遠推算**：base_row 是物件自己宣告哪一列踩在地面線上 */
        blit(fb, a, k, slot_x + d->ax[c], GROUND_Y - slot_dy - d->base_row, pal);
    }
}

static void draw_fixed_objects(const game_t *g, uint16_t *fb)
{
    for (int i = 0; i < OBJ_COUNT; i++) {
        const obj_def_t *d = &OBJ_DEF[i];
        if (d->mode != OBJ_FIXED) continue;
        const ipa_asset_t *a = ipa_asset(OBJ_NAME[i]);
        if (!a) continue;

        uint16_t k = 0;
        /* 電燈開關：第 0 格撥桿在下＝燈關、第 1 格在上＝燈開。**不是時間驅動的**。 */
        if (i == OBJ_LAMP_SWITCH) {
            k = game_light_on(g) ? 1 : 0;
        } else if (i == OBJ_BATTERY) {
            power_hint_t h = game_power_hint(g);
            if (h == PWR_NONE) continue;                   /* 滿電就完全不出現 */
            k = (h == PWR_CHARGING) ? 0 : (h == PWR_CRITICAL ? 2 : 1);
        } else if (d->frames > 1 && d->ms) {
            k = (uint16_t)((s_obj_ms / d->ms) % d->frames);
        }
        blit(fb, a, k, d->at_x, d->at_y,
             ipa_palette(game_light_on(g) ? a->palette_day : a->palette_night));
    }
}

/* ---- 角色 -------------------------------------------------------- */

static void draw_character(const game_t *g, uint16_t *fb, uint8_t c,
                           int slot_x, int slot_dy)
{
    uint16_t k = 0;
    const ipa_asset_t *a = char_anim(g, c, &k);
    if (!a) return;

    int x = slot_x + game_char_x_offset(g, c);
    int top = GROUND_Y - (a->h - 1) - slot_dy;
    const ipa_frame_t *f = ipa_frame(a, k);
    if (f) { x += f->screen_dx; top += f->screen_dy; }   /* 繪製時偏移，沒烘進資產 */

    draw_cue_obj(g, fb, c, slot_x, slot_dy, a->h, 1);     /* 睡墊墊在身下 */
    blit(fb, a, k, x, top, char_palette(g, c, a));
    draw_cue_obj(g, fb, c, slot_x, slot_dy, a->h, 0);     /* 碗畫在角色之上 */

    /* 配件：掛在**逐格**頭部錨點上，所以 lie_down 頭降 32 px 時帽子跟著降。
       錨點是 tools/anchors.py 從髮色像素量出來的，不是填的。 */
    if (c == CHAR_ICE_PRINCESS) {
        uint8_t mask = game_accessory_mask(g, c);
        uint8_t aid = s_clk[c].anim;
        uint16_t fk = k < HEAD_MAX_FRAMES ? k : (uint16_t)(HEAD_MAX_FRAMES - 1);
        int hx = HEAD_ANCHOR[aid][fk][0], hy = HEAD_ANCHOR[aid][fk][1];
        for (int i = 0; i < ACC_COUNT; i++) {
            if (!(mask & (1u << i))) continue;
            const ipa_asset_t *ac = ipa_asset(ACC_NAME[i]);
            if (!ac) continue;
            blit(fb, ac, 0, x + hx + ACC_DEF[i].dx, top + hy + ACC_DEF[i].dy,
                 ipa_palette(game_light_on(g) ? ac->palette_day : ac->palette_night));
        }
    }
}

/* ---- UI ---------------------------------------------------------- */

#define C_UI_BG   0x18E3
#define C_ICON_BG 0x2124
#define C_ICON_HI 0x3186
#define C_LINE    0x6B4D
#define C_SELECT  0xC2C9   /* 和 specs/scene.json 的 select_box.color 同一個紅 */
#define C_BAR_BG  0x39C7
#define C_BAR     0xAE95

static void draw_cursor(const game_t *g, uint16_t *fb)
{
    if (g->ui != UI_MAIN) return;
    uint8_t n = game_main_slot_count(g);
    uint8_t cur = g->cursor < n ? g->cursor : 0;

    /* **兩種標記，判準是「接不接地」**——和 specs/scene.json 的 _placement 同一條。
       角色站在地上給地板箭頭；門、衣櫃、開關在牆上給紅框。 */
    if (cur == SLOT_PRINCESS || cur == SLOT_DOG) {
        const ipa_asset_t *a = ipa_asset("obj/cursor");
        if (!a) return;
        int sx = (cur == SLOT_PRINCESS) ? SLOT_ICE_PRINCESS_X : SLOT_DOG_X;
        int dy = (cur == SLOT_PRINCESS) ? SLOT_ICE_PRINCESS_DY : SLOT_DOG_DY;
        const obj_def_t *d = &OBJ_DEF[OBJ_CURSOR];
        blit(fb, a, 0, sx + d->ax[0], GROUND_Y - dy - d->base_row,
             ipa_palette(a->palette_day));
        return;
    }
    int bx, by, bw, bh;
    if (cur == SLOT_LIGHT) {
        const obj_def_t *d = &OBJ_DEF[OBJ_LAMP_SWITCH];
        bx = d->at_x; by = d->at_y; bw = d->w; bh = d->h;
    } else if (cur == SLOT_DOOR) {
        bx = 6; by = 46; bw = 40; bh = 86;          /* 門，見 specs/scene.json 的 background */
    } else {
        bx = 262; by = 46; bw = 56; bh = 86;        /* 衣櫃 */
    }
    /* **框要夾在畫面內。** 衣櫃右緣在 318，加 6 px 的 pad 就是 324——
       超出去那一邊的框線整條不見，看起來像框破了一角。 */
    int x0 = bx - UI_BOX_PAD, y0 = by - UI_BOX_PAD;
    int x1 = bx + bw + UI_BOX_PAD, y1 = by + bh + UI_BOX_PAD;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > SCR_W) x1 = SCR_W;
    if (y1 > UI_BAR_Y) y1 = UI_BAR_Y;
    box(fb, x0, y0, x1 - x0, y1 - y0, UI_BOX_THICK, C_SELECT);
}

/* 游標停在角色身上就直接顯示進度條，不必再按一次。
   **規則 1：低的時候不變紅、不閃、不倒數**，只是短一點。 */
static void draw_bars(const game_t *g, uint16_t *fb)
{
    if (g->ui != UI_MAIN) return;
    uint8_t n = game_main_slot_count(g);
    uint8_t cur = g->cursor < n ? g->cursor : 0;
    if (cur != SLOT_PRINCESS && cur != SLOT_DOG) return;
    uint8_t c = g->selected;
    if (c >= CHAR_COUNT) return;

    uint8_t v[8];
    uint8_t k = game_need_bars(g, c, v, 8);
    /* **位置固定在左上角，不跟著角色跑。** 第一版畫在角色正上方，
       公主站中間時整排條子壓在窗戶上，看起來像畫面壞掉。
       對這個年齡「每次都在同一個地方」比「靠近它講的那個角色」重要。
       底下墊一塊深色，否則淺色的牆會讓條子讀不出來。 */
    int bw = 48, bh = 5, gap = 3, x = 5, y = 5;
    fill(fb, x - 3, y - 3, bw + 6, k * (bh + gap) + 3, C_UI_BG);
    for (uint8_t i = 0; i < k; i++) {
        int yy = y + i * (bh + gap);
        fill(fb, x, yy, bw, bh, C_BAR_BG);
        fill(fb, x, yy, bw * v[i] / 100, bh, C_BAR);
    }
}

static void draw_ui_bar(const game_t *g, uint16_t *fb)
{
    fill(fb, 0, UI_BAR_Y, SCR_W, UI_BAR_H, C_UI_BG);
    _Bool menu = (g->ui == UI_MENU || g->ui == UI_CALL || g->ui == UI_DRESS);
    for (int i = 0; i < 3; i++) {
        int x = UI_ICON_X[i];
        fill(fb, x, UI_ICON_Y, UI_ICON_SIZE, UI_ICON_SIZE,
             menu ? C_ICON_HI : C_ICON_BG);
        box(fb, x, UI_ICON_Y, UI_ICON_SIZE, UI_ICON_SIZE, 1, C_LINE);
    }
    if (!menu) return;
    /* 選中的那一格加一圈紅框。**框是幾何不是 sprite**——四個 fill 比存一張
       60×60 的 4bpp 圖（1.8 KB）便宜，而且改粗細不必重烘焙。 */
    uint8_t cur = g->cursor < 3 ? g->cursor : 0;
    box(fb, UI_ICON_X[cur] - UI_BOX_PAD, UI_ICON_Y - UI_BOX_PAD,
        UI_ICON_SIZE + UI_BOX_PAD * 2, UI_ICON_SIZE + UI_BOX_PAD * 2,
        UI_BOX_THICK, C_SELECT);
}

/* ---- 主流程 ------------------------------------------------------ */

_Bool render_init(void)
{
    memset(s_clk, 0, sizeof s_clk);
    s_obj_ms = s_vis_ms = 0;
    s_room = ipa_asset("scene/room");
    if (!s_room) return 0;
    for (int i = 0; i < OBJ_COUNT; i++)
        if (!ipa_asset(OBJ_NAME[i])) return 0;
    for (int c = 0; c < CHAR_COUNT; c++)
        for (int a = 0; a < 21; a++) {
            char n[48];
            size_t k = 0;
            for (const char *p = CHAR_ID[c]; *p; p++) n[k++] = *p;
            n[k++] = '/';
            for (const char *p = ANIM_ID[a]; *p; p++) n[k++] = *p;
            n[k] = 0;
            if (!ipa_asset(n)) return 0;
        }
    return 1;
}

void render_frame(const game_t *g, uint16_t *fb, uint32_t dt_ms)
{
    s_decoded = s_sprites = 0;
    advance_clocks(g, dt_ms);

    /* 房間。日夜是同一張點陣圖換調色盤——32 個 byte 的差別，零張新圖。 */
    _Bool night = !game_light_on(g) || g->ui == UI_POWEROFF;
    blit(fb, s_room, 0, 0, 0,
         ipa_palette(night ? s_room->palette_night : s_room->palette_day));

    /* 開機：房間 + 四隻的第 0 格 + 放大 4 倍的既有愛心。**零張新圖。**
       不用純色 splash——小孩等不了空白畫面。 */
    if (g->ui == UI_BOOT) {
        /* **只有房間 + 愛心，不畫公主。** 第一版連她一起畫，放大 4 倍的愛心
           正好蓋在她臉上——開機畫面反而變成「公主被一顆紅色方塊擋住」。
           房間本身已經夠了：小孩看到房間就知道「它醒過來了」。 */
        const ipa_asset_t *h = ipa_asset("obj/heart");
        if (h) {
            const uint16_t *pal = ipa_palette(h->palette_day);
            uint16_t k = (uint16_t)((s_obj_ms / 180) % h->frame_count);
            if (ipa_pixels(h, k, s_px)) {
                int M = 4, ox = (SCR_W - h->w * M) / 2, oy = 96;
                for (int y = 0; y < h->h * M; y++)
                    for (int x = 0; x < h->w * M; x++) {
                        uint8_t i = s_px[(y / M) * h->w + (x / M)];
                        if (i) fb[(oy + y) * SCR_W + ox + x] = pal[i];
                    }
            }
        }
        return;                                  /* 開機不畫 UI 條、不畫游標 */
    }

    draw_character(g, fb, CHAR_ICE_PRINCESS, SLOT_ICE_PRINCESS_X, SLOT_ICE_PRINCESS_DY);
    if (g->present < CHAR_COUNT)
        draw_character(g, fb, g->present, SLOT_DOG_X, SLOT_DOG_DY);
    if (g->leaving < CHAR_COUNT)
        draw_character(g, fb, g->leaving, SLOT_DOG_X, SLOT_DOG_DY);

    draw_fixed_objects(g, fb);

    /* 閒置訪客。x 由遊戲層給（牠會走動），y 用 base_row 推算——和接地物件同一條。 */
    visitor_t v = game_visitor(g);
    if (v != VISITOR_NONE) {
        int vi = (int)v - 1;
        const ipa_asset_t *a = ipa_asset(VISITOR_NAME[vi]);
        if (a) {
            const visitor_def_t *d = &VISITOR_DEF[vi];
            uint16_t k = (uint16_t)((s_vis_ms / (d->ms ? d->ms : 200)) % d->frames);
            blit(fb, a, k, game_visitor_x(g), GROUND_Y - d->base_row,
                 ipa_palette(game_light_on(g) ? a->palette_day : a->palette_night));
        }
    }

    if (g->ui == UI_POWEROFF) return;            /* 關機只有房間 + 睡著的角色 */

    /* 里程碑：星星浮在頭上。和愛心一樣是 float——不接地，y 由人填是對的。 */
    if (g->ui == UI_CELEBRATE) {
        const ipa_asset_t *st = ipa_asset("obj/star");
        const obj_def_t *d = &OBJ_DEF[OBJ_STAR];
        if (st) {
            uint16_t k = (uint16_t)((s_obj_ms / (d->ms ? d->ms : 150)) % d->frames);
            int top = GROUND_Y - 95 - SLOT_ICE_PRINCESS_DY;
            blit(fb, st, k, SLOT_ICE_PRINCESS_X + d->fx[CHAR_ICE_PRINCESS],
                 top + d->fy[CHAR_ICE_PRINCESS], ipa_palette(st->palette_day));
        }
    }

    draw_cursor(g, fb);
    draw_bars(g, fb);
    draw_ui_bar(g, fb);
}

void render_stats(uint32_t *px, uint32_t *sp)
{
    if (px) *px = s_decoded;
    if (sp) *sp = s_sprites;
}
