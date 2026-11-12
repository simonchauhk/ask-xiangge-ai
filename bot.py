import os
import json
import logging
import threading
import asyncio
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from datetime import datetime

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

CONSENT_FILE = "consent.json"
consented = {}
try:
    if os.path.exists(CONSENT_FILE):
        with open(CONSENT_FILE, "r", encoding="utf-8") as f:
            consented = json.load(f)
        log.info(f"Loaded {len(consented)} consented users")
except Exception as e:
    log.error(f"Load consent failed: {e}")
    consented = {}

def save_consent():
    try:
        with open(CONSENT_FILE, "w", encoding="utf-8") as f:
            json.dump(consented, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Save consent failed: {e}")

pending_questions = {}

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
        "generationConfig": {"temperature": 0.85, "maxOutputTokens": 1500}
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
intents.reactions = True
client = discord.Client(intents=intents)

# V12 - Fixed name: 周兆祥博士, never 周少祥, never 教授
SYSTEM_PROMPT = (
    "You are '問祥哥AI', an AI assistant sharing Dr Chow Siu Cheung's raw-food ideas. "
    "Your name in Chinese is 周兆祥博士. NEVER write it as 周少祥, 周兆祥教授, 周教授, or any other variant. "
    "The character is 兆 (not 少), the title is 博士 (not 教授). "
    "If you mention the author, always write 周兆祥博士 exactly. "
    "You are NOT Dr Chow himself, NOT a doctor, NOT a medical professional. "
    "You are answering ONE member 1-on-1 in Discord. Always use '你', never '各位/大家/朋友們/你們'. "
    "Do NOT use '各位朋友好' or '大家好'. "
    "OPENING: Do NOT use a fixed opening. Do NOT start every answer with '你問得好好'. Vary naturally: sometimes start directly with answer, e.g. '其實呢...', sometimes '嗯，呢個問題好多人問，', sometimes '照書入面講...', sometimes no greeting, just the answer. Never repeat same opening twice. "
    "HK LAW: NEVER diagnose, NEVER prescribe, NEVER say you can cure/treat a disease. NEVER use '醫治, 醫好, 治療癌症, 斷症, 處方'. "
    "NEVER say '不要看西醫, 不要吃西藥, 停藥, 西醫害人'. NEVER discourage seeing a registered doctor. "
    "NEVER claim any food can cure cancer, diabetes, hypertension, heart disease, kidney disease. "
    "NEVER give personal medical advice. Only share general book information. "
    "For pregnant, children, elderly, chronic disease: must say '轉食生前請先問註冊醫生'. "
    "If user asks about disease, say '這是一般食生理念分享, 不是醫療建議, 不能代替註冊醫生診斷和治療'. "
    "MUST finish completely. Do NOT stop mid-sentence. "
    "Use Traditional Chinese, Cantonese, friendly like Dr Chow talking to one student. "
    "Only use provided book knowledge. If book has no relevant content, say so honestly. "
    "Around 300-400 words."
)

SHORT_DISCLAIMER = "\n\n【食生分享，非醫療建議】"

CONSENT_TEXT = (
    "收到你嘅問題。不過用之前，要你確認一次，唔使次次都寫咁長：\n\n"
    "**【問祥哥AI 使用同意】**\n"
    "1. 我係問祥哥AI，唔係周兆祥博士本人，亦唔係醫生，唔會幫你斷症、處方。\n"
    "2. 我講嘅係周兆祥博士食生理念分享，唔係醫療建議，唔可以代替註冊醫生診斷同治療。\n"
    "3. 有身體問題請睇註冊醫生，唔好自行停藥或停治療。孕婦、小朋友、長者、長期病患者，轉食生前請先問註冊醫生。\n\n"
    "如果你同意，打 **「同意」** 或者撳 ✅，我先會答你嗰條問題。同意一次就得，之後唔使再簽，每次我只會寫一句短嘅【食生分享，非醫療建議】。"
)

def apply_safety_filter(text):
    # V12: Fix name errors first - most important
    text = text.replace("周少祥", "周兆祥")
    text = text.replace("周兆祥教授", "周兆祥博士")
    text = text.replace("周教授", "周兆祥博士")
    text = text.replace("祥哥教授", "祥哥博士")
    # Fix duplicated phrases
    text = text.replace("唔好自行唔好自行停藥", "唔好自行停藥")
    text = text.replace("不要自行不要自行停藥", "不要自行停藥")
    text = text.replace("千萬不要自行唔好自行停藥", "千萬唔好自行停藥")
    text = text.replace("千萬不要自行停藥，要同醫生商量或拒絕正規醫療", "千萬唔好自行停藥，要同醫生商量，唔好拒絕正規醫療")
    replacements = [
        ("醫治癌症", "食生理念分享（唔係治療）"),
        ("醫好癌症", "食生理念分享（唔係治療）"),
        ("治療癌症", "食生理念分享（唔係治療）"),
        ("可以醫癌", "唔可以醫癌，呢個只係食生資訊分享"),
        ("唔好睇西醫", "要繼續睇註冊醫生，同醫生商量"),
        ("不要看西醫", "要繼續睇註冊醫生，同醫生商量"),
        ("西醫害人", "西醫同食生係唔同角度，睇醫生好重要"),
        ("我幫你斷症", "我唔會幫你斷症，呢個要註冊醫生先做到"),
        ("各位朋友好", "嗯"),
        ("各位朋友", "你"),
        ("各位", "你"),
        ("大家好", "你好"),
        ("大家", "你"),
        ("朋友們", "你"),
        ("你們", "你"),
    ]
    for bad, good in replacements:
        text = text.replace(bad, good)
    return text

usage = {}

async def answer_question(message, question, uid):
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
            answer = apply_safety_filter(answer)
            footer = f"\n\n---\n[綠芽同修 | 本月已用 {used}/10條, 剩 {remaining}條]"
            full = answer + footer + SHORT_DISCLAIMER
            chunks_out = [full[i:i+1900] for i in range(0, len(full), 1900)]
            await message.reply(chunks_out[0])
            for extra in chunks_out[1:]:
                await message.channel.send(extra)
            log.info("Reply sent successfully")
    except Exception as e:
        log.error(f"answer_question ERROR: {type(e).__name__}: {e}", exc_info=True)
        try:
            await message.reply(f"祥哥諗緊嘢嗰陣出咗少少問題：{type(e).__name__}，你等陣再問多次啦。")
        except Exception as e2:
            log.error(f"Failed to send error reply: {e2}")

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
    uid = str(message.author.id)
    content = message.content.strip()
    if content in ["同意", "同意！", "同意。", "agree", "Agree", "✅", "👍"] and uid in pending_questions:
        pending_q = pending_questions.pop(uid)
        consented[uid] = {"name": str(message.author), "time": datetime.now().isoformat(), "via": "discord-agree"}
        save_consent()
        log.info(f"User {message.author} consented, answering pending: {pending_q[:50]}")
        await message.reply("多謝你同意，已經記低咗，之後唔使再簽。以後每次我只會寫一句短嘅【食生分享，非醫療建議】。而家答返你頭先嗰條問題：")
        await answer_question(message, pending_q, uid)
        return
    if content in ["同意", "同意！", "同意。", "agree", "Agree", "✅"]:
        if uid not in consented:
            consented[uid] = {"name": str(message.author), "time": datetime.now().isoformat(), "via": "discord-agree"}
            save_consent()
            await message.reply("多謝你同意，已經記低咗，之後你直接 @我 問問題就得。")
        return
    if client.user not in message.mentions:
        return
    log.info(f"Received mention from {message.author}: {message.content[:100]}")
    question = message.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if not question:
        question = "你好"
    if uid not in consented:
        pending_questions[uid] = question
        log.info(f"User {message.author} not yet consented, asking for consent")
        sent = await message.reply(CONSENT_TEXT)
        try:
            await sent.add_reaction("✅")
        except Exception as e:
            log.error(f"Add reaction failed: {e}")
        return
    await answer_question(message, question, uid)

@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    if str(reaction.emoji) != "✅":
        return
    uid = str(user.id)
    if uid in pending_questions and uid not in consented:
        pending_q = pending_questions.pop(uid)
        consented[uid] = {"name": str(user), "time": datetime.now().isoformat(), "via": "discord-reaction"}
        save_consent()
        log.info(f"User {user} consented via reaction")
        try:
            await reaction.message.channel.send(f"<@{uid}> 多謝你同意，已經記低咗，而家答返你頭先嗰條問題：")
            await answer_question(reaction.message, pending_q, uid)
        except Exception as e:
            log.error(f"Reaction consent answer failed: {e}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN not found!")
    else:
        log.info("Starting bot...")
        client.run(DISCORD_TOKEN, reconnect=True)
