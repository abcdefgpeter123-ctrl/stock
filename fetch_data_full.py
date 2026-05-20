"""
台股資料抓取腳本 v3
- TWII:        Yahoo Finance（境外可用）
- 三大法人:    依序試多個 TWSE 端點（含開放資料 API，不限 IP）
- 全台個股:    先試 TWSE/TPEX 開放資料 API；若被擋，改用 Yahoo Finance 抓主要清單
GitHub Actions 從 GitHub 伺服器（美國）執行，開放資料 API 設計給境外存取。
"""

import json
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
    "2330","2317","3711","6669","5274","5347","2449","3081","2455","4971",
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

def fetch_all_twse_openapi():
    """
    TWSE 開放資料平台 — 全部上市股票當日收盤
    https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
    欄位：Code / ClosingPrice / Change / TradeVolume / OpeningPrice / HighestPrice / LowestPrice
    """
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        r = requests.get(url, headers=HEADERS, timeout=60)
        rows = r.json()
        if not isinstance(rows, list):
            print(f"⚠️ TWSE openapi 非預期格式: {type(rows)}")
            return {}
        prices = {}
        for row in rows:
            # 欄位名稱：ClosingPrice（不是 ClosePrice）
            code  = str(row.get("Code", "")).strip()
            close = safe_float(row.get("ClosingPrice", row.get("ClosePrice", "")))
            chg   = safe_float(row.get("Change", ""))
            vol_s = str(row.get("TradeVolume", "0")).replace(",", "")
            vol   = int(vol_s) if vol_s.isdigit() else 0
            if not code or close == 0:
                continue
            prev    = close - chg
            chg_pct = round(chg / prev * 100, 2) if prev else 0.0
            prices[code] = {
                "price":   close,
                "change":  round(chg, 2),
                "changeP": chg_pct,
                "open":    safe_float(row.get("OpeningPrice", "")),
                "high":    safe_float(row.get("HighestPrice", "")),
                "low":     safe_float(row.get("LowestPrice", "")),
                "vol":     vol,
            }
        return prices
    except Exception as e:
        print(f"❌ TWSE openapi 上市: {e}")
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
            close = safe_float(row.get("Close", row.get("close", "")))
            chg   = safe_float(row.get("Change", row.get("change", "")))
            if not code or close == 0:
                continue
            prev    = close - chg
            chg_pct = round(chg / prev * 100, 2) if prev else 0.0
            prices[code] = {
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
    twse = fetch_all_twse_openapi()
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

    # 4. 時間戳
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    data["updated_at"] = datetime.datetime.now(tz_tw).strftime("%Y/%m/%d %H:%M")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！更新時間: {data['updated_at']}")


if __name__ == "__main__":
    main()
