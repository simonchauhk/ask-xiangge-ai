import os
import json
import logging
import threading
import asyncio
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord

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

chunks = []
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
    log.info(f"Knowledge loaded, {len(chunks)} chunks")
except Exception as e:
    log.error(f"Failed to load knowledge: {e}")
    chunks = []

def find_relevant_chunks(question, top_n=5):
    if not chunks:
        return ""
    q_words = re.findall(r"[\u4e00-\u9fff]{2,4}", question)
    q_chars = set([c for c in question if ord(c) > 127])
    scored = []
    for i, c in enumerate(chunks):
        score = 0
        for w in q_words:
            if w in c:
                score += len(w) * 3
        for ch in q_chars:
            if ch in c:
                score += 1
        for kw in ["寒", "涼", "體質", "寒涼", "食生", "陽氣"]:
            if kw in question and kw in c:
                score += 10
        scored.append((score, i, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [s for s in scored if s[0] > 0][:top_n]
    if not top:
        top = scored[:3]
    top_sorted = sorted(top, key=lambda x: x[1])
    result = "\n\n".join([c for _, _, c in top_sorted])
    log.info(f"Relevant search top scores={[s[0] for s in top_sorted]}")
    return result

def call_gemini_rest(prompt, model="gemini-2.0-flash"):
    # Direct REST API - no google-genai library, avoids ClientError bug
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    log.info(f"Gemini REST {model} status={r.status_code}")
    if r.status_code != 200:
        raise Exception(f"Gemini {model} {r.status_code}: {r.text[:500]}")
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        log.error(f"Gemini parse failed: {data}")
        raise Exception(f"Gemini empty response: {data}")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
client = discord.Client(intents=intents)

SYSTEM_PROMPT = "You are Dr Chow Siu Cheung, raw food teacher. Reply in Cantonese, friendly, like Dr Chow. Only use the provided book knowledge. If book has no relevant content, say so honestly. Use Traditional Chinese, around 400 words. Do NOT make up medical advice beyond the book."

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
    uid = str(message.author.id)
    used = usage.get(uid, 0) + 1
    usage[uid] = used
    remaining = max(0, 10 - used)
    try:
        async with message.channel.typing():
            if not GEMINI_KEY:
                await message.reply("祥哥而家連唔到個腦 (Gemini Key 未設定)，等陣再試下啦。")
                return
            kb = find_relevant_chunks(question, top_n=5)
            if not kb:
                kb = "\n\n".join(chunks[:3]) if chunks else ""
            prompt = SYSTEM_PROMPT + "\n\n[Book Knowledge]\n" + kb + "\n\n[Question]\n" + question + "\n\nPlease reply in Cantonese Traditional Chinese, as Dr Chow:"
            log.info("Calling Gemini via REST...")
            answer = ""
            last_err = None
            # Try 2.0-flash first (stable via REST), then 2.5-flash, then 3.8-flash
            for model in ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-3.8-flash"]:
                for attempt in range(2):
                    try:
                        # Run sync requests in thread so it does not block Discord loop
                        answer = await asyncio.to_thread(call_gemini_rest, prompt, model)
                        if answer and answer.strip():
                            log.info(f"Gemini {model} answered, {len(answer)} chars")
                            break
                    except Exception as e_g:
                        last_err = e_g
                        log.error(f"Gemini {model} attempt {attempt+1} failed: {e_g}")
                        await asyncio.sleep(1.5)
                        continue
                if answer and answer.strip():
                    break
            if not answer or not answer.strip():
                log.error("All Gemini models failed")
                await message.reply(f"祥哥諗緊嘢嗰陣出咗少少問題：{last_err}，你等陣再問多次啦。")
                return
            answer = answer.strip()
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
