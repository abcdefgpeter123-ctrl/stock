#!/usr/bin/env python3
"""
本機專用：抓主動型 ETF 每日增減持股（來源：籌碼小宇 ETF）。

╔══════════════════════════════════════════════════════════════════════╗
║ 這支腳本刻意「只在本機手動執行」，不要放進 GitHub Actions。            ║
║                                                                      ║
║ 來源站 robots.txt 是 Allow: /，也沒有禁止程式讀取的條款，抓取本身沒問題。║
║ 問題在再散布：該站自述「本站資料整理自 CMoney（其數據源自臺灣證券      ║
║ 交易所、櫃買中心、公開資訊觀測站等公開資訊），僅供研究觀察之用」——     ║
║ 它跟我們一樣是被授權方/整理者，沒有立場把 CMoney 的資料再授權給我們。   ║
║ 而本專案 repo 是 public、又用 GitHub Pages 對外提供，把數字 commit     ║
║ 進去就等於再散布。跟 fetch_targets_local.py 是同一個判斷。            ║
║                                                                      ║
║ 因此輸出檔 active_etf_local.json 已列入 .gitignore，只存在你的電腦上， ║
║ index.html 載入不到時會自動略過該區塊。請勿手動 commit 它。           ║
║                                                                      ║
║ 想要能公開的版本，正途是自己從投信每日 PCF／TWSE 公告算持股差異，      ║
║ 那是原始公開資訊，沒有轉授權問題（但工程量大得多）。                   ║
╚══════════════════════════════════════════════════════════════════════╝

用法：
    python3 fetch_active_etf_local.py

一天跑一次就夠（來源本身也是日更）。
"""

import json
import os
import sys
from datetime import datetime

import requests

URL = "https://xiaoyu-etf.pages.dev/data.js"
D   = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(D, "active_etf_local.json")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "zh-TW,zh;q=0.9",
}

TOP_N = 15          # 全市場買、賣各留幾檔（畫面上只看得完這麼多）
KEEP_ETFS = 4       # 每檔個股列出幾家 ETF
PER_ETF_N = 12      # 每檔 ETF 的加碼／減碼各列幾檔
HOLD_N    = 15      # 回填 ETF 專區圓餅圖的前幾大持股

# 儀表板 ETF 專區有的那幾檔（index.html 的 ETFS）。
# 這裡是自己追蹤的清單，改了要跟 index.html 對齊。
MY_ETFS = ["0050", "0056", "00992A", "00981A", "00988A", "00990A"]


def slim(row):
    """只留畫面會用到的欄位，順便把 ETF 名稱縮短"""
    etfs = sorted(row.get("etfs", []), key=lambda e: -abs(e.get("d1") or 0))[:KEEP_ETFS]
    return {
        "code":  row["code"],
        "name":  row["name"],
        "lots":  row.get("lots"),          # 張（正買負賣）
        "money": row.get("money"),         # 億元
        "chg":   row.get("chg"),           # 當日漲跌%
        "n":     row.get("etf_count"),     # 幾檔 ETF 動作
        "etfs":  [{"name": e["name"].replace("主動", ""), "lots": e.get("d1")} for e in etfs],
    }


def etf_moves(etf):
    """單一 ETF 的當日持股增減。d1 是張數變化，new/clear 是新進與清倉。"""
    adds, cuts, news, outs = [], [], [], []
    for h in etf.get("holdings") or []:
        row = {"code": h["code"], "name": h["name"], "lots": h.get("lots"),
               "d1": h.get("d1") or 0, "w": h.get("weight"), "chg": h.get("chg")}
        if h.get("new"):     news.append(row)
        elif h.get("clear"): outs.append(row)
        elif row["d1"] > 0:  adds.append(row)
        elif row["d1"] < 0:  cuts.append(row)
    adds.sort(key=lambda r: -r["d1"])
    cuts.sort(key=lambda r: r["d1"])
    # ETF 專區的圓餅圖也吃這份——原本那裡是好幾個月前的手動快照
    top = sorted((etf.get("holdings") or []),
                 key=lambda h: -(h.get("weight") or 0))[:HOLD_N]
    return {
        "code": etf["code"], "name": etf["name"],
        "updated": etf.get("updated"),        # False＝這檔今天還沒揭露，數字是舊的
        "date": etf.get("date"),
        "net": etf.get("net"), "add_n": etf.get("add_n"), "cut_n": etf.get("cut_n"),
        "adds": adds[:PER_ETF_N], "cuts": cuts[:PER_ETF_N],
        "news": news[:6], "outs": outs[:6],
        "holdings": [{"code": h["code"], "name": h["name"], "weight": h.get("weight")}
                     for h in top],
    }


def main():
    try:
        r = requests.get(URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f"✗ 抓取失敗：{e}")
        return 1

    text = r.text
    try:
        data = json.loads(text[text.index("{"):].rstrip().rstrip(";"))
    except Exception as e:
        print(f"✗ 解析失敗（來源格式可能改了）：{e}")
        return 1

    rank = (data.get("rank") or {}).get("active") or {}
    d1   = rank.get("d1") or {}
    if not d1.get("buy") and not d1.get("sell"):
        print("✗ 找不到主動型 ETF 的當日增減資料——來源結構可能改了")
        return 1

    meta = data.get("meta") or {}
    result = {
        "generated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "data_date":    meta.get("latest_slash"),
        "prev_date":    meta.get("prev_slash"),
        "active_total": meta.get("active_total"),
        "updated":      meta.get("active_updated"),
        "source": "籌碼小宇 ETF（整理自 CMoney）— 本機自用，請勿散布",
        "buy":  [slim(x) for x in d1.get("buy",  [])[:TOP_N]],
        "sell": [slim(x) for x in d1.get("sell", [])[:TOP_N]],
        # 自己追蹤的那幾檔 ETF，逐檔列出當天加碼／減碼了什麼
        "mine": [etf_moves(e) for e in data.get("etfs", []) if e.get("code") in MY_ETFS],
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"✓ 資料日 {result['data_date']}（前一日 {result['prev_date']}）"
          f"／{result['updated']}/{result['active_total']} 檔主動型 ETF 已更新")
    print(f"✓ 買超 {len(result['buy'])} 檔、賣超 {len(result['sell'])} 檔 → {OUT}")
    print(f"  （{os.path.getsize(OUT)/1024:.0f}K，未納入 git，僅本機可見）\n")
    print("  我的 ETF：")
    for m in result["mine"]:
        stale = "" if m["updated"] else "  ⚠️尚未揭露，數字為舊值"
        top = "、".join(f"{a['name']}{a['d1']:+}" for a in m["adds"][:2]) or "—"
        print(f"    {m['code']:<8} {m['name']:<14} 加{m['add_n']:>2}/減{m['cut_n']:>2}  "
              f"主要加碼 {top}{stale}")
    print()
    for side, label in (("buy", "買超"), ("sell", "賣超")):
        print(f"  {label}前 3：")
        for x in result[side][:3]:
            who = "、".join(f"{e['name']}{e['lots']:+}" for e in x["etfs"][:2])
            print(f"    {x['code']} {x['name']:<6} {x['lots']:+6} 張  {x['money']:+.2f} 億  ({who})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
