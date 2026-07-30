"""
台股資料抓取腳本 v3
- TWII:        Yahoo Finance（境外可用）
- 三大法人:    依序試多個 TWSE 端點（含開放資料 API，不限 IP）
- 全台個股:    先試 TWSE/TPEX 開放資料 API；若被擋，改用 Yahoo Finance 抓主要清單
GitHub Actions 從 GitHub 伺服器（美國）執行，開放資料 API 設計給境外存取。
"""

import json
import os
import re
import requests
import datetime
import time
try:
    import yfinance as yf
    _YF_AVAILABLE = True
except Exception:
    _YF_AVAILABLE = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# 備用清單：被動/主動型 ETF 持股監控清單（台股）+ ETF 本身
# 更新日期：2026-05-29（資料來源 etfinfo.tw）
FALLBACK_CODES = [
    # 半導體（0050/0056/00891/00929）
    "2330","3711","2454","2303","3661","6488","2449","3034","2379",
    "2344","6223","2408","3443","6239","2404","3105",
    # ABF載板（0050）
    "2383","3037",
    # AI伺服器（0050/0056/00929）
    "2382","6669","2356","2376","3231","2357","2324","2308","2317","2301","2059",
    # 液冷散熱
    "3017","8996",
    # 網通（0050）
    "2345",
    # 海運（0056）
    "2603",
    # 傳產
    "2002","6505",
    # 電信
    "2412",
    # 金融（0050/0056）
    "2881","2882","2891","2884","2885","2886","2887","2880",
    # 光學 / 被動元件 / 面板 / 航空 / 傳產追加
    "3008","2327","2492","2472",
    "3481","2409","2618","1101","1301",
    "6789",
    # 半導體（新增）
    "5347","3374","6182","2455","3583",
    # 網通（新增）
    "4906",
    # 海運 / 航空 / 電信 / 光學（新增）
    "2609","2610","4904","3484",
    # ETF
    "0050","0056","00878","00919","00929","00940",
    "00713","00757","00662","00891",
]


