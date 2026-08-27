#!/usr/bin/env python3
"""
純函式的最小回歸測試。

【範圍刻意很窄】
不測抓取、不測網路、不 mock HTTP——那些本來就會因為外部網站改版而壞，
維護成本高於價值。這裡只測「給定輸入必得同一輸出」的純函式，
而且優先測**曾經真的出過事**的那幾個：

  · roc_to_ad_date()     — 認不得 '20260731' 格式，把 07/31 的法人資料標成 08/03
  · merge_eps_history()  — Yahoo 視窗滑動，直接覆蓋會讓早期 EPS 一去不回
  · split_heavy_payloads() / 融資歷史累積 — 讀錯來源會把 5000 筆砍成 30 筆
  · weekdays_between()   — 用「幾天」而不是「幾個平日」會在週末必定誤報

執行：
    python3 test_pure.py          # 全部，失敗會印出差異並 exit 1

不依賴 pytest，因為 Actions 的 runner 不想再多裝一個套件。
"""

import datetime
import json
import os
import sys
import tempfile

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)

FAILED = []


def eq(got, want, label):
    if got == want:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}\n      得到 {got!r}\n      預期 {want!r}")
        FAILED.append(label)


# ── roc_to_ad_date ────────────────────────────────────────────────
def test_roc_to_ad_date():
    """TWSE 各端點日期格式不統一，三種都要認得。"""
    from fetch_data_full import roc_to_ad_date
    fb = datetime.date(2026, 8, 3)          # 「今天」——認不得時的退路

    print("roc_to_ad_date")
    eq(roc_to_ad_date("115/05/19", fb), "2026/05/19", "民國有斜線")
    eq(roc_to_ad_date("1150519",   fb), "2026/05/19", "民國無斜線")
    # 這一條就是當初的 bug：認不得 → 落到 fallback → 07/31 的資料被標成 08/03
    eq(roc_to_ad_date("20260731",  fb), "2026/07/31", "西元無斜線（曾經誤判）")
    eq(roc_to_ad_date("2026/07/31", fb), "2026/07/31", "西元有斜線")
    eq(roc_to_ad_date("115/5/9",   fb), "2026/05/09", "個位數要補零")

    # 認不得的一律回 fallback，不可以拋例外——它跑在無人值守的 Actions 裡
    for bad in ["", None, "abc", "115/13", "999999999", "115/05/19/01"]:
        eq(roc_to_ad_date(bad, fb), "2026/08/03", f"壞輸入 {bad!r} → fallback")


# ── safe_float ────────────────────────────────────────────────────
def test_safe_float():
    from fetch_data_full import safe_float
    print("safe_float")
    eq(safe_float("1,234.5"), 1234.5, "去掉千分位逗號")
    eq(safe_float("  12  "), 12.0, "去掉空白")
    eq(safe_float("--"), 0.0, "TWSE 的空值符號 → 預設")
    eq(safe_float(None), 0.0, "None → 預設")
    eq(safe_float("x", default=-1), -1, "自訂預設值")


# ── merge_eps_history ─────────────────────────────────────────────
def test_merge_eps_history():
    """Yahoo 的 EPS 視窗會往前滑，舊季度不可以因此消失。"""
    from fetch_data_full import merge_eps_history
    print("merge_eps_history")

    old = [{"q": "2025Q1", "eps": 1.0}, {"q": "2025Q2", "eps": 2.0}]
    new = [{"q": "2025Q2", "eps": 2.5}, {"q": "2025Q3", "eps": 3.0}]
    got = merge_eps_history(old, new, "q", 8)
    eq([x["q"] for x in got], ["2025Q1", "2025Q2", "2025Q3"], "聯集且依 key 排序")
    eq(got[1]["eps"], 2.5, "同一期以新值覆蓋")

    # 這是重點：新視窗不含 2025Q1，但它必須留著
    eq(merge_eps_history(old, [{"q": "2025Q3", "eps": 3.0}], "q", 8)[0]["q"],
       "2025Q1", "新視窗滑掉的舊季度仍保留")

    eq(len(merge_eps_history(
        [{"q": f"20{i:02d}Q1", "eps": 1} for i in range(20)], [], "q", 8)),
       8, "只留最近 n_keep 筆")
    eq(merge_eps_history(None, None, "q", 8), [], "兩邊都空不會炸")
    eq(merge_eps_history([{"eps": 1}], [], "q", 8), [], "沒有 key 的列被略過")


