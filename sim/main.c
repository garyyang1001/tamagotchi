/* 桌機模擬器。**這是整個專案裡唯一一個桌機專屬的檔。**
 *
 * 它只做三件事：把 assets.bin 讀進記憶體、把鍵盤變成三顆按鍵、
 * 把渲染層畫好的 RGB565 畫格推到視窗上。
 * 遊戲邏輯（game.c）、渲染層（render.c）、資產存取（assets.c）
 * 和燒進 ESP32 的是同一份原始碼。
 *
 * 為什麼先做桌機：渲染層的錯（座標、調色盤、RLE 解碼）在這裡五分鐘看得到，
 * 燒到板子上要五分鐘一輪。這裡跑通之後，ESP32 那一層只剩 SPI LCD 驅動
 * 和按鍵 GPIO——遊戲和畫面都不必再動。
 *
 * 編譯與執行：
 *     make -C sim run
 *
 * 按鍵：
 *     ←  / A       上一個
 *     空白 / Enter  確認
 *     →  / D       下一個
 *     P            電源鍵（長按 = 關機；關機途中按三顆鍵任一顆可取消）
 *     B            切電量：滿 → 充電中 → 20% → 5% → 滿
 *     N            快轉一小時（看夜間、看需求衰減）
 *     V            立刻叫一隻訪客進來（平常要閒置好幾分鐘）
 *     1..5         直接換第 n 套服裝（測調色盤置換）
 *     0            切換全部配件
 *     S            存一張 PNG 到 build/sim_shot.ppm
 *     ESC / Q      離開
 */

#include <SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "game.h"
#include "save.h"
#include "render.h"
#include "assets.h"
#include "layout.h"

#define SCALE 3

/* save.c 需要 esp_partition，桌機上給一組假的：模擬器不存檔。
   這是刻意的——每次啟動都從出廠預設開始，測試才可重現。 */
#include "esp_partition.h"
uint8_t fake_flash[8192];
int fake_fail_after_bytes = -1;
const esp_partition_t *esp_partition_find_first(esp_partition_type_t t,
                                                esp_partition_subtype_t s,
                                                const char *l)
{ (void)t; (void)s; (void)l; return 0; }
esp_err_t esp_partition_read(const esp_partition_t *p, size_t o, void *d, size_t n)
{ (void)p; (void)o; (void)d; (void)n; return -1; }
esp_err_t esp_partition_write(const esp_partition_t *p, size_t o, const void *d, size_t n)
{ (void)p; (void)o; (void)d; (void)n; return -1; }
esp_err_t esp_partition_erase_range(const esp_partition_t *p, size_t o, size_t n)
{ (void)p; (void)o; (void)n; return -1; }

static uint16_t fb[SCR_W * SCR_H];
static uint32_t rgba[SCR_W * SCR_H];

/* RGB565 → RGBA8888，並套上背光亮度。
   背光是真的會發生的事（60 秒降 50%），在這裡模擬才看得出它對畫面的影響。 */
static void to_rgba(uint8_t backlight)
{
    for (int i = 0; i < SCR_W * SCR_H; i++) {
        uint16_t v = fb[i];
        unsigned r = ((v >> 11) & 0x1F) * 255 / 31;
        unsigned g = ((v >> 5) & 0x3F) * 255 / 63;
        unsigned b = (v & 0x1F) * 255 / 31;
        r = r * backlight / 100; g = g * backlight / 100; b = b * backlight / 100;
        rgba[i] = 0xFF000000u | (r << 16) | (g << 8) | b;
    }
}

static void shot(const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) { printf("寫不了 %s\n", path); return; }
    fprintf(f, "P6\n%d %d\n255\n", SCR_W, SCR_H);
    for (int i = 0; i < SCR_W * SCR_H; i++) {
        uint32_t c = rgba[i];
        fputc((c >> 16) & 0xFF, f); fputc((c >> 8) & 0xFF, f); fputc(c & 0xFF, f);
    }
    fclose(f);
    printf("→ %s\n", path);
}

static const char *UI_NAME[] = { "主畫面", "動作選單", "呼叫", "換裝",
                                 "開機", "關機", "慶祝" };

static void run_ms(game_t *g, uint32_t ms)
{
    for (uint32_t i = 0; i < ms; i += 16) {
        game_tick(g, 16);
        memset(fb, 0, sizeof fb);
        render_frame(g, fb, 16);
    }
}

