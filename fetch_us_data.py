#!/usr/bin/env python3
"""美股資料抓取腳本"""

import yfinance as yf
import json
import datetime
import time
import os
import requests

US_STOCKS = [
    {"code": "MSFT",  "name": "微軟",         "theme": "科技巨頭"},
    {"code": "AAPL",  "name": "蘋果",         "theme": "科技巨頭"},
    {"code": "GOOGL", "name": "Alphabet",     "theme": "科技巨頭"},
    {"code": "AMZN",  "name": "亞馬遜",       "theme": "科技巨頭"},
    {"code": "META",  "name": "Meta",         "theme": "科技巨頭"},
    {"code": "NVDA",  "name": "輝達",         "theme": "AI半導體"},
    {"code": "AVGO",  "name": "博通",         "theme": "AI半導體"},
    {"code": "AMD",   "name": "超微",         "theme": "AI半導體"},
    {"code": "MU",    "name": "美光",         "theme": "AI半導體"},
    {"code": "ANET",  "name": "Arista網路",   "theme": "AI半導體"},
    {"code": "TSLA",  "name": "特斯拉",       "theme": "電動車"},
    {"code": "V",     "name": "Visa",         "theme": "金融消費"},
    {"code": "COST",  "name": "好市多",       "theme": "金融消費"},
    {"code": "NFLX",  "name": "Netflix",      "theme": "串流媒體"},
    {"code": "NOW",   "name": "ServiceNow",   "theme": "企業軟體"},
    {"code": "CRM",   "name": "Salesforce",   "theme": "企業軟體"},
    {"code": "INTU",  "name": "Intuit",       "theme": "企業軟體"},
    {"code": "PANW",  "name": "Palo Alto",    "theme": "網路安全"},
    {"code": "ISRG",  "name": "直覺外科",     "theme": "醫療科技"},
    {"code": "LLY",   "name": "禮來",         "theme": "醫療科技"},
    {"code": "ADBE",  "name": "Adobe",        "theme": "企業軟體"},
]

ALL_CODES = [s["code"] for s in US_STOCKS] + ["QQQ", "VOO", "SOXX"]


def fetch_market_indices():
    """抓 S&P500、NASDAQ、Dow Jones 指數"""
    indices = {
        "sp500":  "^GSPC",
        "nasdaq": "^IXIC",
        "dow":    "^DJI",
    }
    result = {}
    for key, symbol in indices.items():
        try:
            t = yf.Ticker(symbol)
            h = t.history(period="2d", interval="1d")
            if len(h) >= 2:
                prev  = h["Close"].iloc[-2]
                cur   = h["Close"].iloc[-1]
                chg   = cur - prev
                chgP  = chg / prev * 100
                result[key] = {"price": round(cur, 2), "chg": round(chg, 2), "chgP": round(chgP, 2)}
            elif len(h) == 1:
                cur = h["Close"].iloc[-1]
                result[key] = {"price": round(cur, 2), "chg": 0, "chgP": 0}
            time.sleep(0.3)
        except Exception as e:
            print(f"   ⚠️ {symbol}: {e}")
    return result


def fetch_vix():
    try:
        t = yf.Ticker("^VIX")
        h = t.history(period="2d", interval="1d")
        if len(h) >= 2:
            prev = h["Close"].iloc[-2]
            cur  = h["Close"].iloc[-1]
            chg  = cur - prev
            chgP = chg / prev * 100
            return {"price": round(cur, 2), "chg": round(chg, 2), "chgP": round(chgP, 2)}
    except Exception as e:
        print(f"   ⚠️ VIX: {e}")
    return None


def fetch_stock_prices(codes):
    """批次抓取個股當日股價"""
    prices = {}
    name_map = {s["code"]: s["name"] for s in US_STOCKS}
    for code in codes:
        try:
            t = yf.Ticker(code)
            h = t.history(period="2d", interval="1d")
            if len(h) >= 2:
                prev  = h["Close"].iloc[-2]
                cur   = h["Close"].iloc[-1]
                chg   = cur - prev
                chgP  = chg / prev * 100
                prices[code] = {
                    "name":    name_map.get(code, code),
                    "price":   round(cur, 2),
                    "change":  round(chg, 2),
                    "changeP": round(chgP, 2),
                    "open":    round(float(h["Open"].iloc[-1]), 2),
                    "high":    round(float(h["High"].iloc[-1]), 2),
                    "low":     round(float(h["Low"].iloc[-1]), 2),
                    "vol":     int(h["Volume"].iloc[-1]),
                }
            elif len(h) == 1:
                cur = h["Close"].iloc[-1]
                prices[code] = {
                    "name":    name_map.get(code, code),
                    "price":   round(cur, 2),
                    "change":  0, "changeP": 0,
                    "open": round(cur, 2), "high": round(cur, 2),
                    "low": round(cur, 2), "vol": 0,
                }
            time.sleep(0.2)
        except Exception as e:
            print(f"   ⚠️ {code}: {e}")
    return prices


