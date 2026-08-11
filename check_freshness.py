#!/usr/bin/env python3
"""
資料新鮮度守門員 — 由 watchdog.yml 每天執行。

【為什麼需要，明明已經有 if: failure() 了】
`if: failure()` 只在「job 有跑起來但失敗」時觸發。Podcast 那支跑在自架 Mac 上，
電腦沒醒著時 job 根本不會開始執行，狀態是 cancelled——沒有任何一個 step 會跑到，
自然也不會有人通知。實際紀錄是最近 8 次有 4 次 cancelled，而且完全無聲。

所以這支從結果面檢查：不管中間發生什麼事，只要輸出檔案太舊就報。
跑在 ubuntu-latest 上，不依賴任何自架機器。

本機也可以直接跑：
    python3 check_freshness.py
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

D = os.path.dirname(os.path.abspath(__file__))

# 檔案, 顯示名稱, 取時間戳的欄位, 容許幾個「平日」沒更新
CHECKS = [
    ("data.json",            "台股資料",      "prices_date", 2),
    ("us_data.json",         "美股資料",      "updated_at",  2),
    ("podcast_summary.json", "Podcast 摘要",  "updated_at",  4),
]


def parse(ts):
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\D+(\d{1,2}):(\d{2}))?", str(ts or ""))
    if not m:
        return None
    y, mo, d, h, mi = m.group(1), m.group(2), m.group(3), m.group(4) or 0, m.group(5) or 0
    return datetime(int(y), int(mo), int(d), int(h), int(mi))


def weekdays_between(a, b):
    """a→b 之間相隔幾個平日（不含起日、含迄日）"""
    n, cur = 0, a.date()
    while cur < b.date():
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def main():
    now = datetime.now()
    stale = []

    for fn, label, field, limit in CHECKS:
        path = os.path.join(D, fn)
        if not os.path.exists(path):
            stale.append(f"- **{label}**：`{fn}` 不存在")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            stale.append(f"- **{label}**：`{fn}` 讀取失敗 — {e}")
            continue

        ts = parse(data.get(field) or data.get("updated_at"))
        if ts is None:
            stale.append(f"- **{label}**：`{fn}` 沒有可辨識的時間戳")
            continue

        # 未滿 40 小時一律視為正常：吸收時區差（美股那支寫的是 UTC）與排程延遲
        hours = (now - ts).total_seconds() / 3600
        gap = weekdays_between(ts, now)
        status = "OK " if (hours < 40 or gap <= limit) else "舊 "
        print(f"{status} {label:<14} {ts:%Y/%m/%d %H:%M}  "
              f"({hours:5.1f} 小時前 / 相隔 {gap} 個平日，容許 {limit})")
        if status == "舊 ":
            stale.append(f"- **{label}**：停在 `{ts:%Y/%m/%d %H:%M}`，"
                         f"相隔 {gap} 個平日（容許 {limit}）")

    if not stale:
        print("\n✓ 全部資料都在容許範圍內")
        return 0

    body = ("以下資料檔太久沒更新，排程可能沒跑成功（自架 runner 沒醒著時，"
            "job 會直接 cancelled，`if: failure()` 抓不到）：\n\n"
            + "\n".join(stale)
            + "\n\n連假期間可能是正常現象，確認後請關閉這張 issue。")
    print("\n" + body)

    # 給 workflow 用：寫進 GITHUB_OUTPUT
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("stale=true\n")
            f.write("body<<EOF\n" + body + "\nEOF\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
