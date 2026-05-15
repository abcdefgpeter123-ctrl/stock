"""
台股資料抓取腳本
每天從台灣證交所 + Yahoo Finance 抓取最新數據，寫入 data.json
GitHub Actions 從 GitHub 伺服器執行，IP 不會被擋
"""

import json
import requests
import datetime
import time

# 股票清單（與 index.html 中的 STOCKS 對應）
STOCK_CODES = [
    # 核心科技題材
    "2330", "2317", "3711", "6669", "5274", "5347", "4863",
    "3006", "2408", "2344", "2369", "2449", "3081", "2455",
    "4971", "3163", "3363", "6442", "2327", "2492", "2351",
    "3034", "3008", "6274", "3556", "6538", "3152", "3048", "6488",
    # 海運/航空
    "2603", "2609", "2615", "2606", "2618", "2610", "6706", "2634", "6505",
    # 金融
    "2881", "2882", "2891", "2884", "2885",
    # 電信
    "2412", "3045", "4904", "2308", "3682",
    # 生技
    "4147", "6446", "3705", "4736", "4128",
    # 傳產
    "2002", "1301", "1303", "1326", "1101",
    # 食品零售
    "1216", "2912", "5903", "1227", "9907",
    # 電動車
    "2227", "2228", "1539", "1536", "6431",
    # ETF
    "0050", "0056", "00878", "00919", "00929", "00940",
    "00713", "00757", "00662", "00891"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
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


def fetch_institutional():
    """抓三大法人（往前找最近交易日）"""
    for i in range(0, 8):
        d = datetime.date.today() - datetime.timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
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
                return {
                    "foreign": foreign,
                    "trust": trust,
                    "dealer": dealer,
                    "date": data.get("date", d.strftime("%Y/%m/%d"))
                }
        except Exception as e:
            print(f"⚠️ 法人 {date_str}: {e}")
            continue
    print("❌ 法人數據抓取失敗")
    return None


def fetch_stock_price(code):
    """抓個股價格"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW?interval=1d&range=2d"
        r = requests.get(url, headers=HEADERS, timeout=10)
        meta = r.json()["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        if not price or not prev:
            return None
        return {
            "price": round(price, 2),
            "change": round(price - prev, 2),
            "changeP": round((price - prev) / prev * 100, 2),
            "open": round(meta.get("regularMarketOpen", 0), 2),
            "high": round(meta.get("regularMarketDayHigh", 0), 2),
            "low": round(meta.get("regularMarketDayLow", 0), 2),
            "vol": meta.get("regularMarketVolume", 0)
        }
    except Exception as e:
        # 上市抓不到改試上櫃
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.TWO?interval=1d&range=2d"
            r = requests.get(url, headers=HEADERS, timeout=10)
            meta = r.json()["chart"]["result"][0]["meta"]
            price = meta["regularMarketPrice"]
            prev = meta.get("previousClose") or meta.get("chartPreviousClose")
            return {
                "price": round(price, 2),
                "change": round(price - prev, 2),
                "changeP": round((price - prev) / prev * 100, 2),
                "open": round(meta.get("regularMarketOpen", 0), 2),
                "high": round(meta.get("regularMarketDayHigh", 0), 2),
                "low": round(meta.get("regularMarketDayLow", 0), 2),
                "vol": meta.get("regularMarketVolume", 0)
            }
        except Exception as e2:
            print(f"⚠️ {code}: {e2}")
            return None


def main():
    print("🚀 開始抓取台股資料...")
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

    # 2. 法人
    inst = fetch_institutional()
    if inst:
        data["institutional"] = inst
        print(f"✅ 法人({inst['date']}): 外{inst['foreign']/1e8:+.0f}億 投{inst['trust']/1e8:+.0f}億 自{inst['dealer']/1e8:+.0f}億")

    # 3. 個股
    print(f"📊 抓取 {len(STOCK_CODES)} 支個股...")
    prices = {}
    for code in STOCK_CODES:
        p = fetch_stock_price(code)
        if p:
            prices[code] = p
        time.sleep(0.3)  # 避免被擋
    if prices:
        data["prices"] = prices
        print(f"✅ 成功抓取 {len(prices)}/{len(STOCK_CODES)} 支")

    # 4. 更新時間戳
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    data["updated_at"] = datetime.datetime.now(tz_tw).strftime("%Y/%m/%d %H:%M")

    # 寫入
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！更新時間: {data['updated_at']}")


if __name__ == "__main__":
    main()
