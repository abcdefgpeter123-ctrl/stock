#!/usr/bin/env node
/*
 * build_daily_summary.js — 產生 daily_summary.json
 *
 * 【為什麼需要這支】
 * 每天有排程（Cowork 雲端）要讀這個儀表板做分析報告，原本的做法是用無頭
 * 瀏覽器去抓 https://stock-pi-rose.vercel.app 的畫面。這個做法一直失敗：
 *   · 雲端出口代理擋掉該網域 → ERR_TUNNEL_CONNECTION_FAILED
 *   · 改用 WebFetch 又需要人工核准網域，無人值守排程沒人能按
 * 而且就算連得上也不理想——排程要的是數字，卻得先跑完整個前端把 JS 渲染出來。
 *
 * 所以改成：**資料在來源就先算好，排程直接讀 JSON**。
 *   https://raw.githubusercontent.com/<owner>/<repo>/main/daily_summary.json
 * raw.githubusercontent.com 是 GitHub 本身，雲端環境本來就要能連（不然 git
 * 都不能用），繞過整個 Vercel 與瀏覽器。
 *
 * 【為什麼是 Node 不是 Python】
 * 牛熊燈號的計分邏輯在 market_status.js，首頁／美股頁／每日健檢三頁共用。
 * 用 Python 重寫一份就會有第四份實作，這個專案已經被「同一份邏輯兩份實作
 * 走鐘」咬過一次（監控清單漏廣達）。用 Node 直接載入同一個檔案，
 * 排程看到的分數與網頁上看到的必然一致。
 *
 * 執行：node build_daily_summary.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const D = __dirname;

const readJson = (f, fallback) => {
  try { return JSON.parse(fs.readFileSync(path.join(D, f), 'utf8')); }
  catch (e) { console.warn(`   ⚠️ 讀不到 ${f}：${e.message}`); return fallback; }
};

// market_status.js 是 `const MarketStatus = (() => {...})()` 的全域寫法，
// 沒有 module.exports。包一層 Function 取回那個物件，不必改動原檔。
function loadMarketStatus() {
  const src = fs.readFileSync(path.join(D, 'market_status.js'), 'utf8');
  return new Function(`${src}; return MarketStatus;`)();
}

const ma = (closes, n) => {
  if (!closes || closes.length < n) return null;
  return closes.slice(-n).reduce((a, b) => a + b, 0) / n;
};
const r1 = v => (v == null ? null : Math.round(v * 10) / 10);

function buildTW(MarketStatus) {
  const data   = readJson('data.json', {});
  const stocks = readJson('stocks.json', {});
  const watch  = stocks.tw_watchlist || [];
  const codes  = watch.map(s => s.code);
  const WATCH  = new Set(codes);
  const nameOf = c => (data.prices?.[c]?.name) || watch.find(s => s.code === c)?.name || c;

  const prices    = data.prices || {};
  const histories = data.histories || {};
  const targets   = data.analyst_targets || {};
  const opps      = data.opportunities || [];

  // ── 每檔的均線與法人籌碼（排程原本要逐檔去別的網站查的東西）──
  const perStock = watch.map(s => {
    const p = prices[s.code] || {};
    const closes = histories[s.code]?.closes;
    const price = p.price ?? null;
    const m = { ma5: ma(closes, 5), ma20: ma(closes, 20), ma60: ma(closes, 60) };
    const above = k => (price != null && m[k] != null ? price > m[k] : null);
    const inst = data.inst_stocks?.[s.code];
    return {
      code: s.code, name: s.name, theme: s.theme,
      price, change_pct: p.changeP ?? null,
      ma5: r1(m.ma5), ma20: r1(m.ma20), ma60: r1(m.ma60),
      above_ma5: above('ma5'), above_ma20: above('ma20'), above_ma60: above('ma60'),
      // 法人買賣超（張），f=外資 t=投信 s=自營
      inst: inst ? { foreign: inst.f, trust: inst.t, dealer: inst.s } : null,
      foreign_holding_pct: data.foreign_holding?.[s.code] ?? null,
    };
  });

  /*
   * 機會懶人包 —— 與 index.html 的 renderCheatSheet() 同一組判準。
   * 門檻（分析師最低價、8–12pt）刻意寫在這裡與前端兩處，但兩邊都只是「篩選」，
   * 真正的計算（目標價、gap）都來自 data.json，所以不會算出不同的數字。
   */
  const belowLow = Object.entries(targets)
    .filter(([code, at]) => WATCH.has(code) && at.low && prices[code]?.price
                            && prices[code].price < at.low)
    .map(([code, at]) => ({
      code, name: nameOf(code), price: prices[code].price,
      low: at.low, analysts: at.count || 0,
      gap_pct: r1((at.low - prices[code].price) / prices[code].price * 100),
      only_one_analyst: (at.count || 0) <= 1,
    }))
    .sort((a, b) => b.analysts - a.analysts);

  const tsmcCloses = histories['2330']?.closes;
  const tsmcPrice  = prices['2330']?.price ?? null;
  const tsmcMa = [5, 10, 20].map(n => {
    const v = ma(tsmcCloses, n);
    return { period: n, ma: r1(v), broken: (v != null && tsmcPrice != null) ? tsmcPrice < v : null };
  });

  const GAP_LO = 8, GAP_HI = 12;
  const themeLag = opps
    .filter(o => o.gap != null && o.gap >= GAP_LO && o.gap <= GAP_HI)
    .map(o => ({ code: o.code, name: o.name, theme: o.theme, gap: o.gap,
                 leader: o.leader, reason: o.reason }));

  // ── 機會點交集圖（兩圈版：① 目標價上漲空間 ② 題材補漲）──
  const setB = new Map();
  Object.entries(targets).forEach(([code, at]) => {
    if (!WATCH.has(code) || !at.mean) return;
    const price = prices[code]?.price;
    if (!price) return;
    const upside = ((at.median || at.mean) - price) / price * 100;   // 中位數優先
    if (upside <= 0) return;
    setB.set(code, { code, name: nameOf(code), upside: r1(upside) });
  });
  const setC = new Map(themeLag.map(o => [o.code, o]));
  const both = [...setB.keys()].filter(c => setC.has(c));

  // ── 法人目標價追蹤 ──
  const analystTargets = Object.entries(targets)
    .filter(([code, at]) => WATCH.has(code) && at.mean)
    .map(([code, at]) => {
      const price = prices[code]?.price ?? null;
      const mid = at.median || at.mean;
      return {
        code, name: nameOf(code), price,
        target_median: at.median ?? null, target_mean: at.mean ?? null,
        high: at.high ?? null, low: at.low ?? null,
        analysts: at.count || 0, rating: at.rec || null,
        upside_pct: price ? r1((mid - price) / price * 100) : null,
        mean_change: at.mean_change ?? null,
        below_low: !!(at.low && price && price < at.low),
      };
    })
    .sort((a, b) => (b.upside_pct ?? -999) - (a.upside_pct ?? -999));

  // ── 大盤牛熊燈號（與網頁共用 market_status.js，不會有第二套標準）──
  let marketStatus = null;
  const idxCloses = data.twii_history?.closes;
  if (idxCloses && idxCloses.length >= 70) {
    const ev = MarketStatus.evaluate({
      market: 'TW',
      idxCloses,
      histories,
      prices,
      groupCodes: watch.filter(s => ['AI伺服器ODM', '伺服器兼營廠'].includes(s.theme)).map(s => s.code),
      watchCodes: codes,
    });
    marketStatus = {
      score: ev.score, raw_score: ev.rawScore, max: ev.max,
      label: ev.level.label, desc: ev.level.desc,
      chop_warning: ev.chop?.text || null,
      // results 是 {指標id: true/false}，配上 INDICATORS 的中文名與權重才看得懂
      indicators: MarketStatus.indicatorsFor('TW').map(ind => ({
        id: ind.id, name: ind.label, weight: ind.w,
        ok: ev.results?.[ind.id] ?? null, hint: ind.hint,
      })),
    };
  }

  return {
    updated_at: data.updated_at || null,
    prices_date: data.prices_date || null,
    market_summary: data.market_summary || null,
    weekly_summary: data.weekly_summary || null,
    index: data.twii || null,
    institutional: data.institutional || null,
    vix: data.vix || null, usdtwd: data.usdtwd || null, margin: data.margin || null,
    market_status: marketStatus,
    movers: data.movers || null,
    cheat_sheet: {
      expert_below_low: { count: belowLow.length, stocks: belowLow,
        note: '現價低於全體分析師最低目標；only_one_analyst 為真時「最低」其實是「唯一」，訊號份量較輕' },
      trust_tsmc: { price: tsmcPrice, ma: tsmcMa,
        broken_count: tsmcMa.filter(x => x.broken === true).length,
        note: '台積電跌破均線＝龍頭回檔，broken 為真代表符合這個進場理由' },
      theme_laggards: { count: themeLag.length, band: `${GAP_LO}-${GAP_HI}pt`, stocks: themeLag,
        note: '同題材龍頭已漲、該股尚未反應。8–12pt 是 5 年回測勝率最高的區間（61.9%），刻意不放寬' },
    },
    opportunity_venn: {
      only_target_upside: [...setB.values()].filter(x => !setC.has(x.code)),
      only_theme_lag: [...setC.values()].filter(x => !setB.has(x.code)),
      both: both.map(c => ({ ...setB.get(c), gap: setC.get(c).gap })),
      note: 'both＝兩項同時成立，訊號較強',
    },
    analyst_targets: analystTargets,
    stocks: perStock,
    all_opportunities: opps,
    etf_flows: data.etf_flows || null,
    warnings: data.warnings || [],
  };
}

