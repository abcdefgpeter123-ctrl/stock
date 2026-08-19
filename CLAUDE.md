# 台股監控儀表板 — 專案背景

## 專案概述

靜態 HTML 儀表板，搭配 GitHub Actions 每日自動抓取台股資料。

- **主要檔案**：`index.html`（單頁 SPA）、`fetch_data_full.py`（資料抓取）、`data.json`（日更資料）、`company_info.json`（公司基本資料 + PE/EPS）
- **部署**：GitHub Pages（靜態），Actions 每天 18:30 TWN 在自架 Mac runner 上執行
- **Runner**：本機 Mac（台灣 IP），以 launchd service 方式常駐，確保 TWSE 不擋 IP

---

## 架構

```
index.html          ← 前端 SPA（Chart.js、純 Vanilla JS）
fetch_data_full.py  ← GitHub Actions 每日執行
data.json           ← 由 Actions 寫入，index.html 載入後讀取
company_info.json   ← 公司資料快取（PE、EPS、描述）
stock_info.json     ← 靜態補充資料（EPS fallback）
.github/workflows/update-data.yml
```

### data.json 結構

```json
{
  "updated_at": "2026/05/27 21:58",
  "twii": { "price": 44256.8, "chg": 731.43, "chgP": 1.68, "date": "..." },
  "institutional": { "foreign": 38127903304, "trust": -4219051039, "dealer": -3130647620 },
  "prices": {
    "2330": { "name": "台積電", "price": 2355, "change": 60, "changeP": 2.61,
              "open": 2300, "high": 2360, "low": 2295, "vol": 0,
              "p5": 3.2, "p30": 12.5 }
  },
  "opportunities": [
    { "code": "2303", "name": "聯電", "theme": "半導體",
      "p30": 5.2, "p5": 1.1, "gap": 18.3,
      "leader": "2330", "leader_p30": 23.5,
      "reason": "龍頭台積電已大漲，聯電尚未跟上",
      "eps": 3.5, "profit_ok": true }
  ],
  "histories": { "2330": { "labels": [...], "closes": [...] } },
  "inst_stocks": { "2330": { "f": 12.3, "t": -2.1, "s": 0.5 } },
  "etf_holdings": {
    "0050": [{"code": "2330", "name": "台積電", "weight": 58.28}]
  },
  "twii_history": { "labels": [...], "closes": [...] }
}
```

---

## 監控股票清單 — 唯一來源 `stocks.json`

**改清單只改 `stocks.json`，然後跑 `python3 sync_stocks.py`。**

```
stocks.json          ← 唯一來源（手改這個）
  ├─ sync_stocks.py → stocks.js   ← 產生檔，網頁用（不要手改）
  │                    ├─ index.html        const STOCKS
  │                    └─ health_check.html const WATCH_STOCKS
  └─ fetch_data_full.py 直接讀   FALLBACK_CODES / WATCH_NAMES / THEME_GROUPS
```

| 欄位 | 說明 |
|------|------|
| `tw_watchlist` | 監控個股 `{code, name, theme}`，目前 62 檔 |
| `tw_fetch_extra` | 一併抓取但不列入清單的代號（ETF、部分金融股），14 筆 |
| `us_names` | 美股代號→中文名，供大盤日評顯示 |
| `theme_parent` | 子題材→父題材（半導體、AI伺服器的一鍵篩選） |
| `theme_groups` | 機會點演算法的 leaders / members |

`sync_stocks.py` 會檢查兩個頁面是否真的改用共用清單，沒接上會 exit 1。
update-data.yml 每天執行前也會跑一次同步。

> **為什麼要這樣做**：這份清單原本在 `index.html`、`health_check.html`、
> `fetch_data_full.py` 各寫一份，格式都不一樣。結果 health_check 漏掉廣達(2382)，
> 兩頁的「AI 族群站上20MA」分別算在 8 檔與 7 檔上，而且沒有任何機制會發現。

---

## ETF 設定

定義在 `index.html` 約第 573 行 `const ETFS = [...]`。

**watchlist ETF**（持股納入監控）：

