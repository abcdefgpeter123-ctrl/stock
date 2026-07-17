"""
週報產生器：讀取 data.json + us_data.json，輸出 weekly_report.html
每週五收盤後由 GitHub Actions 自動執行。
"""

import json
import datetime

# ── 台股監控代號 & 名稱對應 ────────────────────────────────────
TW_CODES = [
    "2330","6488","2492","2327","2472","3037","3481","2409","2454","3034",
    "2379","3374","2303","6223","3105","3711","2449","1301","6505","2382",
    "6669","2317","2308","2345","2603","2618","2002","1101","3008","2344",
    "2408","3661","2059","8996","3017","2356","2376","3231","2357","2324",
    "2301","2881","2882","2891","2885","6789","2383","2412",
]

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 無法載入 {path}: {e}")
        return {}

def pct(val):
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"

def cls_str(val):
    if val is None:
        return "neu"
    if val >= 0.5:
        return "up"
    if val <= -0.5:
        return "dn"
    return "neu"

def calc_tw(data):
    prices    = data.get("prices", {})
    histories = data.get("histories", {})
    results   = []
    for code in TW_CODES:
        p     = prices.get(code, {})
        h     = histories.get(code, {})
        price = p.get("price", 0)
        name  = p.get("name", code)
        closes = h.get("closes", [])
        labels = h.get("labels", [])
        if not price or not closes:
            continue
        prev   = closes[-1]
        chg_d  = (price - prev) / prev * 100
        chg_w  = ((price / closes[-6])  - 1) * 100 if len(closes) >= 6  else None
        chg_m  = ((price / closes[-22]) - 1) * 100 if len(closes) >= 22 else None
        ytd_p  = next(
            (cls for lbl, cls in zip(labels, closes)
             if _parse_date(lbl) and _parse_date(lbl) >= datetime.date(datetime.date.today().year, 1, 2)),
            None
        )
        chg_ytd = ((price / ytd_p) - 1) * 100 if ytd_p else None
        results.append({
            "code": code, "name": name, "price": price,
            "d": chg_d, "w": chg_w, "m": chg_m, "ytd": chg_ytd,
        })
    return results

def calc_us(data):
    prices    = data.get("prices", {})
    histories = data.get("histories", {})
    results   = []
    for code, h in histories.items():
        p      = prices.get(code, {})
        closes = h.get("closes", [])
        labels = h.get("labels", [])
        price  = closes[-1] if closes else p.get("price", 0)
        chg_d  = p.get("changeP", 0)
        name   = p.get("name", code)
        if not price:
            continue
        chg_w  = ((price / closes[-6])  - 1) * 100 if len(closes) >= 6  else None
        chg_m  = ((price / closes[-31]) - 1) * 100 if len(closes) >= 31 else None
        ytd_p  = next(
            (cls for lbl, cls in zip(labels, closes)
             if _parse_date_us(lbl) and _parse_date_us(lbl) >= datetime.date(datetime.date.today().year, 1, 2)),
            None
        )
        chg_ytd = ((price / ytd_p) - 1) * 100 if ytd_p else None
        results.append({
            "code": code, "name": name, "price": price,
            "d": chg_d, "w": chg_w, "m": chg_m, "ytd": chg_ytd,
        })
    return results

def _parse_date(s):
    try:
        return datetime.datetime.strptime(s, "%Y/%m/%d").date()
    except Exception:
        return None

def _parse_date_us(s):
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None

def top(rows, key, n=5, reverse=True):
    return sorted([r for r in rows if r.get(key) is not None],
                  key=lambda x: x[key], reverse=reverse)[:n]

def row_html(r, key):
    val  = r.get(key)
    cls  = cls_str(val)
    bar_w = min(int(abs(val) / 15 * 100), 100) if val is not None else 0
    bar_c = "var(--up)" if (val or 0) >= 0 else "var(--dn)"
    return f"""<tr>
      <td><span class="rcode">{r['code']}</span><span class="rname">{r['name']}</span></td>
      <td><div class="bw"><div class="bb"><div class="bf" style="width:{bar_w}%;background:{bar_c}"></div></div></div></td>
      <td class="rval {cls}">{pct(val)}</td>
    </tr>"""

