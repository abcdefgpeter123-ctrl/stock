/*
 * auth.js — 管理員登入 / 資料加密（純前端，無後端）
 *
 * 【為什麼是加密而不是比對密碼】
 * 這個站是 GitHub Pages 靜態站，而且 repo 是公開的。任何寫進原始碼的密碼
 * （即使是雜湊值）都會被所有人看到，也能直接開 DevTools 繞過檢查。
 * 所以這裡不做「比對密碼」，而是用密碼推導金鑰把資料本身加密：
 *   - 密碼不存在原始碼裡，也不存在 localStorage 裡，任何地方都沒有
 *   - 沒有正確密碼 → localStorage 裡只有一團亂碼，DevTools 也看不出東西
 *   - 原始碼公開不影響安全性（安全性在金鑰，不在程式碼）
 * 代價：忘記密碼 = 資料永久救不回來，所以設定時強制先下載備份。
 *
 * 【加密規格】
 *   PBKDF2-SHA256 250,000 次 → AES-GCM 256bit
 *   每個保險庫有自己的 salt，每次寫入換新的 iv
 *
 * 【Session】
 * 解開後把金鑰放 sessionStorage，同分頁換頁不用重打；關掉分頁就失效。
 */
const Auth = (() => {
  const VAULT_KEY   = 'pj_vault_v1';       // localStorage：加密後的保險庫
  const SESSION_KEY = 'pj_session_key_v1'; // sessionStorage：解開後的金鑰
  const ADMIN_NAME  = 'PJ';
  const ITERATIONS  = 250000;

  let cachedKey = null;   // CryptoKey，解鎖後才有

  // ── 編碼工具 ──
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const b64  = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const unb64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

  async function deriveKey(password, salt) {
    const base = await crypto.subtle.importKey(
      'raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt, iterations: ITERATIONS, hash: 'SHA-256' },
      base, { name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
  }

  function readVault() {
    try { return JSON.parse(localStorage.getItem(VAULT_KEY) || 'null'); }
    catch { return null; }
  }

  async function writeVault(key, salt, payload, guest) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv }, key, enc.encode(JSON.stringify(payload)));
    localStorage.setItem(VAULT_KEY, JSON.stringify({
      v: 1, salt: b64(salt), iv: b64(iv), ct: b64(ct),
      guest: guest || {},                 // 訪客模式看得到的部分，不含金額
      updated_at: new Date().toISOString(),
    }));
  }

  // ── 對外 API ──
  const api = {
    ADMIN_NAME,

    /** 是否已經設定過密碼 */
    isSetup: () => !!readVault(),

    /** 目前這個分頁是否已解鎖 */
    isAdmin: () => !!cachedKey,

    /** 訪客模式可見的摘要（無金額） */
    guestData: () => readVault()?.guest || {},

    /** 首次設定密碼，data 為要保護的初始內容 */
    async setup(password, data) {
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const key  = await deriveKey(password, salt);
      await writeVault(key, salt, data, api.buildGuest(data));
      cachedKey = key;
      await cacheSession(key);
      return true;
    },

    /** 用密碼解鎖；密碼錯誤時 AES-GCM 驗證會失敗，回傳 false */
    async unlock(password) {
      const v = readVault();
      if (!v) return false;
      try {
        const key = await deriveKey(password, unb64(v.salt));
        await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(v.iv) }, key, unb64(v.ct));
        cachedKey = key;
        await cacheSession(key);
        return true;
      } catch { return false; }
    },

    /** 讀出解密後的內容；未解鎖回 null */
    async load() {
      const v = readVault();
      if (!v || !cachedKey) return null;
      try {
        const pt = await crypto.subtle.decrypt(
          { name: 'AES-GCM', iv: unb64(v.iv) }, cachedKey, unb64(v.ct));
        return JSON.parse(dec.decode(pt));
      } catch { return null; }
    },

    /** 寫回並重新加密 */
    async save(data) {
      const v = readVault();
      if (!v || !cachedKey) throw new Error('尚未解鎖');
      await writeVault(cachedKey, unb64(v.salt), data, api.buildGuest(data));
    },

    /** 鎖定（清掉 session 金鑰，保留資料） */
    lock() {
      cachedKey = null;
      sessionStorage.removeItem(SESSION_KEY);
    },

    /** 換密碼：用新密碼重新加密同一份資料 */
    async changePassword(oldPw, newPw) {
      if (!await api.unlock(oldPw)) return false;
      const data = await api.load();
      if (data == null) return false;
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const key  = await deriveKey(newPw, salt);
      await writeVault(key, salt, data, api.buildGuest(data));
      cachedKey = key;
      await cacheSession(key);
      return true;
    },

    /**
     * 產生訪客模式可見的摘要。
     * 刻意只放「代號／名稱／報酬率」——沒有股數、價格、成本、金額、理由，
     * 所以就算不解鎖也看不出部位大小。
     */
    buildGuest(data) {
      const trades = data?.trades || [];
      if (!trades.length) return { positions: [], totalPct: null, txCount: 0 };

      const pos = {};
      [...trades].sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id).forEach(t => {
        const p = pos[t.code] || (pos[t.code] =
          { code: t.code, name: t.name, market: t.market, qty: 0, cost: 0, realized: 0 });
        p.name = t.name || p.name;
        if (t.side === 'buy') { p.qty += t.qty; p.cost += t.qty * t.price + t.fee; }
        else {
          const avg = p.qty > 0 ? p.cost / p.qty : 0;
          const sq  = Math.min(t.qty, p.qty);
          p.realized += (t.price * t.qty - t.fee) - avg * sq;
          p.qty -= t.qty; p.cost -= avg * sq;
          if (p.qty <= 0) { p.qty = 0; p.cost = 0; }
        }
      });

      let cost = 0, mv = 0;
      const positions = [];
      Object.values(pos).forEach(p => {
        if (p.qty <= 0) return;
        const pr = (typeof window.__priceLookup === 'function') ? window.__priceLookup(p.code) : null;
        const avg = p.cost / p.qty;
        const pct = (pr != null && avg > 0) ? (pr - avg) / avg * 100 : null;
        positions.push({ code: p.code, name: p.name, market: p.market,
                         pct: pct == null ? null : Math.round(pct * 100) / 100 });
        if (pr != null) { cost += p.cost; mv += pr * p.qty; }
      });

      return {
        positions,
        totalPct: cost > 0 ? Math.round((mv - cost) / cost * 10000) / 100 : null,
        txCount: trades.length,
      };
    },

    /** 匯出（未加密的明碼備份，僅在已解鎖時可用） */
    async exportPlain() {
      const data = await api.load();
      if (!data) return null;
      return data;
    },
  };

  // ── session 金鑰快取 ──
  async function cacheSession(key) {
    try {
      const raw = await crypto.subtle.exportKey('raw', key);
      sessionStorage.setItem(SESSION_KEY, b64(raw));
    } catch { /* 不支援就算了，換頁重打密碼 */ }
  }

  api.restoreSession = async function () {
    const s = sessionStorage.getItem(SESSION_KEY);
    if (!s || !readVault()) return false;
    try {
      cachedKey = await crypto.subtle.importKey(
        'raw', unb64(s), { name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
      return (await api.load()) != null;
    } catch { cachedKey = null; return false; }
  };

  return api;
})();