# ── 題材分組（用於機會點自動偵測）──────────────────────────
# 以被動/主動型 ETF 持股為監控範圍
# leaders: 該題材先行啟動的龍頭股（用來判斷題材是否熱絡）
# members: 同題材但尚未跟上的成員股（機會點候選）
THEME_GROUPS = {
    "半導體": {
        "leaders": ["2330", "3711", "2454"],
        "members": ["2303", "3661", "6488", "2449", "3034", "2379", "2344", "6223",
                    "2408", "3443", "6239", "2404", "3105", "5347", "3374"],
    },
    "ABF載板": {
        "leaders": ["2383"],
        "members": ["3037"],
    },
    "AI伺服器": {
        "leaders": ["2382", "6669", "2317"],
        "members": ["2357", "2356", "2376", "3231", "2324", "2308", "2301", "2059"],
    },
    "液冷散熱": {
        "leaders": ["3017"],
        "members": ["8996"],
    },
    "網通": {
        "leaders": ["2345"],
        "members": [],
    },
    "海運": {
        "leaders": ["2603"],
        "members": [],
    },
    "傳產": {
        "leaders": ["2002"],
        "members": ["6505"],
    },
    "金融": {
        "leaders": ["2881", "2882", "2891"],
        "members": ["2885"],
    },
    "被動元件": {
        "leaders": ["2327"],
        "members": ["2492", "3026", "6173", "3090", "2472"],
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


def _generate_story(code, name, theme, p5, p30, api_key):
    """用 Claude Haiku 生成個股近期故事 + 優缺點，回傳 dict {story, pros, cons}"""
    p5_txt  = (f"+{p5}%" if p5 and p5 > 0 else f"{p5}%") if p5 is not None else "持平"
    p30_txt = (f"+{p30}%" if p30 and p30 > 0 else f"{p30}%") if p30 is not None else "持平"
    prompt = (
        f"台股代號 {code}，公司名稱「{name}」，所屬題材：{theme}。\n"
        f"近5日漲幅：{p5_txt}，近30日漲幅：{p30_txt}。\n"
        "請用繁體中文，以 JSON 格式回傳以下三個欄位：\n"
        "- story: 2-3 句描述近期股價表現原因與題材催化劑（純文字）\n"
        "- pros: 3-4 點利多（字串陣列，每點 15 字以內）\n"
        "- cons: 3-4 點風險（字串陣列，每點 15 字以內）\n"
        "只輸出 JSON，不要其他文字。範例：\n"
        '{"story":"...", "pros":["點1","點2","點3"], "cons":["點1","點2","點3"]}'
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
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=25,
        )
        text = resp.json()["content"][0]["text"].strip()
        start = text.find("{"); end = text.rfind("}") + 1
        return json.loads(text[start:end]) if start >= 0 else None
    except Exception as e:
        print(f"   ⚠️ Story {code}: {e}")
        return None


def generate_stories(company_info, histories_json, all_prices, api_key=None, max_new=35):
    """為沒有 recent_story（或故事過期）的題材股生成近期股價故事（需 API，目前停用）"""
    print("   ⚠️ 故事生成已停用（無 API Key）")
    return company_info

    today_str = datetime.date.today().strftime("%Y/%m/%d")

    # 建立 code → theme 對照表
    code_theme = {}
    for theme, g in THEME_GROUPS.items():
        for code in list(g["leaders"]) + list(g["members"]):
            code_theme[code] = theme

    def pct(closes, n):
        if not closes or len(closes) < n + 1:
            return None
        base = closes[-(n + 1)]
        cur  = closes[-1]
        return round((cur - base) / base * 100, 1) if base > 0 else None

    generated = 0
    print(f"   📝 生成個股故事 + 優缺點（最多 {max_new} 筆）...")
    for code, theme in code_theme.items():
        if generated >= max_new:
            break
        entry = company_info.get(code, {})
        # 今天已生成且有 pros/cons 就跳過
        if entry.get("story_date") == today_str and entry.get("pros") and entry.get("cons"):
            continue
        hist = histories_json.get(code)
        if not hist:
            continue
        closes = hist["closes"]
        name   = all_prices.get(code, {}).get("name", code)
        p5     = pct(closes, 5)
        p30    = pct(closes, 30)
        result = _generate_story(code, name, theme, p5, p30, api_key)
        if result and isinstance(result, dict):
            entry = company_info.setdefault(code, {"generated": today_str})
            entry["recent_story"] = result.get("story", "")
            entry["pros"]         = result.get("pros", [])
            entry["cons"]         = result.get("cons", [])
            entry["story_date"]   = today_str
            company_info[code]    = entry
            generated += 1
            time.sleep(0.5)

    print(f"   ✅ 生成 {generated} 筆近期故事 + 優缺點")
    return company_info


_yf_session = None
_yf_crumb   = None

def _get_yf_crumb():
    """取得 Yahoo Finance crumb（新版 API 需要 cookie + crumb）"""
    global _yf_session, _yf_crumb
    if _yf_crumb:
        return _yf_session, _yf_crumb
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get("https://finance.yahoo.com/", timeout=10)
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        if r.ok and r.text.strip() and len(r.text.strip()) < 50:
            _yf_session = s
            _yf_crumb   = r.text.strip()
            return _yf_session, _yf_crumb
    except Exception:
        pass
    return None, None


def fetch_analyst_targets(codes):
    """
    從 Yahoo Finance 批次抓分析師共識目標價 + 近 180 天券商評等異動。
    回傳 {code: {mean, high, low, count, rec, rec_mean, ratings}} dict。
    """
    import math
    result = {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
    print(f"   📊 抓取 {len(codes)} 支分析師目標價...")
    for i, code in enumerate(codes):
        for suffix in [".TW", ".TWO"]:
            try:
                t = yf.Ticker(f"{code}{suffix}")
                info = t.info
                mean = info.get("targetMeanPrice")
                high = info.get("targetHighPrice")
                low  = info.get("targetLowPrice")
                cnt  = info.get("numberOfAnalystOpinions")
                rec  = info.get("recommendationKey", "")
                rec_m= info.get("recommendationMean")
                if mean and not (isinstance(mean, float) and math.isnan(mean)):
                    entry = {
                        "mean":  round(float(mean), 1),
                        "high":  round(float(high), 1) if high else None,
                        "low":   round(float(low),  1) if low  else None,
                        "count": int(cnt) if cnt else 0,
                        "rec":   rec.lower() if rec else "",
                        "rec_mean": round(float(rec_m), 2) if rec_m else None,
                        "updated": datetime.date.today().strftime("%Y/%m/%d"),
                    }
                    # 券商評等異動（近 180 天）
                    try:
                        df = t.get_upgrades_downgrades()
                        if df is not None and not df.empty:
                            df = df.reset_index()
                            date_col = "GradeDate" if "GradeDate" in df.columns else df.columns[0]
                            df["_date"] = df[date_col].astype(str).str[:10]
                            df = df[df["_date"] >= cutoff].sort_values("_date", ascending=False)
                            ratings = []
                            for _, row in df.head(10).iterrows():
                                ratings.append({
                                    "date":   row["_date"],
                                    "firm":   str(row.get("Firm", row.get("firm", ""))),
                                    "to":     str(row.get("ToGrade", row.get("toGrade", ""))),
                                    "from":   str(row.get("FromGrade", row.get("fromGrade", ""))),
                                    "action": str(row.get("Action", row.get("action", ""))),
                                })
                            entry["ratings"] = ratings
                    except Exception:
                        pass
                    result[code] = entry
                    break
            except Exception:
                continue
        if i % 20 == 19:
            print(f"   進度 {i+1}/{len(codes)}...")
        time.sleep(0.3)
    print(f"   ✅ 取得 {len(result)} 支目標價")
    return result


def fetch_trailing_pe(code):
    """從 Yahoo Finance 抓本益比（trailingPE）與 EPS（trailingEps）"""
    # 優先用 yfinance（自動處理 cookie/crumb，較穩定）
    if _YF_AVAILABLE:
        for suffix in [".TW", ".TWO"]:
            try:
                t = yf.Ticker(f"{code}{suffix}")
                info = t.info
                pe  = info.get("trailingPE")
                eps = info.get("trailingEps")
                if pe is not None or eps is not None:
                    return {
                        "pe":  round(pe,  1) if pe  is not None else None,
                        "eps": round(eps, 2) if eps is not None else None,
                    }
            except Exception:
                continue

    # fallback：手動 crumb + quoteSummary API
    session, crumb = _get_yf_crumb()
    if not session:
        session = requests.Session()
        session.headers.update(HEADERS)
    crumb_param = f"&crumb={crumb}" if crumb else ""

    for suffix in [".TW", ".TWO"]:
        try:
            url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   f"{code}{suffix}?modules=defaultKeyStatistics,summaryDetail{crumb_param}")
            r = session.get(url, timeout=12)
            if not r.ok:
                continue
            result = r.json().get("quoteSummary", {}).get("result") or []
            if not result:
                continue
            d = result[0]
            pe = (d.get("summaryDetail", {})
                   .get("trailingPE", {})
                   .get("raw"))
            eps = (d.get("defaultKeyStatistics", {})
                    .get("trailingEps", {})
                    .get("raw"))
            if pe is not None or eps is not None:
                return {
                    "pe":  round(pe,  1) if pe  is not None else None,
                    "eps": round(eps, 2) if eps is not None else None,
                }
        except Exception:
            continue
    return {}


def fetch_quarterly_eps(code, n_quarters=8):
    """
    從 Yahoo Finance 抓近 n_quarters 季實際 EPS，含季末日期（用於 PE 河流圖）。
    回傳 [{"q":"2024Q1","eps":3.5,"date":"2024-03-31"}, ...]，舊→新排列。
    只回傳季末日期 <= 今日的實際財報數據，排除未來估計值。
    """
    import math
    today_str = datetime.date.today().isoformat()  # "YYYY-MM-DD"
    if _YF_AVAILABLE:
        for suffix in [".TW", ".TWO"]:
            try:
                t = yf.Ticker(f"{code}{suffix}")
                df = t.quarterly_income_stmt
                if df is None or df.empty:
                    continue
                eps_row = next((r for r in df.index if "Diluted EPS" in str(r)), None)
                if eps_row is None:
                    continue
                out = []
                for col in list(df.columns):  # 全部欄位都看，再過濾
                    val = df.loc[eps_row, col]
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        if hasattr(col, "strftime"):
                            date_str = col.strftime("%Y-%m-%d")
                            # 排除未來季度（尚未公布的估計值）
                            if date_str > today_str:
                                continue
                            q_num = (col.month - 1) // 3 + 1
                            label = f"{col.year}Q{q_num}"
                        else:
                            date_str = str(col)[:10]
                            if date_str > today_str:
                                continue
                            label = str(col)[:7]
                        out.append({"q": label, "eps": round(float(val), 2), "date": date_str})
                # 取最近 n_quarters 筆（去重複、按日期排序）
                out_sorted = sorted(out, key=lambda x: x["date"])
                out_sorted = out_sorted[-n_quarters:]
                if out_sorted:
                    return out_sorted  # 舊到新
            except Exception:
                continue

    # fallback：quoteSummary with crumb（只有4季、無日期）
    session, crumb = _get_yf_crumb()
    if not session:
        session = requests.Session()
        session.headers.update(HEADERS)
    crumb_param = f"&crumb={crumb}" if crumb else ""
    for suffix in [".TW", ".TWO"]:
        try:
            url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   f"{code}{suffix}?modules=earnings{crumb_param}")
            r = session.get(url, timeout=12)
            if not r.ok:
                continue
            result = r.json().get("quoteSummary", {}).get("result") or []
            if not result:
                continue
            quarters = (result[0].get("earnings", {})
                                  .get("earningsChart", {})
                                  .get("quarterly", []))
            out = []
            for q in quarters[-4:]:
                actual = q.get("actual", {})
                eps_val = actual.get("raw") if isinstance(actual, dict) else None
                if eps_val is not None:
                    out.append({"q": q.get("date", ""), "eps": round(eps_val, 2), "date": ""})
            if out:
                return out
        except Exception:
            continue
    return []


def build_pe_river(code, quarterly_eps):
    """
    用 2 年股價歷史 + 逐季 EPS 建立 PE 河流圖資料。
    quarterly_eps: [{"q":..., "eps":..., "date":"2024-03-31"}, ...]  舊→新
    回傳 {"dates":[...], "closes":[...], "ttm":[...]} 或 None。
    ttm[i] 是 dates[i] 當天的 trailing 12-month EPS（4季加總）。
    """
    if not quarterly_eps or len(quarterly_eps) < 4:
        return None

    # 只取有日期的季度
    dated = [q for q in quarterly_eps if q.get("date")]
    if len(dated) < 4:
        return None

    # 抓 2 年日線
    closes, dates, _ = fetch_price_history(code)
    if not closes or not dates:
        return None

    # 只保留最近 3 年（約 780 個交易日）
    THREE_YEARS = 790
    if len(closes) > THREE_YEARS:
        closes = closes[-THREE_YEARS:]
        dates  = dates[-THREE_YEARS:]

    # 建立每個交易日的 TTM EPS
    # 邏輯：對每個 date，找在它之前（含當日）已結束的最近 4 季，加總
    ttm_series = []
    for d in dates:
        # 轉成可比較字串
        available = [q for q in dated if q["date"] <= d.replace("/", "-")]
        if len(available) < 4:
            # 資料不足 4 季，用現有的加總（可能不完整，先填 None）
            ttm_series.append(None)
        else:
            recent4 = available[-4:]
            ttm = round(sum(q["eps"] for q in recent4), 2)
            ttm_series.append(ttm)

    # 如果全部都是 None，回傳 None
    valid = [v for v in ttm_series if v is not None]
    if not valid:
        return None

    # 裁剪：只保留第一個有效 TTM 以後的資料，確保河流圖真實反映 EPS 變動
    first_idx = next(i for i, v in enumerate(ttm_series) if v is not None)
    dates      = dates[first_idx:]
    closes     = closes[first_idx:]
    ttm_series = ttm_series[first_idx:]

    if len(dates) < 10:
        return None

    return {"dates": dates, "closes": closes, "ttm": ttm_series}


def update_company_info(all_prices, company_info, api_key=None, max_new=50):
    """
    更新本益比與季 EPS（公司描述生成已停用，不依賴 Claude API）。
    """
    today_str = datetime.date.today().strftime("%Y/%m/%d")

    # ── 優先補齊的代號（主清單 FALLBACK_CODES + 題材分組，涵蓋所有監控清單用到的股票）
    # 舊版只用 THEME_GROUPS（47支），FALLBACK_CODES（74支）裡有些股票（如台泥、中華電、華航）
    # 從未被納入，導致這些股票的 EPS 永遠是空的、健檢頁面永遠顯示「無資料」。
    priority = set(FALLBACK_CODES)
    for g in THEME_GROUPS.values():
        priority.update(g["leaders"])
        priority.update(g["members"])

    # ── 更新本益比 + 季 EPS（優先補還沒有 EPS 的代號，其餘才輪流補新資料）
    missing = [c for c in priority if not company_info.get(c, {}).get("eps")]
    have    = [c for c in priority if company_info.get(c, {}).get("eps")]
    pe_codes = (missing + have)[:60]  # 每天最多抓 60 支，避免超時
    print(f"   （尚無 EPS 的股票 {len(missing)} 支，優先補齊）")
    print(f"   📊 更新 {len(pe_codes)} 支本益比與季 EPS...")
    for code in pe_codes:
        pe_data = fetch_trailing_pe(code)
        if pe_data:
            entry = company_info.setdefault(code, {"generated": today_str})
            entry.update(pe_data)
        qeps = fetch_quarterly_eps(code, n_quarters=12)
        if qeps:
            entry = company_info.setdefault(code, {"generated": today_str})
            entry["quarterly_eps"] = qeps
        time.sleep(0.3)

    return company_info


# ── 個股歷史價格（Yahoo Finance）─────────────────────────

def fetch_price_history(code):
    """
    從 Yahoo Finance 抓個股近 1 年日線（含成交量）。
    回傳 (closes, dates, volumes)，先試 .TW 再試 .TWO。失敗回傳 (None, None, None)。
    """
    for suffix in [".TW", ".TWO"]:
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
                   f"{code}{suffix}?interval=1d&range=5y")
            r = requests.get(url, headers=HEADERS, timeout=15)
            result = r.json()["chart"]["result"][0]
            timestamps  = result.get("timestamp", [])
            quote       = result["indicators"]["quote"][0]
            closes_raw  = quote.get("close",  [])
            volumes_raw = quote.get("volume", [])
            # 補齊 volumes 長度（偶爾比 closes 短）
            volumes_raw = list(volumes_raw) + [None] * max(0, len(closes_raw) - len(volumes_raw))
            triples = [
                (t, c, v)
                for t, c, v in zip(timestamps, closes_raw, volumes_raw)
                if c is not None
            ]
            if len(triples) >= 6:
                closes  = [c for _, c, _ in triples]
                tz_tw   = datetime.timezone(datetime.timedelta(hours=8))
                dates   = [datetime.datetime.fromtimestamp(t, tz=tz_tw).strftime("%Y/%m/%d")
                           for t, _, _ in triples]
                volumes = [int(v) if v is not None else 0 for _, _, v in triples]
                return closes, dates, volumes
        except Exception:
            continue
    return None, None, None


