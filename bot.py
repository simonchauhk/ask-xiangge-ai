import os
import json
import logging
import threading
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

knowledge_text = ""
try:
    with open("knowledge_shengshi.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        parts = []
        for item in data:
            if isinstance(item, dict):
                parts.append(item.get("text", "") or item.get("content", "") or str(item))
            else:
                parts.append(str(item))
        knowledge_text = "\n\n".join(parts)
    elif isinstance(data, dict):
        if "chunks" in data:
            parts = []
            for item in data["chunks"]:
                if isinstance(item, dict):
                    parts.append(item.get("text", "") or item.get("content", "") or str(item))
                else:
                    parts.append(str(item))
            knowledge_text = "\n\n".join(parts)
        else:
            knowledge_text = json.dumps(data, ensure_ascii=False)
    log.info(f"Knowledge loaded, {len(knowledge_text)} chars")
except Exception as e:
    log.error(f"Failed to load knowledge: {e}")
    knowledge_text = ""

gemini_client = None
if GEMINI_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_KEY)
        log.info("Gemini client created")
    except Exception as e:
        log.error(f"Gemini client failed: {e}")
else:
    log.error("GEMINI_API_KEY not found!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
client = discord.Client(intents=intents)

SYSTEM_PROMPT = "You are Dr Chow Siu Cheung, a raw food teacher. Reply in Cantonese, friendly, like Dr Chow in his book. Only use the provided book knowledge. If book has no relevant content, say so honestly. Use Traditional Chinese, around 400 words."

@client.event
async def on_ready():
    log.info(f"Logged in as {client.user} - READY - in {len(client.guilds)} guilds")
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
    log.info(f"Received mention from {message.author} in #{message.channel}: {message.content[:100]}")
    question = message.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if not question:
        question = "你好"
    log.info(f"Question: {question}")
    try:
        async with message.channel.typing():
            if not gemini_client:
                await message.reply("祥哥而家連唔到個腦 (Gemini Key 未設定)，等陣再試下啦。")
                return
            if not knowledge_text:
                await message.reply("祥哥而家搵唔到本書 (knowledge_shengshi.json 讀唔到)，等陣再試下啦。")
                return
            kb = knowledge_text[:12000]
            prompt = SYSTEM_PROMPT + "\n\n[Book Knowledge]\n" + kb + "\n\n[Question]\n" + question + "\n\nPlease reply in Cantonese Traditional Chinese, as Dr Chow:"
            log.info("Calling Gemini...")
            answer = ""
            try:
                resp = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                answer = resp.text if hasattr(resp, "text") and resp.text else ""
            except Exception as e_gemini:
                log.error(f"Gemini 2.5 failed: {e_gemini}")
                try:
                    resp = gemini_client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
                    answer = resp.text if hasattr(resp, "text") and resp.text else ""
                except Exception as e2:
                    log.error(f"Gemini fallback failed: {e2}")
                    raise e2
            if not answer or not answer.strip():
                log.error("Gemini returned empty")
                await message.reply("祥哥諗咗陣，但係諗唔到點答，書入面好似冇提呢樣，你試下問第二樣啦。")
                return
            log.info(f"Gemini answered, {len(answer)} chars")
            answer = answer.strip()
            chunks = [answer[i:i+1900] for i in range(0, len(answer), 1900)]
            await message.reply(chunks[0])
            for extra in chunks[1:]:
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
        log.error("DISCORD_TOKEN not found! Check Render Env - must be exactly DISCORD_TOKEN")
    else:
        log.info("Starting bot...")
        client.run(DISCORD_TOKEN, reconnect=True)
