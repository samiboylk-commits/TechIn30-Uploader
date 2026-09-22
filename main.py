import os
import sys
import time
import asyncio
import datetime
import re
import feedparser
import requests
import edge_tts
import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from google import genai
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip
)
from moviepy.video.VideoClip import VideoClip
from moviepy.audio.AudioClip import AudioClip
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# --- 1. CONFIGURATION ---
RSS_FEEDS = [
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://feeds.arstechnica.com/arstechnica/index"
]

HISTORY_FILE = "last_tech_news.txt"
DAILY_LIMIT = 4
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- 2. CHECK HISTORY & DAILY LIMIT ---
seen_links = set()
today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
today_uploads = 0

if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split("|")
            seen_links.add(parts[0])
            if len(parts) > 1 and parts[1] == today_str:
                today_uploads += 1

if today_uploads >= DAILY_LIMIT:
    print(f"Daily limit reached ({today_uploads}/{DAILY_LIMIT}). Exiting.")
    sys.exit(0)

# --- 3. FETCH RSS FEEDS & GET LATEST TECH STORY ---
print("Fetching Tech & AI RSS Feeds...")
all_entries = []

for feed_url in RSS_FEEDS:
    try:
        f = feedparser.parse(feed_url)
        all_entries.extend(f.entries)
    except Exception as e:
        print(f"Error fetching {feed_url}: {e}")

all_entries.sort(
    key=lambda x: x.get("published_parsed") or x.get("updated_parsed") or time.gmtime(0),
    reverse=True
)

selected_entry = None
primary_image = None

for entry in all_entries:
    entry_id = entry.get("id") or entry.get("link")
    if entry_id in seen_links:
        continue

    found_img = None
    if "media_thumbnail" in entry and len(entry.media_thumbnail) > 0:
        found_img = entry.media_thumbnail[0]["url"]
    elif "enclosures" in entry:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image/"):
                found_img = enc.get("url") or enc.get("href")
                break
    elif "media_content" in entry and len(entry.media_content) > 0:
        found_img = entry.media_content[0].get("url")

    if not found_img:
        desc = entry.get("summary", "") or entry.get("description", "")
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
        if img_match:
            found_img = img_match.group(1)

    if found_img:
        selected_entry = entry
        primary_image = found_img
        break

if not selected_entry:
    print("No fresh tech news found with image. Exiting.")
    sys.exit(0)

raw_title = selected_entry.title
summary = re.sub(r'<[^>]+>', '', selected_entry.get("summary", ""))[:400]
target_id = selected_entry.get("id") or selected_entry.get("link")
print(f"Selected Tech Story: {raw_title}")

# --- 4. GENERATE SCRIPT & TITLE VIA GEMINI ---
script_text = None
viral_title = None

try:
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt_script = f"""
Write a suspenseful, fast-paced 15-20 second YouTube Shorts news script.
Hook viewers intensely in the first sentence.
End with a fast call-to-action: "Follow for instant daily updates!"
Headline: {raw_title}
Context: {summary}
Output spoken words only. Word count strictly between 45 and 55 words. No labels, markdown, emojis, or sound notes.
"""
    res_script = client.models.generate_content(model="gemini-2.5-flash", contents=prompt_script)
    if res_script.text:
        script_text = res_script.text.strip().replace("*", "").replace("\n", " ")

    prompt_title = f"""
Convert this news headline into an ultra-catchy, urgent YouTube Shorts click title under 50 characters. 
Use 1 fitting emoji.
Headline: {raw_title}
Output only the title.
"""
    res_title = client.models.generate_content(model="gemini-2.5-flash", contents=prompt_title)
    if res_title.text:
        viral_title = res_title.text.strip().replace('"', '').replace('\n', '')

except Exception as e:
    print(f"AI generation bypassed: {e}")

if not script_text:
    script_text = f"Tech breakthrough. {raw_title}. {summary}. Follow for instant daily updates!"

if not viral_title or len(viral_title) > 65:
    viral_title = f"{raw_title[:48]}..."

print(f"Script: {script_text}")
print(f"Title: {viral_title}")