def compute_opportunities(all_prices, extra_codes=None, company_info=None, inst_stocks_ref=None):
    """
    比較各題材龍頭 vs 成員的真實 30 日漲幅，
    自動找出「龍頭已大漲、但成員還沒動」的機會點。
    extra_codes: 額外保證抓歷史的代號清單（如監控清單 FALLBACK_CODES）
    company_info: {code: {eps, pe, ...}} 用於過濾虧損股
    inst_stocks_ref: {code: {f, t, s}} 法人資料，用於信心評分
    回傳 list of dict，每筆包含 code/name/theme/p30/p5/leader/leader_p30/gap/reason/eps/profit_ok/score。
    """
    LEADER_THRESHOLD = 12   # 龍頭 30 日漲幅需 >= 12% 才算題材熱絡
    GAP_THRESHOLD    = 8    # 成員落後龍頭 >= 8% 才算有機會
    MAX_MEMBER_P30   = 7    # 成員 30 日漲幅 <= 7% 才算「尚未啟動」
    MIN_MEMBER_P30   = -3   # 成員 30 日跌幅不能超過 -3%（跌太多代表有基本面問題）

    # 合併 stock_info.json（靜態）與 company_info（動態）取得 eps/pe
    si_static = {}
    try:
        with open("stock_info.json", "r", encoding="utf-8") as f:
            si_static = json.load(f)
    except Exception:
        pass
    ci = company_info or {}

    def get_eps(code):
        """回傳最新 EPS（優先 company_info 動態值，再用 stock_info.json 靜態值）"""
        return (ci.get(code, {}).get("eps")
                or si_static.get(code, {}).get("eps")
                if isinstance(si_static.get(code), dict) else None)

    def get_pe(code):
        """回傳最新 PE（同上）"""
        return (ci.get(code, {}).get("pe")
                or (si_static.get(code, {}).get("pe")
                    if isinstance(si_static.get(code), dict) else None))

    # 收集所有需要歷史資料的代號（THEME_GROUPS + 額外監控股票）
    all_codes = set()
    for g in THEME_GROUPS.values():
        all_codes.update(g["leaders"])
        all_codes.update(g["members"])
    if extra_codes:
        all_codes.update(extra_codes)   # 保證監控清單全部有歷史

    # 批次抓取歷史收盤價
    histories      = {}        # code → closes list（用於 pct 計算）
    histories_json = {}        # code → {labels, closes}（存入 data.json 供圖表用）
    codes_list = sorted(all_codes)
    print(f"   📈 抓取 {len(codes_list)} 支個股歷史（機會點偵測，最近1年）...")
    for i, code in enumerate(codes_list, 1):
        closes, dates, volumes = fetch_price_history(code)
        if closes:
            histories[code] = closes          # 完整年資料，供 pct 計算
            histories_json[code] = {
                "labels":  dates,                           # 全年日期（~252 筆）
                "closes":  [round(c, 1) for c in closes],  # 全年收盤
                "volumes": volumes,                         # 全年成交量
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

            # ── 獲利能力檢查（EPS / PE < 0 代表虧損）──────────────────
            eps_val = get_eps(code)
            pe_val  = get_pe(code)
            if eps_val is not None and eps_val < 0:
                continue   # 確認虧損，排除機會點
            if pe_val is not None and pe_val < 0:
                continue   # 負本益比 = 虧損，排除
            # profit_ok: True=確認獲利, False=確認虧損, None=資料不足
            if eps_val is not None:
                profit_ok = eps_val > 0
            elif pe_val is not None:
                profit_ok = pe_val > 0
            else:
                profit_ok = None   # 未知，仍納入但前端標示

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
                "eps":        eps_val,
                "profit_ok":  profit_ok,
            })

    # ── 信心評分 ──────────────────────────────────────────────
    for opp in opportunities:
        score = 0
        tags  = []
        code  = opp["code"]

        lp30 = opp["leader_p30"]
        gap  = opp["gap"]
        m_p30 = opp["p30"]
        m_p5  = opp.get("p5") or 0
        eps   = opp.get("eps")
        inst  = inst_stocks_ref.get(code, {}) if inst_stocks_ref else {}
        f5    = inst.get("f", 0)   # 外資近5日億元

        # 龍頭熱度
        if lp30 >= 30:
            score += 25; tags.append("龍頭強勢")
        elif lp30 >= 15:
            score += 15; tags.append("龍頭溫和")

        # 落差大小
        if gap >= 25:
            score += 25; tags.append("落差極大")
        elif gap >= 15:
            score += 18; tags.append("落差明顯")
        elif gap >= 8:
            score += 10

        # 跟隨股近5日沒有跟漲（真的沒動）
        if m_p5 <= 2:
            score += 15; tags.append("尚未啟動")
        elif m_p5 <= 5:
            score += 8

        # 外資近5日買超
        if f5 > 0.5:
            score += 15; tags.append("外資買超")
        elif f5 < -0.5:
            score -= 10; tags.append("外資賣超")

        # EPS 基本面
        if eps is not None and eps > 0:
            score += 10; tags.append("獲利正常")
        elif eps is None:
            score += 5  # 不扣分但也不加滿

        # 近30日成員漲幅不宜過高（還有空間）
        if m_p30 < 5:
            score += 10
        elif m_p30 > 15:
            score -= 5

        score = max(0, min(100, score))
        if score >= 75:
            grade = "⭐⭐⭐"
        elif score >= 55:
            grade = "⭐⭐"
        else:
            grade = "⭐"

        opp["score"]      = score
        opp["score_grade"] = grade
        opp["score_tags"] = tags

    # 按信心評分 > 落差排序
    opportunities.sort(key=lambda x: (x["score"], x["gap"]), reverse=True)
    print(f"   🎯 機會點偵測完成: {len(opportunities)} 支")
    return opportunities, histories_json


# ── 加權指數歷史（用於大盤走勢摘要）────────────────────────