static void snap(game_t *g, const char *dir, const char *name)
{
    char path[256];
    snprintf(path, sizeof path, "%s/%s.ppm", dir, name);
    memset(fb, 0, sizeof fb);
    render_frame(g, fb, 0);
    to_rgba(game_backlight(g));
    shot(path);
}

/* 走到指定的游標位。**不要用相對按鍵數**——游標格數會隨「有沒有狗在場」改變，
   數按鍵的版本第一次跑就整串漂掉，後面每張圖框的都是錯的東西。 */
static void goto_slot(game_t *g, uint8_t slot)
{
    for (int i = 0; i < 8 && g->cursor != slot; i++) game_button(g, BTN_NEXT);
}

static int headless(game_t *g, const char *dir)
{
    printf("無視窗自檢 → %s\n", dir);
    /* **先把開機畫面走完。** 第一版只跑 500 ms，但 BOOT_MIN_MS 是 1200——
       前三張全是開機圖，而且按鍵在 BOOT 期間被吃掉，後面整串游標位置都錯了。 */
    snap(g, dir, "00_boot");
    while (g->ui == UI_BOOT) run_ms(g, 100);
    run_ms(g, 300);
    snap(g, dir, "01_main");

    /* 牆上那三格各拍一張：紅框之外還要看得到「按下去會怎樣」的提示圖示。
       衣櫃那張是**夾在畫面內**的證據——它貼著右緣（x262-318）。 */
    goto_slot(g, SLOT_DOOR);
    snap(g, dir, "01b_cursor_door");
    goto_slot(g, SLOT_WARDROBE);
    snap(g, dir, "01c_cursor_wardrobe");

    goto_slot(g, SLOT_LIGHT);
    snap(g, dir, "02_cursor_light");

    game_button(g, BTN_OK);                /* 關燈 */
    run_ms(g, 400);
    /* 燈關著時提示要換成第 1 格（「按下去會開燈」），和牆上的開關相反 */
    snap(g, dir, "03_light_off");
    game_button(g, BTN_OK);                /* 開燈 */
    run_ms(g, 400);

    goto_slot(g, SLOT_PRINCESS);
    snap(g, dir, "04_cursor_princess");
    game_button(g, BTN_OK);                /* 進互動選單 */
    printf("  進選單：ui=%u，%u 個動作\n", g->ui, g->menu_n);
    snap(g, dir, "05_menu");
    game_button(g, BTN_NEXT);              /* 選第二個圖示 */
    snap(g, dir, "05b_menu_move");
    game_button(g, BTN_OK);                /* 執行 */
    printf("  執行完 ui=%u（應該回 0 主畫面）\n", g->ui);
    run_ms(g, 900);
    snap(g, dir, "06_action");

    /* 呼叫一隻狗 */
    goto_slot(g, SLOT_DOOR);
    game_button(g, BTN_OK);
    printf("  呼叫選單：ui=%u\n", g->ui);
    snap(g, dir, "06b_call");              /* 三格＝三隻狗的 idle_breathe 第 0 格 */
    game_button(g, BTN_OK);                /* 選第一隻 */
    printf("  在場 = %u\n", g->present);
    run_ms(g, 1400);
    /* 游標停在狗身上：進度條的第四條要是 bladder，不是公主那條 tidy。
       圖示不一樣，這是**唯一**看得到那張圖的畫面。 */
    goto_slot(g, SLOT_DOG);
    snap(g, dir, "07_pet_called");

    /* 狗的動作選單是 5 個（公主 4 個，她沒有 bladder）。
       兩張都要拍——五個圖示是不是真的都畫得出來，只有這張看得到。 */
    game_button(g, BTN_OK);
    printf("  狗的選單：%u 個動作\n", g->menu_n);
    snap(g, dir, "07a_menu_dog");
    /* 不按確認，讓它自己逾時回主畫面——順便驗 MENU_TIMEOUT_MS 這條路還通 */
    run_ms(g, MENU_TIMEOUT_MS + 200);
    printf("  選單逾時 → ui=%u（應該回 0 主畫面）\n", g->ui);

    /* 換狗：直接叫下一隻，舊的自己走回去 */
    goto_slot(g, SLOT_DOOR);
    game_button(g, BTN_OK);
    game_button(g, BTN_NEXT);
    game_button(g, BTN_OK);
    printf("  換狗：在場 %u、離場 %u（離場的播 %d，SAD_WAIT 是 %d）\n",
           g->present, g->leaving,
           (int)game_current_anim(g, g->leaving < CHAR_COUNT ? g->leaving : 0),
           (int)ANIM_SAD_WAIT);
    run_ms(g, 400);
    snap(g, dir, "07b_swap");
    run_ms(g, 1400);

    /* 換裝。**走衣櫃那條路**不要直接改欄位——換裝畫面的每一格是一張色票，
       那是零資產的表示法（一套服裝本來就只是調色盤的五個顏色），
       只有真的進 UI_DRESS 才畫得出來。先全部解鎖，五格才都在。 */
    g->save.chars[CHAR_ICE_PRINCESS].unlocked_outfits = 0x1F;
    goto_slot(g, SLOT_WARDROBE);
    game_button(g, BTN_OK);
    printf("  換裝畫面：ui=%u，游標停在現在穿的第 %u 套\n", g->ui, g->cursor);
    snap(g, dir, "08a_dress");
    game_button(g, BTN_NEXT);
    game_button(g, BTN_NEXT);
    game_button(g, BTN_NEXT);
    game_button(g, BTN_OK);                /* 換上第 3 套 */
    game_set_accessory_mask(g, CHAR_ICE_PRINCESS, 0x1F);
    run_ms(g, 300);
    printf("  穿上第 %u 套\n", game_outfit(g));
    snap(g, dir, "08_outfit_acc");

    /* 玩球：track 型物件唯一的使用者。拍一張確認球真的畫出來了。 */
    goto_slot(g, SLOT_PRINCESS);
    game_do_action(g, CHAR_ICE_PRINCESS, ACT_PLAY);
    /* ACT_PLAY 的序列是 play_bow → chase_ball ×2 → happy，
       要等到 chase_ball 那一段球才會出現。 */
    for (int t = 0; t < 60 && game_current_anim(g, CHAR_ICE_PRINCESS) != ANIM_CHASE_BALL; t++)
        run_ms(g, 100);
    run_ms(g, 200);
    printf("  玩球：動畫 = %d（CHASE_BALL 是 %d）\n",
           (int)game_current_anim(g, CHAR_ICE_PRINCESS), (int)ANIM_CHASE_BALL);
    snap(g, dir, "06c_ball");

    /* 訪客。room_is_quiet 要求離最後一次按鍵超過 POST_INPUT_QUIET_MS，
       所以先空轉過安靜期，再把倒數歸零。 */
    for (int t = 0; t < 40 && game_visitor(g) == VISITOR_NONE; t++) {
        g->visitor_ms = 0;
        run_ms(g, 1000);
    }
    printf("  訪客 = %d，x = %d（角色狀態 %d/%d/%d/%d，leaving %u）\n",
           (int)game_visitor(g), game_visitor_x(g),
           g->rt[0].state, g->rt[1].state, g->rt[2].state, g->rt[3].state, g->leaving);
    snap(g, dir, "09_visitor");

    /* 關機 */
    game_power_off(g);
    run_ms(g, 600);
    snap(g, dir, "10_poweroff");

    uint32_t px, sp;
    render_stats(&px, &sp);
    printf("最後一格：解 %u px、%u 個 sprite\n", px, sp);
    return 0;
}

