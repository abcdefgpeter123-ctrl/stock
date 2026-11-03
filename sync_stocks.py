#!/usr/bin/env python3
"""
由 stocks.json 產生 stocks.js，並檢查各頁面是否真的改用共用清單。

背景：監控清單原本在 index.html、health_check.html、fetch_data_full.py 各寫一份，
格式還都不一樣。結果 health_check.html 漏掉廣達(2382)，兩頁的「AI 族群站上20MA」
分別算在 8 檔與 7 檔上——而且沒有任何機制會告訴你漏了。

現在 stocks.json 是唯一來源：
  · Python 直接讀 stocks.json
  · 網頁讀 stocks.js（本腳本產生，內容與 stocks.json 相同）

改完 stocks.json 後執行：
    python3 sync_stocks.py
"""

import json
import os
import re
import sys

D = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(D, "stocks.json")
OUT = os.path.join(D, "stocks.js")


def build():
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)

    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    body = json.dumps(payload, ensure_ascii=False, indent=1)

    js = (
        "// ⚠️ 這個檔案是產生出來的，不要直接改。\n"
        "// 來源：stocks.json　→　改完執行 python3 sync_stocks.py 重新產生。\n"
        "//\n"
        "// 監控清單的唯一來源。index.html 與 health_check.html 都從這裡取，\n"
        "// 才不會像以前那樣兩頁清單不一致（health_check 曾漏掉廣達 2382，\n"
        "// 造成兩頁的「AI 族群站上20MA」算在不同檔數上）。\n"
        f"window.STOCK_DATA = {body};\n"
    )
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(js)
    return data


def bump_version():
    """
    依 stocks.js 的內容雜湊更新各頁面 <script src="stocks.js?v=..."> 的版本號。

    之前版本號固定是 v=1，清單改了三次（健策、勤誠、亞光代號）都沒動過，
    瀏覽器若沿用舊快取就會拿到舊清單。用內容雜湊，清單沒變就不會改到頁面。
    """
    import hashlib
    h = hashlib.sha1(open(OUT, "rb").read()).hexdigest()[:8]
    changed = []
    for page in ("index.html", "health_check.html"):
        p = os.path.join(D, page)
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        new = re.sub(r'stocks\.js\?v=[^"\']*', f"stocks.js?v={h}", s)
        if new != s:
            open(p, "w", encoding="utf-8").write(new)
            changed.append(page)
    return h, changed


def check(data):
    """確認頁面沒有偷偷留著自己的硬編碼清單。"""
    problems = []
    for page, pat in (("index.html", r"const STOCKS\s*=\s*\["),
                      ("health_check.html", r"const WATCH_STOCKS\s*=\s*\[")):
        p = os.path.join(D, page)
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        if "stocks.js" not in s:
            problems.append(f"{page} 沒有載入 stocks.js")
        m = re.search(pat, s)
        if m:
            tail = s[m.end():m.end() + 400]
            if re.search(r'\{\s*code\s*:\s*"', tail):
                problems.append(f"{page} 仍有硬編碼的清單字面值")
    # 每個題材都要能從首頁的題材按鈕篩到（直接有按鈕，或掛在有按鈕的父題材底下）。
    # 「光學」就是這樣漏掉的：新增題材時沒人記得去 index.html 補按鈕。
    p = os.path.join(D, "index.html")
    if os.path.exists(p):
        pills = set(re.findall(r'data-theme="([^"]+)"', open(p, encoding="utf-8").read()))
        parent = data.get("theme_parent", {})
        for t in sorted({x["theme"] for x in data["tw_watchlist"]}):
            if t not in pills and parent.get(t) not in pills:
                problems.append(f"index.html 沒有「{t}」的題材按鈕（也沒有可篩到它的父題材）")
    return problems


def main():
    data = build()
    tw = data["tw_watchlist"]
    themes = {}
    for s in tw:
        themes[s["theme"]] = themes.get(s["theme"], 0) + 1

    ver, touched = bump_version()
    print(f"✓ 已產生 stocks.js（版本 {ver}" + (f"，已更新 {'、'.join(touched)}" if touched else "") + "）")
    print(f"  台股清單 {len(tw)} 檔／額外抓取 {len(data['tw_fetch_extra'])} 檔"
          f"／題材組 {len(data['theme_groups'])} 組")
    print("  題材分佈：" + "、".join(f"{k} {v}" for k, v in
                                sorted(themes.items(), key=lambda x: -x[1])[:6]) + " …")

    # 清單本身的一致性
    codes = [s["code"] for s in tw]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        print(f"  ⚠️ 清單有重複代號：{sorted(dup)}")
    known = set(codes)
    orphan = set()
    for g in data["theme_groups"].values():
        orphan |= (set(g["leaders"]) | set(g["members"])) - known
    if orphan:
        print(f"  ℹ️ 題材組裡有 {len(orphan)} 檔不在監控清單（只用於機會點偵測）：{sorted(orphan)}")

    problems = check(data)
    if problems:
        print("\n⚠️ 尚未完全接上：")
        for p in problems:
            print(f"   · {p}")
        return 1
    print("  index.html / health_check.html 皆已改用共用清單")
    return 0


if __name__ == "__main__":
    sys.exit(main())