def section(title, tw_rows, us_rows, key, tw_label="台股", us_label="美股", n=5):
    tw_top = top(tw_rows, key, n)
    tw_bot = top(tw_rows, key, n, reverse=False)
    us_top = top(us_rows, key, n)
    us_bot = top(us_rows, key, n, reverse=False)
    return f"""
  <div class="sec"><div class="sec-line"></div><span class="sec-label">{title}</span><div class="sec-line"></div></div>
  <div class="pg">
    <div class="pb">
      <div class="ph"><span class="pbadge">{tw_label}</span><span class="psub">漲幅前五</span></div>
      <table class="rt">
        <tr><th>個股</th><th></th><th style="text-align:right">漲跌</th></tr>
        {''.join(row_html(r, key) for r in tw_top)}
      </table>
      <div class="divlabel">跌幅前五</div>
      <table class="rt">
        <tr><th>個股</th><th style="text-align:right">漲跌</th></tr>
        {''.join(f'<tr><td><span class="rcode">{r["code"]}</span><span class="rname">{r["name"]}</span></td><td class="rval dn">{pct(r.get(key))}</td></tr>' for r in tw_bot)}
      </table>
    </div>
    <div class="pb">
      <div class="ph"><span class="pbadge">{us_label}</span><span class="psub">漲幅前五</span></div>
      <table class="rt">
        <tr><th>個股</th><th></th><th style="text-align:right">漲跌</th></tr>
        {''.join(row_html(r, key) for r in us_top)}
      </table>
      <div class="divlabel">跌幅前五</div>
      <table class="rt">
        <tr><th>個股</th><th style="text-align:right">漲跌</th></tr>
        {''.join(f'<tr><td><span class="rcode">{r["code"]}</span><span class="rname">{r["name"]}</span></td><td class="rval dn">{pct(r.get(key))}</td></tr>' for r in us_bot)}
      </table>
    </div>
  </div>"""