def fetch_history(code, period="5y"):
    """抓取個股一年歷史收盤價"""
    try:
        t = yf.Ticker(code)
        h = t.history(period=period, interval="1d")
        if h.empty:
            return [], [], []
        closes  = [round(float(c), 2) for c in h["Close"]]
        opens   = [round(float(c), 2) for c in h["Open"]]
        highs   = [round(float(c), 2) for c in h["High"]]
        lows    = [round(float(c), 2) for c in h["Low"]]
        volumes = [int(v) for v in h["Volume"]]
        labels  = [d.strftime("%Y/%m/%d") for d in h.index]
        return closes, labels, volumes, opens, highs, lows
    except Exception as e:
        print(f"   ⚠️ history {code}: {e}")
        return [], [], [], [], [], []


def fetch_etf_holdings():
    """抓 ETF 前15大持股"""
    etf_info = {
        "QQQ": {
            "name": "Invesco QQQ",
            "desc": "追蹤那斯達克100指數，科技股龍頭ETF",
            # 靜態前15大持股（定期更新）
            "holdings": [
                {"code": "MSFT",  "name": "微軟",     "weight": 8.5},
                {"code": "AAPL",  "name": "蘋果",     "weight": 7.9},
                {"code": "NVDA",  "name": "輝達",     "weight": 7.2},
                {"code": "AMZN",  "name": "亞馬遜",   "weight": 5.3},
                {"code": "GOOGL", "name": "Alphabet", "weight": 4.8},
                {"code": "META",  "name": "Meta",     "weight": 4.6},
                {"code": "TSLA",  "name": "特斯拉",   "weight": 3.8},
                {"code": "AVGO",  "name": "博通",     "weight": 3.2},
                {"code": "COST",  "name": "好市多",   "weight": 2.8},
                {"code": "NFLX",  "name": "Netflix",  "weight": 2.5},
                {"code": "AMD",   "name": "超微",     "weight": 2.1},
                {"code": "ADBE",  "name": "Adobe",    "weight": 1.9},
                {"code": "INTU",  "name": "Intuit",   "weight": 1.8},
                {"code": "NOW",   "name": "ServiceNow","weight": 1.7},
                {"code": "CRM",   "name": "Salesforce","weight": 1.6},
            ]
        },
        "VOO": {
            "name": "Vanguard S&P 500",
            "desc": "追蹤標普500指數，全美最廣泛市值加權ETF",
            "holdings": [
                {"code": "MSFT",  "name": "微軟",     "weight": 7.1},
                {"code": "AAPL",  "name": "蘋果",     "weight": 6.5},
                {"code": "NVDA",  "name": "輝達",     "weight": 6.0},
                {"code": "AMZN",  "name": "亞馬遜",   "weight": 4.0},
                {"code": "GOOGL", "name": "Alphabet", "weight": 3.5},
                {"code": "META",  "name": "Meta",     "weight": 2.8},
                {"code": "TSLA",  "name": "特斯拉",   "weight": 2.2},
                {"code": "AVGO",  "name": "博通",     "weight": 2.0},
                {"code": "LLY",   "name": "禮來",     "weight": 1.5},
                {"code": "COST",  "name": "好市多",   "weight": 1.4},
                {"code": "V",     "name": "Visa",     "weight": 1.3},
                {"code": "NFLX",  "name": "Netflix",  "weight": 1.1},
                {"code": "ISRG",  "name": "直覺外科", "weight": 0.9},
                {"code": "CRM",   "name": "Salesforce","weight": 0.8},
                {"code": "NOW",   "name": "ServiceNow","weight": 0.8},
            ]
        },
        "SOXX": {
            "name": "iShares 費城半導體",
            "desc": "追蹤費城半導體指數，半導體產業龍頭ETF",
            "holdings": [
                {"code": "NVDA",  "name": "輝達",   "weight": 10.5},
                {"code": "AVGO",  "name": "博通",   "weight": 8.8},
                {"code": "AMD",   "name": "超微",   "weight": 5.2},
                {"code": "QCOM",  "name": "高通",   "weight": 4.8},
                {"code": "INTC",  "name": "英特爾", "weight": 4.2},
                {"code": "MU",    "name": "美光",   "weight": 4.0},
                {"code": "AMAT",  "name": "應材",   "weight": 3.8},
                {"code": "KLAC",  "name": "科磊",   "weight": 3.5},
                {"code": "LRCX",  "name": "蘭姆研究","weight": 3.2},
                {"code": "TXN",   "name": "德儀",   "weight": 3.0},
                {"code": "MRVL",  "name": "邁威爾", "weight": 2.8},
                {"code": "ON",    "name": "安森美", "weight": 2.2},
                {"code": "MPWR",  "name": "芒果",   "weight": 2.0},
                {"code": "ADI",   "name": "亞德諾", "weight": 1.9},
                {"code": "MCHP",  "name": "微芯科技","weight": 1.8},
            ]
        },
    }
    return etf_info


