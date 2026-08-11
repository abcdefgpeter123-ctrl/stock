#!/usr/bin/env python3
"""機會點 + 均線突破訊號的歷史回測（只用價量，不用任何未來資訊）"""
import json, os, statistics as st
from collections import defaultdict

D = "/Users/peter/Desktop/Skills/股票/ＳＴＯＣＫ/stock"
data = json.load(open(f"{D}/data.json"))
h5   = json.load(open(f"{D}/history_5y.json"))
hd   = data["history_dates"]

import importlib.util
spec = importlib.util.spec_from_file_location("f", f"{D}/fetch_data_full.py")
# 直接讀 THEME_GROUPS 常數，避免 import 觸發網路
src = open(f"{D}/fetch_data_full.py").read()
ns = {}
start = src.index("THEME_GROUPS = {")
end   = src.index("\n}\n", start) + 3
exec(src[start:end], ns)
THEME_GROUPS = ns["THEME_GROUPS"]

# ── 組出每檔的完整（日期, 收盤, 量）序列 ────────────────────────
series = {}
for code, rec in data["histories"].items():
    dates  = hd[int(rec.get("l", 0))]
    closes = rec["closes"]
    vols   = rec.get("volumes") or [None] * len(closes)
    n = min(len(dates), len(closes))
    cur = list(zip(dates[-n:], closes[-n:], vols[-n:]))
    old = h5.get(code)
    if old:
        seen = {d for d, _, _ in cur}
        ov = old.get("volumes") or [None] * len(old["closes"])
        pre = [(d, c, v) for d, c, v in zip(old["labels"], old["closes"], ov) if d not in seen]
        cur = pre + cur
    cur.sort(key=lambda x: x[0])
    if len(cur) > 300:
        series[code] = cur

print(f"可用個股 {len(series)} 檔，最長 {max(len(v) for v in series.values())} 個交易日\n")

DATES = sorted({d for v in series.values() for d, _, _ in v})
idx = {c: {d: i for i, (d, _, _) in enumerate(v)} for c, v in series.items()}

def close(c, i): return series[c][i][1]
def ma(c, i, n):
    if i + 1 < n: return None
    w = [series[c][j][1] for j in range(i - n + 1, i + 1)]
    return sum(w) / n
def pct(c, i, n):
    if i - n < 0: return None
    b = close(c, i - n)
    return (close(c, i) - b) / b * 100 if b > 0 else None
def fwd(c, i, n):
    if i + n >= len(series[c]): return None
    b = close(c, i)
    return (close(c, i + n) - b) / b * 100 if b > 0 else None

# 大盤狀態代理：0050（5 年都有）
MKT = series.get("0050")
mkt_i = {d: i for i, (d, _, _) in enumerate(MKT)} if MKT else {}
def mkt_up(d):
    """大盤是否站上 60MA（多頭環境）"""
    i = mkt_i.get(d)
    if i is None or i < 60: return None
    m = sum(MKT[j][1] for j in range(i - 59, i + 1)) / 60
    return MKT[i][1] > m

LEADER_TH, GAP_TH, MAX_M, MIN_M = 12, 8, 7, -3

# ── 逐日掃訊號 ────────────────────────────────────────────────
rows = []   # {date, code, A, C, ...features, f5, f20, f60}
for d in DATES:
    # 每個題材的最強龍頭 30 日漲幅
    leader_p30 = {}
    for theme, g in THEME_GROUPS.items():
        best = None
        for lc in g["leaders"]:
            i = idx.get(lc, {}).get(d)
            if i is None: continue
            r = pct(lc, i, 30)
            if r is not None and (best is None or r > best): best = r
        leader_p30[theme] = best

    member_theme = {}
    for theme, g in THEME_GROUPS.items():
        for mc in g["members"]:
            member_theme[mc] = theme

    for code, ix in idx.items():
        i = ix.get(d)
        if i is None or i < 65: continue
        p = close(code, i)
        ma5, ma20, ma60 = ma(code, i, 5), ma(code, i, 20), ma(code, i, 60)
        if None in (ma5, ma20, ma60): continue
        A = p > ma5 and p > ma20 and p > ma60

        C = False; gap = None
        th = member_theme.get(code)
        if th and leader_p30.get(th) is not None and leader_p30[th] >= LEADER_TH:
            m30 = pct(code, i, 30)
            if m30 is not None:
                gap = leader_p30[th] - m30
                C = gap >= GAP_TH and MIN_M <= m30 <= MAX_M
        if not (A or C): continue

        ma60_prev = ma(code, i - 5, 60)
        vols = [series[code][j][2] for j in range(i - 19, i + 1) if series[code][j][2]]
        vr = (series[code][i][2] / (sum(vols) / len(vols))) if series[code][i][2] and len(vols) >= 10 else None
        hi60 = max(close(code, j) for j in range(i - 59, i + 1))

        rows.append(dict(
            date=d, code=code, A=A, C=C, gap=gap,
            ma60_up=(ma60_prev is not None and ma60 > ma60_prev),
            p5=pct(code, i, 5), p20=pct(code, i, 20),
            volr=vr, from_hi=(p - hi60) / hi60 * 100,
            mkt=mkt_up(d),
            f5=fwd(code, i, 5), f20=fwd(code, i, 20), f60=fwd(code, i, 60),
        ))