def fetch_twii_history():
    """
    從 Yahoo Finance 抓大盤（^TWII）近1年日線。
    回傳 (closes, dates)，失敗回傳 (None, None)。
    """
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&range=5y"
        r = requests.get(url, headers=HEADERS, timeout=15)
        result = r.json()["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes_raw = result["indicators"]["quote"][0].get("close", [])
        pairs = [(t, c) for t, c in zip(timestamps, closes_raw) if c is not None]
        if len(pairs) >= 10:
            closes = [round(c, 1) for c in [c for _, c in pairs]]
            tz_tw  = datetime.timezone(datetime.timedelta(hours=8))
            dates  = [datetime.datetime.fromtimestamp(t, tz=tz_tw).strftime("%Y/%m/%d")
                      for t, _ in pairs]
            return closes, dates
    except Exception as e:
        print(f"   ⚠️ TWII 歷史: {e}")
    return None, None


def _generate_twii_summary(closes, dates, cur_price, api_key):
    """用 Claude Haiku 生成大盤走勢一句話摘要"""
    if not api_key or not closes or len(closes) < 20:
        return None

    # 計算關鍵統計
    cur   = closes[-1]
    p3m   = closes[-63:] if len(closes) >= 63 else closes
    p6m   = closes[-126:] if len(closes) >= 126 else closes
    low3  = min(p3m);  low3_d  = dates[closes.index(min(p3m, key=lambda x: x)) + max(0, len(closes)-63)]
    high3 = max(p3m);  high3_d = dates[closes.index(max(p3m, key=lambda x: x)) + max(0, len(closes)-63)]
    chg3  = round((cur - p3m[0]) / p3m[0] * 100, 1) if p3m[0] else 0
    drop  = round((high3 - low3) / high3 * 100, 1) if high3 else 0

    # 更簡單的計算方式
    idx_base = max(0, len(closes) - 63)
    p3_base   = closes[idx_base]
    chg3      = round((cur - p3_base) / p3_base * 100, 1) if p3_base else 0
    low3      = min(closes[idx_base:])
    high3     = max(closes[idx_base:])
    low3_d    = dates[idx_base + closes[idx_base:].index(low3)]
    high3_d   = dates[idx_base + closes[idx_base:].index(high3)]
    drop_from_high = round((high3 - low3) / high3 * 100, 1) if high3 else 0
    rally         = round((cur - low3) / low3 * 100, 1) if low3 else 0

    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    prompt = (
        f"今天是 {today_str}，台灣加權指數（^TWII）目前 {cur:,.0f} 點。\n"
        f"近三個月：起點 {p3_base:,.0f} 點（{dates[idx_base]}），"
        f"區間最低 {low3:,.0f} 點（{low3_d}，跌幅 {drop_from_high}%），"
        f"區間最高 {high3:,.0f} 點（{high3_d}），"
        f"三個月整體漲跌 {chg3:+.1f}%，"
        f"從低點反彈 {rally:+.1f}%。\n"
        "請用繁體中文寫 2 句話：第一句描述三個月內的重大走勢（重挫或大漲、關鍵點位），"
        "第二句描述目前狀態與近期趨勢。直接輸出文字，不要引號或標題。"
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
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"   ⚠️ 大盤摘要生成失敗: {e}")
        return None


def generate_daily_commentary(twii, inst, all_prices, histories, api_key=None):
    """
    整合今日大盤、法人、題材表現，用規則式邏輯生成 3-4 句日評。
    不依賴 Claude API。
    """
    THEME_MAP = {
        "2330":"晶圓代工","2303":"晶圓代工","5347":"晶圓代工","6789":"晶圓代工",
        "2454":"IC設計","3661":"IC設計","3034":"IC設計","2379":"IC設計","3443":"IC設計",
        "3711":"半導體封測","2449":"半導體封測","6223":"半導體封測","6239":"半導體封測","3374":"半導體封測",
        "2344":"記憶體","2408":"記憶體","6488":"矽晶圓",
        "3105":"化合物半導體","2404":"半導體設備",
        "2383":"ABF載板","3037":"ABF載板",
        "2382":"AI伺服器","6669":"AI伺服器","2356":"AI伺服器","2376":"AI伺服器",
        "3231":"AI伺服器","2357":"AI伺服器","2324":"AI伺服器","2308":"AI伺服器",
        "2317":"AI伺服器","2301":"AI伺服器","2059":"AI伺服器",
        "3017":"液冷散熱","8996":"液冷散熱","2345":"網通","4906":"網通",
        "3481":"面板","2409":"面板","2603":"海運","2609":"海運",
        "2618":"航空","2610":"航空","2002":"傳產","6505":"傳產","1101":"傳產","1301":"傳產",
        "2412":"電信","4904":"電信","2881":"金融","2882":"金融","2891":"金融","2885":"金融",
        "3008":"光學","2327":"被動元件","2492":"被動元件","2472":"被動元件",
    }

    # ── 大盤
    price = twii.get("price", 0)
    chgP  = twii.get("chgP", 0)
    chg   = twii.get("chg", 0)
    if abs(chgP) >= 2:
        trend_word = "大漲" if chgP > 0 else "大跌"
    elif abs(chgP) >= 0.5:
        trend_word = "上漲" if chgP > 0 else "下跌"
    else:
        trend_word = "小幅收紅" if chgP >= 0 else "小幅收黑"

    # ── 法人（億元）
    foreign = round(inst.get("foreign", 0) / 1e8, 0)
    trust   = round(inst.get("trust",   0) / 1e8, 0)
    dealer  = round(inst.get("dealer",  0) / 1e8, 0)
    total   = foreign + trust + dealer

    if foreign > 0:
        inst_lead = f"外資買超 {abs(foreign):.0f} 億"
    elif foreign < 0:
        inst_lead = f"外資賣超 {abs(foreign):.0f} 億"
    else:
        inst_lead = "外資持平"

    if total > 0:
        inst_tone = f"三大法人合計買超 {abs(total):.0f} 億，資金動能偏多"
    elif total < 0:
        inst_tone = f"三大法人合計賣超 {abs(total):.0f} 億，籌碼略顯鬆動"
    else:
        inst_tone = "三大法人動向中性"

    # ── 題材強弱
    theme_chg = {}
    for code, theme in THEME_MAP.items():
        p = all_prices.get(code, {})
        c = p.get("changeP")
        if c is not None:
            theme_chg.setdefault(theme, []).append(c)
    theme_avg = {t: round(sum(v)/len(v), 2) for t, v in theme_chg.items() if v}
    top3 = sorted(theme_avg.items(), key=lambda x: x[1], reverse=True)[:3]
    bot3 = sorted(theme_avg.items(), key=lambda x: x[1])[:3]
    top_txt = "、".join(f"{t}（{v:+.1f}%）" for t, v in top3)
    bot_txt = "、".join(f"{t}（{v:+.1f}%）" for t, v in bot3)

    # ── 個股異動
    movers_up, movers_dn = [], []
    for code in THEME_MAP:
        p = all_prices.get(code, {})
        c = p.get("changeP")
        name = p.get("name", code)
        if c is None:
            continue
        if c >= 5:
            movers_up.append(f"{name}（{c:+.1f}%）")
        elif c <= -5:
            movers_dn.append(f"{name}（{c:+.1f}%）")

    # ── 組裝句子
    s1 = f"今日加權指數{trend_word} {chgP:+.2f}%，收 {price:,.0f} 點（{chg:+.0f}），{inst_lead}，{inst_tone}。"
    s2 = f"題材面以{top_txt}表現較強；{bot_txt}相對落後。"

    if movers_up and movers_dn:
        s3 = f"個股表現分化，{movers_up[0]} 等強勢，{movers_dn[0]} 等承壓。"
    elif movers_up:
        s3 = f"個股亮點為 {movers_up[0]}{'、' + movers_up[1] if len(movers_up) > 1 else ''}，漲幅顯著。"
    elif movers_dn:
        s3 = f"留意 {movers_dn[0]}{'、' + movers_dn[1] if len(movers_dn) > 1 else ''} 等個股壓力。"
    else:
        s3 = "監控個股漲跌幅度溫和，無明顯異常波動。"

    # ── 第四句：短線展望
    if chgP >= 1.5 and total > 0:
        s4 = "法人買盤持續，短線多方氣氛偏強，可留意強勢族群續漲機會。"
    elif chgP <= -1.5 and total < 0:
        s4 = "法人持續調節，短線需注意支撐能否守穩，建議控管部位風險。"
    elif abs(chgP) < 0.3:
        s4 = "大盤量縮整理，方向未明，宜觀望等待訊號明朗化。"
    else:
        s4 = "整體走勢偏向盤堅，可逢低留意具題材支撐的個股機會。"

    result = " ".join([s1, s2, s3, s4])
    print(f"   ✅ 規則式日評生成完成（{len(result)} 字）")
    return result


def detect_stock_movers(all_prices, histories, inst_stocks):
    """
    偵測今日漲跌幅 >=5% 的監控個股，生成一句規則式說明。
    回傳 {code: {name, changeP, reason}} dict，存入 data["movers"]。
    """
    WATCH_CODES = set()
    for g in THEME_GROUPS.values():
        WATCH_CODES.update(g["leaders"])
        WATCH_CODES.update(g["members"])

    def pct(closes, n):
        if not closes or len(closes) < n + 1:
            return None
        base = closes[-(n + 1)]
        cur  = closes[-1]
        return round((cur - base) / base * 100, 1) if base > 0 else None

    movers = {}
    for code in WATCH_CODES:
        p = all_prices.get(code, {})
        chgP = p.get("changeP")
        if chgP is None or abs(chgP) < 5:
            continue

        name  = p.get("name", code)
        direction = "大漲" if chgP > 0 else "大跌"
        sign  = "+" if chgP > 0 else ""

        # 拿近期走勢數據
        hist   = histories.get(code, {})
        closes = hist.get("closes", [])
        p5     = pct(closes, 5)
        p30    = pct(closes, 30)

        # 法人狀況
        inst   = inst_stocks.get(code, {})
        f5     = inst.get("f", 0)
        if f5 > 0.3:
            inst_txt = f"外資近5日買超 {f5:.1f} 億"
        elif f5 < -0.3:
            inst_txt = f"外資近5日賣超 {abs(f5):.1f} 億"
        else:
            inst_txt = "外資動向中性"

        # 組裝理由
        context = []
        if p30 is not None:
            trend_30 = f"近30日{'已漲' if p30 > 0 else '已跌'}{abs(p30):.0f}%"
            context.append(trend_30)
        context.append(inst_txt)
        if chgP > 0 and p30 is not None and p30 > 20:
            context.append("留意高位追漲風險")
        elif chgP < 0 and p30 is not None and p30 < -10:
            context.append("下跌趨勢留意支撐")

        reason = f"今日{direction} {sign}{chgP:.1f}%，{' / '.join(context)}"
        movers[code] = {"name": name, "changeP": chgP, "reason": reason}

    print(f"   ✅ 個股異動偵測：{len(movers)} 支（±5%）")
    return movers


def generate_weekly_summary(all_prices, histories, inst, twii):
    """
    產生本週台股敘事摘要段落，存入 weekly_report.html 的 data-summary 或由 generate_report.py 讀取。
    回傳純文字字串。
    """
    def pct(closes, n):
        if not closes or len(closes) < n + 1:
            return None
        base = closes[-(n + 1)]
        cur  = closes[-1]
        return round((cur - base) / base * 100, 1) if base > 0 else None

    # ── 本週大盤
    twii_p5 = None
    twii_hist = histories.get("^TWII") or histories.get("TWII")
    if twii_hist:
        twii_p5 = pct(twii_hist.get("closes", []), 5)
    twii_chgP = twii.get("chgP", 0)
    twii_price = twii.get("price", 0)

    # ── 各個股本週漲跌
    weekly = []
    for code, p in all_prices.items():
        hist = histories.get(code, {})
        closes = hist.get("closes", [])
        w = pct(closes, 5)
        name = p.get("name", code)
        if w is not None:
            weekly.append((code, name, w))

    if not weekly:
        return None

    weekly.sort(key=lambda x: x[2], reverse=True)
    top5 = weekly[:5]
    bot5 = weekly[-5:][::-1]  # 跌最多的5支

    top_txt = "、".join(f"{n}（{v:+.1f}%）" for _, n, v in top5 if v > 0)
    bot_txt = "、".join(f"{n}（{v:+.1f}%）" for _, n, v in bot5 if v < 0)

    # ── 題材強弱（用 THEME_GROUPS）
    theme_weekly = {}
    for theme, g in THEME_GROUPS.items():
        vals = []
        for code in list(g["leaders"]) + list(g["members"]):
            hist = histories.get(code, {})
            w = pct(hist.get("closes", []), 5)
            if w is not None:
                vals.append(w)
        if vals:
            theme_weekly[theme] = round(sum(vals) / len(vals), 1)

    top_theme = sorted(theme_weekly.items(), key=lambda x: x[1], reverse=True)[:3]
    bot_theme = sorted(theme_weekly.items(), key=lambda x: x[1])[:2]
    top_theme_txt = "、".join(f"{t}（平均{v:+.1f}%）" for t, v in top_theme if v > 0)
    bot_theme_txt = "、".join(f"{t}（{v:+.1f}%）" for t, v in bot_theme if v < 0)

    # ── 法人
    foreign = round(inst.get("foreign", 0) / 1e8, 0)
    if foreign > 0:
        inst_txt = f"外資本週買超 {foreign:.0f} 億"
    elif foreign < 0:
        inst_txt = f"外資本週賣超 {abs(foreign):.0f} 億"
    else:
        inst_txt = "外資本週持平"

    # ── 組句
    if twii_chgP >= 1:
        s1 = f"本週加權指數收漲 {twii_chgP:+.2f}%，收 {twii_price:,.0f} 點，多方氣氛延續。"
    elif twii_chgP <= -1:
        s1 = f"本週加權指數收跌 {twii_chgP:.2f}%，收 {twii_price:,.0f} 點，市場偏弱整理。"
    else:
        s1 = f"本週加權指數小幅波動，週收 {twii_price:,.0f} 點（{twii_chgP:+.2f}%），方向不明朗。"

    s2 = ""
    if top_theme_txt:
        s2 += f"題材面以{top_theme_txt}表現最佳"
    if bot_theme_txt:
        s2 += f"；{bot_theme_txt}相對落後"
    if s2:
        s2 += f"。{inst_txt}。"
    else:
        s2 = f"{inst_txt}。"

    s3 = ""
    if top_txt:
        s3 += f"本週個股亮點為{top_txt}"
    if bot_txt:
        s3 += f"；跌幅較深者有{bot_txt}"
    if s3:
        s3 += "。"

    result = s1 + " " + s2
    if s3:
        result += " " + s3

    print(f"   ✅ 週報敘事段落生成（{len(result)} 字）")
    return result


# ── 融資餘額（觀察融資是否洗乾淨）──────────────────────────

def fetch_margin_summary():
    """
    從 TWSE OpenAPI MI_MARGN 抓全市場融資餘額（加總所有個股，單位：張）。
    回傳 {balance_today, balance_prev, change_lots, change_pct} 或 None。
    """
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
        r = requests.get(url, headers=HEADERS, timeout=25)
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return None

        def to_int(v):
            try:
                return int(str(v).replace(",", "").strip() or 0)
            except Exception:
                return 0

        today = sum(to_int(row.get("融資今日餘額")) for row in rows)
        prev  = sum(to_int(row.get("融資前日餘額")) for row in rows)
        if today <= 0 or prev <= 0:
            return None

        change_lots = today - prev
        change_pct  = round(change_lots / prev * 100, 2)
        return {
            "balance_today": today,
            "balance_prev":  prev,
            "change_lots":   change_lots,
            "change_pct":    change_pct,
            "date": datetime.date.today().strftime("%Y/%m/%d"),
        }
    except Exception as e:
        print(f"   ⚠️ 融資餘額: {e}")
        return None


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


# ── VIX 恐慌指數 ─────────────────────────────────────────

def fetch_usdtwd():
    """Yahoo Finance — USD/TWD 匯率"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/USDTWD%3DX?interval=1d&range=5d"
        r = requests.get(url, headers=HEADERS, timeout=10)
        result = r.json()["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c]
        if len(closes) < 2:
            return None
        price = round(closes[-1], 3)
        prev  = round(closes[-2], 3)
        chg   = round(price - prev, 3)
        chgP  = round(chg / prev * 100, 2)
        return {"price": price, "chg": chg, "chgP": chgP}
    except Exception as e:
        print(f"   ⚠️ USD/TWD: {e}")
        return None


def fetch_vix():
    """Yahoo Finance — ^VIX，境外可用"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=2d"
        r = requests.get(url, headers=HEADERS, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
        if not price or not prev:
            return None
        return {
            "price": round(price, 2),
            "chg":   round(price - prev, 2),
            "chgP":  round((price - prev) / prev * 100, 2),
        }
    except Exception as e:
        print(f"   ⚠️ VIX: {e}")
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

    # 今天的 ROC 日期字串，例如 "1150713"
    today_roc = str(datetime.date.today().year - 1911) + datetime.date.today().strftime("%m%d")

    # ── 端點 1：openapi.twse.com.tw ──
    for url in [
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL",
    ]:
        try:
            r = requests.get(url, headers=headers_rwd, timeout=30)
            rows = r.json()
            if isinstance(rows, list) and rows:
                # 驗證資料日期是否為今天
                sample_date = str(rows[0].get("Date", "")).replace("/", "")
                if sample_date and sample_date != today_roc:
                    print(f"   ⚠️ openapi 資料日期 {sample_date} 非今天 {today_roc}，略過")
                    break
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
            # 驗證日期（title 通常含日期，如 "112年01月05日 各類指數日成交量值"）
            title = resp.get("title", "")
            rwd_date = re.search(r"(\d+)年(\d+)月(\d+)日", title)
            if rwd_date:
                rwd_roc = rwd_date.group(1) + rwd_date.group(2) + rwd_date.group(3)
                if rwd_roc != today_roc:
                    print(f"   ⚠️ rwd STOCK_DAY_ALL 日期 {rwd_roc} 非今天 {today_roc}，略過")
                else:
                    prices = _parse_twse_rwd_rows(resp)
                    if prices:
                        print(f"   ✅ rwd STOCK_DAY_ALL → {len(prices)} 支")
                        return prices
            else:
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
            title = resp.get("title", "")
            rwd_date = re.search(r"(\d+)年(\d+)月(\d+)日", title)
            if rwd_date:
                rwd_roc = rwd_date.group(1) + rwd_date.group(2) + rwd_date.group(3)
                if rwd_roc != today_roc:
                    print(f"   ⚠️ rwd MI_INDEX 日期 {rwd_roc} 非今天 {today_roc}，略過")
                    # fall through to Yahoo fallback
                else:
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
                   f"{code}{suffix}?interval=1d&range=5d")
            r    = requests.get(url, headers=HEADERS, timeout=10)
            result = r.json()["chart"]["result"][0]
            meta  = result["meta"]
            price = meta["regularMarketPrice"]
            prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
            if not price or not prev:
                continue
            mkt_ts = meta.get("regularMarketTime")
            mkt_date = (datetime.datetime.fromtimestamp(mkt_ts).strftime("%Y/%m/%d")
                        if mkt_ts else datetime.date.today().strftime("%Y/%m/%d"))
            # 從 indicators.quote 取今日開盤/最高/最低（meta 欄位台股常為 None）
            quote = result.get("indicators", {}).get("quote", [{}])[0]
            opens  = [v for v in quote.get("open",  []) if v is not None]
            highs  = [v for v in quote.get("high",  []) if v is not None]
            lows   = [v for v in quote.get("low",   []) if v is not None]
            vols   = [v for v in quote.get("volume",[]) if v is not None]
            open_p = round(opens[-1],  2) if opens  else round(meta.get("regularMarketOpen",  0) or 0, 2)
            high_p = round(highs[-1],  2) if highs  else round(meta.get("regularMarketDayHigh",0) or 0, 2)
            low_p  = round(lows[-1],   2) if lows   else round(meta.get("regularMarketDayLow", 0) or 0, 2)
            vol    = int(vols[-1])         if vols   else meta.get("regularMarketVolume", 0)
            return {
                "price":   round(price, 2),
                "change":  round(price - prev, 2),
                "changeP": round((price - prev) / prev * 100, 2),
                "open":    open_p,
                "high":    high_p,
                "low":     low_p,
                "vol":     vol,
                "date":    mkt_date,
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


# ── ETF 持股比重（TWSE OpenAPI / etfinfo.tw）─────────────────

# etfinfo.tw 支援的被動型 ETF（台股上市）
ETF_ETFINFO_CODES = ["0050", "0056", "00929", "00891"]

def _fetch_etfinfo_holdings(etf_code, top_n=50):
    """從 etfinfo.tw 抓取 ETF 前 N 大成分股（SSR 頁面，requests 可直接解析）"""
    url = f"https://www.etfinfo.tw/etf/{etf_code}/holdings"
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "text/html"}, timeout=25)
        if not r.ok:
            return None
        html = r.text

        # ── 策略 1：__NEXT_DATA__ JSON（Next.js SSR）──
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            try:
                page_data = json.loads(m.group(1))
                props = page_data.get("props", {}).get("pageProps", {})
                raw = (props.get("holdings") or props.get("etfHoldings") or
                       props.get("stockList") or props.get("components"))
                if raw and isinstance(raw, list):
                    result = []
                    for h in raw:
                        code = str(h.get("code") or h.get("stockCode") or h.get("id") or "").strip()
                        name = str(h.get("name") or h.get("stockName") or h.get("n") or "").strip()
                        weight = float(h.get("weight") or h.get("ratio") or h.get("w") or 0)
                        if code and code not in ("C_NTD", "CASH") and weight > 0:
                            result.append({"code": code, "name": name, "weight": round(weight, 2)})
                    if result:
                        result.sort(key=lambda x: -x["weight"])
                        return result[:top_n]
            except Exception:
                pass

        # ── 策略 2：BeautifulSoup HTML table 解析 ──
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            result = []
            seen = set()
            for tr in soup.find_all("tr"):
                stock_link = tr.find("a", href=re.compile(r"/stock/\d+"))
                if not stock_link:
                    continue
                m2 = re.search(r"/stock/(\d+)", stock_link.get("href", ""))
                if not m2:
                    continue
                code = m2.group(1)
                if code in seen:
                    continue
                seen.add(code)
                # 名稱（td 裡去除 code 數字後的文字）
                td = stock_link.find_parent("td")
                name = td.get_text(strip=True).replace(code, "").strip()[:10] if td else ""
                # 比重：找該 row 裡第一個純數字百分比的 <strong>（不含 +/-）
                weight = 0.0
                for strong in tr.find_all("strong"):
                    t = strong.get_text().strip()
                    if re.match(r"^[\d.]+%$", t):
                        weight = float(t.rstrip("%"))
                        break
                if weight > 0 and code not in ("C_NTD", "CASH"):
                    result.append({"code": code, "name": name, "weight": weight})
            if result:
                result.sort(key=lambda x: -x["weight"])
                return result[:top_n]
        except ImportError:
            pass  # beautifulsoup4 未安裝，改用 regex

        # ── 策略 3：regex fallback ──
        code_pattern = re.compile(
            r'href="(?:https://www\.etfinfo\.tw)?/stock/(\d+)"[^>]*>(?:\d+)</a>\s*([^<\n]{1,12})'
        )
        weight_pattern = re.compile(r"<strong>([\d.]+)%</strong>")
        result = []
        seen = set()
        for mc in code_pattern.finditer(html):
            code = mc.group(1).strip()
            if code in seen or code in ("C_NTD", "CASH"):
                continue
            seen.add(code)
            name = mc.group(2).strip()
            after = html[mc.end(): mc.end() + 2000]
            mw = weight_pattern.search(after)
            if mw:
                weight = float(mw.group(1))
                if weight > 0:
                    result.append({"code": code, "name": name, "weight": weight})
        if result:
            result.sort(key=lambda x: -x["weight"])
            return result[:top_n]

        return None
    except Exception as e:
        print(f"   ⚠️ etfinfo.tw {etf_code}: {e}")
        return None


