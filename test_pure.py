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

    eq(len(wl), 64, "監控清單 64 檔")
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


# ── prev_close_before（Yahoo 昨收）──────────────────────────────
def test_prev_close_before():
    """
    2026/10/29：Yahoo 日 K 還沒附上今天那根時，舊寫法 closes[-2] 拿到「前天」，
    聯發科 −4.49% 顯示成 +2.15%。昨收必須是「日期早於今天的最後一根」。
    """
    from fetch_data_full import prev_close_before
    print("prev_close_before")
    ts = lambda y, m, d: int(datetime.datetime(y, m, d, 9, 0).timestamp())
    days = [ts(2026, 10, 27), ts(2026, 10, 28)]
    # 今天(10/29)那根還沒出現：昨收應是 10/28，不是 10/27
    eq(prev_close_before(days, [3960, 4235], "2026/10/29"), 4235, "今天的 K 棒缺席時取昨天（曾經誤取前天）")
    # 今天那根已附上：一樣取 10/28
    eq(prev_close_before(days + [ts(2026, 10, 29)], [3960, 4235, 4045], "2026/10/29"), 4235,
       "今天的 K 棒已存在時取昨天")
    # 今天那根 close 是 None
    eq(prev_close_before(days + [ts(2026, 10, 29)], [3960, 4235, None], "2026/10/29"), 4235,
       "今天的 close 為 None 不影響")
    # 昨天那根 close 是 None → 往前找
    eq(prev_close_before(days, [3960, None], "2026/10/29"), 3960, "昨天為 None 時往前取")
    eq(prev_close_before([], [], "2026/10/29"), None, "沒有資料回 None")


# ── _parse_twse_rwd_rows（上市個股漲跌方向）─────────────────────
def test_parse_twse_rwd_direction():
    """
    MI_INDEX 的「漲跌(+/-)」是 HTML（'<p style= color:green>-</p>'），
    舊版只比對 '-'，所有下跌都被算成上漲。
    """
    from fetch_data_full import _parse_twse_rwd_rows
    print("_parse_twse_rwd_rows 漲跌方向")
    fields = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價',
              '最低價', '收盤價', '漲跌(+/-)', '漲跌價差']
    row = lambda code, d, chg, close: [code, 'x', '1,000', '1', '1', close, close, close, close, d, chg]
    p = _parse_twse_rwd_rows({"fields": fields, "data": [
        row('2454', '<p style= color:green>-</p>', '130.00', '3,915.00'),
        row('1101', '<p style= color:red>+</p>', '0.15', '28.35'),
        row('2330', '<p> </p>', '0.00', '2,370.00'),
        row('9999', '<p>X</p>', '5.00', '100.00'),
    ]})
    eq(p['2454']['changeP'], -3.21, "HTML 綠色 '-' → 下跌（曾經誤判成 +3.43%）")
    eq(p['2454']['change'], -130.0, "下跌的漲跌價差為負")
    eq(p['1101']['changeP'], 0.53, "HTML 紅色 '+' → 上漲")
    eq(p['2330']['changeP'], 0.0, "平盤")
    eq(p['9999']['changeP'], 0.0, "X（不比價）當作 0")


def main():
    for fn in [test_roc_to_ad_date, test_safe_float, test_merge_eps_history,
               test_split_heavy_payloads, test_weekdays_between, test_prev_close_before,
               test_parse_twse_rwd_direction,
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