print(f"訊號樣本 {len(rows)} 筆（{DATES[0]} ~ {DATES[-1]}）\n")

# 基準：全體個股日
base = []
for code, v in series.items():
    for i in range(65, len(v)):
        r = fwd(code, i, 20)
        if r is not None: base.append(r)

def stat(vals, label, n5=None):
    vals = [v for v in vals if v is not None]
    if len(vals) < 20:
        print(f"{label:<34} 樣本不足 ({len(vals)})"); return
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    print(f"{label:<34} n={len(vals):>6}  平均{st.mean(vals):+6.2f}%  "
          f"中位{st.median(vals):+6.2f}%  勝率{win:5.1f}%")

print("═══ 20 日後報酬 ═══")
stat(base, "基準：全體個股日")
stat([r["f20"] for r in rows if r["A"] and not r["C"]], "① 只有突破均線")
stat([r["f20"] for r in rows if r["C"] and not r["A"]], "③ 只有題材補漲")
stat([r["f20"] for r in rows if r["A"] and r["C"]],     "①＋③ 兩項同時")

print("\n═══ 不同持有期（①＋③）═══")
both = [r for r in rows if r["A"] and r["C"]]
for k, lab in [("f5", "5 日"), ("f20", "20 日"), ("f60", "60 日")]:
    stat([r[k] for r in both], f"①＋③ 持有 {lab}")
print()
for k, lab in [("f5", "5 日"), ("f20", "20 日"), ("f60", "60 日")]:
    stat([r[k] for r in rows if r["C"]], f"③ 題材補漲 持有 {lab}")

print("\n═══ 候選加分條件（都在 ③ 題材補漲的樣本內，20日）═══")
c = [r for r in rows if r["C"]]
stat([r["f20"] for r in c], "③ 全部")
stat([r["f20"] for r in c if r["ma60_up"]],        "③ ＋ 60MA 上揚")
stat([r["f20"] for r in c if not r["ma60_up"]],    "③ ＋ 60MA 下彎")
stat([r["f20"] for r in c if r["mkt"]],            "③ ＋ 大盤站上60MA")
stat([r["f20"] for r in c if r["mkt"] is False],   "③ ＋ 大盤破60MA")
stat([r["f20"] for r in c if r["volr"] and r["volr"] >= 1.5], "③ ＋ 爆量(量能≥1.5倍)")
stat([r["f20"] for r in c if r["volr"] and r["volr"] < 0.8],  "③ ＋ 量縮(<0.8倍)")
stat([r["f20"] for r in c if r["from_hi"] > -5],   "③ ＋ 距60日高<5%")
stat([r["f20"] for r in c if r["from_hi"] < -15],  "③ ＋ 距60日高>15%")
stat([r["f20"] for r in c if r["gap"] and r["gap"] >= 20], "③ ＋ 落差≥20%")
stat([r["f20"] for r in c if r["gap"] and r["gap"] < 15],  "③ ＋ 落差<15%")
stat([r["f20"] for r in c if r["p5"] is not None and r["p5"] > 0], "③ ＋ 近5日已翻正")

