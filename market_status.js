/*
 * market_status.js — 大盤狀態判斷（首頁與每日健檢共用）
 *
 * 【為什麼要抽成共用模組】
 * 這段邏輯原本在 index.html 與 health_check.html 各寫一份，結果兩邊走鐘：
 * 健檢已經改成加權 8 項（總分 10），首頁還停在等權 6 項，同一天會顯示
 * 不同的市場狀態。判斷標準只能有一份，兩頁都從這裡取。
 *
 * 【計分方式】
 * 早期版本 8 項各 1 分，有三個問題（一年回測 1084 個交易日驗證）：
 *   ① 站上5MA／台積電20MA／AI族群20MA／漲跌家數 這 4 項高度相關，
 *      大盤單日大漲時同時翻正，等於一天的漲跌被重複計分 4 次
 *      （2026/07/30 是 2/8「小熊」，07/31 單日 +7.98% 後直接變 6/8「小牛」）
 *   ② 20MA>60MA 與 60MA>120MA 反應太慢，-15% 崩跌中連續 22 個交易日
 *      維持成立，等於白送 2 分底分，導致「大熊」永遠不可能出現
 *   ③ 真正衡量中期趨勢的「站上20MA／站上60MA」只佔 8 分之 2，被稀釋
 * 改法：均線「排列」換成均線「方向」（斜率會隨行情變動，排列不會），
 * 並依重要性配權重。回測結果：
 *   矛盾天數（跌破雙均線卻判為牛）25 → 0
 *   判定跳動次數 477 → 277（少 42%）
 *   明確多頭仍抓到 98% → 100%（不是變得永遠偏空）
 */
