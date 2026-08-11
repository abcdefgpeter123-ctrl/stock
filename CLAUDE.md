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

## 其他工具

**xbar 選單列 plugin**：`~/Documents/Claude/Projects/股票投資/taiwan-stocks.15m.py`
- 讀取本機 `data.json`
- 顯示大盤、三大法人、個股漲跌、機會點
- 安裝：複製到 `~/Library/Application Support/xbar/plugins/` 並 `chmod +x`
