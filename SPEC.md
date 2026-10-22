# ABI 市場追蹤系統 — 系統診斷、資料一致性與自動測試規格書 (SPEC)

> **版本**：v1.0
> **更新日期**：2026-10-22
> **適用組件**：`abi_bot.py` (v2.5)、`abi_page.py`、`Abi.kt` (Android App v1.6)、`audit.py`

---

## 1. 背景與核心痛點

本系統因目標網站 `abi-tracker.azurewebsites.net` 封鎖雲端機房 IP（GCP / GitHub Actions 皆回傳 503），採用「**GCP VM 常駐 Discord Bot + GitHub Repo 作為訊息佇列 + 住宅 IP Android 手機隱藏 WebView 抓取**」的三端分離架構。

在實際運行中發生以下核心痛點：
1. **指令常回傳舊資料**：使用者在 Discord 輸入指令後，常等待逾時並沿用舊資料。
2. **三端黑盒難以定位故障點**：當資料沒更新時，無法一眼判斷是 **GCP 無法寫入 GitHub**、**手機 App 沒收到請求**、**手機 WebView 抓不到資料**，還是 **手機無法上傳至 GitHub**。
3. **跨分類資料互相覆蓋（如 `!chart100` 是新資料，但 `!top` 是舊資料）**：不同指令觸發的抓取會互相干擾，甚至重算模型時新價格被舊 CSV 洗掉。

---

## 2. 根因分析 (Root Cause Analysis)

經檢視現有程式碼與資料快照，確認共有 5 個系統性缺陷：

| 編號 | 根因 | 發生位置 | 影響與症狀 |
|---|---|---|---|
| **RC-1** | **手機上傳採「整檔覆寫」而非「分類合併」** | `Abi.kt` (`upload` / `putFile`) | 手機每次上傳 `phone_prices.json` 只包含當次抓取的分類。例如抓完 `!top`（13 個雜物分類）後，只要抓一次 `!keys`（`40505`），就會把 13 個雜物的手機報價全部清空，導致 `!top` 退回 3 天前的 GitHub CSV 舊資料。 |
| **RC-2** | **13 個分類抓取時間超過固定 75s Timeout** | `abi_bot.py` (`_ensure_fresh`) | 手機每 20s 輪詢一次，且每個分類需穩定取樣 3 次（約 5~8s）。`!top` 抓 13 個雜物分類總耗時約 90~110s，必然超過寫死的 75s，導致 Bot 先放棄並回舊資料，手機事後才上傳完成。 |
| **RC-3** | **重算快照時，舊 `tv_keys.csv` 倒蓋手機新報價** | `abi_page.py` (第 846 行) | `abi_page.py` 雖先將 `phone_prices.json` 覆蓋進 `rows`，但隨後又執行 `if h.get("hist"): low = h["hist"][-1]`，無條件用舊 `tv_keys.csv` 最後一筆蓋掉手機剛抓到的最新價格。 |
| **RC-4** | **分類新鮮度判定以偏概全 (`any` vs `all`)** | `abi_bot.py` (`quote_time_for`) | 原本只要 `phone_prices.json` 內有**任一筆**商品前綴符合請求分類，就把整個請求視為新鮮。若 13 個雜物分類只抓了 1 個，其餘 12 個仍會沿用舊資料卻不觸發重抓。 |
| **RC-5** | **缺乏端對端 Request ID 追蹤與健康檢查** | 全系統 | 缺乏 `scrape_request.id` 與 `phone_prices.req` 的對帳機制，且無診斷工具可獨立驗證 GCP ↔ GitHub ↔ Phone 三端狀態。 |

---

## 3. 資料契約規格 (Data Schema v2)

### 3.1 `scrape_request.json`（Bot 寫入 → 手機讀取）
```json
{
  "id": 1761029411,
  "minor": "40801,40802,40803",
  "mode": "grid",
  "ts": 1761029411,
  "cmd": "!top"
}
```
- `id` / `ts`：Unix timestamp（秒），手機以 `id > lastReq` 判斷是否為新請求。
- `minor`：逗號分隔的分類代碼。
- `mode`：
  - `"grid"`（預設）：抓取列表最低價與基本在售量。
  - `"history"`：展開詳情 Dialog 抓取在售數量與 30 天日線 / 100 天週線 / 近期成交（解決 `history.json` 停更問題）。

