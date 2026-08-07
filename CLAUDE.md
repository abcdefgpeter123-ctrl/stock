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

## 監控股票清單（STOCKS）

共 30 支，定義在 `index.html` 約第 382 行：

| 題材 | 股票代號 |
|------|---------|
| 半導體 | 2330、3711、2454、2303、3661、6488、2449、3034、2379、2344、6223 |
| ABF載板 | 2383、3037 |
| AI伺服器代工 | 2382、6669、2356、2376、3231、2357、2324、2317 |
| 伺服器電源 | 2308、2301 |
| 伺服器機構件 | 2059 |
| 光通訊 | 3081、6442 |
| 液冷散熱 | 3017 |
| 網通 | 2345 |
| 海運 | 2603 |
| 傳產 | 2002、6505 |
| 金融 | 2881、2882、2891 |

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

## 其他工具

**xbar 選單列 plugin**：`~/Documents/Claude/Projects/股票投資/taiwan-stocks.15m.py`
- 讀取本機 `data.json`
- 顯示大盤、三大法人、個股漲跌、機會點
- 安裝：複製到 `~/Library/Application Support/xbar/plugins/` 並 `chmod +x`
