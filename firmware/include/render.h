/* 渲染層。把 game_t 的狀態畫成一張 320×240 的 RGB565 畫格。
 *
 * **這一層是可攜的。** 它只認得三件事：assets.h（資產包）、layout.h（版面，
 * 由 specs/ 編譯期產生）、game.h（狀態）。不碰 SDL、不碰 SPI、不碰 GPIO。
 * 桌機模擬器和 ESP32 韌體共用同一份——差別只在誰提供畫布、誰把畫布推到螢幕上。
 *
 * 為什麼先寫桌機版：渲染層的錯（座標、調色盤、RLE 解碼）在桌機上五分鐘看得到，
 * 燒到板子上要五分鐘一輪。七項驗收全過之後，ESP32 那一層只剩 SPI LCD 驅動
 * 和按鍵 GPIO。
 */
#ifndef ICEPET_RENDER_H
#define ICEPET_RENDER_H

#include <stdint.h>
#include "game.h"

/* 開機時呼叫一次。回傳 false 代表資產包裡少了東西（名字對不上），
   這種錯要在開機就爆掉，不要讓它變成畫面上一塊空白。 */
_Bool render_init(void);

/* 畫一格。fb 要有 SCR_W * SCR_H 個 uint16_t。
   dt_ms 用來推進動畫時鐘——**動畫的節奏由資產包的 frame.ms 決定，不是遊戲層**。
   遊戲層只說「播 walk」，播到第幾格是這一層的事。 */
void render_frame(const game_t *g, uint16_t *fb, uint32_t dt_ms);

/* 上一次 render_frame 解了幾個像素、畫了幾個 sprite。給驗收用。 */
void render_stats(uint32_t *out_decoded_px, uint32_t *out_sprites);

#endif /* ICEPET_RENDER_H */