### 3.2 `phone_prices.json`（手機合併上傳 → Bot / `abi_page.py` 讀取）
```json
{
  "fetched_at": "2026-10-21T10:27:37Z",
  "req": 1761029411,
  "app_version": "1.6",
  "minor": "40505",
  "category_ts": {
    "40505": "2026-10-21T10:27:37Z",
    "40801": "2026-10-21T10:15:00Z"
  },
  "items": [
    {
      "id": 405050901,
      "minor": "40505",
      "name": "企劃室鑰匙",
      "low": 2959020,
      "count": 12,
      "ts": "2026-10-21T10:27:37Z"
    }
  ]
}
```
- **核心不變量（Merge Invariant）**：
  - 手機端與 Bot 端更新 `phone_prices.json` 時，必須先讀取舊檔，將新抓取的 `items` 依 `id` 覆蓋合併，並更新對應分類的 `category_ts[minor]`。
  - **禁止刪除本次未抓取分類的既有商品與時間戳**。

---

## 4. 功能與行為規格

### 4.1 分類獨立新鮮度判定 (`quote_time_for(minors)`)
1. 輸入單一或多個分類 `minors`（如 `40801,...,40814`）。
2. 針對每個 requested minor，取得其在 `phone_prices.json`（透過 `category_ts` 或該分類下商品的 `ts`）與 `prices_all_full.csv` 的時間。
3. **覆蓋率與最舊時間原則**：
   - 若請求多個分類，必須取**所有請求分類中最舊（minimum）的時間**作為該組合的有效新鮮度時間，避免部分分類缺漏卻被當作全部新鮮。
   - 只有當所有請求分類皆在手機資料中且時間較新時，來源才標示為 `"手機"`；若有部分分類缺手機資料，則回傳實際覆蓋率（如 `手機 1/13 分類`）。

### 4.2 動態 Timeout 與「先看舊資料」降級機制 (`_ensure_fresh`)
1. **動態等待上限**：
   - 依請求分類數量 `k = len(minors)` 動態計算：`timeout = max(75, 30 + k * 10)` 秒（1 個分類 75 秒，13 個雜物分類 160 秒）。
2. **支援指令直接看舊資料**：
   - `!top old`、`!top 15 old`、`!topg old`、`!keys old` 等，直接跳過 `_ensure_fresh()` 立即以現有 `snapshot.json` 回覆。
3. **互動式「略過等待」按鈕**：
   - 當發出「📲 向手機要資料中…」訊息時，附帶 Discord UI 按鈕 `【⏩ 不等了，直接看舊資料】`。
   - 使用者點擊後立即中斷等待迴圈，立刻渲染並回傳現有資料。

### 4.3 `abi_page.py` 模型重算優先序修正
1. 每個商品的價格來源優先序：**比較 `phone_prices.json` 該商品時間戳 vs `tv_keys.csv` 最後一筆時間戳 vs `prices_all_full.csv` 時間戳，嚴格取時間最新者**。
2. 當手機提供新價格時，同步以新價格重新計算：
   - 鑰匙的 `sig`（`deep` / `buy` / `sell` / `wait`）與 `vs_avg`
   - 雜物與全站商品的 `net`（稅後淨收入）、`pos_pct`、`ref_ratio` 與 `score`（賣出推薦分）。

### 4.4 詳情頁抓取（解決在售量 `count` 與 `history.json` 停更）
1. 在 `Abi.kt` 新增詳情頁提取腳本 `DETAIL_JS`：
   - 自動點擊卡片的 `.market-details-button`，從 `#marketItemDialog` 讀取 `.market-item-count`（在售數量）。
   - 當 `mode == "history"`（或針對 `40505` 鑰匙分類執行歷史更新）時，一併讀取 `recent`、`day`（30 天日線）、`week`（100 天週線），上傳至 `phone_history.json`，由 Bot 自動合併入 `history.json`。