def generate():
    tw   = load_json("data.json")
    us   = load_json("us_data.json")
    now  = datetime.datetime.now()
    date_str = now.strftime("%Y/%m/%d")
    updated  = tw.get("updated_at", date_str)
    prices_date = tw.get("prices_date", tw.get("twii", {}).get("date", date_str))

    twii      = tw.get("twii", {})
    twii_p    = twii.get("price", 0)
    twii_chg  = twii.get("chgP", 0)
    twii_cls  = cls_str(twii_chg)
    twii_sign = "+" if twii_chg >= 0 else ""

    inst      = tw.get("institutional", {})
    foreign   = inst.get("foreign", 0) / 1e8
    trust     = inst.get("trust", 0) / 1e8
    dealer    = inst.get("dealer", 0) / 1e8
    total_inst = foreign + trust + dealer

    mkt       = us.get("market", {})
    sp500     = mkt.get("sp500", {})
    nasdaq    = mkt.get("nasdaq", {})
    dow       = mkt.get("dow", {})
    vix       = us.get("vix", {})

    tw_rows = calc_tw(tw)
    us_rows = calc_us(us)

    # 7/17 style: compute actual daily from history last vs current price
    for r in tw_rows:
        pass  # already computed in calc_tw

    html = f"""<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台美股市週報 — {prices_date}</title>
<style>
:root{{
  --bg:#0C1018;--bg2:#131820;--bg3:#1A2130;
  --border:#222B3A;--border2:#2D3A4E;
  --text:#D8E4F0;--text2:#8A9BB0;--text3:#4E5F72;
  --up:#2EC96E;--dn:#F05656;--accent:#4D9FEC;--gold:#C9993A;--warn:#E0933A;
  --mono:"SF Mono","Fira Code","Consolas",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --r:6px;
}}
@media(prefers-color-scheme:light){{:root{{
  --bg:#F4F6F9;--bg2:#FFF;--bg3:#EBF0F7;
  --border:#D4DCE8;--border2:#B8C4D4;
  --text:#1A2232;--text2:#4A5668;--text3:#8A9AB0;
  --accent:#2A7DD4;--gold:#A07520;--warn:#C07020;
}}}}
:root[data-theme="light"]{{--bg:#F4F6F9;--bg2:#FFF;--bg3:#EBF0F7;--border:#D4DCE8;--border2:#B8C4D4;--text:#1A2232;--text2:#4A5668;--text3:#8A9AB0;--accent:#2A7DD4;--gold:#A07520;--warn:#C07020;}}
:root[data-theme="dark"]{{--bg:#0C1018;--bg2:#131820;--bg3:#1A2130;--border:#222B3A;--border2:#2D3A4E;--text:#D8E4F0;--text2:#8A9BB0;--text3:#4E5F72;--accent:#4D9FEC;--gold:#C9993A;--warn:#E0933A;}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;line-height:1.5;padding:28px 16px 60px}}
.wrap{{max-width:980px;margin:0 auto}}
.masthead{{display:flex;align-items:baseline;justify-content:space-between;border-bottom:1px solid var(--border2);padding-bottom:12px;margin-bottom:24px;flex-wrap:wrap;gap:6px}}
.m-title{{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:600}}
.m-date{{font-size:11px;color:var(--text3);font-family:var(--mono)}}
.alert{{background:rgba(240,86,86,.1);border:1px solid rgba(240,86,86,.3);border-left:3px solid var(--dn);border-radius:var(--r);padding:10px 14px;margin-bottom:20px;display:flex;gap:10px;align-items:flex-start}}
.alert-icon{{font-size:14px;line-height:1.4;flex-shrink:0}}
.alert-body{{font-size:12px;color:var(--text2);line-height:1.6}}
.alert-body strong{{color:var(--dn)}}
.sec{{display:flex;align-items:center;gap:10px;margin:24px 0 14px}}
.sec-line{{flex:1;height:1px;background:var(--border)}}
.sec-label{{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--text3);font-weight:600;white-space:nowrap}}
.ov-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:580px){{.ov-grid,.pg{{grid-template-columns:1fr}}}}
.panel{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px 18px}}
.panel-flag{{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--text3);margin-bottom:10px}}
.big-val{{font-family:var(--mono);font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
.big-chg{{font-family:var(--mono);font-size:12px;font-variant-numeric:tabular-nums;margin-left:8px}}
.sub-rows{{display:flex;flex-direction:column;gap:5px;margin-top:10px}}
.sub-row{{display:flex;align-items:baseline;gap:8px}}
.s-name{{font-size:11px;color:var(--text2);min-width:72px}}
.s-val{{font-family:var(--mono);font-size:12px;font-variant-numeric:tabular-nums}}
.up{{color:var(--up)}}.dn{{color:var(--dn)}}.neu{{color:var(--text3)}}.warn{{color:var(--warn)}}
.inst{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:12px 18px;margin-top:10px;display:flex;gap:24px;flex-wrap:wrap;align-items:center}}
.inst-item{{display:flex;flex-direction:column;gap:2px}}
.inst-label{{font-size:10px;color:var(--text3);letter-spacing:.04em}}
.inst-val{{font-family:var(--mono);font-size:13px;font-weight:600;font-variant-numeric:tabular-nums}}
.inst-total{{margin-left:auto;padding-left:20px;border-left:1px solid var(--border)}}
.pg{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
.pb{{margin-bottom:4px}}
.ph{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
.pbadge{{font-size:10px;font-weight:700;letter-spacing:.1em;padding:2px 8px;border-radius:3px;background:var(--bg3);color:var(--accent);text-transform:uppercase}}
.psub{{font-size:11px;color:var(--text3)}}
.rt{{width:100%;border-collapse:collapse}}
.rt th{{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--text3);padding:4px 0;text-align:left;border-bottom:1px solid var(--border);font-weight:500}}
.rt th:last-child{{text-align:right}}
.rt td{{padding:5px 0;vertical-align:middle}}
.rt tr:not(:last-child) td{{border-bottom:1px solid var(--border)}}
.rname{{font-size:12px;color:var(--text)}}
.rcode{{font-size:10px;color:var(--text3);font-family:var(--mono);margin-right:4px}}
.rval{{font-family:var(--mono);font-size:12px;font-weight:600;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.bw{{width:56px;display:inline-block;vertical-align:middle}}
.bb{{height:3px;border-radius:2px;overflow:hidden;background:var(--bg3)}}
.bf{{height:100%;border-radius:2px}}
.divlabel{{font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--text3);margin:10px 0 6px;padding-top:12px;border-top:1px solid var(--border)}}
.obs-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}}
.obs{{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:var(--r);padding:12px 14px}}
.obs.dn{{border-left-color:var(--dn)}}.obs.up{{border-left-color:var(--up)}}.obs.gold{{border-left-color:var(--gold)}}.obs.warn{{border-left-color:var(--warn)}}
.obs-t{{font-size:11px;font-weight:600;color:var(--text);margin-bottom:4px}}
.obs-b{{font-size:11px;color:var(--text2);line-height:1.6}}
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead">
    <span class="m-title">台美股市週報</span>
    <span class="m-date">{prices_date}（週五收盤）｜ 更新 {updated}</span>
  </div>

  <div class="sec"><div class="sec-line"></div><span class="sec-label">大盤概況</span><div class="sec-line"></div></div>
  <div class="ov-grid">
    <div class="panel">
      <div class="panel-flag">🇹🇼 台灣加權指數</div>
      <div>
        <span class="big-val">{twii_p:,.0f}</span>
        <span class="big-chg {twii_cls}">{twii_sign}{twii_chg:.2f}%</span>
      </div>
      <div class="sub-rows" style="margin-top:10px">
        <div class="sub-row"><span class="s-name">USD/TWD</span>
          <span class="s-val">{tw.get("usdtwd", {}).get("price", "—")}</span>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-flag">🇺🇸 美股大盤</div>
      <div class="sub-rows">
        <div class="sub-row"><span class="s-name">S&amp;P 500</span>
          <span class="s-val">{sp500.get("price","—"):,}</span>
          <span class="s-val {cls_str(sp500.get('chgP',0))}" style="font-size:11px">&nbsp;{pct(sp500.get('chgP'))}</span>
        </div>
        <div class="sub-row"><span class="s-name">NASDAQ</span>
          <span class="s-val">{nasdaq.get("price","—"):,}</span>
          <span class="s-val {cls_str(nasdaq.get('chgP',0))}" style="font-size:11px">&nbsp;{pct(nasdaq.get('chgP'))}</span>
        </div>
        <div class="sub-row"><span class="s-name">Dow Jones</span>
          <span class="s-val">{dow.get("price","—"):,}</span>
          <span class="s-val {cls_str(dow.get('chgP',0))}" style="font-size:11px">&nbsp;{pct(dow.get('chgP'))}</span>
        </div>
        <div class="sub-row" style="margin-top:6px"><span class="s-name">VIX 恐慌</span>
          <span class="s-val warn">{vix.get("price","—")} <span style="font-size:10px">{pct(vix.get('chgP'))}</span></span>
        </div>
      </div>
    </div>
  </div>
  <div class="inst">
    <div class="inst-item"><span class="inst-label">外資</span>
      <span class="inst-val {cls_str(foreign)}">{foreign:+.0f}億</span>
    </div>
    <div class="inst-item"><span class="inst-label">投信</span>
      <span class="inst-val {cls_str(trust)}">{trust:+.0f}億</span>
    </div>
    <div class="inst-item"><span class="inst-label">自營商</span>
      <span class="inst-val {cls_str(dealer)}">{dealer:+.0f}億</span>
    </div>
    <div class="inst-item inst-total">
      <span class="inst-label">三大法人合計</span>
      <span class="inst-val {cls_str(total_inst)}" style="font-size:15px">{total_inst:+.0f}億</span>
    </div>
  </div>

  {section("今日（週五）", tw_rows, us_rows, "d", "台股 週五", "美股 週五")}
  {section("本週", tw_rows, us_rows, "w")}
  {section("本月", tw_rows, us_rows, "m")}
  {section("今年以來（YTD）", tw_rows, us_rows, "ytd")}

  <div class="sec"><div class="sec-line"></div><span class="sec-label">本週回顧</span><div class="sec-line"></div></div>
  <div class="obs-grid">
    {"".join(_obs_cards(tw_rows, us_rows, twii_chg, total_inst))}
  </div>
</div>
</body>
</html>"""
    with open("weekly_report.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ weekly_report.html 已產生（{prices_date}）")


