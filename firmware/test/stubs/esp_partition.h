/* 主機端測試用的 esp_partition 樁：以 RAM 模擬一塊 flash 分區。
   支援模擬寫入中途掉電，用來驗證 A/B 雙槽的掉電安全性。 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef int esp_err_t;
#define ESP_OK   0
#define ESP_FAIL -1

typedef enum { ESP_PARTITION_TYPE_DATA = 1 } esp_partition_type_t;
typedef enum { ESP_PARTITION_SUBTYPE_ANY = 0xff } esp_partition_subtype_t;

typedef struct { const char *label; uint32_t size; } esp_partition_t;

/* 8 KB 假 flash，erase 後為 0xFF */
extern uint8_t  fake_flash[8192];
extern int      fake_fail_after_bytes;   /* >=0 時，寫到這麼多位元組就「掉電」 */

const esp_partition_t *esp_partition_find_first(esp_partition_type_t t,
                                                esp_partition_subtype_t s,
                                                const char *label);
esp_err_t esp_partition_read(const esp_partition_t *p, size_t off, void *dst, size_t n);
esp_err_t esp_partition_write(const esp_partition_t *p, size_t off, const void *src, size_t n);
esp_err_t esp_partition_erase_range(const esp_partition_t *p, size_t off, size_t n);