| ETF | 類型 | 資料來源 | 最後更新 |
|-----|------|----------|---------|
| 0050 | 市值型 | etfinfo.tw | 2026-05-29 |
| 0056 | 高息型 | etfinfo.tw | 2026-05-29 |
| 00929 | 高息型 | etfinfo.tw | 2026-05-29 |
| 00891 | 產業型（半導體）| etfinfo.tw | 2026-05-29 |
| 00992A | 國內主動式 | 群益投信官網 | 2026-05-28 |
| 00981A | 國內主動式 | 統一投信官網 | 2026-05-27 |

**ETF 持股自動更新**：`fetch_data_full.py` 的 `fetch_etf_holdings()` 第 4 策略從 etfinfo.tw 抓（BeautifulSoup 解析）。

---

## fetch_data_full.py 重要設定

```python
FALLBACK_CODES   # 監控股票清單（約 40 支）
THEME_GROUPS     # 題材分組，用於機會點演算法
ETF_TRACK_CODES  # 要追蹤的 ETF 代號
ETF_ETFINFO_CODES = ["0050", "0056", "00929", "00891"]  # etfinfo.tw 自動抓取
```

### 資料來源優先順序

1. **股價**：TWSE OpenAPI → 上市全量 → Yahoo Finance 備援（FALLBACK_CODES）
2. **ETF 持股**：TWSE OpenAPI → TWSE rwd → TWT84U → **etfinfo.tw**（最終備援）
3. **PE/EPS**：Yahoo Finance `quoteSummary`（需 crumb 認證）→ stock_info.json fallback
4. **公司描述**：Claude API 自動生成（每次最多 5 筆，避免超時）

### Yahoo Finance Crumb 認證

```python
# _get_yf_crumb() 先取 crumb 再帶入 API 請求
# 端點：query1.finance.yahoo.com/v1/test/getcrumb
```

---

## GitHub Actions

兩支獨立排程，分開跑台股／美股資料：

**`.github/workflows/update-data.yml`**（台股）
- **執行時間**：週一到週五，UTC 10:30（台灣 18:30）
- **runner**：`ubuntu-latest`（GitHub-hosted）
- **依賴**：`requests yfinance anthropic beautifulsoup4`
- **寫入檔案**：`data.json`、`company_info.json`、週五另產生 `weekly_report.html`
- 選在 18:30 是因為 TWSE 盤後資料（尤其 ETF 申購買回清單）約 17:00 後才陸續發布完整，留緩衝時間

**`.github/workflows/update-us-data.yml`**（美股）
- **執行時間**：週一到週五，台灣時間 07:00（UTC 前一日 23:00，cron `0 23 * * 0-4`）
- **runner**：`ubuntu-latest`（GitHub-hosted，只打 Yahoo Finance，無需台灣 IP）
- **依賴**：`requests yfinance pandas`
- **寫入檔案**：`us_data.json`
- 選在 07:00 是為了台股開盤（09:00）前就能參考美股走勢：美股收盤約台灣時間 04:00–05:00，07:00 抓取留有 2–3 小時緩衝確保 Yahoo Finance 資料穩定

### Runner 管理

```bash
# 查看狀態
cd ~/actions-runner && ./svc.sh status

# 重啟
./svc.sh stop && ./svc.sh start

# 安裝為 launchd service（已完成）
./svc.sh install && ./svc.sh start
```

---

## 已知問題與解法

### git lock 檔案
沙箱環境（Cowork）有時無法清除 lock，需手動：
```bash
rm -f .git/index.lock .git/HEAD.lock
```

### 推送前先 pull
Actions 會 auto-commit，本機推送前必須先 pull：
```bash
git pull origin main --no-rebase && git push origin main
```

### TWSE API 失效
- `DAILYBASKETContent` — URL 不存在，回傳 404 HTML，**永遠失敗**
- `TWT84U` — 偶爾有資料，不穩定
- etfinfo.tw — SSR 頁面，BeautifulSoup 解析，台灣 IP 可用，**目前主力備援**

### FinMind 免費版
HTTP 400「Token is illegal」或 HTTP 422，免費帳號**無 ETF 成分股資料**，不要再嘗試。

### numpy 架構衝突（M1 Mac）
```bash
pip3 install --upgrade numpy yfinance --break-system-packages --force-reinstall
```

---

## EPS 處理邏輯