# 要追蹤的 ETF 代號（含主動型）
ETF_TRACK_CODES = [
    "0050", "0056", "00878", "00919", "00929", "00940",
    "00713", "00757", "00662", "00891",
    "00992A", "00981A", "00988A", "00990A",
]

def _parse_etf_basket_items(raw_items):
    """解析 ETF 申購買回清單 list，回傳 {etf_code: [{code,name,weight}]} dict"""
    result = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        etf_code = (
            item.get("基金代號") or item.get("ETFid") or item.get("fund_id") or ""
        ).strip()
        if etf_code not in ETF_TRACK_CODES:
            continue
        comp_code = (
            item.get("成分股代號") or item.get("ComponentStockCode") or ""
        ).strip()
        comp_name = (
            item.get("成分股名稱") or item.get("ComponentStockName") or ""
        ).strip()
        w_raw = (
            item.get("比重(%)") or item.get("Ratio") or item.get("比重") or "0"
        )
        try:
            w = float(str(w_raw).replace(",", "").strip())
        except ValueError:
            w = 0.0
        if not comp_code:
            continue
        result.setdefault(etf_code, []).append(
            {"code": comp_code, "name": comp_name, "weight": w}
        )
    return result


def fetch_0050_by_mktcap(all_prices=None, top_n=50):
    """
    用 TWSE 上市公司已發行股數 × 收盤價 估算市值，取前 top_n 支重建 0050 成份。
    回傳 [{code, name, weight}, ...]，weight 為相對佔比（百分比）。
    """
    try:
        # 已發行股數
        r1 = requests.get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
            headers=HEADERS, timeout=20
        )
        if not r1.ok:
            return None
        company_list = r1.json()
        shares_map = {}
        name_map = {}
        for x in company_list:
            code = x.get("公司代號", "").strip()
            shares_str = x.get("已發行普通股數或TDR原股發行股數", "").replace(",", "").strip()
            name = x.get("公司簡稱", code).strip()
            if code and shares_str.isdigit():
                shares_map[code] = int(shares_str)
                name_map[code] = name

        # 收盤價（優先 all_prices，備用 BWIBBU_d）
        price_map = {}
        if all_prices:
            for code, p in all_prices.items():
                if p.get("price"):
                    price_map[code] = p["price"]
        if not price_map:
            r2 = requests.get(
                "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d",
                headers=HEADERS, timeout=20
            )
            if r2.ok:
                for x in r2.json():
                    c = x.get("Code", "").strip()
                    p = x.get("ClosePrice", "")
                    try:
                        price_map[c] = float(p)
                    except ValueError:
                        pass

        # 計算市值，過濾：只留 4 位數字代號的普通股（排除 ETF、特別股、KDR）
        mktcap = {}
        for code, shares in shares_map.items():
            if not re.fullmatch(r"\d{4}", code):
                continue
            if code not in price_map:
                continue
            mktcap[code] = price_map[code] * shares

        # 取前 top_n，計算相對比重
        top = sorted(mktcap.items(), key=lambda x: -x[1])[:top_n]
        if not top:
            return None
        total_cap = sum(v for _, v in top)
        result = []
        for code, cap in top:
            result.append({
                "code":   code,
                "name":   name_map.get(code, code),
                "weight": round(cap / total_cap * 100, 2),
            })
        print(f"   📊 0050 市值重建: {len(result)} 支 (前3: {[r['code'] for r in result[:3]]})")
        return result
    except Exception as e:
        print(f"   ⚠️ 0050 市值重建失敗: {e}")
        return None


