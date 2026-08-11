/*
 * freshness.js — 資料過期警示橫幅
 *
 * 【為什麼需要】
 * 頁面原本只有「fetch 失敗」時才會顯示錯誤列。但真正危險的失效模式不是載入失敗，
 * 而是 GitHub Actions 掛了好幾天：data.json 還在、還讀得到，頁面照常渲染，
 * 只有角落一行小字寫著日期。對一個拿來做買賣判斷的工具來說，
 * 「安靜地給你過期的答案」比「明顯壞掉」糟糕得多。
 *
 * 【判斷方式】
 * 用「相隔幾個平日」而不是「相隔幾天」，否則週末一定誤報。
 * 連假仍可能誤判（無法從資料推出國定假日），所以文案寫成推測語氣、
 * 並明講遇連假可忽略——寧可偶爾多嘴，也不要該提醒時沉默。
 */
(function () {
  /** 'YYYY/MM/DD HH:MM'（時間可省略）→ Date；失敗回 null */
  function parseDate(s) {
    const m = String(s || "").match(/(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\D+(\d{1,2}):(\d{2}))?/);
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)) : null;
  }

  /** 兩個日期之間相隔幾個平日（不含起日、含迄日） */
  function weekdaysBetween(from, to) {
    let n = 0;
    const d = new Date(from.getTime());
    while (d < to) {
      d.setDate(d.getDate() + 1);
      const w = d.getDay();
      if (w !== 0 && w !== 6) n++;
    }
    return n;
  }

  /**
   * @param {string} dateStr   資料日期（data.json 的 prices_date 或 updated_at）
   * @param {string} marketName 顯示用名稱，例如「台股」
   * @param {string} hint      過期時給的下一步提示
   */
  window.checkFreshness = function (dateStr, marketName, hint) {
    const d = parseDate(dateStr);
    if (!d) return;

    // 先看實際經過幾小時。美股那支 workflow 跑在 ubuntu runner 上，
    // updated_at 是 UTC；瀏覽器用台灣本地日期去比會整整差一天而誤報。
    // 40 小時的緩衝足以吸收時區差與排程延遲，又遠小於「漏跑一天」的間隔。
    const hours = (Date.now() - d.getTime()) / 36e5;
    if (hours < 40) return;

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const day0 = new Date(d.getTime());
    day0.setHours(0, 0, 0, 0);
    const stale = weekdaysBetween(day0, today);

    // 1 個平日內屬正常：當天收盤資料要等盤後排程跑完才會有
    if (stale < 2) return;

    const strong = stale >= 4;
    const bar = document.createElement("div");
    bar.id = "stale-bar";
    bar.style.cssText = `
      background:${strong ? "rgba(248,113,113,.12)" : "rgba(251,146,60,.1)"};
      border:1px solid ${strong ? "rgba(248,113,113,.4)" : "rgba(251,146,60,.35)"};
      color:${strong ? "#f87171" : "#fb923c"};
      border-radius:8px;padding:10px 14px;margin-bottom:16px;
      font-size:13px;line-height:1.7`;
    bar.innerHTML =
      `${strong ? "🚨" : "⚠️"} <b>${marketName}資料停在 ${dateStr.split(" ")[0]}，`
      + `已相隔 ${stale} 個交易日</b> —— 排程可能沒跑成功，`
      + `畫面上的價格與訊號都是舊的。`
      + `<span style="opacity:.75">${hint ? "　" + hint : ""}　（若遇連假可忽略）</span>`;

    document.body.insertBefore(bar, document.body.firstChild);
  };
})();