1. 優先從 `company_info.json` 取真實 EPS
2. 若無，從 Yahoo Finance `quoteSummary` 抓（需 crumb）
3. 若 Yahoo 也無，用 `EPS ≈ Price ÷ PE` 估算（前端 JS 顯示「⚠️估算」badge）

機會點過濾：EPS < 0 或 PE < 0 的股票排除。

---

## 前端重要函式（index.html）

| 函式 | 說明 |
|------|------|
| `renderETFs()` | ETF 卡片，展開顯示持股圓餅圖 |
| `renderDetail(code)` | 個股展開面板（股價走勢、PE/EPS、法人） |
| `etfSourceMap` | stock code → ETF 代號陣列，用於個股表格的 ETF 標籤 |
| `compute_opportunities()` | 機會點演算法（`fetch_data_full.py`） |

### etfSourceMap 建立邏輯
```javascript
// 載入 data.json 後，從所有 watchlist: true 的 ETF 建立反查表
ETFS.filter(e => e.watchlist).forEach(etf => {
  etf.holdings.forEach(h => { etfSourceMap[h.code].push(etf.code) })
})
```

### ETF 持股動態更新
```javascript
// data.json 的 etf_holdings 有資料時自動覆蓋靜態持股
if (data.etf_holdings) {
  ETFS.forEach(etf => {
    const fresh = data.etf_holdings[etf.code]
    if (fresh?.length) { etf.holdings = fresh; etf._holdingsLive = true }
  })
}
```

---

## ETF 持股加減碼（自算，可公開）

`track_etf_holdings()`（`fetch_data_full.py`）用我們每天抓的官方持股揭露，
算出各 ETF 的加減碼，寫進 `data.json` 的 `etf_flows`，前端 `renderEtfFlows()` 顯示。

**為什麼不是直接看權重變化**：`etf_holdings` 給的是權重(%)不是股數。
某檔漲停權重自然變高，但基金可能一股沒動。所以用

```
隱含股數 ∝ 權重 × 基金淨值 ÷ 股價
```

再取「相對前一日的比值」——同一檔 ETF 同一天的淨值是共同因子會約掉，
所以不需要知道淨值就能估出股數變動%。**這是推算值，不是官方申贖清單的精確股數。**
1% 以內視為四捨五入雜訊濾除。

`etf_holdings_history.json` 存每日快照（權重＋當日股價），只留 10 天。

⚠️ **覆蓋範圍受限於能自動抓到持股的 ETF**，目前只有 etfinfo 來源的
`0050 / 0056 / 00929 / 00891`。儀表板上的 4 檔主動式（00992A、00981A、
00988A、00990A）**無法自動化**：

| 嘗試過的來源 | 結果 |
|-------------|------|
| `etfinfo.tw/Fund/Detail/00992A` 等 | 404，該站不收主動式 |
| TWSE `ETFReport/ETFPCF`、`ETF/DailyBasket` | 回傳 HTML 或 302，無可用 JSON |
| `openapi.twse.com.tw/v1/` | 404（索引不存在） |

各家投信的 PCF 檔格式不一、也沒有統一公開 API，要做得各別寫 parser 且極易失效。
那 4 檔的持股目前仍是手動快照（見 ETF 設定表的「最後更新」）。
海外主動式（00988A、00990A）持有外國股票，TWSE 本來就不會有。

---

## 本機專用：主動型 ETF 每日增減（不進版控）

22 檔主動型 ETF 的單日持股增減，含「哪幾檔基金動的手」。

```bash
python3 fetch_active_etf_local.py
# 或在 Finder 點兩下「更新主動ETF增減.command」
```

- 來源：`https://xiaoyu-etf.pages.dev/data.js`（單一 2.4MB 檔，`window.DATA`）
- 全市場排行在 `rank.active.d1.buy / .sell`；腳本各取前 15 檔、每檔列 4 家 ETF
- **我的 6 檔逐檔調整**在 `etfs[].holdings[]`，每筆有 `lots`（張）、`d1`（當日增減）、
  `new`（新進榜）、`clear`（已清倉）。`MY_ETFS` 定義要追蹤哪幾檔，需與 index.html 的 ETFS 對齊
