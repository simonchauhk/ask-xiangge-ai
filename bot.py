import os
import json
import logging
import threading
import asyncio
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from google import genai

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s')
log = logging.getLogger("xiangge")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Xiangge AI is running")
    def log_message(self, format, *args):
        return

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except Exception as e:
        log.error(f"Keep-alive failed: {e}")

threading.Thread(target=keep_alive, daemon=True).start()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# Load knowledge as chunks for smart search
chunks = []
knowledge_text = ""
try:
    with open("knowledge_shengshi.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                t = item.get("text", "") or item.get("content", "") or str(item)
            else:
                t = str(item)
            if t.strip():
                chunks.append(t)
    elif isinstance(data, dict):
        if "chunks" in data:
            for item in data["chunks"]:
                if isinstance(item, dict):
                    t = item.get("text", "") or item.get("content", "") or str(item)
                else:
                    t = str(item)
                if t.strip():
                    chunks.append(t)
        else:
            knowledge_text = json.dumps(data, ensure_ascii=False)
            chunks = [knowledge_text]
    if not chunks and knowledge_text:
        chunks = [knowledge_text]
    knowledge_text = "\n\n".join(chunks)
    log.info(f"Knowledge loaded, {len(chunks)} chunks, {len(knowledge_text)} chars")
except Exception as e:
    log.error(f"Failed to load knowledge: {e}")
    chunks = []

def find_relevant_chunks(question, top_n=8):
    if not chunks:
        return ""
    # Extract keywords: Chinese chars 2+ and also split question
    # Simple scoring: count overlapping chars / keywords
    q_chars = set([c for c in question if ord(c) > 127])
    # Also extract 2-char words
    q_words = re.findall(r"[\u4e00-\u9fff]{2,4}", question)
    scored = []
    for i, c in enumerate(chunks):
        score = 0
        for w in q_words:
            if w in c:
                score += len(w) * 3
        # char overlap bonus
        for ch in q_chars:
            if ch in c:
                score += 1
        # boost if chunk contains key terms
        for kw in ["寒", "涼", "體質", "寒涼", "食生", "生冷", "陽氣"]:
            if kw in question and kw in c:
                score += 10
        scored.append((score, i, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Take top chunks, but if all scores 0, take first 3
    top = [s for s in scored if s[0] > 0][:top_n]
    if not top:
        top = scored[:3]
    # Keep original order for coherence
    top_sorted = sorted(top, key=lambda x: x[1])
    result = "\n\n".join([c for _, _, c in top_sorted])
    log.info(f"Relevant search: question={question[:30]}, top scores={[s[0] for s in top_sorted]}")
    return result

gemini_client = None
if GEMINI_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_KEY)
        log.info("Gemini client created")
    except Exception as e:
        log.error(f"Gemini client failed: {e}")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
client = discord.Client(intents=intents)

SYSTEM_PROMPT = "You are Dr Chow Siu Cheung, raw food teacher. Reply in Cantonese, friendly, like Dr Chow in his book. Only use the provided book knowledge. If book has no relevant content, say so honestly. Use Traditional Chinese, around 400 words."

# Simple in-memory usage counter for test - will be replaced by Patreon tier logic later
usage = {}

@client.event
async def on_ready():
    log.info(f"Logged in as {client.user} - READY")
    try:
        await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="member questions"))
    except Exception as e:
        log.error(f"change_presence failed: {e}")

@client.event
async def on_disconnect():
    log.warning("DISCONNECTED from Discord Gateway!")

@client.event
async def on_resumed():
    log.info("RESUMED Discord Gateway connection")

@client.event
async def on_message(message):
    if message.author.bot:
        return
    if client.user not in message.mentions:
        return
    log.info(f"Received mention from {message.author}: {message.content[:100]}")
    question = message.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if not question:
        question = "你好"
    # Usage counting (test logic: 10 per user per month, keep as before)
    uid = str(message.author.id)
    used = usage.get(uid, 0) + 1
    usage[uid] = used
    remaining = max(0, 10 - used)
    try:
        async with message.channel.typing():
            if not gemini_client:
                await message.reply("祥哥而家連唔到個腦 (Gemini Key 未設定)，等陣再試下啦。")
                return
            kb = find_relevant_chunks(question, top_n=8)
            if not kb:
                kb = knowledge_text[:8000]
            prompt = SYSTEM_PROMPT + "\n\n[Book Knowledge - relevant parts]\n" + kb + "\n\n[Question]\n" + question + "\n\nPlease reply in Cantonese Traditional Chinese, as Dr Chow:"
            log.info("Calling Gemini...")
            answer = ""
            last_err = None
            # Retry 3 times for ClientError / network hiccups
            for attempt in range(3):
                try:
                    # Create fresh client each attempt to avoid stale connection ClientError
                    fresh = genai.Client(api_key=GEMINI_KEY)
                    resp = fresh.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                    answer = resp.text if hasattr(resp, "text") and resp.text else ""
                    if answer and answer.strip():
                        break
                    log.warning(f"Attempt {attempt+1} empty answer, retrying")
                except Exception as e_gemini:
                    last_err = e_gemini
                    log.error(f"Gemini attempt {attempt+1} failed: {type(e_gemini).__name__}: {e_gemini}")
                    if attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    # Fallback model on last attempt
                    try:
                        log.info("Trying fallback gemini-2.0-flash")
                        fresh2 = genai.Client(api_key=GEMINI_KEY)
                        resp2 = fresh2.models.generate_content(model="gemini-2.0-flash", contents=prompt)
                        answer = resp2.text if hasattr(resp2, "text") and resp2.text else ""
                    except Exception as e2:
                        log.error(f"Fallback also failed: {e2}")
                        raise e2
            if not answer or not answer.strip():
                log.error("Gemini returned empty after retries")
                await message.reply("祥哥諗咗陣，但係諗唔到點答，書入面好似冇提呢樣，你試下問第二樣啦。")
                return
            log.info(f"Gemini answered, {len(answer)} chars")
            answer = answer.strip()
            # Add usage footer like before
            footer = f"\n\n---\n[綠芽同修 | 本月已用 {used}/10條, 剩 {remaining}條]"
            full = answer + footer
            chunks_out = [full[i:i+1900] for i in range(0, len(full), 1900)]
            await message.reply(chunks_out[0])
            for extra in chunks_out[1:]:
                await message.channel.send(extra)
            log.info("Reply sent successfully")
    except Exception as e:
        log.error(f"on_message ERROR: {type(e).__name__}: {e}", exc_info=True)
        try:
            await message.reply(f"祥哥諗緊嘢嗰陣出咗少少問題：{type(e).__name__}，你等陣再問多次啦。")
        except Exception as e2:
            log.error(f"Failed to send error reply: {e2}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN not found!")
    else:
        log.info("Starting bot...")
        client.run(DISCORD_TOKEN, reconnect=True)
