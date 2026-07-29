#!/usr/bin/env python3
"""
股癌 Gooaye Podcast 精華抓取腳本（規則式，不依賴 AI API）
1. 讀 RSS，找出最新一集
2. 若該集已處理過（podcast_summary.json 內 guid 相同）→ 跳過
3. 下載音檔 → Whisper 本機轉錄（中文）
4. 用關鍵字啟發式跳過開頭業配，依語音停頓分段成可讀段落
5. 寫入 podcast_summary.json

註：Whisper 轉出的中文逐字稿幾乎沒有標點符號，無法用句子切分做重點抽取，
   因此這裡不做「AI 摘要重點」，而是保留完整逐字稿、跳過業配、依停頓分段。
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

# 業配內容常見的收尾關鍵字，用來判斷「業配大概講到這裡結束」
AD_END_MARKERS = ["資訊欄", "折扣碼", "傳送門", "全館滿", "官網搜尋", "打上我的"]
AD_SEARCH_WINDOW = 3000  # 只在逐字稿前 3000 字內找業配收尾點


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


def transcribe_segments(path):
    """回傳 whisper 的 segments 列表（含 start/end/text），保留語音停頓資訊供分段用"""
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(path, language="zh", initial_prompt="股癌 Gooaye 財經 podcast 股票 台股 美股 投資")
    return result["segments"]


def trim_ads_and_paragraph(segments):
    """
    1. 在前 AD_SEARCH_WINDOW 字內找業配收尾關鍵字，之後的內容才保留
    2. 依語音停頓（segment 間隔 >= 1 秒視為換段）把剩餘內容分成可讀段落
    """
    full_text = "".join(s["text"] for s in segments)

    cut_pos = 0
    window_text = full_text[:AD_SEARCH_WINDOW]
    for marker in AD_END_MARKERS:
        idx = window_text.rfind(marker)
        if idx != -1:
            cut_pos = max(cut_pos, idx + len(marker))

    # 找出對應到 cut_pos 之後的第一個 segment
    running = 0
    start_idx = 0
    for i, s in enumerate(segments):
        running += len(s["text"])
        if running >= cut_pos:
            start_idx = i + 1
            break
    kept = segments[start_idx:] or segments  # 若全被裁掉，保底用全部

    # 依停頓分段，每段最多約 12 個 segment 或間隔 >=1.2 秒就換段
    paragraphs, cur, cur_count = [], [], 0
    prev_end = None
    for s in kept:
        gap = (s["start"] - prev_end) if prev_end is not None else 0
        if cur and (gap >= 1.2 or cur_count >= 12):
            paragraphs.append("".join(cur).strip())
            cur, cur_count = [], 0
        cur.append(s["text"])
        cur_count += 1
        prev_end = s["end"]
    if cur:
        paragraphs.append("".join(cur).strip())

    return [p for p in paragraphs if p]


def main():
    print("🎙️ 股癌 Podcast 精華抓取開始（規則式，不依賴 AI API）...")

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
        segments = transcribe_segments(AUDIO_TMP)
    except Exception as e:
        print(f"   ⚠️ 轉錄失敗: {e}")
        os.path.exists(AUDIO_TMP) and os.remove(AUDIO_TMP)
        return
    total_chars = sum(len(s["text"]) for s in segments)
    print(f"   ✅ 轉錄完成（{total_chars} 字，耗時 {time.time()-t0:.0f}s）")

    os.path.exists(AUDIO_TMP) and os.remove(AUDIO_TMP)

    paragraphs = trim_ads_and_paragraph(segments)
    if not paragraphs:
        print("   ⚠️ 分段後無內容，不更新 podcast_summary.json")
        return

    data = {
        "guid": ep["guid"],
        "title": ep["title"],
        "episode_no": ep["episode_no"],
        "pub_date": ep["pub_date"],
        "paragraphs": paragraphs,
        "show_url": SHOW_URL,
        "updated_at": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"),
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"   ✅ podcast_summary.json 已更新（{ep['title']}，{len(paragraphs)} 段）")


if __name__ == "__main__":
    main()