- 輸出約 15K
- 輸出 `active_etf_local.json`，**已列入 .gitignore**
- `index.html` 的 `loadActiveEtf()` 載入不到就整區隱藏（線上版必然如此，不是錯誤）；
  `renderMyEtf()` 畫「我的 ETF 今日調整」

⚠️ 來源的 `updated` 欄位是 False 代表該檔今天**還沒揭露**，顯示的是舊數字。
前端一定要標出來（橘色提示），否則會把「還沒更新」誤讀成「今天沒動作」——
海外主動式（00988A、00990A）常態性比較晚。

⚠️ **絕對不要放進 GitHub Actions。**
來源站 robots.txt 是 `Allow: /`、也沒有禁止程式讀取，抓取本身沒問題；
問題在再散布——該站自述「本站資料整理自 CMoney（其數據源自臺灣證券交易所、
櫃買中心、公開資訊觀測站等公開資訊），僅供研究觀察之用」，它跟我們一樣是
整理者，沒有立場把 CMoney 的資料轉授權。本 repo 是 public 且用 GitHub Pages
對外提供，commit 進去就是再散布。與 `fetch_targets_local.py` 同一個判斷。

> 想要能公開的版本，正途是自己從投信每日 PCF／TWSE 公告算持股差異——
> 那是原始公開資訊，沒有轉授權問題，但工程量大得多。

---

## 本機專用：券商目標價（不進版控）

Yahoo Finance 只給 mean/median/high/low，**沒有個別券商目標價與日期**，
所以 H/L 落差常常上百 %（含好幾季前沒更新的舊目標）。
`fetch_targets_local.py` 從鉅亨網外資評等表補上帶日期的個別目標價。

```bash
python3 fetch_targets_local.py
# 或在 Finder 點兩下「更新券商目標價.command」
```

- 輸出 `targets_local.json`，**已列入 .gitignore**
- `index.html` 的 `loadLocalTargets()` 會嘗試載入，404 就靜靜略過（線上版必然如此，不是錯誤）
- 每次執行會與舊檔合併（日期＋券商＋新目標價 去重），累積歷史
- 每檔保留最近 8 筆，卡片顯示 3 筆

⚠️ **絕對不要放進 GitHub Actions 或 run_and_push.sh。**
來源網站服務條款禁止未經書面授權的「重製、公開傳播、散布」，
本 repo 是 public 且以 GitHub Pages 對外提供，把資料 commit 進去就是條款明文禁止的行為。
原始資料源是 FactSet，鉅亨自身也只是被授權方，無法轉授權。
robots.txt 沒有擋 `/twstock/board/`，所以「本機自用、低頻、不散布」是可接受的用法。

---

## 交易紀錄：不需登入，各自記錄

2026/08 起 `trades.html` **不再有管理員／訪客之分**，資料就是純 localStorage
（`stock_trades_v1`、`stock_trade_notes_v1`），誰打開就看誰自己的。
移除了 `isGuest()`、`renderGuest()`、`AuthUI` 掛載與保險庫讀寫。

**一次性搬遷**：偵測到舊的 `pj_vault_v1` 且本機還沒有資料時，頁面頂端會出現
提示，輸入一次舊密碼就把 `trades` / `notes` 倒回明碼 localStorage
（`offerVaultMigration` / `doVaultMigration`，完成後記 `trades_migrated_v1`）。
**刻意不刪除保險庫**——index.html 的 GitHub Token 還放在同一個保險庫裡。

⚠️ 沒有任何備援：換裝置、清除資料、無痕視窗都會看不到，只能靠「匯出備份」。

### 順帶修掉的隱含全域

`renderMarket()` 用到的 `twiiHistory` / `spxHistory` / `mktMarket` / `mktPeriod` /
`MKT_PERIODS` **全部沒有宣告**，只在 `loadPrices()` 裡直接賦值。
`renderAll()` 在 `loadPrices()` 之前就會先跑一次 → ReferenceError，
而錯誤被 async IIFE 吞掉，畫面上其他區塊照常渲染——所以「大盤對照圖」
從實作以來就**從來沒有出現過，也沒有任何錯誤訊息**。補上宣告後才正常顯示。

---

## GitHub Token（「立即更新資料」按鈕）