const MarketStatus = (() => {

  // 台股與美股用同一套指標與權重，只有名稱與對應標的不同。
  // 兩邊各寫一份的話一定會走鐘——首頁與健檢就發生過，不再重蹈。
  const MARKETS = {
    TW: { index:'加權指數', bellwether:'台積電（2330）', bellwetherCode:'2330',
          group:'AI 族群',
          groupHint:'AI伺服器代工成分股平均站上20MA（廣達、緯創、緯穎等 8 檔）' },
    US: { index:'S&P 500', bellwether:'輝達（NVDA）', bellwetherCode:'NVDA',
          group:'AI 半導體族群',
          groupHint:'AI半導體成分股平均站上20MA（輝達、博通、超微、美光、Arista）' },
  };

  /** 產生某個市場的指標定義（id 與權重共用，只換文字） */
  function indicatorsFor(market = 'TW') {
    const m = MARKETS[market] || MARKETS.TW;
    return [
      { id:'idx_20ma',  label:`${m.index}站上 20MA`, w:2,   pts:'+2',   hint:'中期趨勢核心' },
      { id:'idx_60ma',  label:`${m.index}站上 60MA`, w:2,   pts:'+2',   hint:'中期趨勢核心' },
      { id:'ma20_up',   label:'20MA 方向向上',        w:1.5, pts:'+1.5', hint:'20MA 高於 5 個交易日前（取代原本的多頭排列，排列反應太慢）' },
      { id:'ma60_up',   label:'60MA 方向向上',        w:1.5, pts:'+1.5', hint:'60MA 高於 10 個交易日前' },
      { id:'lead_20ma', label:`${m.bellwether}站上 20MA`, w:1, pts:'+1' },
      { id:'grp_20ma',  label:`${m.group}站上 20MA`,  w:1,   pts:'+1',   hint:m.groupHint },
      { id:'idx_5ma',   label:`${m.index}站上 5MA`,   w:0.5, pts:'+0.5', hint:'短期動能，權重低（單日大漲就會翻正）' },
      { id:'advance',   label:'觀察名單上漲家數 > 下跌家數', w:0.5, pts:'+0.5', hint:'短期動能，權重低（只反映當天）' },
    ];
  }

  const INDICATORS = indicatorsFor('TW');   // 預設台股，向後相容
  const MAX_SCORE = INDICATORS.reduce((a, i) => a + i.w, 0);   // = 10

  // 配色依台股慣例：暖色＝多方、冷色＝空方，中間用琥珀當中性。
  // 由多到空是一條連續的色溫漸層（深紅→淺紅→琥珀→淺綠→深綠），
  // 光看顏色就能判斷方向，不必先讀懂「小牛／小熊」哪個是多。
  const LEVELS = [
    { min:8,   label:'大牛', emoji:'🐂', desc:'市場資金充沛，聚焦主流族群',
      color:'#ef4444', strat:'bull2' },   // 深紅
    { min:6,   label:'小牛', emoji:'🙂', desc:'開始輪動，找補漲股',
      color:'#f87171', strat:'bull1' },   // 淺紅（原為青色，與多方語意相反）
    { min:3.5, label:'橫盤', emoji:'😐', desc:'縮小範圍，保持耐心觀望',
      color:'#fbbf24', strat:'side'  },   // 琥珀（中性）
    { min:1.5, label:'小熊', emoji:'🙁', desc:'建立觀察名單，準備下一波',
      color:'#4ade80', strat:'bear1' },   // 淺綠（原為橘色，比橫盤還暖，方向會看反）
    { min:0,   label:'大熊', emoji:'🐻', desc:'多項指標翻空，幾乎不買，保留現金',
      color:'#22c55e', strat:'bear2' },   // 深綠
  ];

  const ma = (arr, n) => {
    if (!arr || arr.length < n) return null;
    return arr.slice(-n).reduce((a, b) => a + b, 0) / n;
  };

  /**
   * 計算各項指標。回傳 { id: true/false }，資料不足的項目不會出現在物件裡
   * （而不是給 false，避免把「不知道」當成「翻空」）。
   *
   * @param {number[]} idxCloses   大盤指數收盤序列（台股加權／美股 S&P500）
   * @param {object}   histories   code → { closes }
   * @param {object}   prices      code → { changeP }
   * @param {string}   market      'TW' | 'US'，決定龍頭股是誰
   * @param {string[]} groupCodes  代表性族群的股票代號
   * @param {string[]} watchCodes  觀察清單全部代號（算漲跌家數用）
   */
  function computeIndicators({ idxCloses, twiiCloses, histories = {}, prices = {},
                               market = 'TW', groupCodes, aiCodes,
                               watchCodes = [] }) {
    const auto = {};
    const closes = idxCloses || twiiCloses;          // twiiCloses 為舊名，保留相容
    const group  = groupCodes || aiCodes || [];
    const leadCode = (MARKETS[market] || MARKETS.TW).bellwetherCode;

    if (closes && closes.length >= 70) {
      const c = closes;
      const last = c[c.length - 1];
      auto.idx_5ma  = last > ma(c, 5);
      auto.idx_20ma = last > ma(c, 20);
      auto.idx_60ma = last > ma(c, 60);
      // 均線「方向」而非「排列」：排列在下跌段可以連續數十天維持成立，
      // 完全不反映當下轉弱；斜率則會在趨勢反轉後數日內翻負。
      auto.ma20_up = ma(c, 20) > ma(c.slice(0, -5),  20);
      auto.ma60_up = ma(c, 60) > ma(c.slice(0, -10), 60);
    }

    const lead = histories[leadCode]?.closes;
    if (lead && lead.length >= 20) {
      auto.lead_20ma = lead[lead.length - 1] > ma(lead, 20);
    }

    const ratios = group.map(code => {
      const c = histories[code]?.closes;
      if (!c || c.length < 20) return null;
      const m = ma(c, 20);
      return m ? c[c.length - 1] / m : null;
    }).filter(v => v != null);
    if (ratios.length) {
      auto.grp_20ma = ratios.reduce((a, b) => a + b, 0) / ratios.length > 1;
    }

    let up = 0, dn = 0;
    watchCodes.forEach(code => {
      let chg = prices[code]?.changeP;
      if (chg == null) {
        // 回推前幾天的分數時沒有當日報價，改用收盤序列的前後差。
        // 少了這段的話，往前推算的日子會固定缺這一項（0.5 分），
        // 3 日平均就被系統性低估。
        const c = histories[code]?.closes;
        if (c && c.length >= 2) chg = c[c.length - 1] - c[c.length - 2];
      }
      if (chg == null) return;
      if (chg > 0) up++; else if (chg < 0) dn++;
    });
    if (up + dn > 0) auto.advance = up > dn;

    return auto;
  }

  /** 依權重加總。results 可混入使用者手動勾選的結果。（權重與市場無關） */
  function score(results) {
    let s = 0;
    INDICATORS.forEach(ind => { if (results[ind.id]) s += ind.w; });
    return Math.round(s * 10) / 10;    // 權重含 .5，避免浮點誤差
  }

  const levelOf = (s) => LEVELS.find(l => s >= l.min) || LEVELS[LEVELS.length - 1];

  /**
   * 把輸入資料往前推 k 個交易日，用來重算前幾天的分數。
   * 只是把每個序列的尾端切掉 k 筆，指標算法完全共用。
   */
  function shift(input, k) {
    if (!k) return input;
    const cut = arr => (arr && arr.length > k) ? arr.slice(0, arr.length - k) : arr;
    const hist = {};
    Object.entries(input.histories || {}).forEach(([code, h]) => {
      hist[code] = { ...h, closes: cut(h.closes) };
    });
    return { ...input, idxCloses: cut(input.idxCloses || input.twiiCloses),
             twiiCloses: undefined, histories: hist,
             prices: {} };   // 漲跌家數只有當天的資料，往前推時改由收盤序列推算
  }

  const SMOOTH_DAYS = 3;

  /**
   * 一次算完：回傳 { results, score, rawScore, max, level }
   *
   * score 是最近 SMOOTH_DAYS 天分數的平均，results 仍是「今天」的狀態。
   * 為什麼要平滑：站上/跌破均線是二元的，指數貼著均線時一根大陽線會同時
   * 翻正「站上20MA(+2)」「站上60MA(+2)」「台積電20MA(+1)」——
   * 2026/08/05 就是這樣一天之內從 3.5「橫盤」跳到 8.5「大牛」，跳過小牛。
   * 一年回測：判定跳動 280 → 195（少 30%），跨兩級的跳躍 40 次 → 0 次。
   * （也試過改用「連續 3 日站上才算」，以及加入融資籌碼指標；
   *   前者改善有限，後者反而讓跳動增加到 343，都不採用。）
   */
  function evaluate(input) {
    const results = computeIndicators(input);
    const raw = score(results);

    const scores = [raw];
    for (let k = 1; k < SMOOTH_DAYS; k++) {
      const past = computeIndicators(shift(input, k));
      // 資料不足以回推時就不列入，避免把缺漏當成 0 分拉低平均
      if (Object.keys(past).length >= 5) scores.push(score(past));
    }
    const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 10) / 10;

    return { results, score: avg, rawScore: raw, days: scores.length,
             max: MAX_SCORE, level: levelOf(avg) };
  }

  /**
   * 回推最近 days 個交易日的指標與分數，用來看「轉變有沒有持續」——
   * 單日成立和連續多日成立的意義完全不同，只看今天的勾勾判斷不出來。
   *
   * 回傳由舊到新的陣列：[{ date, chgP, results, score, level }]
   * input 需額外帶 twiiLabels（與 twiiCloses 等長的日期字串）才有日期可標。
   */
  function evaluateSeries(input, days = 5) {
    const labels = input.idxLabels || input.twiiLabels || [];
    const closes = input.idxCloses  || input.twiiCloses || [];
    const out = [];

    for (let k = days - 1; k >= 0; k--) {
      const shifted = k === 0 ? input : shift(input, k);
      const results = computeIndicators(shifted);
      if (Object.keys(results).length < 5) continue;   // 資料不足就不列

      const i = closes.length - 1 - k;
      const s = score(results);
      out.push({
        date:  labels[i] || '',
        chgP:  (i > 0 && closes[i - 1]) ? (closes[i] / closes[i - 1] - 1) * 100 : null,
        results, score: s, level: levelOf(s),
      });
    }
    return out;
  }

  return { INDICATORS, MAX_SCORE, LEVELS, SMOOTH_DAYS, MARKETS, ma, indicatorsFor,
           computeIndicators, score, levelOf, evaluate, evaluateSeries };
})();