def main():
    print("🚀 美股資料抓取開始...")
    print(f"   時間: {datetime.datetime.now()}")

    # 載入舊資料（保留上次成功的欄位）
    data = {}
    if os.path.exists("us_data.json"):
        with open("us_data.json", "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except Exception:
                data = {}

    # 1. 市場指數
    print("📈 美股大盤指數...")
    market = fetch_market_indices()
    if market:
        data["market"] = market
        for k, v in market.items():
            emoji = "📈" if v["chgP"] >= 0 else "📉"
            print(f"   {emoji} {k}: {v['price']} ({v['chgP']:+.2f}%)")

    # 2. VIX
    print("😱 VIX 恐慌指數...")
    vix = fetch_vix()
    if vix:
        data["vix"] = vix
        print(f"   ✅ VIX: {vix['price']} ({vix['chg']:+.2f})")

    # 3. 個股價格
    print(f"📊 抓取 {len(US_STOCKS)} 支個股價格...")
    codes = [s["code"] for s in US_STOCKS]
    prices = fetch_stock_prices(codes)
    data["prices"] = prices
    print(f"   ✅ 成功: {len(prices)} 支")

    # 4. 歷史走勢（用於圖表 + p5/p30/p180 計算）
    print(f"📉 抓取歷史走勢...")
    histories = {}
    for i, code in enumerate(codes, 1):
        closes, labels, volumes, opens, highs, lows = fetch_history(code)
        if closes:
            histories[code] = {
                "labels":  labels,
                "closes":  closes,
                "volumes": volumes,
                "opens":   opens,
                "highs":   highs,
                "lows":    lows,
            }
        time.sleep(0.2)
        if i % 5 == 0:
            print(f"   進度 {i}/{len(codes)}...")
    data["histories"] = histories
    print(f"   ✅ {len(histories)} 支歷史已儲存")

    # 5. 機構持股比例
    print("🏦 抓取機構持股比例...")
    inst_data = {}
    for i, code in enumerate(codes, 1):
        try:
            info = yf.Ticker(code).info
            inst_pct   = info.get("heldPercentInstitutions")
            short_pct  = info.get("shortPercentOfFloat")
            insider_pct= info.get("heldPercentInsiders")
            inst_data[code] = {
                "inst":    round(inst_pct * 100, 1)   if inst_pct   else None,
                "short":   round(short_pct * 100, 1)  if short_pct  else None,
                "insider": round(insider_pct * 100, 1) if insider_pct else None,
            }
            time.sleep(0.15)
        except Exception as e:
            print(f"   ⚠️ {code}: {e}")
        if i % 5 == 0:
            print(f"   進度 {i}/{len(codes)}...")
    data["inst_data"] = inst_data
    print(f"   ✅ {len(inst_data)} 支機構持股已儲存")

    # 6. ETF 持股
    print("📦 ETF 持股資料...")
    etf_holdings = fetch_etf_holdings()
    data["etf_holdings"] = etf_holdings
    print(f"   ✅ {len(etf_holdings)} 檔 ETF")

    # 6. 更新時間
    data["updated_at"] = datetime.datetime.now().strftime("%Y/%m/%d %H:%M")

    # 儲存
    with open("us_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n🎉 完成！更新時間: {data['updated_at']}")


if __name__ == "__main__":
    main()