Token 存在 `auth.js` 的加密保險庫裡（`pj_vault_v1`），**與交易紀錄共用同一把金鑰**。
按下按鈕的流程：未設定管理密碼 → 引導去交易紀錄頁設定；已設定未解鎖 → 要求輸入密碼；
解鎖後沒有 token → 開 modal 輸入，存入時一併加密。收到 401 會自動把 token 從保險庫移除。

開站時會檢查並清除舊版明碼殘留（`localStorage["gh_actions_token"]`），
並提醒去 GitHub 撤銷那一組——它曾以明碼形式存在過。

> **為什麼要改**：舊版直接 `localStorage.setItem` 明碼存放一組有 `actions:write`
> 權限的 PAT。威脅模型是反的——「我的最愛」用 PBKDF2 25 萬輪加密，
> 能改整個 repo 的憑證卻是明碼，而這個頁面是公開 origin。

建議用 **Fine-grained token**：只授權這個 repo、權限只給 `Actions: Read and write`、
有效期 30～90 天。classic token 對你所有 repo 都有效，風險大得多。

---

## 資料過期警示（freshness.js）

三個頁面（index / us / health_check）載入資料後呼叫 `checkFreshness(dateStr, 市場名, 提示)`，
資料太舊就在頁面最上方插入橫幅：相隔 2–3 個交易日橘色、4 個以上紅色。

判斷用兩道關卡：
1. **距今未滿 40 小時 → 一律不顯示。** 美股 workflow 跑在 ubuntu runner，
   `updated_at` 是 UTC，瀏覽器用台灣本地日期比會整整差一天而誤報（開發時實際踩到）。
2. 再看相隔幾個**平日**（不是幾天，否則週末必誤報）。

連假仍可能誤判——無法從資料推出國定假日——所以文案是推測語氣並註明「若遇連假可忽略」。
寧可偶爾多嘴，也不要該提醒時沉默。

> **為什麼需要**：原本只有 fetch 失敗才有錯誤列。但最危險的失效模式是排程掛掉好幾天：
> `data.json` 還在、還讀得到，頁面照常渲染舊價格，只有角落一行小字寫日期。
> 對拿來做買賣判斷的工具，「安靜地給你過期的答案」比「明顯壞掉」糟糕得多。

---

## 排程失敗通知

兩層，因為單靠一層抓不全：

**1. `if: failure()`** — 三支 workflow 都有。job 跑起來但失敗時，
用固定標題搜尋既有 issue：有就留言、沒有才開新的（避免每天洗版）。

**2. `watchdog.yml` ＋ `check_freshness.py`** — 每天 20:00 台灣時間跑在 ubuntu-latest。
從**結果面**檢查 `data.json` / `us_data.json` / `podcast_summary.json` 的時間戳。

> **為什麼要第二層**：`if: failure()` 只在「job 有跑起來但失敗」時觸發。
> Podcast 那支跑在自架 Mac 上，電腦沒醒著時 job 根本不會開始執行，狀態是 cancelled——
> 沒有任何 step 會跑到，自然沒人通知。實際紀錄是最近 8 次有 4 次 cancelled 且完全無聲。

容許值：台股／美股 2 個平日、Podcast 4 個平日；未滿 40 小時一律視為正常
（吸收時區差，美股那支的 `updated_at` 寫的是 UTC）。

本機可直接跑 `python3 check_freshness.py` 檢查，exit 1 代表有東西過期。

### 讓 Mac 在排程時間醒著

Podcast 需要 Mac 在台灣時間 07:30 是開機且醒著的。設定每天 07:20 自動喚醒：

```bash
sudo pmset repeat wake MTWRFSU 07:20:00
```

（需要你自己輸入密碼執行。查目前設定：`pmset -g sched`）

---

## 資料品質警告（WARNINGS / warnings）

`fetch_data_full.py` 有二十幾處 `except: pass`。在無人值守的 Actions 裡這特別危險：
抓取失敗時腳本照樣 exit 0、Actions 顯示綠勾、頁面顯示上一輪的舊值，沒有人會察覺。
先前兩個 bug 都是這個模式（`roc_to_ad_date()` 認不得 `20260731`、
`fetch_yahoo()` 取到過期的 `previousClose` 害 71 檔漲跌幅全錯），
兩次都是靠肉眼看出數字怪怪的才發現。

