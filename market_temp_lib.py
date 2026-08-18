#!/usr/bin/env python3
"""
市場溫度（大牛／小牛／橫盤／小熊／大熊）的歷史回測與工具。

指標定義與權重完全比照 market_status.js（含 3 日平滑），
差別只在這裡是用 5 年歷史逐日重算，用來回答「什麼溫度進場比較好」。

單獨執行會印出完整回測；也可以被匯入取得 market_levels()，
供 backtest_belowlow.py 把訊號依當天溫度分組。
"""

import json, statistics as st
D="/Users/peter/Desktop/Skills/股票/ＳＴＯＣＫ/stock"
d=json.load(open(f"{D}/data.json")); h5=json.load(open(f"{D}/history_5y.json")); hd=d["history_dates"]

# 個股序列（給龍頭/族群/漲跌家數用）
series={}
for code,rec in d["histories"].items():
    dates=hd[int(rec.get("l",0))]; cl=rec["closes"]
    n=min(len(dates),len(cl)); cur=list(zip(dates[-n:],cl[-n:]))
    old=h5.get(code)
    if old:
        seen={x[0] for x in cur}
        cur=[(a,b) for a,b in zip(old["labels"],old["closes"]) if a not in seen]+cur
    cur.sort(); series[code]=dict(cur)

TW=d["twii_history"]; tl,tc=TW["labels"],TW["closes"]
AI=["2382","6669","2356","2376","3231","2357","2324","2317"]
watch=[c for c in series if c.isdigit() and len(c)==4]

def ma(a,i,n): return sum(a[i-n+1:i+1])/n if i+1>=n else None
def up20(code,day,idx_of):
    s=series.get(code); 
    if not s: return None
    ks=idx_of.get(code)
    if not ks: return None
    keys,vals=ks
    try: j=keys.index(day)
    except ValueError: return None
    if j<19: return None
    return vals[j] > sum(vals[j-19:j+1])/20

idx_of={c:(list(s.keys()),list(s.values())) for c,s in series.items()}

rows=[]
for i in range(65,len(tc)):
    day=tl[i]
    r={}
    r['idx_20ma']=tc[i]>ma(tc,i,20); r['idx_60ma']=tc[i]>ma(tc,i,60)
    r['idx_5ma'] =tc[i]>ma(tc,i,5)
    m20p=ma(tc,i-5,20); m60p=ma(tc,i-10,60)
    r['ma20_up']=ma(tc,i,20)>m20p if m20p else None
    r['ma60_up']=ma(tc,i,60)>m60p if m60p else None
    r['lead_20ma']=up20("2330",day,idx_of)
    g=[up20(c,day,idx_of) for c in AI]; g=[x for x in g if x is not None]
    r['grp_20ma']=(sum(g)/len(g)>0.5) if g else None
    adv=dn=0
    for c in watch:
        ks=idx_of[c][0]; vs=idx_of[c][1]
        try: j=ks.index(day)
        except ValueError: continue
        if j<1: continue
        if vs[j]>vs[j-1]: adv+=1
        elif vs[j]<vs[j-1]: dn+=1
    r['advance']= adv>dn if (adv+dn) else None
    W={'idx_20ma':2,'idx_60ma':2,'ma20_up':1.5,'ma60_up':1.5,'lead_20ma':1,'grp_20ma':1,'idx_5ma':.5,'advance':.5}
    raw=sum(W[k] for k,v in r.items() if v)
    rows.append((day,i,raw))

# 3 日平滑（與線上一致）
sm=[]
for k,(day,i,raw) in enumerate(rows):
    w=[rows[j][2] for j in range(max(0,k-2),k+1)]
    sm.append((day,i,sum(w)/len(w)))

def level(s):
    return '大牛' if s>=8 else '小牛' if s>=6 else '橫盤' if s>=3.5 else '小熊' if s>=1.5 else '大熊'

def fwd(i,n):
    if i+n>=len(tc): return None
    return (tc[i+n]-tc[i])/tc[i]*100

def market_levels():
    """{日期: 溫度} —— 給其他回測腳本依當天市場狀態分組用"""
    return {day: level(sc) for day, _, sc in sm}


def report():
    print(f"樣本 {len(sm)} 個交易日（{sm[0][0]} ~ {sm[-1][0]}）\n")
    for horizon in (20,60,120):
        print(f"═══ 買進加權指數，持有 {horizon} 個交易日 ═══")
        buckets={}
        for day,i,s in sm:
            buckets.setdefault(level(s),[]).append(fwd(i,horizon))
        for lv in ['大牛','小牛','橫盤','小熊','大熊']:
            v=[x for x in buckets.get(lv,[]) if x is not None]
            if len(v)<30: print(f"  {lv}  樣本不足 ({len(v)})"); continue
            w=sum(1 for x in v if x>0)/len(v)*100
            print(f"  {lv}  n={len(v):>4}  平均{st.mean(v):+6.2f}%  中位{st.median(v):+6.2f}%  勝率{w:5.1f}%")
        print()

    print("═══ 對照：不看溫度，任何一天買進 ═══")
    for h in (20,60,120):
        v=[fwd(i,h) for _,i,_ in sm]; v=[x for x in v if x is not None]
        w=sum(1 for x in v if x>0)/len(v)*100
        print(f"  持有{h:>3}日  n={len(v):>4}  平均{st.mean(v):+6.2f}%  中位{st.median(v):+6.2f}%  勝率{w:5.1f}%")

    print("\n═══ 穩健度：大牛 vs 大熊，逐年（60 日中位）═══")
    yrs={}
    for day,i,s in sm:
        yrs.setdefault(day[:4],[]).append((level(s),fwd(i,60)))
    for y in sorted(yrs):
        a=[r for l,r in yrs[y] if l=='大牛' and r is not None]
        b=[r for l,r in yrs[y] if l in ('小熊','大熊') and r is not None]
        if len(a)<20 or len(b)<20:
            print(f"  {y}  大牛 n={len(a):>3} / 空頭 n={len(b):>3}  樣本不足"); continue
        ok='✓' if st.median(a)>st.median(b) else '✗'
        print(f"  {y}  大牛 中位{st.median(a):+7.2f}% (n={len(a):>3})   小熊+大熊 中位{st.median(b):+7.2f}% (n={len(b):>3})  {ok}")

    print("\n═══ 各溫度佔比 ═══")
    from collections import Counter
    c=Counter(level(s) for _,_,s in sm); tot=sum(c.values())
    for lv in ['大牛','小牛','橫盤','小熊','大熊']:
        print(f"  {lv}  {c[lv]:>4} 天  {c[lv]/tot*100:>5.1f}%")


if __name__ == "__main__":
    report()
