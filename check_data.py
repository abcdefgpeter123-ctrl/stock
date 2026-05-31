#!/usr/bin/env python3
"""快速檢查 data.json / company_info.json / stock_info.json 的小工具。

用法：
  python3 check_data.py              # 顯示 data.json 概況
  python3 check_data.py 2454         # 查某支股票的所有資料
  python3 check_data.py opps         # 列出機會點
  python3 check_data.py etf          # 顯示 ETF 持股筆數
"""

import json, sys
from pathlib import Path

BASE = Path(__file__).parent

def load(name):
    p = BASE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

data    = load("data.json")
ci      = load("company_info.json")
si      = load("stock_info.json")

arg = sys.argv[1] if len(sys.argv) > 1 else ""

if not arg:
    prices = data.get("prices", {})
    twii   = data.get("twii", {})
    print(f"updated_at : {data.get('updated_at', '—')}")
    print(f"TWII       : {twii.get('price')}  {twii.get('chg'):+}  ({twii.get('chgP'):+.2f}%)")
    print(f"prices     : {len(prices)} 支")
    missing_p5 = [c for c, v in prices.items() if v.get("p5") is None]
    print(f"缺 p5/p30  : {len(missing_p5)} 支  {missing_p5[:8]}")
    print(f"company_info non-empty : {sum(1 for v in ci.values() if v)} / {len(ci)}")
    opps = data.get("opportunities", [])
    print(f"opportunities : {len(opps)} 筆")

elif arg == "opps":
    for o in data.get("opportunities", []):
        print(f"  {o['code']} {o['name']:8s}  p30={o.get('p30'):+.1f}%  gap={o.get('gap'):.1f}  leader={o.get('leader')}")

elif arg == "etf":
    for code, holdings in data.get("etf_holdings", {}).items():
        print(f"  {code}: {len(holdings)} 支持股")

else:
    code = arg
    print(f"=== {code} ===")
    print("prices      :", json.dumps(data.get("prices", {}).get(code), ensure_ascii=False))
    print("inst_stocks :", json.dumps(data.get("inst_stocks", {}).get(code), ensure_ascii=False))
    h = data.get("histories", {}).get(code)
    print("history     :", f"{len(h['closes'])} 筆" if h else "無")
    print("company_info:", json.dumps(ci.get(code), ensure_ascii=False))
    print("stock_info  :", json.dumps(si.get(code), ensure_ascii=False))