def fetch_etf_holdings():
    """
    從 TWSE OpenAPI 抓取 ETF 申購/買回清單（日常籃子 = 成分股 + 比重）。
    依序嘗試多個端點，GitHub Actions 境外伺服器 openapi 有時回傳空，rwd 端點通常可用。
    回傳: { "0050": [{code, name, weight}, ...前10大...], ... }
    """
    result = {}
    headers_rwd = {**HEADERS, "Referer": "https://www.twse.com.tw/"}

    # ① TWSE OpenAPI（境外設計，但偶爾空回應）
    try:
        url = "https://openapi.twse.com.tw/v1/ETF/DAILYBASKETContent"
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.ok and r.text.strip():
            result = _parse_etf_basket_items(r.json())
            if result:
                print(f"   ✅ OpenAPI ETF 持股: {len(result)} 檔")
    except Exception as e:
        print(f"   ⚠️ OpenAPI ETF: {e}")

    # ② TWSE rwd 端點（上市 ETF，與 T86 同源，通常可用）
    if not result:
        for url in [
            "https://www.twse.com.tw/rwd/zh/ETF/DAILYBASKETContent?response=json",
            "https://www.twse.com.tw/ETF/DAILYBASKETContent?response=json",
        ]:
            try:
                r = requests.get(url, headers=headers_rwd, timeout=25)
                if not r.ok or not r.text.strip():
                    continue
                body = r.json()
                rows = body if isinstance(body, list) else (body.get("data") or [])
                result = _parse_etf_basket_items(rows)
                if result:
                    print(f"   ✅ rwd ETF 持股: {len(result)} 檔")
                    break
            except Exception as e:
                print(f"   ⚠️ rwd ETF {url[-40:]}: {e}")

    # ③ TWT84U 逐檔備援（被動型）— 試 rwd 和舊端點
    if not result:
        for code in ETF_TRACK_CODES[:10]:
            for url in [
                f"https://www.twse.com.tw/rwd/zh/fund/TWT84U?response=json&strDate=&fundNo={code}",
                f"https://www.twse.com.tw/fund/TWT84U?response=json&strDate=&fundNo={code}",
            ]:
                try:
                    r = requests.get(url, headers=headers_rwd, timeout=15)
                    if not r.ok or not r.text.strip():
                        continue
                    body = r.json()
                    rows = body.get("data") or []
                    holdings = []
                    for row in rows[:10]:
                        if len(row) >= 4:
                            holdings.append({
                                "code": str(row[0]).strip(),
                                "name": str(row[1]).strip(),
                                "weight": safe_float(row[3]),
                            })
                    if holdings:
                        result[code] = holdings
                        break
                except Exception as e:
                    print(f"   ⚠️ TWT84U {code}: {e}")
            time.sleep(0.3)
        if result:
            print(f"   ✅ TWT84U 備援 ETF 持股: {len(result)} 檔")

    # 按比重排序，保留前10
    for code in result:
        result[code] = sorted(result[code], key=lambda x: -x["weight"])[:10]

    # ④ etfinfo.tw（被動型 ETF 備援，SSR 解析，台灣自架 runner 可連線）
    missing = [c for c in ETF_ETFINFO_CODES if c not in result]
    if missing:
        print(f"   🌐 etfinfo.tw 補抓: {missing}")
        for code in missing:
            holdings = _fetch_etfinfo_holdings(code, top_n=50)
            if holdings:
                result[code] = holdings
                print(f"      ✅ {code}: {len(holdings)} 檔（etfinfo.tw）")
            else:
                print(f"      ⚠️ {code}: etfinfo.tw 失敗")
            time.sleep(1.0)

    if not result:
        print("   ⚠️ ETF 持股所有端點均失敗，將保留前次資料")

    return result


# ── 個股三大法人（T86）─────────────────────────────────────

