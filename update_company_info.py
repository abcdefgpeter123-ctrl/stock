"""
update_company_info.py
─────────────────────
針對 company_info.json 裡的每支台股：
  1. 用 yfinance 抓 trailingPE / trailingEps（ticker = {code}.TW，失敗則試 .TWO）
  2. 把公司基本資料 + 財務數字傳給 Claude，生成一段近況故事存入 story 欄位
  3. 更新 generated 日期後寫回 company_info.json

環境變數：
  ANTHROPIC_API_KEY  （必填）
"""

import json
import os
import time
from datetime import date

import anthropic
import yfinance as yf

# ── 設定 ──────────────────────────────────────────────────────────────────────
INPUT_FILE = "company_info.json"
OUTPUT_FILE = "company_info.json"   # 直接覆蓋；若想保留備份可改路徑
CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # 速度快、費用低，適合批次作業

# 每支股票處理完後暫停秒數（避免 API rate limit）
YAHOO_SLEEP = 1.0
CLAUDE_SLEEP = 0.5
# ──────────────────────────────────────────────────────────────────────────────


def fetch_yahoo(code: str) -> dict:
    """
    嘗試 {code}.TW，失敗再試 {code}.TWO。
    回傳 {"pe": float|None, "eps": float|None}
    """
    for suffix in (".TW", ".TWO"):
        ticker = yf.Ticker(f"{code}{suffix}")
        info = ticker.info or {}
        pe = info.get("trailingPE") or info.get("forwardPE")
        eps = info.get("trailingEps")
        # yfinance 有時回傳空 dict 但不報錯，用 regularMarketPrice 當作健全性檢查
        if info.get("regularMarketPrice") or pe or eps:
            return {"pe": round(pe, 1) if pe else None,
                    "eps": round(eps, 2) if eps else None}
    return {"pe": None, "eps": None}


def generate_story(client: anthropic.Anthropic, code: str, info: dict,
                   pe: float | None, eps: float | None) -> str:
    """
    呼叫 Claude 生成 2~3 句繁體中文近況故事。
    """
    pe_str = f"{pe}" if pe else "N/A"
    eps_str = f"{eps}" if eps else "N/A"

    prompt = f"""你是台股分析師，請根據以下資料，用繁體中文寫出 2~3 句該公司的近況故事。
風格：簡潔、具體、有觀點，適合散戶快速閱讀。不要加標題，直接輸出段落文字。

股票代號：{code}
核心產品：{info.get('core_products', '')}
產業：{info.get('industry', '')}
主要客戶：{info.get('major_clients', '')}
業務描述：{info.get('business_desc', '')}
本益比（P/E）：{pe_str}
每股盈餘（EPS）：{eps_str}

請聚焦於：當前本益比反映的市場期待、核心成長驅動力、以及投資人需留意的一個風險點。"""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("請設定環境變數 ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=api_key)
    today = date.today().strftime("%Y/%m/%d")

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data: dict = json.load(f)

    total = len(data)
    for i, (code, info) in enumerate(data.items(), 1):
        print(f"[{i}/{total}] 處理 {code} …", end=" ", flush=True)

        # 1. 抓財務數字
        fin = fetch_yahoo(code)
        time.sleep(YAHOO_SLEEP)

        # 2. 生成故事
        story = generate_story(client, code, info, fin["pe"], fin["eps"])
        time.sleep(CLAUDE_SLEEP)

        # 3. 寫回欄位
        info["pe_ratio"] = fin["pe"]
        info["eps"]      = fin["eps"]
        info["story"]    = story
        info["generated"] = today

        print(f"PE={fin['pe']} EPS={fin['eps']} ✓")

    # 寫回檔案
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n完成！已更新 {total} 支股票 → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