function buildUS() {
  const d = readJson('us_data.json', {});
  const prices = d.prices || {};
  const targets = d.analyst_targets || {};
  const nameOf = c => prices[c]?.name || c;

  const belowLow = Object.entries(targets)
    .filter(([c, at]) => at.low && prices[c]?.price && prices[c].price < at.low)
    .map(([c, at]) => ({ code: c, name: nameOf(c), price: prices[c].price, low: at.low,
                         analysts: at.count || 0,
                         gap_pct: r1((at.low - prices[c].price) / prices[c].price * 100) }));

  const idx = d.market_history?.ndx || d.market_history?.nasdaq;
  const cur = idx?.closes?.length ? idx.closes[idx.closes.length - 1] : null;
  const ndxMa = [5, 10, 20].map(n => {
    const v = ma(idx?.closes, n);
    return { period: n, ma: r1(v), broken: (v != null && cur != null) ? cur < v : null };
  });

  return {
    updated_at: d.updated_at || null,
    market_summary: d.market_summary || null,
    indices: d.market || null,
    vix: d.vix || null,
    cheat_sheet: {
      expert_below_low: { count: belowLow.length, stocks: belowLow },
      trust_megacaps: {
        index: d.market_history?.ndx ? 'NASDAQ 100 (^NDX)' : 'NASDAQ 綜合 (^IXIC)',
        price: cur, ma: ndxMa, broken_count: ndxMa.filter(x => x.broken === true).length,
      },
    },
    analyst_targets: Object.entries(targets).map(([c, at]) => ({
      code: c, name: nameOf(c), price: prices[c]?.price ?? null,
      target_mean: at.mean ?? null, high: at.high ?? null, low: at.low ?? null,
      analysts: at.count || 0, rating: at.rec || null,
      upside_pct: prices[c]?.price ? r1((at.mean - prices[c].price) / prices[c].price * 100) : null,
    })).sort((a, b) => (b.upside_pct ?? -999) - (a.upside_pct ?? -999)),
  };
}

