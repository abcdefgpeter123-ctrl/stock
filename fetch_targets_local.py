#!/usr/bin/env python3
"""
本機專用：抓鉅亨網「國外法人(QFII)個股評等」表，取得帶日期的個別券商目標價。

╔══════════════════════════════════════════════════════════════════════╗
║ 這支腳本刻意「只在本機手動執行」，不要放進 GitHub Actions。            ║
║                                                                      ║
║ 原因：鉅亨網服務條款禁止未經書面授權的「重製、公開傳播、散布」，       ║
║ 而本專案的 repo 是 public、又用 GitHub Pages 對外提供，把抓下來的      ║
║ 數字 commit 進去就是條款明文禁止的行為。原始資料源是 FactSet，        ║
║ 鉅亨自己也只是被授權方，不可能再轉授權給我們。                        ║
║                                                                      ║
║ 因此輸出檔 targets_local.json 已列入 .gitignore，只存在你的電腦上，   ║
║ index.html 載入不到時會自動略過這段顯示。請勿手動 commit 它。         ║
╚══════════════════════════════════════════════════════════════════════╝

用法：
    python3 fetch_targets_local.py

一天跑一次就夠（評等異動本來就是低頻資料）。
"""

import html
import json
import os
import re
import sys
from datetime import datetime

import requests

URL = "https://www.cnyes.com/twstock/board/ratediff.aspx"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "targets_local.json")
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "zh-TW,zh;q=0.9",
}

ROW_RE  = re.compile(r"<TR>(.*?)</TR>", re.I | re.S)
CELL_RE = re.compile(r"<TD[^>]*>(.*?)</TD>", re.I | re.S)
TAG_RE  = re.compile(r"<[^>]+>")


def _text(cell: str) -> str:
    return html.unescape(TAG_RE.sub("", cell)).replace("\xa0", " ").strip()


def _num(s: str):
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse(page: str):
    """回傳 [{date, code, name, broker, rec_from, dir, rec_to, old, new, price}]"""
    out = []
    for row in ROW_RE.findall(page):
        c = [_text(x) for x in CELL_RE.findall(row)]
        if len(c) < 9:
            continue
        date, codename, broker, rec_from, direction, rec_to, old, new, price = c[:9]
        if not re.fullmatch(r"\d{8}", date):
            continue
        m = re.match(r"(\d{4}[A-Z]?)-(.+)", codename)
        if not m:
            continue
        out.append({
            "date":   f"{date[:4]}/{date[4:6]}/{date[6:]}",
            "code":   m.group(1),
            "name":   m.group(2),
            "broker": broker or "—",
            "rec_from": rec_from,
            "dir":    direction,
            "rec_to": rec_to,
            "old":    _num(old),
            "new":    _num(new),
            "price":  _num(price),
        })
    return out


def watchlist():
    """只留自己有在追的股票，減少檔案大小也避免無謂持有整份表。"""
    try:
        with open(DATA, encoding="utf-8") as f:
            return set(json.load(f).get("prices", {}).keys())
    except Exception:
        return set()


def main():
    try:
        r = requests.get(URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"✗ 抓取失敗：{e}")
        return 1
    r.encoding = r.apparent_encoding or "utf-8"

    rows = parse(r.text)
    if not rows:
        print("✗ 解析不到任何列——頁面版型可能改了，請開網頁確認")
        return 1

    watch = watchlist()
    kept = [x for x in rows if not watch or x["code"] in watch]

    # 這頁一次只列出最近幾天的異動（本次 ~30 筆），所以要跟舊檔合併才累積得出歷史。
    # 以 (日期, 券商, 新目標價) 當唯一鍵去重。
    by_code = {}
    try:
        with open(OUT, encoding="utf-8") as f:
            for c, v in json.load(f).get("targets", {}).items():
                by_code[c] = list(v)
    except Exception:
        pass

    added = 0
    for x in sorted(kept, key=lambda v: v["date"]):
        cur = by_code.setdefault(x["code"], [])
        key = (x["date"], x["broker"], x["new"])
        if any((y.get("date"), y.get("broker"), y.get("new")) == key for y in cur):
            continue
        cur.append(x)
        added += 1
    for c in by_code:
        by_code[c].sort(key=lambda v: v.get("date", ""))

    result = {
        "generated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "source": "cnyes.com 外資評等（FactSet）— 本機自用，請勿散布",
        # 每檔只留最近 8 筆，卡片也只顯示 3 筆
        "targets": {c: v[-8:] for c, v in by_code.items()},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    latest = max((x["date"] for x in kept), default="—")
    print(f"✓ 全表 {len(rows)} 筆，命中觀察名單 {len(kept)} 筆（新增 {added} 筆）"
          f"／累積 {len(by_code)} 檔（最新 {latest}）")
    print(f"✓ 已寫入 {OUT}（未納入 git，僅本機可見）")
    for c, v in list(by_code.items())[:5]:
        t = v[-1]
        print(f"   {c} {t['name']:<6} {t['date']}  目標 {t['old'] or '—'} → {t['new']}  {t['rec_to'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
