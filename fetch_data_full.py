"""
台股資料抓取腳本
每天從台灣證交所 + TPEX 批量抓取全台上市上櫃股票，寫入 data.json
GitHub Actions 從 GitHub 伺服器執行，IP 不會被擋

改版說明：
- 改用 TWSE MI_INDEX + TPEX 批量 API，一次抓全台所有股票（不限固定清單）
- 修正三大法人日期格式：TWSE 需要民國年格式（1150519），非西元（20260519）
- 修正 TWSE 回傳日期自動轉換為西元顯示
"""

import json
import requests
import datetime
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.twse.com.tw/",
}


def fetch_twii():
    """抓加權指數（用 Yahoo Finance）"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&range=2d"
        r = requests.get(url, headers=HEADERS, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        return {
            "price": round(price, 2),
            "chg": round(price - prev, 2),
            "chgP": round((price - prev) / prev * 100, 2),
            "vol": meta.get("regularMarketVolume", 0),
            "date": datetime.datetime.fromtimestamp(meta["regularMarketTime"]).strftime("%Y/%m/%d")
        }
    except Exception as e:
        print(f"❌ 加權指數抓取失敗: {e}")
        return None


def roc_date_str(d):
    """把 datetime.date 轉成 TWSE 需要的民國年格式，例如 1150519"""
    roc_year = d.year - 1911
    return f"{roc_year}{d.month:02d}{d.day:02d}"


def parse_twse_date(twse_date_str, fallback_date):
    """
    把 TWSE 回傳的民國日期（如 '115/05/19'）轉成西元格式（'2026/05/19'）。
    如果格式不符則用 fallback_date。
    """
    if twse_date_str and "/" in twse_date_str:
        parts = twse_date_str.split("/")
        if len(parts) == 3:
            try:
                year = int(parts[0]) + 1911
                return f"{year}/{parts[1]}/{parts[2]}"
            except ValueError:
                pass
    return fallback_date.strftime("%Y/%m/%d")


def fetch_institutional():
    """抓三大法人（往前找最近交易日）。
    注意：TWSE BFI82U 的 dayDate 參數需要民國年格式（如 1150519），不是西元格式。
    """
    for i in range(0, 8):
        d = datetime.date.today() - datetime.timedelta(days=i)
        date_str = roc_date_str(d)   # ← 民國格式，修正舊版西元格式的 bug
        try:
            url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&dayDate={date_str}&type=day"
            r = requests.get(url, headers=HEADERS, timeout=10)
            data = r.json()
            if data.get("data"):
                foreign = trust = dealer = 0
                for row in data["data"]:
                    name = row[0]
                    net = int(row[4].replace(",", "")) if row[4] else 0
                    if "外資" in name:
                        foreign += net
                    elif "投信" in name:
                        trust += net
                    elif "自營" in name:
                        dealer += net
                display_date = parse_twse_date(data.get("date", ""), d)
                print(f"   ✅ 法人資料日期：{display_date}")
                return {
                    "foreign": foreign,
                    "trust": trust,
                    "dealer": dealer,
                    "date": display_date
                }
        except Exception as e:
            print(f"⚠️ 法人 {date_str}: {e}")
            continue
    print("❌ 法人數據抓取失敗")
    return None


def fetch_all_twse_stocks():
    """
    從 TWSE MI_INDEX 一次抓取全部上市股票當日收盤價。
    比逐支 Yahoo Finance 快很多，且涵蓋全市場。
    """
    try:
        url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999"
        r = requests.get(url, headers=HEADERS, timeout=30)
        resp = r.json()
        if resp.get("stat") != "OK":
            print(f"⚠️ TWSE MI_INDEX 回傳非 OK：{resp.get('stat')}")
            return {}

        prices = {}
        for table in resp.get("tables", []):
            fields = table.get("fields", [])
            if "收盤價" not in fields:
                continue
            fi = {f: idx for idx, f in enumerate(fields)}
            for row in table.get("data", []):
                code = row[fi["證券代號"]].strip()
                close_str = row[fi["收盤價"]].replace(",", "").strip()
                if not close_str or close_str in ["--", "除權", "除息", "暫停"]:
                    continue
                try:
                    price = float(close_str)
                    # 漲跌價差
                    chg = 0.0
                    if "漲跌價差" in fi:
                        raw = row[fi["漲跌價差"]].replace(",", "").strip().lstrip("▲▼+ ")
                        # TWSE 用 ▲ / ▼ 表示漲跌方向
                        orig_raw = row[fi["漲跌價差"]].strip()
                        try:
                            chg = float(raw) if raw else 0.0
                            if "▼" in orig_raw or orig_raw.startswith("-"):
                                chg = -abs(chg)
                        except ValueError:
                            chg = 0.0
                    prev = price - chg
                    chg_pct = round(chg / prev * 100, 2) if prev else 0.0
                    vol = 0
                    if "成交股數" in fi:
                        vol_str = row[fi["成交股數"]].replace(",", "")
                        vol = int(vol_str) if vol_str.isdigit() else 0
                    prices[code] = {
                        "price": price,
                        "change": round(chg, 2),
                        "changeP": chg_pct,
                        "open": 0,
                        "high": 0,
                        "low": 0,
                        "vol": vol
                    }
                except Exception:
                    continue
        return prices
    except Exception as e:
        print(f"❌ 上市批量抓取失敗: {e}")
        return {}


def fetch_all_otc_stocks():
    """
    從 TPEX 一次抓取全部上櫃股票當日收盤價。
    """
    try:
        url = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&response=json"
        r = requests.get(url, headers=HEADERS, timeout=30)
        resp = r.json()
        prices = {}
        for row in resp.get("aaData", []):
            # row: [代號, 名稱, 收盤, 漲跌, 開盤, 最高, 最低, 成交股數, ...]
            code = str(row[0]).strip()
            if not code:
                continue
            try:
                def safe_float(v):
                    s = str(v).replace(",", "").strip()
                    return float(s) if s and s not in ["--", ""] else 0.0
                price  = safe_float(row[2])
                change = safe_float(row[3])
                opn    = safe_float(row[4])
                high   = safe_float(row[5])
                low    = safe_float(row[6])
                vol_s  = str(row[7]).replace(",", "")
                vol    = int(vol_s) if vol_s.isdigit() else 0
                prev   = price - change
                chg_pct = round(change / prev * 100, 2) if prev else 0.0
                prices[code] = {
                    "price": price,
                    "change": round(change, 2),
                    "changeP": chg_pct,
                    "open": opn,
                    "high": high,
                    "low": low,
                    "vol": vol
                }
            except Exception:
                continue
        return prices
    except Exception as e:
        print(f"❌ 上櫃批量抓取失敗: {e}")
        return {}


def main():
    print("🚀 開始抓取台股資料（全市場模式）...")
    print(f"   時間: {datetime.datetime.now()}")

    # 讀取現有 data.json（保留無法更新的欄位）
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"twii": {}, "institutional": {}, "prices": {}}

    # 1. 大盤
    twii = fetch_twii()
    if twii:
        data["twii"] = twii
        print(f"✅ 加權指數: {twii['price']} ({twii['chg']:+.2f})")

    # 2. 三大法人（民國年格式修正）
    inst = fetch_institutional()
    if inst:
        data["institutional"] = inst
        print(f"✅ 法人({inst['date']}): 外{inst['foreign']/1e8:+.0f}億 投{inst['trust']/1e8:+.0f}億 自{inst['dealer']/1e8:+.0f}億")

    # 3. 全台上市股票（TWSE MI_INDEX 批量）
    print("📊 抓取上市股票（TWSE 批量）...")
    twse_prices = fetch_all_twse_stocks()
    print(f"   上市：抓到 {len(twse_prices)} 支")

    # 4. 全台上櫃股票（TPEX 批量）
    print("📊 抓取上櫃股票（TPEX 批量）...")
    otc_prices = fetch_all_otc_stocks()
    print(f"   上櫃：抓到 {len(otc_prices)} 支")

    # 合併（上市優先，上櫃補充）
    all_prices = {**otc_prices, **twse_prices}
    if all_prices:
        data["prices"] = all_prices
        print(f"✅ 全市場共 {len(all_prices)} 支股票寫入 data.json")
    else:
        print("⚠️ 批量抓取無資料，保留舊有 prices")

    # 5. 更新時間戳
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    data["updated_at"] = datetime.datetime.now(tz_tw).strftime("%Y/%m/%d %H:%M")

    # 寫入
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！更新時間: {data['updated_at']}")


if __name__ == "__main__":
    main()
