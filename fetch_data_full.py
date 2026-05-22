"""
台股資料抓取腳本 v3
- TWII:        Yahoo Finance（境外可用）
- 三大法人:    依序試多個 TWSE 端點（含開放資料 API，不限 IP）
- 全台個股:    先試 TWSE/TPEX 開放資料 API；若被擋，改用 Yahoo Finance 抓主要清單
GitHub Actions 從 GitHub 伺服器（美國）執行，開放資料 API 設計給境外存取。
"""

import json
import os
import requests
import datetime
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# 備用清單：若開放 API 失敗，仍可用 Yahoo Finance 抓這些主要股票
FALLBACK_CODES = [
    # AI 伺服器 / 半導體
    "2330","2317","3711","6669","5274","3661","2449","3081","2455","4971",
    "3163","3363","6442","6488","2308","3008","2382","2356","3515","2376",
    "2395","2357","2353","2354","3673","6415","4983","3037","5483","2368",
    "2385","3702","8046","6533","4906","3003","6508","6278","5264","3443",
    # 記憶體
    "3006","2408","2344","2369","2351","2392",
    # 液冷散熱
    "6274","3556","6538",
    # 低軌衛星
    "3152","3048","6411","4977",
    # 被動元件
    "2327","2492","3034",
    # 光學
    "3008","2364",
    # 海運
    "2603","2609","2615","2606","2634",
    # 航空
    "2618","2610","6706","6505",
    # 金融
    "2881","2882","2891","2884","2885","2886","2887","2888","2890","2892",
    "2883","5880","2880","2889",
    # 電信
    "2412","3045","4904","3682",
    # 生技
    "4147","6446","3705","4736","4128","6547","4166","4174","1786","4119",
    "6456","4720","4123","4106","4119","6456","1789","4726","6197","6488",
    # 傳產 / 鋼鐵 / 化工
    "2002","1301","1303","1326","1101","1102","1304","1402","1408","1440",
    "2207","2201","1402","1513",
    # 食品零售
    "1216","2912","5903","1227","9907","2915",
    # 電動車
    "2227","2228","1539","1536","6431","1516",
    # 電源 / 儲能
    "6121","1513",
    # 其他
    "2303","2301","2454","2379","6456","3231","3702",
    # ETF
    "0050","0056","00878","00919","00929","00940",
    "00713","00757","00662","00891","00900","00923","00850","00864",
]


# ── 題材分組（用於機會點自動偵測）──────────────────────────
# leaders: 該題材先行啟動的龍頭股（用來判斷題材是否熱絡）
# members: 同題材但尚未跟上的成員股（機會點候選）
THEME_GROUPS = {
    "記憶體": {
        "leaders": ["2408", "2344", "3006"],          # 南亞科, 華邦電, 晶豪科
        "members": ["2369", "2351", "2337", "4256"],  # 菱生精密, 順德, 旺宏, 光罩
    },
    "液冷散熱": {
        "leaders": ["6274", "3017", "3230"],           # 台燿, 奇鋐, 雙鴻
        "members": ["6538", "3556", "1626", "6290"],   # 倉和, 禾瑞亞, 艾姆勒, 良維
    },
    "低軌衛星": {
        "leaders": ["3152", "6438"],                   # 璟德, 昇達科
        "members": ["3048", "6411", "4977"],           # 益登, 上詮光, 上緯新創
    },
    "矽光子CPO": {
        "leaders": ["3081", "2455", "4971"],           # 聯亞, 全新, IET-KY
        "members": ["3163", "3363", "6442", "2413"],   # 波若威, 上詮, 光聖, 環瑞
    },
    "AI伺服器": {
        "leaders": ["2330", "6669", "3661"],                    # 台積電, 緯穎, 世芯-KY
        "members": ["3008", "4863", "2449", "2458", "6412", "2441"],  # 大立光, 新光銅, 京元電, 義隆, 群電, 超豐
    },
    "被動元件": {
        "leaders": ["2327", "2492"],                   # 國巨, 禾伸堂
        "members": ["2351", "3034", "2360"],           # 順德, 聯詠, 致茂
    },
    "海運": {
        "leaders": ["2603", "2609"],                   # 長榮, 陽明
        "members": ["2615", "2606", "2605"],           # 萬海, 裕民, 新興
    },
    "航空": {
        "leaders": ["2618", "2634"],                   # 長榮航, 漢翔
        "members": ["2610", "6706", "2650"],           # 華航, 惠普, 台虎
    },
    "電動車": {
        "leaders": ["2227", "2228", "1539"],           # 裕日車, 劍麟, 巨庭
        "members": ["1536", "6431", "2388", "1537"],   # 和大, 光麗-KY, 威盛, 廣隆
    },
    "生技": {
        "leaders": ["4147", "6446"],                   # 中裕, 藥華藥
        "members": ["3705", "4736", "4128", "4166"],   # 永信, 泰博, 中天, 藥技
    },
}

