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
from PIL import Image, ImageFilter, ImageDraw, ImageFont, ImageEnhance
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
Write an intense, fast-paced 20-25 second YouTube Shorts script on this tech breakthrough.
Structure strictly:
- Bold hook in sentence 1 that makes the viewer stop scrolling.
- Direct facts/specs in 2 concise sentences.
- Outro: "Follow Tech In 30 for daily futuristic breakdowns!"
Headline: {raw_title}
Context: {summary}
Output spoken words only. Word count strictly between 45 and 55 words. Do not include markdown, emojis, or sound notes.
"""
    res_script = client.models.generate_content(model="gemini-2.5-flash", contents=prompt_script)
    if res_script.text:
        script_text = res_script.text.strip().replace("*", "").replace("\n", " ")

    prompt_title = f"""
Convert this tech headline into an ultra-viral YouTube Shorts title under 48 characters. Include 1 tech emoji (⚡ or 🤖).
Headline: {raw_title}
Output only the title.
"""
    res_title = client.models.generate_content(model="gemini-2.5-flash", contents=prompt_title)
    if res_title.text:
        viral_title = res_title.text.strip().replace('"', '').replace('\n', '')

except Exception as e:
    print(f"AI generation bypassed: {e}")

if not script_text:
    script_text = f"Major tech alert. {raw_title}. {summary}. Follow Tech In 30 for daily breakdowns!"

if not viral_title or len(viral_title) > 65:
    viral_title = f"{raw_title[:45]}... ⚡"

# --- 5. EDGE TTS (VOICE GENERATION) ---
async def generate_voice(text, output_file):
    communicate = edge_tts.Communicate(text, voice="en-US-ChristopherNeural", rate="+15%")
    await communicate.save(output_file)

asyncio.run(generate_voice(script_text, "voice.mp3"))
voice_audio = AudioFileClip("voice.mp3")
total_duration = voice_audio.duration + 0.6

# --- 6. 1-2 WORD RAPID VIRAL CAPTIONS ---
def generate_smart_chunks(text):
    raw_words = text.split()
    chunks = []
    current = []
    for w in raw_words:
        clean = re.sub(r'[^\w\s]', '', w).upper()
        if not clean:
            continue
        current.append(clean)
        if len(current) >= 2 or any(c in w for c in ['.', '!', '?']):
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks

chunks = generate_smart_chunks(script_text)
chunk_duration = voice_audio.duration / max(len(chunks), 1)

# --- 7. LOAD AND PROCESS ASSETS ---
headers = {'User-Agent': 'Mozilla/5.0'}
r1 = requests.get(primary_image, headers=headers, timeout=15)
with open("raw.jpg", "wb") as f:
    f.write(r1.content)

raw_im = Image.open("raw.jpg").convert("RGB")

# Boost color saturation for crisp futuristic look
enhancer = ImageEnhance.Color(raw_im)
raw_im = enhancer.enhance(1.2)

# Full vertical 9:16 background
bg_scale = max(1080 / raw_im.width, 1920 / raw_im.height)
bg_sz = (int(raw_im.width * bg_scale), int(raw_im.height * bg_scale))
bg_base = raw_im.resize(bg_sz, Image.Resampling.BILINEAR)
l = (bg_base.width - 1080) // 2
t = (bg_base.height - 1920) // 2
bg_base = bg_base.crop((l, t, l + 1080, t + 1920)).filter(ImageFilter.GaussianBlur(radius=55))

# Dark Tech Gradient Overlay
overlay_tint = Image.new("RGBA", (1080, 1920), (5, 9, 18, 205))
bg_base = Image.alpha_composite(bg_base.convert("RGBA"), overlay_tint).convert("RGB")

# Cyberpunk Subtle Grid
draw_bg = ImageDraw.Draw(bg_base)
for gy in range(0, 1920, 100):
    draw_bg.line([(0, gy), (1080, gy)], fill=(15, 30, 50), width=1)
for gx in range(0, 1080, 100):
    draw_bg.line([(gx, 0), (gx, 1920)], fill=(15, 30, 50), width=1)

def load_font(size):
    for f in ["DejaVuSans-Bold.ttf", "FreeSansBold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(f, size)
        except:
            pass
    return ImageFont.load_default()

font_hud = load_font(24)
font_title = load_font(42)
font_caption = load_font(68)

def wrap_title(text, max_chars=28):
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

title_lines = wrap_title(raw_title, max_chars=28)[:3]

# --- 8. ULTRA HIGH-TECH HUD FRAME RENDERING ---
card_w = 1000
card_h = 800
pos_x = (1080 - card_w) // 2
pos_y = 520

def make_tech_frame(t):
    frame = bg_base.copy()
    draw = ImageDraw.Draw(frame)
    progress = t / total_duration

    # Ken Burns Zoom & Dynamic Drift
    zoom = 1.0 + 0.08 * progress
    sway = int(np.sin(progress * np.pi) * 18)
    
    scaled_w = int(card_w * zoom)
    scaled_h = int((card_w * (raw_im.height / raw_im.width)) * zoom)
    scaled_img = raw_im.resize((scaled_w, max(scaled_h, card_h)), Image.Resampling.BILINEAR)

    crop_x = max(0, min(scaled_w - card_w, (scaled_w - card_w) // 2 + sway))
    crop_y = max(0, min(scaled_img.height - card_h, (scaled_img.height - card_h) // 2))
    fg_cropped = scaled_img.crop((crop_x, crop_y, crop_x + card_w, crop_y + card_h))

    # Mask for crisp rounded card
    mask = Image.new("L", (card_w, card_h), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle([0, 0, card_w, card_h], radius=24, fill=255)
    frame.paste(fg_cropped, (pos_x, pos_y), mask)

    # 1. Neon Glowing Outer Frame & Tech Brackets
    draw.rounded_rectangle([pos_x, pos_y, pos_x + card_w, pos_y + card_h], radius=24, outline=(0, 240, 255), width=3)
    
    b_len = 45
    neon = (0, 255, 220)
    # 4 Corner High-Tech Brackets
    draw.line([(pos_x - 6, pos_y - 6), (pos_x + b_len, pos_y - 6)], fill=neon, width=6)
    draw.line([(pos_x - 6, pos_y - 6), (pos_x - 6, pos_y + b_len)], fill=neon, width=6)

    draw.line([(pos_x + card_w + 6, pos_y - 6), (pos_x + card_w - b_len, pos_y - 6)], fill=neon, width=6)
    draw.line([(pos_x + card_w + 6, pos_y - 6), (pos_x + card_w + 6, pos_y + b_len)], fill=neon, width=6)

    draw.line([(pos_x - 6, pos_y + card_h + 6), (pos_x + b_len, pos_y + card_h + 6)], fill=neon, width=6)
    draw.line([(pos_x - 6, pos_y + card_h + 6), (pos_x - 6, pos_y + card_h - b_len)], fill=neon, width=6)

    draw.line([(pos_x + card_w + 6, pos_y + card_h + 6), (pos_x + card_w - b_len, pos_y + card_h + 6)], fill=neon, width=6)
    draw.line([(pos_x + card_w + 6, pos_y + card_h + 6), (pos_x + card_w + 6, pos_y + card_h - b_len)], fill=neon, width=6)

    # 2. Tech HUD Top Header
    blink = int(t * 3) % 2 == 0
    rec_col = (255, 40, 60) if blink else (140, 20, 30)
    draw.ellipse([pos_x, 165, pos_x + 16, 181], fill=rec_col)
    draw.text((pos_x + 26, 162), "LIVE INTEL // TECH IN 30", font=font_hud, fill=(0, 240, 255))
    draw.text((pos_x + card_w - 180, 162), "SYS_AI // v2.6", font=font_hud, fill=(120, 160, 200))

    # 3. Floating Modern Title Glass Card
    box_y = 210
    draw.rounded_rectangle([pos_x, box_y, pos_x + card_w, box_y + 240], radius=18, fill=(8, 14, 25), outline=(0, 180, 255), width=2)
    
    t_y = box_y + 28
    for line in title_lines:
        draw.text((pos_x + 32, t_y + 2), line, font=font_title, fill=(0, 0, 0))
        draw.text((pos_x + 30, t_y), line, font=font_title, fill=(255, 255, 255))
        t_y += 58

    # 4. Viral High-Retention Subtitles (Alex Hormozi Style)
    chunk_idx = min(int(t / chunk_duration), len(chunks) - 1)
    caption_text = chunks[chunk_idx]

    cap_y = 1440
    # Heavy Black Outline for maximum contrast
    for dx, dy in [(-4, -4), (-4, 4), (4, -4), (4, 4), (-5, 0), (5, 0), (0, -5), (0, 5)]:
        draw.text((540 + dx, cap_y + dy), caption_text, font=font_caption, fill=(0, 0, 0), anchor="mm")
    
    # Alternating High-Visibility Tech Colors
    cap_fill = (255, 230, 0) if (chunk_idx % 2 == 0) else (0, 240, 255)
    draw.text((540, cap_y), caption_text, font=font_caption, fill=cap_fill, anchor="mm")

    # 5. Neon Glowing Retention Bar
    bar_w = int(1080 * progress)
    draw.rectangle([0, 1910, 1080, 1920], fill=(10, 15, 25))
    draw.rectangle([0, 1910, bar_w, 1920], fill=(0, 240, 255))

    return np.array(frame)

animated_video = VideoClip(make_tech_frame, duration=total_duration)

# --- 9. AUDIO MIX (CYBER SUB IMPACT) ---
def tech_sound_effect(t):
    sub = 0.04 * np.sin(2 * np.pi * 60 * t)
    pulse = 0.02 * np.sin(2 * np.pi * 1800 * t) * np.exp(-40 * (t % 0.8))
    mono = sub + pulse
    return np.column_stack((mono, mono))

sfx_audio = AudioClip(tech_sound_effect, duration=total_duration)
final_audio = CompositeAudioClip([voice_audio, sfx_audio]).set_duration(total_duration)
animated_video = animated_video.set_audio(final_audio)

# --- 10. RENDERING ---
print("Rendering Cyber Tech Short...")
animated_video.write_videofile(
    "final_shorts.mp4",
    fps=30,
    codec="libx264",
    audio_codec="aac",
    bitrate="5500k",
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
        "description": f"{script_text}\n\nDaily 30-second breakdowns on the latest tech, AI, and futuristic gadgets.\n#Shorts #Tech #AI #Technology #FutureTech",
        "tags": ["Shorts", "Tech", "AI", "Technology", "FutureTech", "ArtificialIntelligence", "Gadgets"],
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

# --- 12. LOG HISTORY ---
with open(HISTORY_FILE, "a", encoding="utf-8") as f:
    f.write(f"{target_id}|{today_str}\n")
print(f"Saved {target_id} to {HISTORY_FILE}")
