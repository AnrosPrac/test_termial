"""
Full Pipeline: YouTube URL / Playlist → Transcript → LLM → Topics + Questions
------------------------------------------------------------------------------
Step 1: Detect if input is a single video or a playlist
        If playlist → extract all video IDs via yt-dlp
Step 2: Per video: fetch transcript, clean, smart chunk
Step 3: Feed chunks to Groq LLM → extract topic_keys + coding challenges
Step 4: Merge all results across chunks (and across videos for playlists)
        → deduplicate questions, time-based weightage, rank
"""

import re
import os
import json
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from groq import Groq
import yt_dlp

# Load API keys from .env file
load_dotenv()


# ─── CONFIG ──────────────────────────────────────────────────────────────────
MAX_TOKENS_PER_CHUNK = 500
WORDS_PER_TOKEN      = 0.75
NOISE_WORDS = {
    "foreign", "applause", "music", "laughter",
    "inaudible", "crosstalk", "silence", "outro", "intro"
}

GROQ_MODEL = "openai/gpt-oss-120b"
# ─────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1: TRANSCRIPT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc in ("youtu.be",):
        return parsed.path.lstrip("/")
    if "youtube.com" in parsed.netloc:
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[1].split("/")[0]
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
    raise ValueError(f"Could not extract video ID from URL: {url}")


def fetch_transcript(video_id: str) -> list[dict]:
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["en"])
        fetched = transcript.fetch()
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]
    except TranscriptsDisabled:
        raise RuntimeError("Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise RuntimeError("No English transcript found for this video.")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch transcript: {e}")


