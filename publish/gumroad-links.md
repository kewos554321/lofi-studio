# Gumroad 連結清單（給 YouTube 描述用）

> 最後同步：2026-10-17（用 `lofi release gumroad` 推上去後，從 API 撈回來的實際網址）
> 憑證：`.secrets/gumroad_token.txt`（不進版控）。重撈：見本檔最後一段。

## 商店首頁

- **https://kewos.gumroad.com** ← 描述裡的「買音樂」一律指這個

## 商品（依上架狀態）

| 狀態 | 專輯 | 連結 |
|---|---|---|
| ✅ 已上架 | Into the Forest | https://kewos.gumroad.com/l/into-the-forest |
| ✅ 已上架 | Kitchen at Dawn, Vol. 1 | https://kewos.gumroad.com/l/kitchen-at-dawn-vol-1 |
| ✅ 已上架 | Kitchen at Dawn, Vol. 2 | https://kewos.gumroad.com/l/kitchen-at-dawn-vol-2 |
| ✅ 已上架 | Kitchen at Dawn, Vol. 3 | https://kewos.gumroad.com/l/kitchen-at-dawn-vol-3 |
| ✅ 已上架 | The Windowsill, Vol. 2 | https://kewos.gumroad.com/l/the-windowsill-vol-2 |
| ✅ 已上架 | The Windowsill, Vol. 3 | https://kewos.gumroad.com/l/the-windowsill-vol-3 |
| 🚧 **draft** | The Windowsill, Vol. 1 | https://kewos.gumroad.com/l/the-windowsill-vol-1 |
| 🚧 **draft** | Night Library, Vol. 1 | https://kewos.gumroad.com/l/night-library-vol-1 |
| 🚧 **draft** | Night Library, Vol. 2 | https://kewos.gumroad.com/l/night-library-vol-2 |
| 🚧 **draft** | Night Library, Vol. 3 | https://kewos.gumroad.com/l/night-library-vol-3 |

⚠️ **draft 的連結還不能貼**——網址是穩定的（發佈後不會變），但商品頁對外顯示「未開賣」。
發佈時用：

```bash
./scripts/lofi release gumroad --run nujab02_1 --suggested-price 2 --publish
# 或到 Gumroad dashboard 逐個按 Publish
```

## 可直接貼進 YouTube 描述（英文）

```
🎧 Own the music
Full albums — lossless FLAC, cover art, pay what you want:
https://kewos.gumroad.com

Night Library, Vol. 1 — https://kewos.gumroad.com/l/night-library-vol-1
Night Library, Vol. 2 — https://kewos.gumroad.com/l/night-library-vol-2
Night Library, Vol. 3 — https://kewos.gumroad.com/l/night-library-vol-3
```

單卷長片只貼該卷那行＋商店首頁即可（三行全貼會分散點擊）。

## 重撈最新狀態

```bash
PYTHONPATH=src python3 -c "
from lofi import gumroad as g
tok = g.load_token()
u = g.api('GET', '/user', token=tok)['user']
print('商店：', u['url'])
for p in sorted(g.list_products(token=tok), key=lambda x: x.get('name') or ''):
    print(('PUB  ' if p.get('published') else 'draft'), '|', p.get('name'), '|', p.get('short_url'))
"
```
