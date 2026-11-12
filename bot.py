
# 問祥哥AI - Discord Bot (測試版, 只用《食生基本法》)
# 用法: 在 Railway / Render 設定環境變數 DISCORD_TOKEN, GEMINI_API_KEY, 然後跑 python bot.py
import os, json, re, datetime
from collections import defaultdict
import discord
from discord.ext import commands

# ===== 1. 你的設定 (已幫你填好, 唔好改 ID) =====
CHANNEL_ID = 1569585153406271568  # #問祥哥ai (會員專用)
ROLE_SEED = 1569600820335485028   # 🌱 種子同行者 $1 - 唔俾用
ROLE_SPROUT = 1569592074393616538 # 🌿 綠芽同修 $10 - 每月10條, 淺版
ROLE_FOREST = 1569600637283602523 # 🌳 森林知己 $100 - 每月100條, 深入版
PATREON_LINK = "https://www.patreon.com/周兆祥"  # 記得換返你真實 Patreon Link

LIMITS = {
    ROLE_SPROUT: {"monthly": 10, "max_chars": 400, "allow_followup": False, "label": "綠芽同修"},
    ROLE_FOREST: {"monthly": 100, "max_chars": 1000, "allow_followup": True, "label": "森林知己"},
}

# ===== 2. 讀知識庫 =====
with open("knowledge_shengshi.json", encoding="utf-8") as f:
    KNOWLEDGE = json.load(f)

def search_knowledge(question, top_n=4):
    # 簡單關鍵字檢索 (測試版夠用, 之後可升級做向量檢索)
    q_chars = set(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]+', question))
    # 用字重疊計分
    scored=[]
    for item in KNOWLEDGE:
        text=item["text"]
        score=sum(1 for ch in q_chars if ch in text)
        # 加埋詞語
        for w in re.findall(r'[\u4e00-\u9fff]{2,}', question):
            if w in text:
                score+=5
        scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:top_n] if x[0]>0]

# ===== 3. 計次數 (存在記憶體, 正式版建議用 Supabase / JSON file) =====
usage = defaultdict(lambda: {"month": "", "count": 0})
def check_limit(role_id, user_id):
    now = datetime.datetime.now()
    month_key = f"{now.year}-{now.month}"
    key = f"{user_id}-{role_id}"
    if usage[key]["month"] != month_key:
        usage[key] = {"month": month_key, "count": 0}
    limit = LIMITS[role_id]["monthly"]
    if usage[key]["count"] >= limit:
        return False, limit, usage[key]["count"]
    usage[key]["count"] += 1
    return True, limit, usage[key]["count"]

# ===== 4. 叫 AI 答 (用 Gemini, 唔使阿里雲) =====
async def ask_gemini(question, contexts, max_chars, deep):
    # 這裡用 google-generativeai, 你只需要在 Railway 設定 GEMINI_API_KEY
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-3.8-flash")
        ctx = "\n\n---\n\n".join([c["text"] for c in contexts])
        style = "深入版, 800-1000字, 要引用出處, 可以對比, 語氣似周兆祥, 親切, 鼓勵, 用廣東話書面語" if deep else "淺白版, 300-400字, 唔好太深入, 語氣似周兆祥"
        prompt = f"""你是周兆祥 (祥哥), 用以下《食生基本法》書中內容回答問題, 唔好作嘢, 唔好超出書中內容。
如果書中無講, 就老實講: 呢本書入面無講呢個, 等祥哥之後補充。
風格: {style}
字數上限: {max_chars}字

書中內容:
{ctx}

會員問題: {question}

請用繁體中文回答, 保持祥哥口吻。"""
        resp = await model.generate_content_async(prompt)
        return resp.text[:max_chars]
    except Exception as e:
        return f"系統暫時有問題, 請稍後再試。({e})"

# ===== 5. Discord Bot =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"問祥哥AI 已上線: {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.channel.id != CHANNEL_ID:
        return
    # 只回覆 @問祥哥AI 或者以「祥哥」開頭的問題
    if bot.user not in message.mentions and not message.content.strip().startswith("祥哥"):
        return
    question = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not question or question == "祥哥":
        await message.reply("你好, 我係問祥哥AI, 請問你想問咩食生問題? 例如: 祥哥, 體質寒涼食生會唔會愈食愈寒?")
        return
    role_ids = [r.id for r in message.author.roles]
    # $1 種子 - 唔俾用
    if ROLE_SEED in role_ids and ROLE_SPROUT not in role_ids and ROLE_FOREST not in role_ids:
        await message.reply(f"呢個功能只開放俾 $10 或以上 Patreon 會員, 想升級請去: {PATREON_LINK}")
        return
    # 判斷 Tier: 森林優先
    tier = ROLE_FOREST if ROLE_FOREST in role_ids else ROLE_SPROUT if ROLE_SPROUT in role_ids else None
    if tier is None:
        await message.reply(f"呢個頻道只開放俾 Patreon 付費會員, 請先去 Patreon 連結 Discord: {PATREON_LINK}")
        return
    ok, limit, used = check_limit(tier, message.author.id)
    if not ok:
        await message.reply(f"你本月額度已用完 ({used}/{limit}條), 下個月1號會重置。如需更多, 可升級到森林知己。")
        return
    contexts = search_knowledge(question)
    if not contexts:
        await message.reply("呢本《食生基本法》入面暫時搵唔到相關內容, 等祥哥之後補充其他書啦。")
        return
    cfg = LIMITS[tier]
    answer = await ask_gemini(question, contexts, cfg["max_chars"], deep=(tier==ROLE_FOREST))
    # 顯示剩餘額度
    remaining = limit - used
    await message.reply(f"{answer}\n\n---\n[{cfg['label']} | 本月已用 {used}/{limit}條, 剩 {remaining}條]")

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