---

## 5. 全鏈路診斷與測試規格 (`audit.py` & Discord `!diag`)

### 5.1 四節點診斷鏈路 (`python audit.py` / Discord `!diag`)

| 節點 | 檢查項目 | 綠燈條件 (PASS) | 紅/黃燈處置建議 |
|---|---|---|---|
| **Node 1: Local/VM 資料層** | 檢查 `snapshot.json`, `forecast.json`, `history.json`, `prices_all_full.csv` 格式與筆數 | 檔案皆存在、JSON 解析成功、商品數 `> 1000` | 執行 `_rebuild_models()` 重建 |
| **Node 2: GCP → GitHub 寫入** | 測試 `~/.github-pat` 推播權限與 `git ls-remote deploy` SSH 連線 | PAT 具 `push` 權限 **或** Deploy Key 可連線 | 將 VM 的 `~/.ssh/id_ed25519.pub` 加到 GitHub Deploy Keys 並勾選 Allow write access |
| **Node 3: GitHub ↔ Phone 迴路** | 比對 `scrape_request.json` (`id`, `ts`) 與 `phone_prices.json` (`req`, `fetched_at`) | `phone.req >= req.id`，或請求發出未滿 90 秒 | 若 `req < id` 且逾時：檢查手機 App 是否開啟監聽、懸浮窗權限、省電設定或手機 PAT |
| **Node 4: 分類新鮮度 & 模型同步** | 檢查 `40505`（鑰匙）與 `40801~40814`（13 雜物）各自時間、覆蓋率，並核對 `phone_prices` 與 `snapshot` 價格是否一致 | 價格與分數 100% 同步，無舊 CSV 倒蓋現象 | 自動觸發 `abi_page.py` + `forecast.py` 重算 |

### 5.2 自動化測試案例 (`python audit.py --test`)

1. **`test_phone_merge_preserves_categories`（防分類互洗測試）**
   - 模擬手機先上傳 13 個雜物分類，再單獨上傳 `40505` 鑰匙分類。
   - **驗證**：合併後的資料必須同時包含 `40505` 與 13 個雜物分類，且各自的 `category_ts` 獨立保留。
2. **`test_abi_page_phone_overrides_older_tv_csv`（防舊 CSV 倒蓋測試）**
   - 模擬 `tv_keys.csv` 最後一筆為較舊時間（如 `10:00`，價格 `1,000,000`），而 `phone_prices.json` 為較新時間（如 `12:00`，價格 `2,500,000`）。
   - **驗證**：執行 `abi_page.py` 產出的 `snapshot.json` 中，該鑰匙 `low` 必須為 `2500000`，且 `score` 與訊號依新價格計算。
3. **`test_quote_time_for_partial_coverage`（分類覆蓋率與新鮮度測試）**
   - 模擬 `40505` 在手機資料中為 1 分鐘前，但 `40801~40814` 只有 1 個分類在手機資料中、其餘 12 個在 3 天前的 CSV。
   - **驗證**：`quote_time_for("40505")` 回傳 `< 5 分鐘 (手機)`；`quote_time_for(MISC_MINORS)` 必須回傳 `3 天前`（取最舊分類），確保 `!top` 會正確觸發手機抓取缺漏分類。
4. **`test_history_merge_dedup`（手機歷史資料合併測試）**
   - 模擬手機回傳 `phone_history.json` 含新一日 `day`、新 `week` 與重疊的 `recent` 成交紀錄。
   - **驗證**：合併後 `recent` 正確去重排序、`day` 更新至最新日期，且 `forecast.py` 能據此算出最新預估。
5. **`test_time_format_consistency`（時間格式一致性測試）**
   - **驗證**：`dstamp()`、快報 `daily_brief_text()`、訊號推播皆輸出統一的 `MM-DD HH:MM（...，來源 ...）` 格式。
