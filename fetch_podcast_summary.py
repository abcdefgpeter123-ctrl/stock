#!/usr/bin/env python3
"""
股癌 Gooaye Podcast 精華抓取腳本
1. 讀 RSS，找出最新一集
2. 若該集已處理過（podcast_summary.json 內 episode 相同）→ 跳過
3. 下載音檔 → Whisper 轉錄（中文）→ Claude 摘要重點
4. 寫入 podcast_summary.json
"""

import json
import os
import re
import time
import datetime
import xml.etree.ElementTree as ET
import requests

RSS_URL = "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml"
SHOW_URL = "https://open.spotify.com/show/1zWxx5pKk0XBEzMupVC7UZ"
AUDIO_TMP = "podcast_ep.mp3"
OUT_FILE = "podcast_summary.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def fetch_latest_episode():
    """讀 RSS，回傳最新一集的 {title, pub_date, audio_url, episode_no, guid}"""
    r = requests.get(RSS_URL, headers=HEADERS, timeout=30)
    root = ET.fromstring(r.content)
    item = root.find("./channel/item")
    if item is None:
        return None

    title = (item.findtext("title") or "").strip()
    pub_date_raw = (item.findtext("pubDate") or "").strip()
    guid = (item.findtext("guid") or title).strip()
    enclosure = item.find("enclosure")
    audio_url = enclosure.get("url") if enclosure is not None else None

    m = re.search(r"EP\s*(\d+)", title, re.IGNORECASE)
    episode_no = m.group(1) if m else None

    try:
        dt = datetime.datetime.strptime(pub_date_raw[:25], "%a, %d %b %Y %H:%M:%S")
        pub_date = dt.strftime("%Y/%m/%d")
    except Exception:
        pub_date = pub_date_raw

    if not audio_url:
        return None

    return {
        "guid": guid,
        "title": title,
        "pub_date": pub_date,
        "audio_url": audio_url,
        "episode_no": episode_no,
    }


def download_audio(url, dest):
    r = requests.get(url, headers=HEADERS, timeout=180, stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)


def transcribe(path):
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(path, language="zh", initial_prompt="股癌 Gooaye 財經 podcast 股票 台股 美股 投資")
    return result["text"]


def summarize(transcript, title, api_key):
    """用 Claude 把逐字稿整理成重點條列，過濾業配內容"""
    if not api_key or not transcript:
        return None

    # 過長就截斷（Haiku 上下文足夠，但避免超額 token 費用）
    text = transcript[:24000]

    prompt = (
        f"以下是財經 Podcast「股癌 Gooaye」單集《{title}》的逐字稿（語音轉文字，可能有同音字錯誤）。\n\n"
        f"{text}\n\n"
        "請整理成繁體中文重點摘要，規則：\n"
        "1. 完全略過業配/贊助商廣告內容（通常在開頭），不要摘要廠商產品資訊\n"
        "2. 用條列式（每行開頭用 •），5–8 點，涵蓋節目實際討論的市場觀點、個股、總經話題\n"
        "3. 提到的具體股票、數字、百分比盡量保留原始說法\n"
        "4. 不要加開場白或結語，直接輸出條列重點\n"
        "5. 轉錄可能有同音字誤植（如公司/人名），依財經常識合理修正後再摘要"
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"   ⚠️ 摘要生成失敗: {e}")
        return None


def main():
    print("🎙️ 股癌 Podcast 精華抓取開始...")

    try:
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    ep = fetch_latest_episode()
    if not ep:
        print("   ⚠️ RSS 讀取失敗，保留舊資料")
        return

    print(f"   最新集數: {ep['title']}（{ep['pub_date']}）")

    if existing.get("guid") == ep["guid"]:
        print("   ✅ 已是最新集數，跳過（沒有新集數）")
        return

    print("   📥 下載音檔...")
    try:
        download_audio(ep["audio_url"], AUDIO_TMP)
    except Exception as e:
        print(f"   ⚠️ 音檔下載失敗: {e}")
        return

    print("   🔤 Whisper 轉錄中（可能需要幾分鐘）...")
    t0 = time.time()
    try:
        transcript = transcribe(AUDIO_TMP)
    except Exception as e:
        print(f"   ⚠️ 轉錄失敗: {e}")
        os.path.exists(AUDIO_TMP) and os.remove(AUDIO_TMP)
        return
    print(f"   ✅ 轉錄完成（{len(transcript)} 字，耗時 {time.time()-t0:.0f}s）")

    os.path.exists(AUDIO_TMP) and os.remove(AUDIO_TMP)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    summary = summarize(transcript, ep["title"], api_key)
    if not summary:
        print("   ⚠️ 摘要失敗，不更新 podcast_summary.json")
        return

    data = {
        "guid": ep["guid"],
        "title": ep["title"],
        "episode_no": ep["episode_no"],
        "pub_date": ep["pub_date"],
        "summary": summary,
        "show_url": SHOW_URL,
        "updated_at": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"),
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"   ✅ podcast_summary.json 已更新（{ep['title']}）")


if __name__ == "__main__":
    main()