# ── 工具函式 ──────────────────────────────────────────────

def roc_to_ad_date(twse_date: str, fallback: datetime.date) -> str:
    """把 TWSE 回傳的民國日期字串（如 '115/05/19'）轉成西元（'2026/05/19'）"""
    if twse_date and "/" in twse_date:
        parts = twse_date.split("/")
        if len(parts) == 3:
            try:
                return f"{int(parts[0])+1911}/{parts[1]}/{parts[2]}"
            except ValueError:
                pass
    return fallback.strftime("%Y/%m/%d")


def safe_float(v, default=0.0):
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return default


# ── 公司基本資料 / 自動生成 ──────────────────────────────

def load_company_info():
    try:
        with open("company_info.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _info_needs_update(entry, days=90):
    """超過 days 天沒更新，或根本沒有資料，都視為需要更新"""
    if not entry or "generated" not in entry:
        return True
    try:
        gen = datetime.datetime.strptime(entry["generated"], "%Y/%m/%d").date()
        return (datetime.date.today() - gen).days > days
    except Exception:
        return True


def _call_claude(code, name, api_key):
    """呼叫 Claude Haiku 生成公司簡介（JSON）"""
    prompt = (
        f"台股代號 {code}，公司名稱「{name}」。\n"
        "請用繁體中文，只輸出以下 JSON 格式，不要任何其他文字：\n"
        '{"core_products":"核心產品2-4字","business_desc":"主要業務1-2句含主要客戶","industry":"產業類別","major_clients":"主要客戶（若不確定可寫—）"}'
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        text = resp.json()["content"][0]["text"].strip()
        # 取出第一個 {...}
        start = text.find("{")
        end   = text.rfind("}") + 1
        return json.loads(text[start:end]) if start >= 0 else None
    except Exception as e:
        print(f"   ⚠️ Claude API {code}: {e}")
        return None


def fetch_trailing_pe(code):
    """從 Yahoo Finance 抓本益比（trailingPE）"""
    for suffix in [".TW", ".TWO"]:
        try:
            url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   f"{code}{suffix}?modules=defaultKeyStatistics,summaryDetail")
            r = requests.get(url, headers=HEADERS, timeout=12)
            data = r.json()["quoteSummary"]["result"][0]
            pe = (data.get("summaryDetail", {})
                      .get("trailingPE", {})
                      .get("raw"))
            eps = (data.get("defaultKeyStatistics", {})
                       .get("trailingEps", {})
                       .get("raw"))
            if pe or eps:
                return {
                    "pe":  round(pe,  1) if pe  else None,
                    "eps": round(eps, 2) if eps else None,
                }
        except Exception:
            continue
    return {}


def update_company_info(all_prices, company_info, api_key, max_new=50):
    """
    為沒有描述（或描述已過期）的股票呼叫 Claude 自動生成，
    每次最多生成 max_new 筆（避免單次執行太久）。
    同時為 STOCKS + THEME_GROUPS 成員更新本益比。
    """
    today_str = datetime.date.today().strftime("%Y/%m/%d")

    # ── 優先補齊的代號（主清單 + 題材分組）
    priority = set()
    for g in THEME_GROUPS.values():
        priority.update(g["leaders"])
        priority.update(g["members"])

    # 排序：優先股先補，其餘按代號排序
    def sort_key(code):
        return (0 if code in priority else 1, code)

    codes_to_check = sorted(all_prices.keys(), key=sort_key)
    generated = 0

    if api_key:
        print(f"   🤖 Claude API 自動生成公司描述（最多 {max_new} 筆）...")
        for code in codes_to_check:
            if generated >= max_new:
                break
            entry = company_info.get(code, {})
            if not _info_needs_update(entry):
                continue
            name = all_prices.get(code, {}).get("name", code)
            info = _call_claude(code, name, api_key)
            if info:
                info["generated"] = today_str
                # 保留已有的 pe/eps
                if "pe" in entry:
                    info["pe"]  = entry["pe"]
                if "eps" in entry:
                    info["eps"] = entry["eps"]
                company_info[code] = info
                generated += 1
                time.sleep(0.4)
        print(f"   ✅ 新增 {generated} 筆公司描述")
    else:
        print("   ⚠️ 無 ANTHROPIC_API_KEY，跳過 AI 描述生成")

    # ── 更新本益比（優先清單）
    pe_codes = list(priority)[:60]  # 每天最多抓 60 支，避免超時
    print(f"   📊 更新 {len(pe_codes)} 支本益比...")
    for code in pe_codes:
        pe_data = fetch_trailing_pe(code)
        if pe_data:
            entry = company_info.setdefault(code, {"generated": today_str})
            entry.update(pe_data)
        time.sleep(0.2)

    return company_info


# ── 個股歷史價格（Yahoo Finance）─────────────────────────

def fetch_price_history(code):
    """
    從 Yahoo Finance 抓個股近 2 個月日線。
    回傳 (closes, dates)：closes 為收盤價 list，dates 為對應 "MM/DD" 字串 list。
    先試 .TW（上市），再試 .TWO（上櫃）。失敗回傳 (None, None)。
    """
    for suffix in [".TW", ".TWO"]:
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
                   f"{code}{suffix}?interval=1d&range=2mo")
            r = requests.get(url, headers=HEADERS, timeout=12)
            result = r.json()["chart"]["result"][0]
            timestamps = result.get("timestamp", [])
            closes_raw = result["indicators"]["quote"][0]["close"]
            pairs = [(t, c) for t, c in zip(timestamps, closes_raw) if c is not None]
            if len(pairs) >= 6:
                closes = [c for _, c in pairs]
                dates  = [datetime.datetime.utcfromtimestamp(t).strftime("%m/%d")
                          for t, _ in pairs]
                return closes, dates
        except Exception:
            continue
    return None, None