作法不是把每個 except 改成 raise（多數單筆失敗本來就該略過），而是讓它**看得見**：

| 機制 | 用途 |
|------|------|
| `warn(msg, level)` | 來源級失敗（crumb 認證、stock_info.json 讀不到）直接記一筆 |
| `tally(source, code)` | 單筆失敗只計數；**同一檔只算一次**，且只在整檔放棄時呼叫 |
| `report_quality(expected)` | 結尾比對「拿到幾筆／應有幾筆」：<50% 報 error、<85% 報 warn |

結果印在 Actions log，同時寫進 `data.json` 的 `warnings`。
前端 `freshness.js` 的 `showDataWarnings()` 會在頁面頂端顯示（error 紅、warn 黃），
排在資料過期橫幅下方。

⚠️ **`tally()` 絕對不能放在 `.TW` / `.TWO` 的重試迴圈裡。**
上櫃股票一定會先 `.TW` 失敗再 `.TWO` 成功，放在迴圈內等於把正常的備援路徑算成失敗。
2026/08/17 那則「個股歷史 有 29 筆單獨失敗」就是這樣來的——其中至少 8 筆是上櫃股
（世界先進、旺矽、精材、環球晶、合晶、穩懋、聯亞、亞光），實際上都成功抓到了。
現在改成只有兩個交易所都拿不到才計數，訊息也會列出實際代號。

判斷準則刻意用「拿到的比例」而不是「有沒有拋例外」——少數個股抓不到是常態，
整批掛掉才是問題。

---

## 首屏瘦身：抽出用不到的大塊資料

`split_heavy_payloads()`（`fetch_data_full.py`）在寫檔前把兩塊資料移出 `data.json`：

| 資料 | 原本 | 現在 | 何時載入 |
|------|------|------|----------|
| `pe_river` | 311K（23%） | 獨立 `pe_river.json` | 點開個股 →「河流圖」分頁才抓 |
| `margin.history` | 261K（20%，5001 筆） | 留 30 筆 ＋ `ratios` 陣列（896 個） | 完整版存 `margin_history.json`，前端不載入 |

實測 `data.json` **1327K → 761K raw（−43%）、299K → 233K gzip（−22%）**。

⚠️ **`margin_history.json` 是融資歷史的正本。** `main()` 累積時必須從這個檔讀，
不能從 `data.json` 的 `margin.history` 讀——那裡只有 30 筆，讀錯來源會每跑一次
就把五千多筆歷史砍成 30 筆（開發時差點踩到，已加註解與 fallback）。

前端的 `margin.ratios` 若不存在會退回舊算法，舊格式 `data.json` 仍能正常運作。

`histories`（317K）**沒有**拆——它在首屏就要用（均線指標、Venn、機會點 v2 的 60MA），
拆了得先解決載入順序，風險大於收益。5 年份早就另存 `history_5y.json` 延後載入了。

---

## 目標價快照（targets_history.json）

`record_target_snapshots()` 每天寫一筆「目標價 + 當時股價」，用來日後回測
**「現價低於全體分析師最低目標」** 這個訊號到底有沒有用。

```json
"2449": [{"d":"2026/08/12","p":246.0,"lo":300.0,"md":346.5,"mn":348.7,
          "hi":423.0,"c":14,"b":1}]
```

`b=1` 表示當天現價低於最低目標（先算好，回測時不用重算）。
短鍵是為了控制體積：63 檔約 6K/天，一年約 1.4MB。前端不載入這個檔。
純累積型，只增不減，同一天重跑不會重複記。

**起算日 2026/08/12。** 之前沒有任何可回測的資料——Yahoo 只給當下快照、
沒有歷史目標價序列，所以這個訊號的績效在那之前是無法驗證的，只能從那天起自己累積。

回測用：

```bash
python3 backtest_belowlow.py
```

會分別看 20／60 個交易日報酬，並拆成「2 位以上分析師 vs 只有 1 位」、
「低於最低目標 ≥15% vs <15%」。樣本少於 30 筆時會標警告——
20 日結果大約要累積 2 個月、60 日約 4 個月才有參考價值。

---

## 2026/08 移除的區塊

