#!/usr/bin/env python3
"""
財經 Podcast 每日自動精華（全本機，不需要任何 API key / 不花錢）

流程：
  1. 讀 podcast_sources.json 的節目清單，各自抓 RSS 最新一集
  2. 跟 podcast_summary.json 比對 guid，已處理過的直接跳過
  3. 下載音檔 → Whisper large-v3 轉逐字稿
  4. Ollama（本機 LLM）map-reduce 摘要：
       map    逐段抽財經重點，業配/閒聊由 LLM 判斷後丟掉
       reduce 合併成分主題的「本集精華」
  5. 寫回 podcast_summary.json

實測（M4 Pro，49 分鐘節目）：轉錄 336s + 摘要 67s ≈ 7 分鐘/集。

依賴：
  pip3 install requests mlx-whisper        # Apple Silicon（快 8 倍，建議）
  pip3 install requests openai-whisper     # 其他平台的備援
  ollama serve + ollama pull qwen2.5:7b
"""

import datetime
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

SOURCES_FILE = "podcast_sources.json"
OUT_FILE = "podcast_summary.json"
AUDIO_TMP = "podcast_ep.mp3"

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("PODCAST_LLM", "qwen2.5:7b")
WHISPER_REPO = "mlx-community/whisper-large-v3-mlx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# Whisper 會模仿提示詞的用字，把詞彙寫進去可大幅降低同音錯字
# （實測：base→large-v3 已修正「暴水→報稅」「消破→Sell Put」，
#  再加上這份詞表可再修正「川胡→川湖」「彩玉→采鈺」這類台股個股名）。
#
# ⚠️ Whisper 的 initial_prompt 只吃最後 224 個 token，寫太長前面會被截斷失效，
#    所以這裡只放「最常出現且最容易被聽錯」的詞，不要無限追加。
WHISPER_PROMPT = (
    "以下是普通話的台股財經節目。常見詞彙："
    "台積電、聯發科、鴻海、廣達、緯創、緯穎、奇鋐、川湖、采鈺、環球晶、"
    "世界先進、日月光、聯詠、瑞昱、華邦電、南亞科、智邦、台達電、輝達、"
    "法說會、殖利率、本益比、資本支出、庫存去化、晶圓代工、記憶體、"
    "聯準會、升息、降息、通膨、槓桿、爆倉、融資、處置股、當沖。"
)

CHUNK_SIZE = 3000       # 每段送進 LLM 的逐字稿字數
PARAGRAPH_GAP = 1.2     # 語音停頓幾秒視為換段


# ── RSS ────────────────────────────────────────────────────