def compute_opportunities(all_prices):
    """
    比較各題材龍頭 vs 成員的真實 30 日漲幅，
    自動找出「龍頭已大漲、但成員還沒動」的機會點。
    回傳 list of dict，每筆包含 code/name/theme/p30/p5/leader/leader_p30/gap/reason。
    """
    LEADER_THRESHOLD = 12   # 龍頭 30 日漲幅需 >= 12% 才算題材熱絡
    GAP_THRESHOLD    = 8    # 成員落後龍頭 >= 8% 才算有機會
    MAX_MEMBER_P30   = 7    # 成員 30 日漲幅 <= 7% 才算「尚未啟動」
    MIN_MEMBER_P30   = -3   # 成員 30 日跌幅不能超過 -3%（跌太多代表有基本面問題）

    # 收集所有需要歷史資料的代號
    all_codes = set()
    for g in THEME_GROUPS.values():
        all_codes.update(g["leaders"])
        all_codes.update(g["members"])

    # 批次抓取歷史收盤價
    histories      = {}        # code → closes list（用於 pct 計算）
    histories_json = {}        # code → {labels, closes}（存入 data.json 供圖表用）
    codes_list = sorted(all_codes)
    print(f"   📈 抓取 {len(codes_list)} 支個股歷史（機會點偵測）...")
    for i, code in enumerate(codes_list, 1):
        closes, dates = fetch_price_history(code)
        if closes:
            histories[code] = closes
            histories_json[code] = {
                "labels": dates[-30:],
                "closes": [round(c, 1) for c in closes[-30:]],
            }
        time.sleep(0.25)
        if i % 15 == 0:
            print(f"   進度 {i}/{len(codes_list)}...")

    def pct(closes, n):
        """最近 n 個交易日漲跌幅（%）"""
        if not closes or len(closes) < n + 1:
            return None
        base = closes[-(n + 1)]
        cur  = closes[-1]
        return round((cur - base) / base * 100, 1) if base > 0 else None

    opportunities = []

    for theme, group in THEME_GROUPS.items():
        # ─ 計算龍頭漲幅
        leader_stats = []
        for code in group["leaders"]:
            closes = histories.get(code)
            if not closes:
                continue
            r30 = pct(closes, 30)
            if r30 is None:
                continue
            name = all_prices.get(code, {}).get("name", code)
            leader_stats.append((code, name, r30))

        if not leader_stats:
            continue

        # 最強龍頭
        best = max(leader_stats, key=lambda x: x[2])
        leader_code, leader_name, leader_p30 = best

        if leader_p30 < LEADER_THRESHOLD:
            continue  # 題材尚未熱絡，跳過

        # ─ 找落後成員
        for code in group["members"]:
            closes = histories.get(code)
            if not closes:
                continue
            m_p30 = pct(closes, 30)
            m_p5  = pct(closes, 5)
            if m_p30 is None:
                continue

            gap = round(leader_p30 - m_p30, 1)
            if gap < GAP_THRESHOLD or m_p30 > MAX_MEMBER_P30 or m_p30 < MIN_MEMBER_P30:
                continue  # 落差不夠、成員已大漲、或成員跌幅過深（基本面疑慮）

            name = all_prices.get(code, {}).get("name", code)

            # 動態產生原因說明
            direction = "漲" if m_p30 >= 0 else "跌"
            reason = (
                f"{theme}龍頭【{leader_name}】近30日大漲+{leader_p30:.0f}%，"
                f"【{name}】同屬{theme}供應鏈，近30日僅{direction}{abs(m_p30):.0f}%，"
                f"落差{gap:.0f}%，法人尚未大舉介入，具補漲潛力"
            )

            opportunities.append({
                "code":       code,
                "name":       name,
                "theme":      theme,
                "p30":        m_p30,
                "p5":         m_p5,
                "leader":     leader_name,
                "leader_p30": leader_p30,
                "gap":        gap,
                "reason":     reason,
            })

    # 按落差由大到小排序
    opportunities.sort(key=lambda x: x["gap"], reverse=True)
    print(f"   🎯 機會點偵測完成: {len(opportunities)} 支")
    return opportunities, histories_json