# --- 5. EDGE TTS (VOICE GENERATION) ---
async def generate_voice(text, output_file):
    communicate = edge_tts.Communicate(text, voice="en-US-ChristopherNeural", rate="+15%")
    await communicate.save(output_file)

asyncio.run(generate_voice(script_text, "voice.mp3"))
voice_audio = AudioFileClip("voice.mp3")
total_duration = voice_audio.duration + 0.8

# --- 6. SMART SUBTITLE CHUNKS ---
def generate_smart_chunks(text, max_words=2, max_chars=16):
    raw_words = text.split()
    chunks = []
    current_chunk = []
    for w in raw_words:
        clean_w = re.sub(r'[^\w\s]', '', w).upper()
        if not clean_w:
            continue
        test_chunk = current_chunk + [clean_w]
        has_break = any(char in w for char in ['.', '?', '!'])
        if len(test_chunk) > max_words or sum(len(x) for x in test_chunk) + len(test_chunk) - 1 > max_chars:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            current_chunk = [clean_w]
        else:
            current_chunk.append(clean_w)
        if has_break:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

chunks = generate_smart_chunks(script_text, max_words=2, max_chars=16)
chunk_duration = voice_audio.duration / max(len(chunks), 1)

# --- 7. IMAGE PREPARATION & FONTS ---
headers = {'User-Agent': 'Mozilla/5.0'}
r1 = requests.get(primary_image, headers=headers, timeout=15)
with open("raw.jpg", "wb") as f:
    f.write(r1.content)

raw_im = Image.open("raw.jpg").convert("RGB")

# Realistic Blurred 9:16 background
bg_scale = max(1080 / raw_im.width, 1920 / raw_im.height)
bg_sz = (int(raw_im.width * bg_scale), int(raw_im.height * bg_scale))
bg_base = raw_im.resize(bg_sz, Image.Resampling.BILINEAR)
l = (bg_base.width - 1080) // 2
t = (bg_base.height - 1920) // 2
bg_base = bg_base.crop((l, t, l + 1080, t + 1920)).filter(ImageFilter.GaussianBlur(radius=35))