# ── split_heavy_payloads ──────────────────────────────────────────
def test_split_heavy_payloads():
    """
    margin_history.json 是融資歷史的正本。
    這個測試釘住的是：切分後 data.json 只留 30 筆，但正本必須是完整的。
    先前差點寫成從 data.json 累積，那會每跑一次就把五千多筆砍成 30 筆。
    """
    import fetch_data_full as F
    print("split_heavy_payloads")

    hist = [{"date": f"d{i}", "ratio": (i if i % 2 == 0 else None)}
            for i in range(100)]
    data = {"pe_river": {"2330": [1, 2, 3]},
            "margin": {"history": list(hist)}}

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            F.split_heavy_payloads(data)
            eq("pe_river" in data, False, "pe_river 已移出 data.json")
            eq(json.load(open("pe_river.json"))["2330"], [1, 2, 3],
               "pe_river 正確落到獨立檔")

            full = json.load(open("margin_history.json"))
            eq(len(full), 100, "margin_history.json 是完整正本")
            eq(len(data["margin"]["history"]), 30, "data.json 只留 30 筆")
            eq(data["margin"]["history"][-1], hist[-1], "留的是最新的 30 筆")
            eq(data["margin"]["ratios"], [i for i in range(100) if i % 2 == 0],
               "ratios 濾掉 None 且保留全部分布")
        finally:
            os.chdir(cwd)


# ── weekdays_between（資料過期判斷）────────────────────────────────
def test_weekdays_between():
    """用「幾個平日」而不是「幾天」，否則週末一定誤報。"""
    from check_freshness import weekdays_between
    print("weekdays_between")
    # 它收的是 datetime（內部呼叫 .date()），不是 date
    d = lambda y, m, day: datetime.datetime(y, m, day)
    eq(weekdays_between(d(2026, 8, 24), d(2026, 8, 25)), 1, "週一→週二 = 1")
    eq(weekdays_between(d(2026, 8, 21), d(2026, 8, 24)), 1,
       "週五→週一 = 1（週末不算，這是不誤報的關鍵）")
    eq(weekdays_between(d(2026, 8, 21), d(2026, 8, 23)), 0, "週五→週日 = 0")
    eq(weekdays_between(d(2026, 8, 24), d(2026, 8, 24)), 0, "同一天 = 0")
    eq(weekdays_between(d(2026, 8, 17), d(2026, 8, 24)), 5, "整整一週 = 5")


# ── stocks.json 的資料完整性 ───────────────────────────────────────
def test_stocks_json():
    """
    唯一來源的自我檢查。清單是手改的，錯字不會有任何機制擋下來。
    """
    print("stocks.json")
    d = json.load(open(os.path.join(D, "stocks.json"), encoding="utf-8"))
    wl = d["tw_watchlist"]

    eq(len(wl), 62, "監控清單 62 檔")
    codes = [s["code"] for s in wl]
    eq(len(set(codes)), len(codes), "沒有重複代號")
    eq([c for c in codes if not c[:4].isdigit()], [], "代號前四碼都是數字")
    eq([s["code"] for s in wl if not s.get("name") or not s.get("theme")], [],
       "每檔都有 name 與 theme")

    overlap = set(codes) & set(d["tw_fetch_extra"])
    eq(overlap, set(), "tw_fetch_extra 不可與監控清單重疊")

    # theme_groups 的 leader 必須也在 members 或清單裡，否則機會點算不出龍頭漲幅
    orphan = [g for g, v in d["theme_groups"].items()
              if not set(v.get("leaders", [])) & set(v.get("members", []) + codes)]
    eq(orphan, [], "每個題材組的 leader 都找得到對應個股")

    # theme_parent 的值（父題材）不該再出現在鍵裡，否則是兩層以上的巢狀
    tp = d.get("theme_parent", {})
    eq([v for v in set(tp.values()) if v in tp], [], "題材父子關係只有一層")


def main():
    for fn in [test_roc_to_ad_date, test_safe_float, test_merge_eps_history,
               test_split_heavy_payloads, test_weekdays_between,
               test_stocks_json]:
        fn()
        print()

    if FAILED:
        print(f"✗ {len(FAILED)} 項失敗：")
        for f in FAILED:
            print(f"    · {f}")
        return 1
    print("✓ 全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