def _obs_cards(tw_rows, us_rows, twii_chg, total_inst):
    cards = []

    # 大盤概況
    twii_cls = "dn" if twii_chg < -2 else ("up" if twii_chg > 1 else "")
    cards.append(f"""<div class="obs {twii_cls}">
      <div class="obs-t">台股大盤 {pct(twii_chg)}</div>
      <div class="obs-b">法人合計 {total_inst:+.0f}億。{"外資賣壓明顯，留意短線風險。" if total_inst < -100 else "法人偏買，市場信心尚可。"}</div>
    </div>""")

    # 本週最強台股
    best_tw = top(tw_rows, "w", 1)
    if best_tw:
        r = best_tw[0]
        cards.append(f"""<div class="obs up">
      <div class="obs-t">台股週冠 {r['name']} {pct(r['w'])}</div>
      <div class="obs-b">{r['code']} 收盤 {r['price']}，月漲 {pct(r['m'])}，YTD {pct(r['ytd'])}。</div>
    </div>""")

    # 本週最弱台股
    worst_tw = top(tw_rows, "w", 1, reverse=False)
    if worst_tw:
        r = worst_tw[0]
        cards.append(f"""<div class="obs dn">
      <div class="obs-t">台股週殺 {r['name']} {pct(r['w'])}</div>
      <div class="obs-b">{r['code']} 收盤 {r['price']}，月漲 {pct(r['m'])}，YTD {pct(r['ytd'])}。</div>
    </div>""")

    # 本週最強美股
    best_us = top(us_rows, "w", 1)
    if best_us:
        r = best_us[0]
        cards.append(f"""<div class="obs up">
      <div class="obs-t">美股週冠 {r['code']} {pct(r['w'])}</div>
      <div class="obs-b">{r['name']}，月漲 {pct(r['m'])}，YTD {pct(r['ytd'])}。</div>
    </div>""")

    # 本週最弱美股
    worst_us = top(us_rows, "w", 1, reverse=False)
    if worst_us:
        r = worst_us[0]
        cards.append(f"""<div class="obs dn">
      <div class="obs-t">美股週殺 {r['code']} {pct(r['w'])}</div>
      <div class="obs-b">{r['name']}，月漲 {pct(r['m'])}，YTD {pct(r['ytd'])}。</div>
    </div>""")

    # YTD 台股榜首
    best_ytd = top(tw_rows, "ytd", 1)
    if best_ytd:
        r = best_ytd[0]
        cards.append(f"""<div class="obs gold">
      <div class="obs-t">台股 YTD 冠軍 {r['name']} {pct(r['ytd'])}</div>
      <div class="obs-b">{r['code']} 今年漲幅驚人，週 {pct(r['w'])}，月 {pct(r['m'])}。</div>
    </div>""")

    return cards


if __name__ == "__main__":
    generate()