def load_font(size):
    for f in ["DejaVuSans-Bold.ttf", "FreeSansBold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(f, size)
        except:
            pass
    return ImageFont.load_default()

font_badge = load_font(34)
font_title = load_font(44)
font_caption_lg = load_font(48)
font_caption_md = load_font(42)
font_caption_sm = load_font(36)

def wrap_title(text, max_chars=30):
    lines, cur = [], []
    for w in text.split():
        if sum(len(x) for x in cur) + len(cur) + len(w) <= max_chars:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines

title_lines = wrap_title(raw_title, max_chars=30)[:3]

# --- 8. EXACT PHOTO-MATCH FRAME RENDERING ---
fg_w = 1000
fg_h = int(fg_w * (raw_im.height / raw_im.width))

def make_clean_frame(t):
    frame = bg_base.copy()
    draw = ImageDraw.Draw(frame)

    # Ken Burns Zoom & Sway
    progress = t / total_duration
    zoom = 1.0 + 0.08 * progress
    sway = int(np.sin(progress * np.pi) * 15)

    scaled_w = int(fg_w * zoom)
    scaled_h = int(fg_h * zoom)
    scaled_img = raw_im.resize((scaled_w, scaled_h), Image.Resampling.BILINEAR)

    crop_x = max(0, min(scaled_w - fg_w, (scaled_w - fg_w) // 2 + sway))
    crop_y = max(0, min(scaled_h - fg_h, (scaled_h - fg_h) // 2))
    fg_cropped = scaled_img.crop((crop_x, crop_y, crop_x + fg_w, crop_y + min(fg_h, scaled_h - crop_y)))

    pos_x = (1080 - fg_w) // 2
    pos_y = 580
    frame.paste(fg_cropped, (pos_x, pos_y))
    # Crisp Clean White Border
    draw.rectangle([pos_x - 3, pos_y - 3, pos_x + fg_w + 3, pos_y + fg_cropped.height + 3], outline=(255, 255, 255), width=3)

    # 1. Clean Red Alert Badge (Top Left)
    draw.rectangle([pos_x, 210, pos_x + 400, 275], fill=(225, 20, 55))
    draw.text((pos_x + 20, 222), "⚡ BREAKING NEWS", font=font_badge, fill=(255, 255, 255))

    # 2. Clean Drop-Shadowed Headline
    line_y = 295
    for line in title_lines:
        draw.text((pos_x + 3, line_y + 3), line, font=font_title, fill=(0, 0, 0))
        draw.text((pos_x, line_y), line, font=font_title, fill=(255, 255, 255))
        line_y += 55

    # 3. Exactly Centered Yellow-on-Black Subtitle Box
    chunk_idx = min(int(t / chunk_duration), len(chunks) - 1)
    current_caption = chunks[chunk_idx]

    if len(current_caption) > 14:
        chosen_font = font_caption_sm
    elif len(current_caption) > 10:
        chosen_font = font_caption_md
    else:
        chosen_font = font_caption_lg

    try:
        bbox = draw.textbbox((0, 0), current_caption, font=chosen_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_w = len(current_caption) * 24
        text_h = 45

    box_w = min(960, max(text_w + 60, 260))
    box_h = max(text_h + 34, 80)
    cap_x1 = 540 - (box_w // 2)
    cap_x2 = 540 + (box_w // 2)
    cap_box_y = min(1680, pos_y + fg_cropped.height + 80)

    # Black Rounded Box with Golden Yellow Outline
    draw.rounded_rectangle([cap_x1, cap_box_y, cap_x2, cap_box_y + box_h], radius=16, fill=(0, 0, 0), outline=(255, 215, 0), width=3)
    draw.text((540, cap_box_y + (box_h // 2)), current_caption, font=chosen_font, fill=(255, 230, 0), anchor="mm")

    # 4. Red Progress Bar
    bar_width = int(1080 * progress)
    draw.rectangle([0, 1912, 1080, 1920], fill=(40, 40, 40))
    draw.rectangle([0, 1912, bar_width, 1920], fill=(225, 20, 55))

    return np.array(frame)

animated_video = VideoClip(make_clean_frame, duration=total_duration)

# --- 9. SFX & AUDIO COMPOSITION ---
def news_sound_effect(t):
    pulse = 0.04 * np.sin(2 * np.pi * 90 * t)
    tick = 0.03 * np.sin(2 * np.pi * 1200 * t) * np.exp(-50 * (t % 0.5))
    mono = pulse + tick
    return np.column_stack((mono, mono))

sfx_audio = AudioClip(news_sound_effect, duration=total_duration)
final_audio = CompositeAudioClip([voice_audio, sfx_audio]).set_duration(total_duration)
animated_video = animated_video.set_audio(final_audio)

# --- 10. RENDERING ---
print("Rendering Clean News Short...")
animated_video.write_videofile(
    "final_shorts.mp4",
    fps=30,
    codec="libx264",
    audio_codec="aac",
    bitrate="5000k",
    preset="ultrafast",
    threads=4,
    logger=None
)

# --- 11. YOUTUBE UPLOAD ---
print("Uploading to YouTube...")
creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/youtube.upload"])
youtube = build("youtube", "v3", credentials=creds)

upload_title = f"{viral_title} | Tech In 30 #Shorts"
body = {
    "snippet": {
        "title": upload_title,
        "description": f"{script_text}\n\nStay tuned for instant tech updates.\n#Shorts #Tech #Technology #Trending",
        "tags": ["Shorts", "Tech", "Technology", "Trending"],
        "categoryId": "28"
    },
    "status": {
        "privacyStatus": "public",
        "selfDeclaredMadeForKids": False
    }
}

media = MediaFileUpload("final_shorts.mp4", chunksize=-1, resumable=True, mimetype="video/mp4")
req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
res = req.execute()
print(f"Uploaded Successfully! Video ID: {res.get('id')}")

# --- 12. UPDATE LOG ---
with open(HISTORY_FILE, "a", encoding="utf-8") as f:
    f.write(f"{target_id}|{today_str}\n")
print(f"Saved {target_id} to {HISTORY_FILE}")