def clean_text(text: str) -> str:
    text = re.sub(r"\[.*?\]", "", text)
    noise_pattern = r'\b(' + '|'.join(re.escape(w) for w in NOISE_WORDS) + r')\b'
    text = re.sub(noise_pattern, '', text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def segments_to_sentences(segments: list[dict]) -> list[dict]:
    word_times = []
    for seg in segments:
        text = clean_text(seg["text"])
        if not text:
            continue
        words = text.split()
        duration = seg.get("duration", 1.0) or 1.0
        word_duration = duration / max(len(words), 1)
        for i, word in enumerate(words):
            word_times.append((word, round(seg["start"] + i * word_duration, 2)))

    if not word_times:
        return []

    full_text = " ".join(w for w, _ in word_times)
    sentence_pattern = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
    raw_sentences = sentence_pattern.split(full_text)

    sentences = []
    word_idx = 0
    for raw in raw_sentences:
        raw = raw.strip()
        if not raw:
            continue
        count = len(raw.split())
        start_time = word_times[word_idx][1] if word_idx < len(word_times) else 0.0
        sentences.append({"sentence": raw, "start_time": start_time})
        word_idx = min(word_idx + count, len(word_times) - 1)

    return sentences


def force_split_by_words(text: str, start_time: float, max_words: int) -> list[dict]:
    words = text.split()
    return [
        {"text": " ".join(words[i:i+max_words]), "start_time": start_time}
        for i in range(0, len(words), max_words)
    ]


def smart_chunk(sentences: list[dict], max_tokens: int = MAX_TOKENS_PER_CHUNK) -> list[dict]:
    max_words = int(max_tokens * WORDS_PER_TOKEN)
    chunks, current, current_words, chunk_id = [], [], 0, 1

    def flush():
        nonlocal current, current_words, chunk_id
        if not current:
            return
        text = " ".join(s["sentence"] for s in current)
        wc = len(text.split())
        if wc > max_words * 1.5:
            for sub in force_split_by_words(text, current[0]["start_time"], max_words):
                swc = len(sub["text"].split())
                chunks.append({"chunk_id": chunk_id, "text": sub["text"],
                                "start_time": sub["start_time"], "end_time": current[-1]["start_time"],
                                "word_count": swc, "approx_tokens": round(swc / WORDS_PER_TOKEN)})
                chunk_id += 1
        else:
            chunks.append({"chunk_id": chunk_id, "text": text,
                           "start_time": current[0]["start_time"], "end_time": current[-1]["start_time"],
                           "word_count": wc, "approx_tokens": round(wc / WORDS_PER_TOKEN)})
            chunk_id += 1
        current.clear()
        current_words = 0

    for sent in sentences:
        wc = len(sent["sentence"].split())
        if current_words + wc > max_words and current:
            flush()
        current.append(sent)
        current_words += wc
    flush()
    return chunks


def get_chunks_from_url(url: str) -> tuple[str, list[dict]]:
    print(f"\n🔗 Processing: {url}")
    video_id = extract_video_id(url)
    print(f"📹 Video ID  : {video_id}")
    print("⬇️  Fetching transcript...")
    segments = fetch_transcript(video_id)
    print(f"✅ Got {len(segments)} raw segments")
    sentences = segments_to_sentences(segments)
    chunks = smart_chunk(sentences)
    print(f"📦 {len(chunks)} chunks ready\n")
    return video_id, chunks


def get_chunks_from_json(filepath: str) -> tuple[str, list[dict]]:
    """Load already-extracted transcript JSON (for testing without internet)."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"📂 Loaded {data['total_chunks']} chunks from {filepath}")
    return data["video_id"], data["chunks"]


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2: LLM TOPIC + QUESTION EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

# Fixed programming topic taxonomy — LLM must pick from these keys only.
# Add more as your DB grows.
TOPIC_TAXONOMY = {
    # ── Fundamentals ──────────────────────────────────────────────
    "variables_and_datatypes"   : "Variables and Data Types",
    "operators"                 : "Operators",
    "type_conversion"           : "Type Conversion",
    "input_output"              : "Input and Output",
    "comments"                  : "Comments",

    # ── Control Flow ──────────────────────────────────────────────
    "if_else"                   : "If-Else Statements",
    "nested_conditions"         : "Nested Conditions",
    "match_case"                : "Match-Case (Switch)",

    # ── Loops ─────────────────────────────────────────────────────
    "for_loop"                  : "For Loop",
    "while_loop"                : "While Loop",
    "nested_loops"              : "Nested Loops",
    "loop_control"              : "Loop Control (break/continue/pass)",

    # ── Functions ─────────────────────────────────────────────────
    "functions_basics"          : "Functions Basics",
    "function_arguments"        : "Function Arguments",
    "recursion"                 : "Recursion",
    "lambda_functions"          : "Lambda Functions",

    # ── Data Structures ───────────────────────────────────────────
    "lists"                     : "Lists",
    "tuples"                    : "Tuples",
    "dictionaries"              : "Dictionaries",
    "sets"                      : "Sets",
    "strings"                   : "Strings",
    "list_comprehension"        : "List Comprehension",

    # ── OOP ───────────────────────────────────────────────────────
    "oop_classes_objects"       : "Classes and Objects",
    "oop_constructor"           : "Constructor (__init__)",
    "oop_inheritance"           : "Inheritance",
    "oop_polymorphism"          : "Polymorphism",
    "oop_encapsulation"         : "Encapsulation",
    "oop_abstraction"           : "Abstraction",

    # ── Error Handling ────────────────────────────────────────────
    "exception_handling"        : "Exception Handling (try/except)",

    # ── File & Modules ────────────────────────────────────────────
    "file_handling"             : "File Handling",
    "modules_and_imports"       : "Modules and Imports",

    # ── Advanced ──────────────────────────────────────────────────
    "decorators"                : "Decorators",
    "generators"                : "Generators",
    "iterators"                 : "Iterators",
}

# Build taxonomy string for the prompt
_TAXONOMY_LINES = "\n".join(f'  "{k}" → {v}' for k, v in TOPIC_TAXONOMY.items())

SYSTEM_PROMPT = f"""You are a programming challenge designer for a coding education platform.

You will receive a transcript chunk from a Python programming tutorial video.
Your job is to:
1. Identify which programming topics are being actively taught in this chunk
2. Generate CODING CHALLENGES — problems the student must write code to solve

IMPORTANT — Topic names must come EXACTLY from this taxonomy (use the key, not the label):
{_TAXONOMY_LINES}

Only pick topics that are clearly and actively taught in this chunk.
Do NOT invent topic names. Do NOT use labels — use the snake_case keys.

Respond ONLY with a valid JSON array. No explanation, no markdown, no code fences.

Format:
[
  {{
    "topic_key": "while_loop",
    "questions": [
      "Write a program that prints numbers from 1 to 10 using a while loop.",
      "Write a program that keeps asking the user for a number until they enter 0, then prints the total sum."
    ]
  }}
]

STRICT RULES for questions:
- Every question must start with "Write a program" or "Write a function"
- Questions must be CODING TASKS the student has to implement, not definitions or explanations
- Base challenges directly on examples shown in the transcript — keep them beginner-level
- 3 to 5 challenges per topic
- NO concept questions like "What does X do?" or "How does Y work?"
- If the chunk has no meaningful programming content, return []
"""


def extract_topics_from_chunk(client: Groq, chunk: dict, chunk_number: int, total: int) -> list[dict]:
    """Send one chunk to the LLM — get back normalized topic keys + questions."""
    print(f"  🤖 Chunk {chunk_number}/{total} (~{chunk['approx_tokens']} tokens)...", end=" ", flush=True)

    prompt = f"""Analyze this Python tutorial transcript chunk.
Identify topics from the taxonomy and generate student questions.

Transcript:
\"\"\"
{chunk['text']}
\"\"\"
"""

    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.2,           # very low — we want consistent key matching
            max_completion_tokens=8192,
            top_p=1,
            reasoning_effort="medium",
            stream=True,
            stop=None
        )

        raw = ""
        for part in completion:
            raw += part.choices[0].delta.content or ""

        raw = raw.strip()
        raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"^```\s*",     "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$",     "", raw, flags=re.MULTILINE)

        items = json.loads(raw)

        results = []
        for item in items:
            key = item.get("topic_key", "").strip().lower()

            # Validate key is in our taxonomy — skip unknown keys
            if key not in TOPIC_TAXONOMY:
                print(f"\n    ⚠️  Unknown topic key '{key}' — skipped", end="")
                continue

            results.append({
                "topic_key"  : key,
                "topic_label": TOPIC_TAXONOMY[key],
                "questions"  : item.get("questions", []),
                "chunk_id"   : chunk["chunk_id"],
                "start_time" : chunk["start_time"],
                "end_time"   : chunk["end_time"],
                "duration"   : chunk["end_time"] - chunk["start_time"],
            })

        print(f"✅ {len(results)} topics")
        return results

    except json.JSONDecodeError as e:
        print(f"⚠️  JSON parse error — skipping chunk")
        return []
    except Exception as e:
        print(f"❌ Error: {e} — skipping chunk")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3: MERGE + TIME-BASED WEIGHTAGE + RANK
# ══════════════════════════════════════════════════════════════════════════════

def merge_topics(all_topics: list[dict], total_video_duration: float) -> list[dict]:
    """
    Merge topics with the same key across chunks:
    - Accumulate total duration spent on each topic
    - Deduplicate questions
    - Weightage = (topic_total_duration / total_video_duration) × 10
    - Sort by weightage descending
    """
    merged: dict[str, dict] = {}

    for t in all_topics:
        key = t["topic_key"]

        if key not in merged:
            merged[key] = {
                "topic_key"       : key,
                "topic_label"     : t["topic_label"],
                "total_duration"  : t["duration"],
                "questions"       : list(t.get("questions", [])),
                "chunk_ids"       : [t["chunk_id"]],
                "first_seen_at"   : t["start_time"],
            }
        else:
            merged[key]["total_duration"] += t["duration"]
            merged[key]["chunk_ids"].append(t["chunk_id"])

            # Deduplicate questions and cap at 6
            existing = set(q.lower() for q in merged[key]["questions"])
            for q in t.get("questions", []):
                if len(merged[key]["questions"]) >= 6:
                    break
                if q.lower() not in existing:
                    merged[key]["questions"].append(q)
                    existing.add(q.lower())

    result = list(merged.values())

    # Cap initial questions list to 6 too (in case first chunk already had more)
    for r in result:
        r["questions"] = r["questions"][:6]

    # Weightage: use chunk count (reliable) as proxy since end_time can be unreliable
    total_chunks = sum(len(r["chunk_ids"]) for r in result)
    for r in result:
        chunk_share = len(r["chunk_ids"]) / total_chunks if total_chunks > 0 else 0
        # Also factor in time if available and non-zero
        time_share = (r["total_duration"] / total_video_duration) if total_video_duration > 0 else 0
        # Blend both signals — if time is broken (all zeros), chunk_share dominates
        if time_share > 0:
            raw_weight = ((chunk_share + time_share) / 2) * 10
        else:
            raw_weight = chunk_share * 10
        r["weightage"] = round(max(1.0, min(10.0, raw_weight)), 2)

    # Sort by weightage descending
    result.sort(key=lambda x: x["weightage"], reverse=True)

    # Clean up internal field
    for r in result:
        del r["total_duration"]

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  PLAYLIST SUPPORT
# ══════════════════════════════════════════════════════════════════════════════

def is_playlist_url(url: str) -> bool:
    """Return True if the URL points to a YouTube playlist."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return "list" in qs and "v" not in qs   # playlist-only URL (no single video)


def extract_playlist_videos(playlist_url: str) -> list[dict]:
    """
    Use yt-dlp to extract all video IDs + titles from a playlist.
    Returns: [{video_id, title, url}, ...]
    No downloading — metadata only.
    """
    ydl_opts = {
        "quiet"          : True,
        "extract_flat"   : True,   # don't fetch full info per video
        "skip_download"  : True,
    }

    print(f"📋 Fetching playlist info...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)

    if not info or "entries" not in info:
        raise RuntimeError("Could not extract playlist. Check the URL.")

    videos = []
    for entry in info["entries"]:
        if entry and entry.get("id"):
            videos.append({
                "video_id": entry["id"],
                "title"   : entry.get("title", f"Video {entry['id']}"),
                "url"     : f"https://www.youtube.com/watch?v={entry['id']}"
            })

    print(f"✅ Found {len(videos)} videos in playlist: '{info.get('title', 'Unknown')}'\n")
    return videos


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def process_single_video(video_url: str, client: Groq) -> tuple[list, float]:
    """
    Run the full pipeline for one video.
    Returns (all_raw_topics, total_video_duration)
    """
    try:
        video_id, chunks = get_chunks_from_url(video_url)
    except Exception as e:
        print(f"  ⚠️  Skipping {video_url}: {e}")
        return [], 0.0

    total_duration = max(c["end_time"] for c in chunks) if chunks else 1.0
    print(f"  ⏱  Duration: {int(total_duration // 60)}m {int(total_duration % 60)}s | {len(chunks)} chunks")

    all_topics = []
    for i, chunk in enumerate(chunks, 1):
        topics = extract_topics_from_chunk(client, chunk, i, len(chunks))
        all_topics.extend(topics)

    return all_topics, total_duration


def run_pipeline(source: str) -> dict:
    """
    source: YouTube single video URL  OR  YouTube playlist URL  OR  transcript JSON file
    Auto-detects and handles all cases.
    """
    client = Groq()

    # ── Case 1: Local JSON file (dev/test) ───────────────────────────────────
    if source.endswith(".json"):
        video_id, chunks = get_chunks_from_json(source)
        total_duration = max(c["end_time"] for c in chunks) if chunks else 1.0
        print(f"⏱  Duration: {int(total_duration//60)}m | 🧠 Extracting topics...")
        all_topics = []
        for i, chunk in enumerate(chunks, 1):
            all_topics.extend(extract_topics_from_chunk(client, chunk, i, len(chunks)))
        final_topics = merge_topics(all_topics, total_duration)
        return {
            "type"                : "single",
            "video_id"            : video_id,
            "source"              : source,
            "total_video_duration": total_duration,
            "total_topics"        : len(final_topics),
            "topics"              : final_topics
        }

    # ── Case 2: Playlist URL ─────────────────────────────────────────────────
    if is_playlist_url(source):
        print(f"\n🎵 Playlist detected!")
        videos = extract_playlist_videos(source)

        all_topics_combined = []
        total_duration_combined = 0.0
        processed, skipped = 0, 0

        for idx, video in enumerate(videos, 1):
            print(f"\n{'─'*55}")
            print(f"▶ Video {idx}/{len(videos)}: {video['title']}")
            print(f"  URL: {video['url']}")

            raw_topics, duration = process_single_video(video["url"], client)

            if raw_topics:
                all_topics_combined.extend(raw_topics)
                total_duration_combined += duration
                processed += 1
            else:
                skipped += 1

        print(f"\n{'─'*55}")
        print(f"✅ Processed: {processed} videos | ⚠️  Skipped: {skipped}")
        print(f"📊 Total raw topics: {len(all_topics_combined)}")
        print("🔀 Merging & ranking across all videos...")

        final_topics = merge_topics(all_topics_combined, total_duration_combined)

        playlist_id = parse_qs(urlparse(source).query).get("list", ["unknown"])[0]
        return {
            "type"                     : "playlist",
            "playlist_id"              : playlist_id,
            "source"                   : source,
            "total_videos"             : len(videos),
            "videos_processed"         : processed,
            "videos_skipped"           : skipped,
            "total_duration_all_videos": total_duration_combined,
            "total_topics"             : len(final_topics),
            "topics"                   : final_topics
        }

    # ── Case 3: Single video URL ─────────────────────────────────────────────
    print(f"\n🎬 Single video detected!")
    video_id, chunks = get_chunks_from_url(source)
    total_duration = max(c["end_time"] for c in chunks) if chunks else 1.0
    print(f"⏱  Duration: {int(total_duration//60)}m {int(total_duration%60)}s")
    print("🧠 Extracting topics...")

    all_topics = []
    for i, chunk in enumerate(chunks, 1):
        all_topics.extend(extract_topics_from_chunk(client, chunk, i, len(chunks)))

    print(f"\n📊 Raw topics: {len(all_topics)}")
    print("🔀 Merging & ranking...")
    final_topics = merge_topics(all_topics, total_duration)

    return {
        "type"                : "single",
        "video_id"            : video_id,
        "source"              : source,
        "total_video_duration": total_duration,
        "total_topics"        : len(final_topics),
        "topics"              : final_topics
    }


def display_results(result: dict):
    print("\n" + "=" * 65)
    if result["type"] == "playlist":
        print(f"  PLAYLIST : {result['source']}")
        print(f"  VIDEOS   : {result['videos_processed']} processed, {result['videos_skipped']} skipped")
    else:
        print(f"  VIDEO    : {result['source']}")
    print(f"  TOPICS   : {result['total_topics']}")
    print("=" * 65)

    for t in result["topics"]:
        w = int(t["weightage"])
        bar = "█" * w + "░" * (10 - w)
        print(f"\n📌 [{t['topic_key']}]  {t['topic_label']}")
        print(f"   Weightage : [{bar}] {t['weightage']}/10")
        print(f"   Challenges:")
        for q in t["questions"]:
            print(f"     • {q}")

    print("\n" + "=" * 65)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        source = sys.argv[1]
    else:
        source = input("Paste YouTube video URL, playlist URL, or transcript JSON path: ").strip()

    try:
        result = run_pipeline(source)
        display_results(result)

        # Output filename based on type
        if result["type"] == "playlist":
            out_file = f"topics_playlist_{result['playlist_id']}.json"
        else:
            out_file = f"topics_{result['video_id']}.json"

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Results saved → {out_file}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)