# ── 加權指數 ──────────────────────────────────────────────

def fetch_twii():
    """Yahoo Finance — 境外可用"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&range=2d"
        r = requests.get(url, headers=HEADERS, timeout=15)
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
        return {
            "price": round(price, 2),
            "chg":   round(price - prev, 2),
            "chgP":  round((price - prev) / prev * 100, 2),
            "vol":   meta.get("regularMarketVolume", 0),
            "date":  datetime.datetime.fromtimestamp(
                         meta["regularMarketTime"]).strftime("%Y/%m/%d"),
        }
    except Exception as e:
        print(f"❌ 加權指數: {e}")
        return None


# ── 三大法人 ──────────────────────────────────────────────

def fetch_institutional():
    """
    試多個 TWSE 端點（開放資料 → 舊端點西元 → 新端點民國），
    往前最多找 8 個日曆日。
    """
    for i in range(8):
        d = datetime.date.today() - datetime.timedelta(days=i)
        ad  = d.strftime("%Y%m%d")                            # 20260519
        roc = f"{d.year-1911}{d.month:02d}{d.day:02d}"       # 1150519

        endpoints = [
            # 1. TWSE 開放資料（設計給境外，最穩）
            f"https://openapi.twse.com.tw/v1/fund/BFI82U?date={ad}",
            # 2. TWSE 舊端點 — 西元
            f"https://www.twse.com.tw/fund/BFI82U?response=json&date={ad}&selectType=day",
            # 3. TWSE 新端點 — 民國
            f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&dayDate={roc}&type=day",
            # 4. TWSE 新端點 — 西元（舊腳本用法，做保底）
            f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&dayDate={ad}&type=day",
        ]

        for url in endpoints:
            try:
                r = requests.get(url, headers=HEADERS, timeout=12)
                resp = r.json()

                # openapi 回傳格式是 list；TWSE 舊/新端點回傳 dict 含 "data"
                rows = resp if isinstance(resp, list) else (resp.get("data") or [])
                if not rows:
                    continue

                foreign = trust = dealer = 0
                date_str = d.strftime("%Y/%m/%d")
                any_parsed = False

                for row in rows:
                    if isinstance(row, dict):
                        name = str(row.get("Name", row.get("name",
                                   row.get("SecuritiesCompanyCode", ""))))
                        net_raw = (row.get("BuyOrSellNetAmount") or
                                   row.get("net") or
                                   row.get("NetBuySell") or "0")
                        net = int(str(net_raw).replace(",", ""))
                    else:
                        if len(row) < 4:
                            continue
                        name = row[0]
                        # 差額固定是最後一欄（不管 4 欄或 5 欄都適用）
                        net_str = str(row[-1]).replace(",", "").strip()
                        try:
                            net = int(net_str)
                        except ValueError:
                            continue

                    if "外資" in name:   foreign += net
                    elif "投信" in name: trust   += net
                    elif "自營" in name: dealer  += net
                    any_parsed = True

                if not any_parsed:
                    continue

                # TWSE 舊/新端點有民國 date 欄位，轉成西元
                if isinstance(resp, dict) and resp.get("date"):
                    date_str = roc_to_ad_date(str(resp["date"]), d)

                print(f"   ✅ 法人({date_str})")
                return {"foreign": foreign, "trust": trust,
                        "dealer": dealer, "date": date_str}

            except Exception as e:
                print(f"   ⚠️  {url[:60]}: {e}")
                continue

    print("❌ 法人數據全部端點失敗")
    return None


# ── 個股：TWSE 開放 API（境外友善）────────────────────────

def _parse_twse_openapi_rows(rows):
    """解析 TWSE openapi list 格式，回傳 prices dict"""
    prices = {}
    if not rows:
        return prices
    # 印出第一筆 debug 訊息，方便確認欄位名稱
    if rows:
        first = rows[0]
        print(f"   [debug] 第一筆欄位: {list(first.keys()) if isinstance(first, dict) else first[:5]}")
    for row in rows:
        if not isinstance(row, dict):
            continue
        code  = str(row.get("Code", row.get("code", ""))).strip()
        name  = str(row.get("StockName", row.get("Name", row.get("name", "")))).strip()
        # TWSE openapi 的收盤價欄位可能叫 ClosingPrice 或 close
        close_raw = row.get("ClosingPrice") or row.get("ClosePrice") or row.get("close") or ""
        close = safe_float(close_raw)
        chg_raw = row.get("Change") or row.get("change") or ""
        chg   = safe_float(chg_raw)
        vol_s = str(row.get("TradeVolume", row.get("volume", "0"))).replace(",", "")
        vol   = int(vol_s) if vol_s.isdigit() else 0
        if not code or close == 0:
            continue
        prev    = close - chg
        chg_pct = round(chg / prev * 100, 2) if prev else 0.0
        prices[code] = {
            "name":    name,
            "price":   close,
            "change":  round(chg, 2),
            "changeP": chg_pct,
            "open":    safe_float(row.get("OpeningPrice", row.get("open", ""))),
            "high":    safe_float(row.get("HighestPrice", row.get("high", ""))),
            "low":     safe_float(row.get("LowestPrice",  row.get("low",  ""))),
            "vol":     vol,
        }
    return prices


def _parse_twse_rwd_rows(resp):
    """
    解析 TWSE rwd 格式（www.twse.com.tw 的 JSON 回應）。
    回傳格式：
      fields: ["證券代號","證券名稱","成交股數",...,"開盤價","最高價","最低價","收盤價","漲跌(+/-)","漲跌價差",...]
      data:   [["0050","元大台灣50","...","93.1","▼","1.8",...], ...]
    """
    prices = {}
    fields = resp.get("fields", [])
    data   = resp.get("data", [])
    if not fields or not data:
        return prices

    def fi(name):
        return fields.index(name) if name in fields else -1

    idx_code  = fi("證券代號")
    idx_name  = fi("證券名稱")
    idx_close = fi("收盤價")
    idx_dir   = fi("漲跌(+/-)")   # ▲ or ▼
    idx_chg   = fi("漲跌價差")
    idx_open  = fi("開盤價")
    idx_high  = fi("最高價")
    idx_low   = fi("最低價")
    idx_vol   = fi("成交股數")

    if idx_code < 0 or idx_close < 0:
        print(f"   [debug] rwd 欄位找不到，fields={fields[:6]}")
        return prices

    print(f"   [debug] rwd fields 找到，共 {len(data)} 列")
    for row in data:
        try:
            code  = str(row[idx_code]).strip()
            name  = str(row[idx_name]).strip() if idx_name >= 0 else ""
            close = safe_float(row[idx_close])
            if close == 0:
                continue
            # 漲跌方向
            direction = str(row[idx_dir]).strip() if idx_dir >= 0 else ""
            chg = safe_float(row[idx_chg]) if idx_chg >= 0 else 0.0
            if "▼" in direction or direction == "-":
                chg = -abs(chg)
            prev    = close - chg
            chg_pct = round(chg / prev * 100, 2) if prev else 0.0
            vol_s   = str(row[idx_vol]).replace(",", "") if idx_vol >= 0 else "0"
            vol     = int(vol_s) if vol_s.isdigit() else 0
            prices[code] = {
                "name":    name,
                "price":   close,
                "change":  round(chg, 2),
                "changeP": chg_pct,
                "open":    safe_float(row[idx_open]) if idx_open >= 0 else 0,
                "high":    safe_float(row[idx_high]) if idx_high >= 0 else 0,
                "low":     safe_float(row[idx_low])  if idx_low  >= 0 else 0,
                "vol":     vol,
            }
        except Exception:
            continue
    return prices


def fetch_all_twse_stocks():
    """
    全部上市股票收盤價，依序嘗試三個端點：
    1. TWSE openapi（開放資料，境外友善）
    2. TWSE rwd afterTrading STOCK_DAY_ALL（主站，有 fields 索引）
    3. TWSE rwd MI_INDEX（全市場，有 tables 結構）
    """
    headers_rwd = {**HEADERS, "Referer": "https://www.twse.com.tw/"}

    # ── 端點 1：openapi.twse.com.tw ──
    for url in [
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL",
    ]:
        try:
            r = requests.get(url, headers=headers_rwd, timeout=30)
            rows = r.json()
            if isinstance(rows, list) and rows:
                prices = _parse_twse_openapi_rows(rows)
                if prices:
                    print(f"   ✅ openapi 端點成功: {url.split('/')[-1]} → {len(prices)} 支")
                    return prices
                print(f"   ⚠️ openapi 回傳 {len(rows)} 列但解析 0 支，欄位可能不符")
        except Exception as e:
            print(f"   ⚠️ {url[-40:]}: {e}")

    # ── 端點 2：rwd afterTrading STOCK_DAY_ALL ──
    try:
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json"
        r = requests.get(url, headers=headers_rwd, timeout=30)
        resp = r.json()
        if resp.get("stat") == "OK":
            prices = _parse_twse_rwd_rows(resp)
            if prices:
                print(f"   ✅ rwd STOCK_DAY_ALL → {len(prices)} 支")
                return prices
        else:
            print(f"   ⚠️ rwd STOCK_DAY_ALL stat={resp.get('stat')}")
    except Exception as e:
        print(f"   ⚠️ rwd STOCK_DAY_ALL: {e}")

    # ── 端點 3：rwd MI_INDEX ──
    try:
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999"
        r = requests.get(url, headers=headers_rwd, timeout=45)
        resp = r.json()
        if resp.get("stat") == "OK":
            prices = {}
            for table in resp.get("tables", []):
                fields = table.get("fields", [])
                if "收盤價" not in fields:
                    continue
                sub = _parse_twse_rwd_rows({"fields": fields, "data": table.get("data", [])})
                prices.update(sub)
            if prices:
                print(f"   ✅ rwd MI_INDEX → {len(prices)} 支")
                return prices
        else:
            print(f"   ⚠️ MI_INDEX stat={resp.get('stat')}")
    except Exception as e:
        print(f"   ⚠️ MI_INDEX: {e}")

    print("❌ TWSE 上市三個端點全部失敗")
    return {}


def fetch_all_otc_openapi():
    """
    TPEX 開放資料 — 全部上櫃股票當日收盤
    https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes
    """
    try:
        url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        r = requests.get(url, headers=HEADERS, timeout=60)
        rows = r.json()
        prices = {}
        for row in rows:
            code  = str(row.get("SecuritiesCompanyCode",
                        row.get("code", ""))).strip()
            name  = str(row.get("CompanyName", row.get("Name",
                        row.get("name", "")))).strip()
            close = safe_float(row.get("Close", row.get("close", "")))
            chg   = safe_float(row.get("Change", row.get("change", "")))
            if not code or close == 0:
                continue
            prev    = close - chg
            chg_pct = round(chg / prev * 100, 2) if prev else 0.0
            prices[code] = {
                "name":    name,
                "price":   close,
                "change":  round(chg, 2),
                "changeP": chg_pct,
                "open":    safe_float(row.get("Open", "")),
                "high":    safe_float(row.get("High", "")),
                "low":     safe_float(row.get("Low", "")),
                "vol":     int(str(row.get("TradeVolume","0")).replace(",","") or 0),
            }
        return prices
    except Exception as e:
        print(f"❌ TPEX openapi 上櫃: {e}")
        return {}


# ── 個股：Yahoo Finance 備援 ──────────────────────────────

def fetch_yahoo(code):
    """Yahoo Finance — 境外可用，逐支抓"""
    for suffix in [".TW", ".TWO"]:
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
                   f"{code}{suffix}?interval=1d&range=2d")
            r    = requests.get(url, headers=HEADERS, timeout=10)
            meta = r.json()["chart"]["result"][0]["meta"]
            price = meta["regularMarketPrice"]
            prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
            if not price or not prev:
                continue
            return {
                "price":   round(price, 2),
                "change":  round(price - prev, 2),
                "changeP": round((price - prev) / prev * 100, 2),
                "open":    round(meta.get("regularMarketOpen", 0), 2),
                "high":    round(meta.get("regularMarketDayHigh", 0), 2),
                "low":     round(meta.get("regularMarketDayLow", 0), 2),
                "vol":     meta.get("regularMarketVolume", 0),
            }
        except Exception:
            continue
    return None


def fetch_fallback_list():
    """用 Yahoo Finance 抓 FALLBACK_CODES 清單"""
    prices = {}
    total  = len(FALLBACK_CODES)
    print(f"   📡 Yahoo Finance 備援，共 {total} 支...")
    for i, code in enumerate(FALLBACK_CODES, 1):
        p = fetch_yahoo(code)
        if p:
            prices[code] = p
        if i % 50 == 0:
            print(f"   進度 {i}/{total}...")
        time.sleep(0.25)
    return prices


# ── 主程式 ────────────────────────────────────────────────

def main():
    print("🚀 台股資料抓取 v3 開始...")
    print(f"   時間: {datetime.datetime.now()}")

    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"twii": {}, "institutional": {}, "prices": {}}

    # 1. 加權指數
    twii = fetch_twii()
    if twii:
        data["twii"] = twii
        print(f"✅ 加權指數: {twii['price']} ({twii['chg']:+.2f})")

    # 2. 三大法人
    inst = fetch_institutional()
    if inst:
        data["institutional"] = inst
        print(f"✅ 法人({inst['date']}): "
              f"外{inst['foreign']/1e8:+.0f}億 "
              f"投{inst['trust']/1e8:+.0f}億 "
              f"自{inst['dealer']/1e8:+.0f}億")

    # 3. 全台個股 — 優先用開放 API
    print("📊 TWSE 開放 API — 上市股票...")
    twse = fetch_all_twse_stocks()
    print(f"   上市: {len(twse)} 支")

    print("📊 TPEX 開放 API — 上櫃股票...")
    otc = fetch_all_otc_openapi()
    print(f"   上櫃: {len(otc)} 支")

    # 上市不足 100 支（API 失敗）→ 用 Yahoo Finance 補主要清單
    if len(twse) < 100:
        print("⚠️  TWSE 開放 API 無上市資料，改用 Yahoo Finance 補主要清單...")
        yf = fetch_fallback_list()
        print(f"   Yahoo Finance: {len(yf)} 支")
        all_prices = {**data.get("prices", {}), **otc, **yf}
    else:
        all_prices = {**otc, **twse}   # 上市優先

    print(f"✅ 全市場合計 {len(all_prices)} 支")

    data["prices"] = all_prices

    # 4. 公司基本資料 / 自動生成
    print("🏢 公司資料更新中...")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    company_info = load_company_info()
    company_info = update_company_info(all_prices, company_info, api_key)
    with open("company_info.json", "w", encoding="utf-8") as f:
        json.dump(company_info, f, ensure_ascii=False, indent=2)
    print(f"   💾 company_info.json 已更新（{len(company_info)} 筆）")

    # 5. 機會點自動偵測（同時收集30日歷史供圖表用）
    print("🔍 機會點偵測中...")
    opps, histories = compute_opportunities(all_prices)
    data["opportunities"] = opps
    data["histories"] = histories
    print(f"   📊 儲存 {len(histories)} 支股票歷史走勢")

    # 6. 時間戳
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    data["updated_at"] = datetime.datetime.now(tz_tw).strftime("%Y/%m/%d %H:%M")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！更新時間: {data['updated_at']}")


if __name__ == "__main__":
    main()
