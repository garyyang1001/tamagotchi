# 桌機模擬器。只有 main.c 是桌機專屬的——game.c / render.c / assets.c / save.c
# 和燒進 ESP32 的是同一份原始碼。
#
#     make -C sim run      編譯並執行
#     make -C sim          只編譯
#     make -C sim clean

ROOT   := ..
CC     ?= cc
# 用 sdl2-config 不用 pkg-config：homebrew 裝 sdl2 不一定會帶 pkg-config，
# 但 sdl2-config 一定在。
SDL_CFG := $(shell command -v sdl2-config || echo /opt/homebrew/bin/sdl2-config)
CFLAGS  := -std=c11 -Wall -Wextra -O2 \
           -I$(ROOT)/firmware/include -I$(ROOT)/firmware/test/stubs \
           $(shell $(SDL_CFG) --cflags)
LDLIBS  := $(shell $(SDL_CFG) --libs)

SRC := main.c \
       $(ROOT)/firmware/src/game.c \
       $(ROOT)/firmware/src/save.c \
       $(ROOT)/firmware/src/render.c \
       $(ROOT)/firmware/src/assets.c

BIN := $(ROOT)/build/icepet-sim

all: $(BIN)

$(BIN): $(SRC) $(ROOT)/firmware/include/layout.h
	@mkdir -p $(ROOT)/build
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDLIBS)

# layout.h 是從 specs/ 產生的，不是手寫的。改了 specs 就要重跑。
$(ROOT)/firmware/include/layout.h: $(ROOT)/specs/scene.json $(ROOT)/specs/visitors.json
	cd $(ROOT) && .venv/bin/python tools/mklayout.py

run: $(BIN)
	cd $(ROOT) && ./build/icepet-sim

clean:
	rm -f $(BIN)

.PHONY: all run clean
