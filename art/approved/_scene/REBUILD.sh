#!/bin/sh
# 從 specs/scene.json 重建房間底圖與物件 sprite。
# 全部是幾何 + 逐像素 art，沒有任何一步是手工在影像編輯器裡做的，
# 所以重跑必須逐位元相同——和角色資產同一個契約。
set -e
cd "$(dirname "$0")/../../.."
.venv/bin/python tools/scene.py --mock