依需求移除，功能由「機會懶人包」與「機會點交集圖」承接：

| 移除的區塊 | 一併清掉的程式 |
|-----------|--------------|
| 🎯 機會點快覽（兩列 chip） | `renderTargetUpsideOpps()`、`renderMarketSummary()` 尾端的快覽列 |
| 🧪 機會點 v2（含說明、範圍外、低於最低目標名單） | `renderOppV2` `scoreOppV2` `cardHtml` `renderV2Outer` `toggleV2Outer` `renderBelowLowStrip` |
| 受惠尚未啟動 — 機會點追蹤（卡片牆＋展開面板） | `render()` 內的 opp-grid 渲染（54 行） |

`_snapshotProgress()`（目標價快照累積進度）原本掛在 v2 底下，已移到懶人包①保留。
⚠️ 它讀 `window.__snapshotStats`，該賦值必須在 `renderCheatSheet()` **之前**執行，
否則第一次渲染時進度條不會出現（改動時踩過一次）。

`scrollToOpp()` 原本捲到 opp-grid 再展開，目標消失後改為直接呼叫 `toggleStockDetail()`。

---

## 機會點交集圖：為什麼從三圈變兩圈

2026/08 移除「突破均線」，剩下 ① 目標價上漲空間、② 題材補漲。

5 年回測（104 檔 / 2021–2026）逐年檢查各候選指標，20 日中位報酬勝過對照組的年數：

| 指標 | 總表勝率 | 逐年成立 |
|------|---------|---------|
| 站上 20MA（原①） | 54.5% | **1/6 年** |
| 60MA 方向向上 | 56.0% | 2/6（60日 4/6） |
| 貼近 60 日新高 | 56.2% | 2/6（60日 3/6） |
| 量能 ≥1.5 倍 | 55.4% | — |
| 相對強弱 >+5pt | 53.6% | — |
| （基準） | 54.8% | — |

總表看起來都贏基準，但**逐年拆開全部崩潰**——優勢來自樣本組成（多頭年份「站上均線」的
日子本來就多），不是指標本身有預測力。原①是其中最差的，所以移除而不是替換。

另外先前已測得「突破均線 ＋ 題材補漲」的交集勝率 52.1%，反而低於單獨任一項的 54.9%。
在這個儀表板上「交集＝更強」的直覺已被資料否定兩次。

`computeOppSets()` 仍然回傳 A（站上 5/20/60 均線），但只給機會點 v2 當補充標籤用，
不再進交集圖；機會點快覽的均線列（`renderMaBreakoutOpps`）一併移除。

---

## 市場溫度與大盤進場（market_temp_lib.py）

指標定義與權重完全比照 `market_status.js`（含 3 日平滑），用 5 年歷史逐日重算。

```bash
python3 market_temp_lib.py      # 完整回測報告
```

**買加權指數、依進場當天溫度分組（2021/11–2026/08，1148 個交易日）**

| 溫度 | 佔比 | 20日勝率 | 60日平均 | 60日勝率 | 120日勝率 |
|------|------|---------|---------|---------|----------|
| 大牛 | 44.1% | 66.5% | **+8.07%** | **74.8%** | **83.1%** |
| 小牛 | 18.0% | 66.0% | +5.64% | 64.6% | 70.4% |
| 橫盤 | 15.8% | 66.9% | +4.52% | 65.9% | 71.3% |
| 小熊 | 9.3% | 54.5% | +1.57% | 58.0% | 70.7% |
| 大熊 | 12.8% | 47.6% | +2.74% | 55.1% | 65.3% |
| 不看溫度 | 100% | 62.9% | +5.76% | 67.4% | 75.3% |

實際可用的結論是**避開小熊／大熊**，不是「等大牛才進場」——前三檔（大牛／小牛／橫盤）
20 日勝率都在 66–67%，彼此在雜訊範圍內。

⚠️ 逐年檢查「大牛 vs 空頭」只有 2/4 年成立（2022 ✓、2023 ✗、2024 ✗、2025 ✓），
而且這 5 年台股整體是多頭（大牛佔 44% 的日子）。樣本偏斜，不要當成穩定規律。