def fetch_inst_stocks(all_prices, target_days=5):
    """
    從 TWSE T86 抓取個股三大法人買賣超（股數）並累加最近 target_days 個交易日。
    轉換成「億元」：shares * 當前股價 / 1e8（近似值，已夠顯示趨勢）。
    回傳 {code: {f, t, s}}，f/t/s 單位：億元（保留1位小數）。
    """
    from collections import defaultdict

    acc = defaultdict(lambda: {"f": 0.0, "t": 0.0, "s": 0.0})
    collected = 0

    for i in range(14):                # 往回最多找 14 個日曆日
        if collected >= target_days:
            break
        d = datetime.date.today() - datetime.timedelta(days=i)
        date_str = d.strftime("%Y%m%d")

        # 先試 openapi，再試 rwd
        urls = [
            f"https://openapi.twse.com.tw/v1/fund/T86?date={date_str}",
            f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL",
            f"https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={date_str}&selectType=ALL",
        ]

        parsed = False
        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=25)
                resp = r.json()

                # openapi 回傳 list；rwd 回傳 dict with "data"
                if isinstance(resp, list):
                    rows = resp
                    # openapi T86 格式：每筆是 dict
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        code = str(row.get("Code", row.get("code", ""))).strip()
                        if not code:
                            continue
                        def pg(k, *aliases):
                            for a in (k,) + aliases:
                                v = row.get(a)
                                if v is not None:
                                    try:
                                        return int(str(v).replace(",", ""))
                                    except Exception:
                                        pass
                            return 0
                        f_net = pg("ForeignInvestmentNetBuySell", "外陸資買賣超股數")
                        t_net = pg("InvestmentTrustNetBuySell",   "投信買賣超股數")
                        s_net = pg("DealerNetBuySell",            "自營商買賣超股數")
                        # 直接以「萬股」儲存，不再乘以股價（避免 price=0 導致全歸零）
                        acc[code]["f"] += f_net / 1e4
                        acc[code]["t"] += t_net / 1e4
                        acc[code]["s"] += s_net / 1e4
                    parsed = bool(rows)

                else:
                    # rwd 格式
                    if resp.get("stat") not in ("OK", "ok"):
                        continue
                    rows = resp.get("data", [])
                    if not rows:
                        continue
                    fields = resp.get("fields", [])
                    # 實際欄位順序（www.twse.com.tw rwd/zh 格式）：
                    # 0=代號, 1=名稱, 2=外陸資買, 3=外陸資賣, 4=外陸資買賣超(不含外資自營商),
                    # 5=外資自營商買, 6=外資自營商賣, 7=外資自營商買賣超,
                    # 8=投信買, 9=投信賣, 10=投信買賣超,
                    # 11=自營商買賣超(合計), 12-14=自營商(自行買賣), 15-17=自營商(避險)
                    def col(name, fallback_idx):
                        try:
                            return fields.index(name)
                        except ValueError:
                            return fallback_idx

                    i_code  = col("證券代號", 0)
                    i_f_net = col("外陸資買賣超股數(不含外資自營商)", 4)
                    i_t_net = col("投信買賣超股數", 10)
                    i_s_net = col("自營商買賣超股數", 11)  # 自行買賣 + 避險 合計淨額

                    def pn(row, idx):
                        try:
                            return int(str(row[idx]).replace(",", ""))
                        except Exception:
                            return 0

                    for row in rows:
                        if len(row) < 5:
                            continue
                        code  = str(row[i_code]).strip()
                        # 直接以「萬股」儲存，不再乘以股價（避免 price=0 導致全歸零）
                        acc[code]["f"] += pn(row, i_f_net) / 1e4
                        acc[code]["t"] += pn(row, i_t_net) / 1e4
                        acc[code]["s"] += pn(row, i_s_net) / 1e4
                    parsed = True

                if parsed:
                    collected += 1
                    print(f"   ✅ T86 {d.strftime('%Y/%m/%d')} 完成（{collected}/{target_days}）")
                    time.sleep(0.4)
                    break

            except Exception as e:
                print(f"   ⚠️ T86 {url[-50:]}: {e}")
                continue

    # ── 補抓 TPEX（上櫃）三大法人 ──────────────────────────────
    tpex_collected = 0
    for i in range(14):
        if tpex_collected >= target_days:
            break
        d = datetime.date.today() - datetime.timedelta(days=i)
        # TPEX 日期格式：民國年/月/日
        tw_date = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
        url = (f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
               f"3itrade_hedge_result.php?l=zh-tw&t=D&d={tw_date}&s=0,asc,0&_=1")
        try:
            r = requests.get(url, headers={**HEADERS, "Referer": "https://www.tpex.org.tw/"}, timeout=25, verify=False)
            resp = r.json()
            tables = resp.get("tables", [])
            if not tables:
                continue
            rows = tables[0].get("data", [])
            if not rows:
                continue
            # TPEX 欄位順序：0=代號, 1=名稱,
            # 2=外買, 3=外賣, 4=外超,  5=外資自營買, 6=外資自營賣, 7=外資自營超,
            # 8=外陸資合計買, 9=外陸資合計賣, 10=外陸資合計超,
            # 11=投信買, 12=投信賣, 13=投信超,
            # 14=自營(自行)買, 15=自營(自行)賣, 16=自營(自行)超,
            # 17=自營(避險)買, 18=自營(避險)賣, 19=自營(避險)超,
            # 20=自營合計買, 21=自營合計賣, 22=自營合計超, 23=三大法人
            def pn_tpex(row, idx):
                try:
                    return int(str(row[idx]).replace(",", ""))
                except Exception:
                    return 0
            for row in rows:
                if len(row) < 14:
                    continue
                code = str(row[0]).strip()
                f_net = pn_tpex(row, 10)   # 外陸資合計超
                t_net = pn_tpex(row, 13)   # 投信超
                s_net = pn_tpex(row, 22)   # 自營合計超
                acc[code]["f"] += f_net / 1e4
                acc[code]["t"] += t_net / 1e4
                acc[code]["s"] += s_net / 1e4
            tpex_collected += 1
            print(f"   ✅ TPEX 法人 {d.strftime('%Y/%m/%d')} 完成（{tpex_collected}/{target_days}）")
            time.sleep(0.3)
        except Exception as e:
            print(f"   ⚠️ TPEX 法人 {tw_date}: {e}")
            continue

    result = {
        code: {
            "f": round(v["f"], 1),
            "t": round(v["t"], 1),
            "s": round(v["s"], 1),
        }
        for code, v in acc.items()
    }
    print(f"   📊 個股法人資料：{len(result)} 支，累計 TWSE {collected} + TPEX {tpex_collected} 個交易日")
    return result


# ── 主程式 ────────────────────────────────────────────────

