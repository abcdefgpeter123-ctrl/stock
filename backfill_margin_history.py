#!/usr/bin/env python3
"""
一次性回補全市場融資餘額歷史資料（回溯到金融海嘯前，約 2006 年初）。
只需執行一次，把結果寫進 data.json 的 margin.history，
之後 fetch_data_full.py 每天照常在尾端累加一筆即可，不用重跑這支。

用法：
    python3 backfill_margin_history.py
    # 產生 margin_history_backfill.json 後，手動合併進 data.json["margin"]["history"]
"""

import json
import time
import datetime
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

TWII_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&period1=1136073600&period2=9999999999"
MARGIN_URL = "https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={date}&selectType=MS"
OUT_FILE = "margin_history_backfill.json"
PROGRESS_FILE = "margin_backfill_progress.json"


def fetch_twii_calendar():
    """回傳 [(date_str YYYYMMDD, close), ...] 依時間排序，做為交易日曆＋大盤漲跌基準"""
    r = requests.get(TWII_URL, headers=HEADERS, timeout=30)
    result = r.json()["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    closes_raw = result["indicators"]["quote"][0].get("close", [])
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    out = []
    for t, c in zip(timestamps, closes_raw):
        if c is None:
            continue
        d = datetime.datetime.fromtimestamp(t, tz=tz_tw)
        out.append((d.strftime("%Y%m%d"), round(c, 2)))
    return out


def fetch_margin_balance(date_str):
    """回傳 (今日餘額_張, 前日餘額_張) 或 None"""
    try:
        url = MARGIN_URL.format(date=date_str)
        r = requests.get(url, headers=HEADERS, timeout=15)
        d = r.json()
        if d.get("stat") != "OK":
            return None
        tables = d.get("tables", [])
        if not tables:
            return None
        rows = tables[0].get("data", [])
        for row in rows:
            if row and "融資" in row[0] and "金額" not in row[0]:
                # ["融資(交易單位)", 買進, 賣出, 現金(券)償還, 前日餘額, 今日餘額]
                prev = int(row[-2].replace(",", ""))
                today = int(row[-1].replace(",", ""))
                return today, prev
        return None
    except Exception:
        return None


def main():
    print("📊 開始回補融資餘額歷史資料...")
    calendar = fetch_twii_calendar()
    print(f"   交易日曆: {len(calendar)} 筆（{calendar[0][0]} ~ {calendar[-1][0]}）")

    # 支援中斷續跑
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            results = json.load(f)
    except Exception:
        results = {}

    total = len(calendar)
    prev_twii = None
    for i, (date_str, twii_close) in enumerate(calendar, 1):
        if date_str in results:
            prev_twii = twii_close
            continue

        bal = fetch_margin_balance(date_str)
        if bal:
            today_lots, prev_lots = bal
            change_pct = round((today_lots - prev_lots) / prev_lots * 100, 2) if prev_lots else None
            twii_chg_p = None
            if prev_twii is not None and prev_twii > 0:
                twii_chg_p = round((twii_close - prev_twii) / prev_twii * 100, 2)
            ratio = None
            if (twii_chg_p is not None and twii_chg_p < -0.3 and
                    change_pct is not None and change_pct <= 0):
                ratio = round(change_pct / twii_chg_p * 100, 1)
            results[date_str] = {
                "balance": today_lots,
                "change_pct": change_pct,
                "twii_chgP": twii_chg_p,
                "ratio": ratio,
            }
        else:
            results[date_str] = None  # 記錄過但無資料（假日/資料缺漏），避免重複嘗試

        prev_twii = twii_close

        if i % 100 == 0 or i == total:
            print(f"   進度 {i}/{total}（{date_str}）...")
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False)

        time.sleep(0.15)

    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)

    # 整理成 history 陣列格式（依日期排序，過濾掉 None）
    history = []
    for date_str, v in sorted(results.items()):
        if v is None:
            continue
        d = datetime.datetime.strptime(date_str, "%Y%m%d")
        history.append({
            "date": d.strftime("%Y/%m/%d"),
            "balance": v["balance"],
            "ratio": v["ratio"],
        })

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"✅ 完成！共 {len(history)} 個交易日寫入 {OUT_FILE}")


if __name__ == "__main__":
    main()
