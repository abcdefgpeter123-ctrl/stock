#!/usr/bin/env python3
"""
回測「現價低於全體分析師最低目標」這個訊號。

【現在跑會說資料不足，這是正常的】
Yahoo 只給目標價的當下快照、沒有歷史序列，所以這個訊號在 2026/08/12 之前
沒有任何可回測的資料。`fetch_data_full.py` 從那天起每天寫一筆快照到
targets_history.json，這支腳本負責在資料夠了之後把答案算出來。

要看到有意義的結果，大概需要：
  · 20 日報酬 → 累積約 2 個月（訊號本身罕見，63 檔裡只有 8 檔左右）
  · 60 日報酬 → 累積約 4 個月
在那之前輸出的數字樣本太小，不要拿來做決定。

用法：
    python3 backtest_belowlow.py
"""

import json
import os
import statistics as st
import sys

D = os.path.dirname(os.path.abspath(__file__))
MIN_SAMPLES = 30          # 少於這個數就只報告、不下結論
LEVELS = ["大牛", "小牛", "橫盤", "小熊", "大熊"]


def market_levels():
    """
    {日期: 市場溫度}。用 market_temp_lib 逐日重算，不需要當初記錄——
    加權指數的 5 年歷史本來就在 data.json 裡，溫度隨時可以回推。
    """
    try:
        import market_temp_lib
        return market_temp_lib.market_levels()
    except Exception as e:
        print(f"（市場溫度分組不可用：{e}）")
        return {}


def load_prices():
    """{code: {日期: 收盤}}，把 data.json 近期與 history_5y.json 舊資料接起來"""
    data = json.load(open(os.path.join(D, "data.json"), encoding="utf-8"))
    hd = data["history_dates"]
    out = {}
    for code, rec in data["histories"].items():
        dates = hd[int(rec.get("l", 0))]
        closes = rec["closes"]
        n = min(len(dates), len(closes))
        out[code] = dict(zip(dates[-n:], closes[-n:]))
    try:
        h5 = json.load(open(os.path.join(D, "history_5y.json"), encoding="utf-8"))
        for code, old in h5.items():
            out.setdefault(code, {})
            for d, c in zip(old["labels"], old["closes"]):
                out[code].setdefault(d, c)
    except FileNotFoundError:
        pass
    return out


def fwd_return(series, dates_sorted, start_date, n):
    """從 start_date 起算 n 個交易日後的報酬（%）；資料不足回 None"""
    try:
        i = dates_sorted.index(start_date)
    except ValueError:
        return None
    if i + n >= len(dates_sorted):
        return None
    a, b = series[dates_sorted[i]], series[dates_sorted[i + n]]
    return (b - a) / a * 100 if a else None


def describe(vals, label):
    vals = [v for v in vals if v is not None]
    if not vals:
        print(f"  {label:<30} 無樣本")
        return
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    note = "" if len(vals) >= MIN_SAMPLES else "  ⚠️樣本不足，僅供參考"
    print(f"  {label:<30} n={len(vals):>5}  平均{st.mean(vals):+6.2f}%  "
          f"中位{st.median(vals):+6.2f}%  勝率{win:5.1f}%{note}")


def main():
    path = os.path.join(D, "targets_history.json")
    if not os.path.exists(path):
        print("✗ 還沒有 targets_history.json——等 Actions 跑過一次就會產生")
        return 1

    hist = json.load(open(path, encoding="utf-8"))
    prices = load_prices()
    all_days = sorted({r["d"] for rows in hist.values() for r in rows})
    print(f"快照涵蓋 {len(hist)} 檔 × {len(all_days)} 天"
          f"（{all_days[0]} ~ {all_days[-1]}）\n")

    signal, baseline, solo, multi, deep, shallow = [], [], [], [], [], []
    for code, rows in hist.items():
        series = prices.get(code)
        if not series:
            continue
        ds = sorted(series)
        for r in rows:
            rets = {n: fwd_return(series, ds, r["d"], n) for n in (20, 60)}
            rec = (r, rets)
            baseline.append(rec)
            if r.get("b"):
                signal.append(rec)
                (solo if (r.get("c") or 0) <= 1 else multi).append(rec)
                if r.get("lo") and r.get("p"):
                    gap = (r["lo"] - r["p"]) / r["p"] * 100
                    (deep if gap >= 15 else shallow).append(rec)

    if not signal:
        print("目前還沒有任何「現價低於最低目標」的樣本累積到可算報酬的天數。")
        print("繼續讓排程跑，過幾週再回來看。")
        return 0

    for n in (20, 60):
        print(f"═══ {n} 個交易日後報酬 ═══")
        describe([r[1][n] for r in baseline], "基準：所有有覆蓋的個股日")
        describe([r[1][n] for r in signal],   "現價低於最低目標")
        describe([r[1][n] for r in multi],    "  └ 2 位以上分析師")
        describe([r[1][n] for r in solo],     "  └ 只有 1 位分析師")
        describe([r[1][n] for r in deep],     "  └ 低於最低目標 ≥15%")
        describe([r[1][n] for r in shallow],  "  └ 低於最低目標 <15%")
        print()

    lv = market_levels()
    if lv:
        print("═══ 依進場當天的市場溫度分組（20 日報酬）═══")
        print("  這是本來想問的問題：這個訊號在哪種市場狀態下最有效。")
        for name in LEVELS:
            describe([r[1][20] for r in signal if lv.get(r[0]["d"]) == name],
                     f"低於最低目標 ＋ {name}")
        print()

    n20 = len([r for r in signal if r[1][20] is not None])
    if n20 < MIN_SAMPLES:
        print(f"⚠️ 20 日樣本只有 {n20} 筆（門檻 {MIN_SAMPLES}），"
              f"現在的數字還不足以判斷這個訊號有沒有用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