def main():
    print("🚀 台股資料抓取 v3 開始...")
    print(f"   時間: {datetime.datetime.now()}")

    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"twii": {}, "institutional": {}, "prices": {}}

    # API Key（提前取得，供大盤摘要與 AI 功能使用）
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # 1. 加權指數 + 歷史走勢
    twii = fetch_twii()
    if twii:
        data["twii"] = twii
        print(f"✅ 加權指數: {twii['price']} ({twii['chg']:+.2f})")

    vix = fetch_vix()
    if vix:
        data["vix"] = vix
        print(f"✅ VIX: {vix['price']} ({vix['chg']:+.2f})")
    else:
        print(f"   ⚠️ VIX 抓取失敗，保留前次資料（{data.get('vix', {}).get('price', '無')}）")

    usdtwd = fetch_usdtwd()
    if usdtwd:
        data["usdtwd"] = usdtwd
        print(f"✅ USD/TWD: {usdtwd['price']} ({usdtwd['chg']:+.3f})")
    else:
        print(f"   ⚠️ USD/TWD 抓取失敗，保留前次資料")

    # 1b. 融資餘額（觀察融資是否洗乾淨）
    margin = fetch_margin_summary()
    if margin:
        # 大盤下跌時才計算「洗盤比值」＝融資降幅 ÷ 指數跌幅（跌越多、融資降越多才有意義）
        twii_chg_p = (twii or {}).get("chgP")
        ratio = None
        if twii_chg_p is not None and twii_chg_p < -0.3 and margin["change_pct"] <= 0:
            ratio = round(margin["change_pct"] / twii_chg_p * 100, 1)
        margin["ratio"] = ratio

        # 保留近 120 個交易日走勢（供前端算歷史百分位，約半年）
        history = data.get("margin", {}).get("history", [])
        if not history or history[-1].get("date") != margin["date"]:
            history.append({"date": margin["date"], "balance": margin["balance_today"], "ratio": ratio})
            history = history[-120:]
        margin["history"] = history
        data["margin"] = margin
        print(f"✅ 融資餘額: {margin['balance_today']:,} 張（{margin['change_pct']:+.2f}%，比值 {ratio}）")
    else:
        print(f"   ⚠️ 融資餘額抓取失敗，保留前次資料")

    print("📈 大盤歷史走勢...")
    twii_closes, twii_dates = fetch_twii_history()
    if twii_closes:
        data["twii_history"] = {
            "labels": [d[5:] for d in twii_dates],   # 存 "MM/DD" 格式
            "closes": twii_closes,
        }
        print(f"   ✅ 大盤歷史: {len(twii_closes)} 筆 ({twii_dates[0]} ~ {twii_dates[-1]})")

    # 2. 三大法人
    inst = fetch_institutional()
    if inst:
        data["institutional"] = inst
        print(f"✅ 法人({inst['date']}): "
              f"外{inst['foreign']/1e8:+.0f}億 "
              f"投{inst['trust']/1e8:+.0f}億 "
              f"自{inst['dealer']/1e8:+.0f}億")
    else:
        prev = data.get("institutional", {})
        if prev:
            print(f"   ⚠️ 法人數據抓取失敗，保留前次資料（{prev.get('date','?')}）")
        else:
            print("   ⚠️ 法人數據抓取失敗，無前次資料可保留")

    # 3. 全台個股 — Yahoo Finance 優先（即時報價），TWSE/TPEX 補齊全市場
    print("📊 Yahoo Finance — 監控清單即時報價（優先）...")
    yf_prices = fetch_fallback_list()
    print(f"   Yahoo Finance: {len(yf_prices)} 支")

    print("📊 TWSE 開放 API — 上市股票（補全市場）...")
    twse = fetch_all_twse_stocks()
    print(f"   上市: {len(twse)} 支")

    print("📊 TPEX 開放 API — 上櫃股票（補全市場）...")
    otc = fetch_all_otc_openapi()
    print(f"   上櫃: {len(otc)} 支")

    # Yahoo 即時報價優先；TWSE/TPEX 補齊其餘個股
    all_prices = {**otc, **twse, **yf_prices}

    # 從 Yahoo 樣本取得實際交易日期
    sample_yf = next((v for v in yf_prices.values() if v.get("date")), None)
    data["prices_date"] = sample_yf["date"] if sample_yf else datetime.date.today().strftime("%Y/%m/%d")

    print(f"✅ 全市場合計 {len(all_prices)} 支")

    data["prices"] = all_prices

    # 4. 公司基本資料 / 自動生成
    print("🏢 公司資料更新中...")
    company_info = load_company_info()
    company_info = update_company_info(all_prices, company_info, api_key)
    with open("company_info.json", "w", encoding="utf-8") as f:
        json.dump(company_info, f, ensure_ascii=False, indent=2)
    print(f"   💾 company_info.json 已更新（{len(company_info)} 筆）")

    # 5a-pre. 個股三大法人（T86，先抓供評分用）
    print("📊 個股三大法人資料（T86）...")
    inst_stocks = fetch_inst_stocks(all_prices, target_days=5)
    # 與舊資料合併：新抓到的覆蓋舊值，缺漏的（例如 TWSE 當次故障）保留昨天資料，
    # 避免單次 API 故障讓大量上市股票法人欄位整批消失。
    old_inst_stocks = data.get("inst_stocks", {})
    if isinstance(old_inst_stocks, dict):
        merged_inst_stocks = {**old_inst_stocks, **inst_stocks}
    else:
        merged_inst_stocks = inst_stocks
    # T86 回傳全市場（含全部個股/權證/ETF，2萬多筆），但前端只會查詢觀察清單的股票，
    # 其餘完全用不到卻會被打包進 data.json 拖慢每次載入，這裡先裁到只留觀察清單需要的代號。
    _watchlist_codes = set(FALLBACK_CODES) | {
        c for g in THEME_GROUPS.values() for c in list(g["leaders"]) + list(g["members"])
    }
    merged_inst_stocks = {k: v for k, v in merged_inst_stocks.items() if k in _watchlist_codes}
    data["inst_stocks"] = merged_inst_stocks
    print(f"   📊 法人資料合併：新 {len(inst_stocks)} 支 + 沿用舊資料，裁剪後保留 {len(merged_inst_stocks)} 支（觀察清單範圍）")

    # 5. 機會點自動偵測（同時收集30日歷史供圖表用）
    print("🔍 機會點偵測中...")
    opps, histories = compute_opportunities(
        all_prices, extra_codes=FALLBACK_CODES,
        company_info=company_info, inst_stocks_ref=merged_inst_stocks
    )
    data["opportunities"] = opps
    data["histories"] = histories
    print(f"   📊 儲存 {len(histories)} 支股票歷史走勢")

    # ── 補上最新交易日（Yahoo 歷史 API 常延遲 1 天）──────────────────
    prices_date = data.get("prices_date", "")
    patched = 0
    for code, h in histories.items():
        labels = h.get("labels", [])
        closes = h.get("closes", [])
        if not labels or not closes or not prices_date:
            continue
        if labels[-1] == prices_date:
            continue  # 已是最新，不需補
        p = all_prices.get(code, {})
        latest_price = p.get("price")
        if latest_price:
            labels.append(prices_date)
            closes.append(round(latest_price, 2))
            if "volumes" in h:
                h["volumes"].append(p.get("vol", 0))
            patched += 1
    if patched:
        data["histories"] = histories
        print(f"   🗓️ 補上最新交易日 {prices_date}：{patched} 支股票")

    # 5b. ETF 持股比重（TWSE OpenAPI）
    print("📊 ETF 成分股持股抓取中...")
    etf_holdings = fetch_etf_holdings()
    if etf_holdings:
        data["etf_holdings"] = etf_holdings
        print(f"   ✅ {len(etf_holdings)} 檔 ETF 持股已更新")
    else:
        prev = len(data.get("etf_holdings") or {})
        print(f"   ⚠️ 今日 ETF 持股抓取失敗，保留前次資料（{prev} 檔）")
        etf_holdings = data.get("etf_holdings", {})

    # 0050 市值重建：若 etfinfo 只有前15支，用市值排名補到完整 50 支
    holdings_0050 = (etf_holdings or {}).get("0050", [])
    if len(holdings_0050) < 40:
        print("   📊 0050 持股不足40支，用市值排名補全...")
        full_0050 = fetch_0050_by_mktcap(all_prices=all_prices, top_n=50)
        if full_0050:
            if not etf_holdings:
                etf_holdings = {}
            etf_holdings["0050"] = full_0050
            data["etf_holdings"] = etf_holdings
            print(f"   ✅ 0050 補全至 {len(full_0050)} 支")

    # 5b-2. 補抓 ETF 成份股歷史走勢（p5/p30 顯示用）
    etf_extra_codes = set()
    for holdings in (etf_holdings or {}).values():
        for h in holdings:
            c = h.get("code", "")
            if c and c not in histories:
                etf_extra_codes.add(c)
    if etf_extra_codes:
        print(f"   📈 補抓 {len(etf_extra_codes)} 支 ETF 成份股歷史走勢...")
        _, extra_hist = compute_opportunities(all_prices, extra_codes=list(etf_extra_codes), company_info=company_info)
        for code, h in extra_hist.items():
            if code not in histories:
                histories[code] = h
        data["histories"] = histories
        print(f"   ✅ 歷史走勢總計 {len(histories)} 支")

    # 5c. 個股近期故事生成（用已抓好的 histories）
    print("📝 生成個股近期故事...")
    company_info = generate_stories(company_info, histories, all_prices, api_key)
    with open("company_info.json", "w", encoding="utf-8") as f:
        json.dump(company_info, f, ensure_ascii=False, indent=2)
    print(f"   💾 company_info.json 已更新（含故事）")

    # 5d. 每日大盤日評
    print("📝 生成每日大盤日評...")
    twii_data = data.get("twii", {})
    inst_data = data.get("institutional", {})
    commentary = generate_daily_commentary(twii_data, inst_data, all_prices, histories, api_key)
    if commentary:
        data["market_summary"] = commentary
        print(f"   ✅ 日評已生成（{len(commentary)} 字）")
    else:
        print("   ⚠️ 日評生成失敗，保留前次資料")

    # 5e. 個股異動偵測（±5%）
    print("🔔 個股異動偵測中...")
    movers = detect_stock_movers(all_prices, histories, merged_inst_stocks)
    data["movers"] = movers

    # 5f. 週報敘事段落（每日都更新，週報產生時直接讀取）
    print("📋 生成週報敘事段落...")
    weekly_summary = generate_weekly_summary(all_prices, histories, inst_data, twii_data)
    if weekly_summary:
        data["weekly_summary"] = weekly_summary

    # 5g. 分析師共識目標價
    print("🎯 分析師目標價追蹤...")
    target_codes = sorted(set(FALLBACK_CODES) | {c for g in THEME_GROUPS.values() for c in list(g["leaders"]) + list(g["members"])})
    prev_targets = data.get("analyst_targets", {})
    new_targets  = fetch_analyst_targets(target_codes)
    # 計算每支股票目標價變化（與前次比較）
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    for code, t in new_targets.items():
        prev = prev_targets.get(code, {})
        prev_mean = prev.get("mean")
        if prev_mean and t["mean"] != prev_mean and prev.get("updated") != today_str:
            t["prev_mean"]   = prev_mean
            t["mean_change"] = round(t["mean"] - prev_mean, 1)
        else:
            t["prev_mean"]   = prev.get("prev_mean", prev_mean)
            t["mean_change"] = prev.get("mean_change", 0)
        # 保留歷史趨勢（最近30天每日快照）
        history = prev.get("history", [])
        if not history or history[-1].get("date") != today_str:
            if prev_mean:
                history.append({"date": today_str, "mean": t["mean"]})
            history = history[-30:]  # 只保留30天
        t["history"] = history
    data["analyst_targets"] = new_targets

    # 5g. PE 河流圖資料（監控個股 2 年股價 + TTM EPS）
    print("📈 建立 PE 河流圖資料...")
    pe_river = data.get("pe_river", {})
    river_codes = set(FALLBACK_CODES)
    for g in THEME_GROUPS.values():
        river_codes.update(g["leaders"])
        river_codes.update(g["members"])
    built = 0
    for code in sorted(river_codes):
        qeps = company_info.get(code, {}).get("quarterly_eps", [])
        river = build_pe_river(code, qeps)
        if river:
            pe_river[code] = river
            built += 1
    data["pe_river"] = pe_river
    print(f"   ✅ PE 河流圖完成：{built} 支")

    # 6. 時間戳
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    data["updated_at"] = datetime.datetime.now(tz_tw).strftime("%Y/%m/%d %H:%M")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！更新時間: {data['updated_at']}")


if __name__ == "__main__":
    main()