function main() {
  console.log('📦 產生 daily_summary.json...');
  const MarketStatus = loadMarketStatus();
  const out = {
    _readme: '給排程／自動化讀的精簡摘要。網頁上看得到的分析在這裡都已算好，' +
             '不需要跑瀏覽器。由 build_daily_summary.js 在 GitHub Actions 中產生。',
    generated_at: new Date().toISOString(),
    source: 'https://github.com/abcdefgpeter123-ctrl/stock',
    tw: buildTW(MarketStatus),
    us: buildUS(),
  };
  const f = path.join(D, 'daily_summary.json');
  fs.writeFileSync(f, JSON.stringify(out, null, 1), 'utf8');
  const kb = (fs.statSync(f).size / 1024).toFixed(0);
  console.log(`   ✅ daily_summary.json（${kb} KB）`);
  console.log(`      監控 ${out.tw.stocks.length} 檔／目標價 ${out.tw.analyst_targets.length} 檔` +
              `／跌破最低目標 ${out.tw.cheat_sheet.expert_below_low.count} 檔` +
              `／題材補漲 ${out.tw.cheat_sheet.theme_laggards.count} 檔`);
  if (out.tw.market_status) {
    console.log(`      大盤：${out.tw.market_status.label} ` +
                `${out.tw.market_status.score}/${out.tw.market_status.max}`);
  } else {
    console.log('      ⚠️ 大盤燈號未產生（twii_history 不足 70 筆）');
  }
}

main();