/* 無視窗自檢。給 CI 與「我改了渲染層有沒有壞」用——
   跑一段腳本、把每個畫面各存一張 PPM，不需要人按鍵、也不需要顯示器。 */
static int headless(game_t *g, const char *dir);

int main(int argc, char **argv)
{
    const char *pack = "data/assets.bin";
    const char *shot_dir = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--shots") && i + 1 < argc) shot_dir = argv[++i];
        else pack = argv[i];
    }
    FILE *f = fopen(pack, "rb");
    if (!f) { printf("找不到 %s——先跑 .venv/bin/python tools/pack.py\n", pack); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    uint8_t *blob = malloc((size_t)n);
    if (fread(blob, 1, (size_t)n, f) != (size_t)n) { printf("讀檔失敗\n"); return 1; }
    fclose(f);
    if (!ipa_open(blob, (size_t)n)) { printf("%s 不是有效的資產包\n", pack); return 1; }
    printf("資產包 %s：%ld B、%u 個資產\n", pack, n, ipa_asset_count());

    if (!render_init()) {
        printf("render_init 失敗——資產包裡缺東西。重跑 tools/pack.py 與 tools/mklayout.py\n");
        return 1;
    }

    save_blob_t s;
    uint64_t now = (uint64_t)time(0);
    save_set_defaults(&s, now);
    game_t g;
    game_init(&g, &s, now);
    game_boot_done(&g);

    if (shot_dir) return headless(&g, shot_dir);

    if (SDL_Init(SDL_INIT_VIDEO) != 0) { printf("SDL: %s\n", SDL_GetError()); return 1; }
    SDL_Window *win = SDL_CreateWindow("ICEPET",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        SCR_W * SCALE, SCR_H * SCALE, SDL_WINDOW_ALLOW_HIGHDPI);
    SDL_Renderer *ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    SDL_Texture *tex = SDL_CreateTexture(ren, SDL_PIXELFORMAT_ARGB8888,
        SDL_TEXTUREACCESS_STREAMING, SCR_W, SCR_H);
    SDL_RenderSetLogicalSize(ren, SCR_W, SCR_H);

    uint8_t batt = 100; _Bool charging = 0;
    uint32_t prev = SDL_GetTicks(), fps_t = prev, frames = 0;
    uint8_t last_ui = 0xFF;
    _Bool run = 1;

    while (run) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) run = 0;
            if (e.type != SDL_KEYDOWN || e.key.repeat) continue;  /* **不做自動重複** */
            switch (e.key.keysym.sym) {
            case SDLK_ESCAPE: case SDLK_q: run = 0; break;
            case SDLK_LEFT:  case SDLK_a: game_button(&g, BTN_PREV); break;
            case SDLK_RIGHT: case SDLK_d: game_button(&g, BTN_NEXT); break;
            case SDLK_SPACE: case SDLK_RETURN: game_button(&g, BTN_OK); break;
            case SDLK_p: game_power_off(&g); break;
            case SDLK_b:
                if (charging) { charging = 0; batt = 20; }
                else if (batt == 20) batt = 5;
                else if (batt == 5) batt = 100;
                else charging = 1;
                game_set_power(&g, batt, charging);
                printf("電量 %u%%%s\n", batt, charging ? "（充電中）" : "");
                break;
            case SDLK_n:
                for (int i = 0; i < 36000; i++) game_tick(&g, 100);   /* 一小時 */
                printf("快轉一小時 → %02u:00\n", g.hour);
                break;
            case SDLK_v:
                g.visitor_ms = 0;
                g.last_input_ms = 0;
                printf("催一隻訪客進來\n");
                break;
            case SDLK_0: {
                uint8_t m = game_accessory_mask(&g, CHAR_ICE_PRINCESS);
                game_set_accessory_mask(&g, CHAR_ICE_PRINCESS, m ? 0 : 0x1F);
                printf("配件 %s\n", m ? "全脫" : "全戴");
                break;
            }
            case SDLK_s: shot("build/sim_shot.ppm"); break;
            default:
                if (e.key.keysym.sym >= SDLK_1 && e.key.keysym.sym <= SDLK_5) {
                    uint8_t o = (uint8_t)(e.key.keysym.sym - SDLK_1);
                    /* 模擬器直接解鎖，方便看五套；實機要靠 affection 里程碑 */
                    s = g.save;
                    g.save.chars[CHAR_ICE_PRINCESS].unlocked_outfits = 0x1F;
                    if (game_set_outfit(&g, o)) printf("換上第 %u 套服裝\n", o);
                }
                break;
            }
        }

        uint32_t nowt = SDL_GetTicks();
        uint32_t dt = nowt - prev;
        if (dt > 100) dt = 100;                 /* 卡頓時不要讓遊戲時間跳太多 */
        prev = nowt;

        game_tick(&g, dt);
        if (game_power_off_done(&g)) { printf("關機完成\n"); run = 0; }

        if (g.ui != last_ui) {
            last_ui = g.ui;
            printf("畫面 → %s（游標 %u/%u，在場 %u）\n",
                   UI_NAME[g.ui < 7 ? g.ui : 0], g.cursor,
                   game_main_slot_count(&g), g.present);
        }

        memset(fb, 0, sizeof fb);
        render_frame(&g, fb, dt);
        to_rgba(game_backlight(&g));

        SDL_UpdateTexture(tex, 0, rgba, SCR_W * 4);
        SDL_RenderClear(ren);
        SDL_RenderCopy(ren, tex, 0, 0);
        SDL_RenderPresent(ren);

        frames++;
        if (nowt - fps_t >= 2000) {
            uint32_t px, sp;
            render_stats(&px, &sp);
            printf("%.1f fps  一格解 %u px / %u 個 sprite  背光 %u%%\n",
                   frames * 1000.0 / (nowt - fps_t), px, sp, game_backlight(&g));
            frames = 0; fps_t = nowt;
        }
    }

    SDL_DestroyTexture(tex);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
    free(blob);
    return 0;
}