注意這與**個股**的結論不同：先前測個股補漲訊號時，橫盤（+2.9%）反而優於大牛（+2.4%）。
買大盤吃的是趨勢，買落後個股吃的是輪動，兩者本來就不同方向。

`market_levels()` 可被匯入，回傳 `{日期: 溫度}`，供其他回測依當天市場狀態分組
（`backtest_belowlow.py` 已接上）。溫度隨時可從 `twii_history` 回推，不需要當初記錄。

---

## 機會懶人包（cheat-grid）

放在加權指數走勢與題材回顧之間，三種「為什麼現在可以買」的理由並列：

| 卡片 | 條件 | 資料來源 |
|------|------|---------|
| ① 專家評估基本面 | 現價 < 全體分析師最低目標 | `analyst_targets[code].low` |
| ② 相信護國神山 | 台積電跌破 5／10／20 日線（✓＝已跌破） | `histories["2330"].closes` |
| ③ 相信整體族群 | 題材補漲落後龍頭 8–12 個百分點 | `opportunities[].gap` |

**刻意不取交集。** 三者背後的假設互相獨立、甚至互相打架——專家目標價是跟著股價
事後調整的，題材補漲挑的卻是還沒漲的，兩者交集常常是空的。而且回測兩次證明
在這個儀表板上「交集＝更強」是錯的（突破均線＋題材補漲 勝率 52.1%，
低於單獨任一項的 54.9%）。並列讓使用者自己選要相信哪一套，而不是硬湊綜合分數。

⚠️ 「有分析師覆蓋」的檔數要用 `Object.values(analystTargets).filter(a => a.mean).length`——
`analyst_targets` 裡含無人追蹤的空殼（`covered: false`），直接數 keys 會多算 16 檔。

---

## 週報（generate_report.py）

每週五由 `update-data.yml` 產生 `weekly_report.html`。

⚠️ **它讀 `data.json` 的 `histories`，而那份已被 `compact_histories()` 壓縮過**——
每檔的 `labels` 被抽成共用的 `history_dates`，個股裡只留索引 `l`。
`calc_tw()` 原本直接讀 `h["labels"]`，壓縮後永遠是空的 → YTD 全部算不出來 →
週報的台股「今年以來」榜單整區空白（美股讀 `us_data.json`，沒被壓縮所以正常）。
現在會 fallback 到 `history_dates[l]`，並把 labels 尾端對齊 closes 長度。

**配色是台股慣例：漲＝紅（`--up:#F05656`）、跌＝綠（`--dn:#2EC96E`）。**
原本沿用美股配色（漲綠跌紅），跟儀表板其他頁面剛好相反。
警示框 `.alert` 用固定紅色而不是 `var(--dn)`——它是「壞消息」語意，
不該跟著漲跌色一起翻轉。

---

## 大盤日評上色（_colorSummary）

正數紅、負數綠（台股慣例），**數字前面的主體一起上色**——
一眼掃過去看的是「哪個題材強」，不是「+2.0 這個數字」。

涵蓋五種樣式，用單一個 master regex 一次掃完：

| 樣式 | 例 |
|------|-----|
| 個股 `名稱(代號) ±x%` | 華新科(2492) +6.7% |
| 題材 `名稱（±x%）` | 被動元件（+2.0%） |
| 括號點數 `（±N）` | （-692） |
| 單獨百分比 | -1.53% |
| 買超／賣超 N 億 | 原文沒有正負號，靠語意判斷 |

⚠️ **不能用多輪 replace**——第二輪會再匹配到第一輪插進去的 HTML，變成巢狀 span。

⚠️ 題材那條的名稱如果直接貪婪匹配，會把前面的連接詞一起吃掉
（「題材面以被動元件」整串變色）。所以用 `_themeNames()`（STOCKS 的 theme
＋ THEME_DESCS ＋ THEME_PARENT 的鍵與值）取最長的後綴切出真正的題材名，
取不到才退回原字串。

---

## 其他工具

**xbar 選單列 plugin**：`~/Documents/Claude/Projects/股票投資/taiwan-stocks.15m.py`
- 讀取本機 `data.json`
- 顯示大盤、三大法人、個股漲跌、機會點
- 安裝：複製到 `~/Library/Application Support/xbar/plugins/` 並 `chmod +x`