def fetch_latest_episode(show):
    """讀 RSS 回傳最新一集；失敗回 None"""
    r = requests.get(show["rss"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    item = root.find("./channel/item")
    if item is None:
        return None

    enclosure = item.find("enclosure")
    audio_url = enclosure.get("url") if enclosure is not None else None
    if not audio_url:
        return None

    title = (item.findtext("title") or "").strip()
    guid = (item.findtext("guid") or title).strip()

    pub_raw = (item.findtext("pubDate") or "").strip()
    try:
        pub_date = datetime.datetime.strptime(pub_raw[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y/%m/%d")
    except Exception:
        pub_date = pub_raw[:16]

    # itunes:duration 可能是秒數，也可能是 HH:MM:SS
    dur_raw = (item.findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration") or "").strip()
    minutes = None
    if dur_raw:
        try:
            if ":" in dur_raw:
                parts = [int(x) for x in dur_raw.split(":")]
                secs = 0
                for p in parts:
                    secs = secs * 60 + p
            else:
                secs = int(float(dur_raw))
            minutes = round(secs / 60)
        except Exception:
            minutes = None

    m = re.search(r"EP\s*\.?\s*(\d+)", title, re.IGNORECASE)

    return {
        "guid": guid,
        "title": title,
        "episode_no": m.group(1) if m else None,
        "pub_date": pub_date,
        "audio_url": audio_url,
        "duration_min": minutes,
    }


def download_audio(url, dest):
    with requests.get(url, headers=HEADERS, timeout=300, stream=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)


# ── Whisper ────────────────────────────────────────────────

def transcribe(path):
    """
    回傳 (全文, 段落list)。優先用 mlx-whisper（Apple Silicon，約 8.5x realtime），
    沒有的話退回 openai-whisper。
    """
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            path, language="zh", initial_prompt=WHISPER_PROMPT,
            path_or_hf_repo=WHISPER_REPO,
        )
    except ImportError:
        import whisper
        result = whisper.load_model("small").transcribe(
            path, language="zh", initial_prompt=WHISPER_PROMPT,
        )

    segments = result.get("segments") or []
    full = _clean(result.get("text", ""))

    # 依語音停頓分成可讀段落
    paragraphs, cur, count, prev_end = [], [], 0, None
    for s in segments:
        gap = (s["start"] - prev_end) if prev_end is not None else 0
        if cur and (gap >= PARAGRAPH_GAP or count >= 12):
            paragraphs.append(_clean("".join(cur)))
            cur, count = [], 0
        cur.append(s["text"])
        count += 1
        prev_end = s["end"]
    if cur:
        paragraphs.append(_clean("".join(cur)))

    return full, [p for p in paragraphs if p]


def _clean(text):
    """統一標點：半形轉全形，並修掉 Whisper 偶爾輸出的怪符號"""
    text = re.sub(r"[﹚﹙]+", "，", text)
    text = (text.replace(",", "，").replace("!", "！")
                .replace("?", "？").replace(";", "；"))
    text = re.sub(r"[，\s]{2,}", "，", text)
    return text.strip("，").strip()


# ── Ollama ─────────────────────────────────────────────────

# qwen2.5 是中國訓練的模型，即使指定「用繁體中文」仍常混入簡體字
# （實測會輸出「环球晶／联咏／华邦電」），所以一律用 OpenCC 做確定性轉換。
try:
    from opencc import OpenCC
    _cc = OpenCC("s2twp")          # 簡體 → 繁體（台灣用詞）
except ImportError:
    _cc = None


def to_traditional(text):
    return _cc.convert(text) if _cc else text


def ollama(prompt, num_predict=500, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.post(OLLAMA_URL, timeout=900, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": num_predict},
            })
            r.raise_for_status()
            return to_traditional(r.json().get("response", "").strip())
        except Exception as e:
            if attempt == retries:
                raise
            print(f"      ⚠️ Ollama 重試（{e}）")
            time.sleep(3)
    return ""


def summarize(text):
    """map-reduce 摘要，回傳 [{topic, points[]}]"""
    chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
    notes = []
    for i, c in enumerate(chunks, 1):
        out = ollama(f"""你是財經節目的編輯。以下是 Podcast 逐字稿的一部分（語音辨識產生，可能有錯字）。

規則：
- 只抽出跟「股市、投資、總經、產業、公司」有關的內容
- 業配廣告、主持人閒聊、生活瑣事、聽眾問候一律忽略
- 如果這段完全沒有財經內容，只回覆兩個字：無
- 用繁體中文，每點一行，開頭用「- 」
- 只寫逐字稿真的有講的，不要補充或杜撰

逐字稿：
{c}

重點：""", 350)
        if out and out.strip() != "無" and len(out) > 12:
            notes.append(out)
        print(f"      抽重點 {i}/{len(chunks)}", end="\r", flush=True)
    print(" " * 30, end="\r")

    if not notes:
        return []

    merged = "\n".join(notes)[:8000]
    final = ollama(f"""以下是一集財經 Podcast 各段落的重點筆記，請彙整成給投資人看的「本集精華」。

規則：
- 用繁體中文（不要用簡體字）
- 合併重複或相似的點，刪掉不重要的
- 依主題分組，每組一個「## 主題」標題，底下用「- 」條列
- 主題不要重疊，最多 5 組、每組最多 4 點
- 只使用筆記中出現過的資訊，絕對不要杜撰

筆記：
{merged}

本集精華：""", 1200)

    sections = _parse_sections(final)
    # num_predict 用完時最後一點常被截斷成半句，寧可丟掉也不要顯示殘句
    if sections and sections[-1]["points"]:
        last = sections[-1]["points"][-1]
        if len(last) > 8 and last[-1] not in "。！？」）":
            sections[-1]["points"].pop()
            if not sections[-1]["points"]:
                sections.pop()
    return sections


def _parse_sections(md):
    """把 '## 主題 / - 重點' 的 markdown 解析成結構化資料"""
    sections, cur = [], None
    for line in md.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if cur and cur["points"]:
                sections.append(cur)
            cur = {"topic": line.lstrip("#").strip(), "points": []}
        elif line.startswith(("-", "•", "*")):
            pt = line.lstrip("-•*").strip()
            if pt:
                if cur is None:
                    cur = {"topic": "重點", "points": []}
                cur["points"].append(pt)
    if cur and cur["points"]:
        sections.append(cur)
    return sections


# ── 主流程 ─────────────────────────────────────────────────

def process_show(show, known_guid):
    """處理單一節目，有新集數才回傳結果，否則回 None"""
    print(f"\n🎙️  {show['name']}")

    try:
        ep = fetch_latest_episode(show)
    except Exception as e:
        print(f"   ⚠️ RSS 讀取失敗，略過：{e}")
        return None
    if not ep:
        print("   ⚠️ RSS 沒有可用的音檔，略過")
        return None

    print(f"   最新：{ep['title'][:40]}（{ep['pub_date']}）")

    if ep["guid"] == known_guid:
        print("   ✅ 已處理過，跳過")
        return None

    limit = show.get("max_minutes")
    if limit and ep.get("duration_min") and ep["duration_min"] > limit:
        print(f"   ⏭️ 長度 {ep['duration_min']} 分鐘超過上限 {limit}，略過")
        return None

    try:
        print("   📥 下載音檔...")
        download_audio(ep["audio_url"], AUDIO_TMP)

        print("   🔤 Whisper 轉錄中...")
        t0 = time.time()
        full_text, paragraphs = transcribe(AUDIO_TMP)
        print(f"   ✅ 轉錄完成（{len(full_text)} 字，{time.time()-t0:.0f}s）")

        if len(full_text) < 500:
            print("   ⚠️ 逐字稿過短，判定為失敗，略過")
            return None

        print("   🧠 Ollama 摘要中...")
        t0 = time.time()
        highlights = summarize(full_text)
        print(f"   ✅ 精華完成（{len(highlights)} 個主題，{time.time()-t0:.0f}s）")

        if not highlights:
            print("   ⚠️ 沒有抽出任何財經重點，略過")
            return None

    except Exception as e:
        print(f"   ❌ 處理失敗：{type(e).__name__}: {e}")
        return None
    finally:
        if os.path.exists(AUDIO_TMP):
            os.remove(AUDIO_TMP)

    return {
        "show_id": show["id"],
        "show": show["name"],
        "show_url": show.get("url", ""),
        "guid": ep["guid"],
        "title": ep["title"],
        "episode_no": ep["episode_no"],
        "pub_date": ep["pub_date"],
        "duration_min": ep.get("duration_min"),
        "highlights": highlights,
        "transcript": paragraphs,
        "processed_at": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"),
    }


def main():
    print("🎙️ 財經 Podcast 每日精華（Whisper + Ollama，全本機）")

    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        shows = json.load(f)["shows"]

    try:
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}

    # show_id → 上次處理的那一集（用來判斷有沒有新集數，也當作沒新集數時的保留值）
    prev = {e["show_id"]: e for e in old.get("episodes", []) if e.get("show_id")}

    episodes, updated = [], 0
    for show in shows:
        known = prev.get(show["id"], {}).get("guid")
        result = process_show(show, known)
        if result:
            episodes.append(result)
            updated += 1
        elif show["id"] in prev:
            episodes.append(prev[show["id"]])      # 沒新集數就保留舊的

    if not episodes:
        print("\n⚠️ 完全沒有可用內容，不覆寫 podcast_summary.json")
        return

    # 新到舊排序，前端就不用自己排
    episodes.sort(key=lambda e: e.get("pub_date", ""), reverse=True)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"),
            "episodes": episodes,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完成！{updated} 集有更新，共 {len(episodes)} 個節目")


if __name__ == "__main__":
    main()