/* ══════════════════════════════════════════════════════
 *  登入列 UI（各頁共用）
 * ══════════════════════════════════════════════════════ */
const AuthUI = (() => {
  const CSS = `
.auth-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:var(--bg2,#111827);
  border:1px solid var(--border,rgba(255,255,255,.07));border-radius:8px;padding:10px 14px;margin-bottom:16px}
.auth-state{font-size:12.5px;display:flex;align-items:center;gap:7px}
.auth-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.auth-dot.on{background:#4ade80;box-shadow:0 0 6px rgba(74,222,128,.6)}
.auth-dot.off{background:#a8bacf}
.auth-who{font-weight:600;color:#a78bfa}
.auth-sub{font-size:11.5px;color:var(--text3,#a8bacf)}
.auth-bar input{background:var(--bg3,#1a2235);border:1px solid var(--border2,rgba(255,255,255,.13));
  border-radius:6px;color:var(--text,#fff);font-family:inherit;font-size:13px;padding:7px 10px;outline:none;width:170px}
.auth-bar input:focus{border-color:#a78bfa}
.auth-btn{padding:7px 15px;border-radius:6px;font-size:12.5px;font-weight:600;cursor:pointer;
  font-family:inherit;background:rgba(167,139,250,.16);border:1px solid rgba(167,139,250,.45);color:#a78bfa}
.auth-btn:hover{background:rgba(167,139,250,.26)}
.auth-btn.ghost{background:var(--bg3,#1a2235);border-color:var(--border2,rgba(255,255,255,.13));color:var(--text3,#a8bacf)}
.auth-btn.ghost:hover{color:var(--text2,#d0daea)}
.auth-err{font-size:12px;color:#f87171}
.auth-spacer{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
`;

  let onChange = () => {};

  function inject() {
    if (document.getElementById('auth-css')) return;
    const s = document.createElement('style');
    s.id = 'auth-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function render(mountId) {
    inject();
    const el = document.getElementById(mountId);
    if (!el) return;
    const admin = Auth.isAdmin();
    const setup = Auth.isSetup();

    if (admin) {
      el.innerHTML = `<div class="auth-bar">
        <span class="auth-state"><span class="auth-dot on"></span>
          <span>管理模式 · <span class="auth-who">${Auth.ADMIN_NAME}</span></span></span>
        <span class="auth-sub">資料已解密，可新增與編輯</span>
        <span class="auth-spacer">
          <button class="auth-btn ghost" onclick="AuthUI.changePw()">變更密碼</button>
          <button class="auth-btn" onclick="AuthUI.lock()">鎖定</button>
        </span></div>`;
    } else if (setup) {
      el.innerHTML = `<div class="auth-bar">
        <span class="auth-state"><span class="auth-dot off"></span><span>訪客模式</span></span>
        <span class="auth-sub">僅顯示報酬率，金額與明細已加密</span>
        <span class="auth-spacer">
          <input type="password" id="auth-pw" placeholder="管理密碼"
                 onkeydown="if(event.key==='Enter')AuthUI.unlock()">
          <button class="auth-btn" onclick="AuthUI.unlock()">登入</button>
          <span class="auth-err" id="auth-err"></span>
        </span></div>`;
    } else {
      el.innerHTML = `<div class="auth-bar">
        <span class="auth-state"><span class="auth-dot off"></span><span>尚未設定管理密碼</span></span>
        <span class="auth-sub">設定後，交易與最愛會用這組密碼加密</span>
        <span class="auth-spacer">
          <input type="password" id="auth-pw" placeholder="設定密碼（至少 8 碼）"
                 onkeydown="if(event.key==='Enter')AuthUI.setup()">
          <button class="auth-btn" onclick="AuthUI.setup()">設定</button>
          <span class="auth-err" id="auth-err"></span>
        </span></div>`;
    }
  }

  const api = {
    mount(mountId, cb) {
      onChange = cb || (() => {});
      api._mountId = mountId;
      render(mountId);
    },
    refresh() { render(api._mountId); },

    async setup() {
      const pw = document.getElementById('auth-pw').value;
      const err = document.getElementById('auth-err');
      if (pw.length < 8) { err.textContent = '至少 8 碼'; return; }
      if (!confirm(
        '設定後，你的交易紀錄與最愛會用這組密碼加密。\n\n' +
        '⚠️ 密碼不會存在任何地方（這正是它安全的原因），\n' +
        '忘記就永久無法還原，請務必記牢或存進密碼管理器。\n\n' +
        '確定要設定嗎？')) return;

      // 把舊的明碼資料一起收進保險庫
      const read = (k, fallback) => {
        try { return JSON.parse(localStorage.getItem(k) || fallback); }
        catch { return JSON.parse(fallback); }
      };
      const legacy = {
        trades:  read('stock_trades_v1',      '[]'),
        notes:   read('stock_trade_notes_v1', '{}'),   // 各檔的檢討備註
        tw_favs: read('tw_stock_favorites',   '[]'),
        us_favs: read('us_stock_favorites',   '[]'),
      };
      await Auth.setup(pw, legacy);
      // 舊的明碼資料要清掉，否則加密就沒有意義
      ['stock_trades_v1', 'stock_trade_notes_v1', 'tw_stock_favorites', 'us_stock_favorites']
        .forEach(k => localStorage.removeItem(k));
      api.refresh(); onChange();
      alert(`已啟用管理模式（${Auth.ADMIN_NAME}）。\n建議立刻用「匯出備份」存一份檔案。`);
    },

    async unlock() {
      const pw = document.getElementById('auth-pw').value;
      const err = document.getElementById('auth-err');
      err.textContent = '驗證中…';
      const ok = await Auth.unlock(pw);
      if (!ok) { err.textContent = '密碼錯誤'; return; }
      api.refresh(); onChange();
    },

    lock() {
      Auth.lock(); api.refresh(); onChange();
    },

    async changePw() {
      const oldPw = prompt('請輸入目前的密碼');
      if (!oldPw) return;
      const newPw = prompt('請輸入新密碼（至少 8 碼）');
      if (!newPw) return;
      if (newPw.length < 8) return alert('新密碼至少 8 碼');
      const ok = await Auth.changePassword(oldPw, newPw);
      alert(ok ? '密碼已變更' : '原密碼錯誤，未變更');
      if (ok) { api.refresh(); onChange(); }
    },
  };
  return api;
})();
