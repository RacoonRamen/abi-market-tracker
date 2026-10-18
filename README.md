# ABI 市場追蹤系統

追蹤 Arena Breakout Infinite 市場價格的爬蟲 + 分析 + 儀表板 + Discord bot。

## 檔案
| 檔案 | 功能 |
|---|---|
| `abi_tracker.py` | 市場價格爬蟲 (Playwright) — `--all` 掃全站 70 分類 / `--minor` 指定分類 |
| `abi_history.py` | 30 天成交歷史抓取 (近100筆成交 + 每日/每週區間) |
| `abi_page.py` | 產生 `dashboard.html` 儀表板 (分類Top5 / 蒐集總表 / 賣出推薦 / 電視台鑰匙 四分頁) + `snapshot.json` |
| `abi_top5.py` | 每分類 Top N 最值得售出 Markdown 報告 |
| `abi_watch.py` | 買賣訊號分析 + Discord Webhook 推播 |
| `abi_bot.py` | Discord bot — `!keys` `!sell` `!price` `!setalert` |

## 自動化
- **GitHub Actions** 每 30 分鐘抓電視台鑰匙 + 歷史、每 3 小時全站掃描，自動重建 `dashboard.html`
- 開啟 GitHub Pages 後：`https://<user>.github.io/abi-market-tracker/dashboard.html`
- `game_zhTW.json` / `items_index.json` 為遊戲靜態資料 (CDN 下載，隨版本可更新)

## 本機執行
```powershell
pip install -r requirements.txt
python -m playwright install chromium
python abi_page.py --open     # 產生並開啟儀表板
python abi_bot.py             # 啟動 Discord bot (token.txt)
```