print("\n═══ 同樣條件套在 ① 突破均線 ═══")
a = [r for r in rows if r["A"]]
stat([r["f20"] for r in a], "① 全部")
stat([r["f20"] for r in a if r["ma60_up"]],      "① ＋ 60MA 上揚")
stat([r["f20"] for r in a if r["mkt"]],          "① ＋ 大盤站上60MA")
stat([r["f20"] for r in a if r["mkt"] is False], "① ＋ 大盤破60MA")
stat([r["f20"] for r in a if r["ma60_up"] and r["mkt"]], "① ＋ 60MA上揚 ＋ 大盤多頭")

print("\n═══ 最佳組合驗證（20日）═══")
stat([r["f20"] for r in rows if r["A"] and r["C"] and r["mkt"] and r["ma60_up"]],
     "①＋③＋大盤多頭＋60MA上揚")
stat([r["f20"] for r in rows if r["C"] and r["mkt"] and r["ma60_up"]],
     "③＋大盤多頭＋60MA上揚")

print("\n\n═══ 穩健度檢查 ═══")
def yr(r): return r["date"][:4]

print("\n【落差大小 vs 20日報酬】(③ 樣本，分桶)")
for lo, hi in [(8,12),(12,16),(16,20),(20,30),(30,999)]:
    v=[r["f20"] for r in c if r["gap"] and lo<=r["gap"]<hi]
    stat(v, f"  落差 {lo}–{hi if hi<999 else '∞'}%")

print("\n【落差<15% 的優勢是否逐年成立】")
for y in sorted({yr(r) for r in c}):
    lo=[r["f20"] for r in c if yr(r)==y and r["gap"] and r["gap"]<15]
    hi=[r["f20"] for r in c if yr(r)==y and r["gap"] and r["gap"]>=20]
    lo=[x for x in lo if x is not None]; hi=[x for x in hi if x is not None]
    if len(lo)<20 or len(hi)<20: 
        print(f"  {y}  樣本不足 (小落差{len(lo)} / 大落差{len(hi)})"); continue
    print(f"  {y}  小落差(<15%) 中位{st.median(lo):+6.2f}% (n={len(lo):>4})   "
          f"大落差(≥20%) 中位{st.median(hi):+6.2f}% (n={len(hi):>4})   "
          f"{'✓ 小勝' if st.median(lo)>st.median(hi) else '✗ 反轉'}")

print("\n【① 突破均線：大盤濾網是否逐年成立】")
for y in sorted({yr(r) for r in a}):
    up=[r["f20"] for r in a if yr(r)==y and r["mkt"]]
    dn=[r["f20"] for r in a if yr(r)==y and r["mkt"] is False]
    up=[x for x in up if x is not None]; dn=[x for x in dn if x is not None]
    if len(up)<20 or len(dn)<20:
        print(f"  {y}  多頭 n={len(up)} / 空頭 n={len(dn)}  樣本不足"); continue
    print(f"  {y}  大盤多頭 中位{st.median(up):+6.2f}% (n={len(up):>5})   "
          f"大盤空頭 中位{st.median(dn):+6.2f}% (n={len(dn):>5})   "
          f"{'✓' if st.median(up)>st.median(dn) else '✗'}")

print("\n【③ 在大盤多頭內，落差效果是否還在】")
cm=[r for r in c if r["mkt"]]
stat([r["f20"] for r in cm if r["gap"] and r["gap"]<15], "  多頭＋落差<15%")
stat([r["f20"] for r in cm if r["gap"] and r["gap"]>=20],"  多頭＋落差≥20%")

print("\n【距60日高點：分桶】")
for lo,hi in [(-5,0),(-10,-5),(-15,-10),(-25,-15),(-999,-25)]:
    stat([r["f20"] for r in c if lo<=r["from_hi"]<hi], f"  距高點 {hi}~{lo}%")

print("\n【建議組合：③ ＋ 落差8–16% ＋ 60MA上揚】")
best=[r for r in c if r["gap"] and 8<=r["gap"]<16 and r["ma60_up"]]
for k,lab in [("f5","5日"),("f20","20日"),("f60","60日")]:
    stat([r[k] for r in best], f"  持有{lab}")
print("  逐年：")
for y in sorted({yr(r) for r in best}):
    v=[r["f20"] for r in best if yr(r)==y and r["f20"] is not None]
    if len(v)>=15: print(f"    {y} n={len(v):>4} 中位{st.median(v):+6.2f}% 勝率{sum(1 for x in v if x>0)/len(v)*100:5.1f}%")
    else: print(f"    {y} n={len(v)} 樣本不足")
