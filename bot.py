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

def find_relevant_chunks(question, top_n=3):
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
        top = scored[:2]
    top_sorted = sorted(top, key=lambda x: x[1])
    result = "\n\n".join([c for _, _, c in top_sorted])
    log.info(f"Relevant search top scores={[s[0] for s in top_sorted]}")
    return result

def call_gemini_rest(prompt, model):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 800}
    }
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    log.info(f"Gemini REST {model} status={r.status_code}")
    if r.status_code != 200:
        raise Exception(f"Gemini {model} {r.status_code}: {r.text[:600]}")
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        log.error(f"Gemini parse failed: {data}")
        raise Exception("Gemini empty response")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
client = discord.Client(intents=intents)

# V8 - HK Legal Guardrails + 1-on-1
SYSTEM_PROMPT = (
    "You are '問祥哥AI', an AI assistant sharing Dr Chow Siu Cheung's raw-food ideas. "
    "You are NOT Dr Chow himself, NOT a doctor, NOT a medical professional. "
    "You are answering ONE member 1-on-1 in Discord. Always use '你', never '各位/大家/朋友們/你們'. "
    "Do NOT start with '各位朋友好' or '大家好'. Start with answer or '你問得好好'. "
    "HK LAW - MUST FOLLOW, NO EXCEPTIONS: "
    "1. NEVER diagnose, NEVER prescribe, NEVER say you can cure/treat a disease. NEVER use '醫治, 醫好, 治療癌症, 斷症, 處方'. "
    "2. NEVER say '不要看西醫, 不要吃西藥, 停藥, 西醫害人'. NEVER discourage seeing a registered doctor or taking prescribed medicine. "
    "3. NEVER claim any food can cure cancer, diabetes, hypertension, heart disease, kidney disease or any disease. "
    "4. NEVER give personal medical advice for the user's own disease. Only share general book information. "
    "5. For pregnant women, children, elderly, chronic disease patients: must say '轉食生前請先問註冊醫生'. "
    "6. If user asks about a disease, say '這是一般食生理念分享, 不是醫療建議, 不能代替註冊醫生診斷和治療'. "
    "Use Traditional Chinese, Cantonese, friendly like Dr Chow talking to one student. "
    "Only use provided book knowledge. If book has no relevant content, say so honestly. "
    "Around 300 words, short and concrete."
)

DISCLAIMER = "\n\n---\n【免責聲明】以上係食生理念分享，唔係醫療建議，唔可以代替註冊醫生診斷同治療。有身體問題請睇註冊醫生，唔好自行停藥或停治療。我係問祥哥AI，唔係祥哥本人，亦唔係醫生。"

# Dangerous phrases -> safe replacements (post-filter)
DANGEROUS_REPLACEMENTS = [
    ("醫治癌症", "食生理念分享（唔係治療）"),
    ("醫好癌症", "食生理念分享（唔係治療）"),
    ("治療癌症", "食生理念分享（唔係治療）"),
    ("可以醫癌", "唔可以醫癌，呢個只係食生資訊分享"),
    ("唔好睇西醫", "要繼續睇註冊醫生，同醫生商量"),
    ("不要看西醫", "要繼續睇註冊醫生，同醫生商量"),
    ("唔好食西藥", "唔好自行停西藥，要同醫生商量"),
    ("不要吃西藥", "唔好自行停西藥，要同醫生商量"),
    ("停藥", "唔好自行停藥，要同醫生商量"),
    ("西醫害人", "西醫同食生係唔同角度，睇醫生好重要"),
    ("我幫你斷症", "我唔會幫你斷症，呢個要註冊醫生先做到"),
    ("你係癌症", "我唔會幫你斷症，呢個要註冊醫生先做到"),
    ("各位朋友好", "你問得好好"),
    ("各位朋友", "你"),
    ("各位", "你"),
    ("大家好", "你好"),
    ("大家", "你"),
    ("朋友們", "你"),
    ("你們", "你"),
]

def apply_safety_filter(text):
    for bad, good in DANGEROUS_REPLACEMENTS:
        text = text.replace(bad, good)
    return text

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
            kb = find_relevant_chunks(question, top_n=3)
            if not kb:
                kb = "\n\n".join(chunks[:2]) if chunks else ""
            prompt = SYSTEM_PROMPT + "\n\n[Book Knowledge]\n" + kb + "\n\n[Question]\n" + question + "\n\nPlease reply in Cantonese Traditional Chinese, 1-on-1 to this one user, following HK law guardrails:"
            log.info(f"Prompt length {len(prompt)} chars, calling Gemini via REST...")
            answer = ""
            last_err = None
            for model in ["gemini-3.5-flash-lite", "gemini-2.0-flash", "gemini-3.8-flash"]:
                for attempt in range(2):
                    try:
                        answer = await asyncio.to_thread(call_gemini_rest, prompt, model)
                        if answer and answer.strip():
                            log.info(f"Gemini {model} answered, {len(answer)} chars")
                            break
                    except Exception as e_g:
                        last_err = e_g
                        log.error(f"Gemini {model} attempt {attempt+1} failed: {e_g}")
                        if "404" in str(e_g) or "NOT_FOUND" in str(e_g):
                            break
                        await asyncio.sleep(2)
                        continue
                if answer and answer.strip():
                    break
            if not answer or not answer.strip():
                log.error("All Gemini models failed")
                await message.reply(f"祥哥諗緊嘢嗰陣出咗少少問題：{last_err}，你等陣再問多次啦。")
                return
            answer = answer.strip()
            # Apply HK legal safety filter
            answer = apply_safety_filter(answer)
            footer = f"\n\n---\n[綠芽同修 | 本月已用 {used}/10條, 剩 {remaining}條]"
            full = answer + footer + DISCLAIMER
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
