# ============================================================
# Post Link Fetcher Bot — Pyrogram
# Telegram post ki link bhejo, uski file pao!
#
# Features:
#  - /start, /help
#  - Post link bhejne par us post ki file fetch karke bhejta hai
#    (document, photo, video, audio, animation, voice, video note)
#  - Photo ko document ke roop mein bhejta hai (quality bani rahe)
#  - PUBLIC bot — koi bhi user use kar sakta hai
#  - 50 MB se badi file par mana kar deta hai (Bot API limit)
#  - Link ke forms: t.me/<username>/<msg_id> aur t.me/c/<chat_id>/<msg_id>
#  - USERBOT: SESSION_STRING se ek user account (@Costombomber) bhi
#    chalata hai — private/protected channels ki posts fetch karne
#    ke liye (jinmein bot account ko access nahi hai). Userbot na
#    ho to bot account se fetch karta hai (public channels).
# ============================================================

import asyncio
import os
import re
from dotenv import load_dotenv, dotenv_values
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import (
    PeerIdInvalid,
    ChannelPrivate,
    ChannelInvalid,
    UsernameInvalid,
    UsernameNotOccupied,
    MessageIdInvalid,
    FloodWait,
)

# ---------- Config ----------
# Local .env (BOT_TOKEN, SESSION_STRING) load karo
load_dotenv()

# API_ID / API_HASH Aand bot ki .env se lo (shared API credentials).
# dotenv_values se sirf ye 2 keys padhte hain — koi value print/copy nahi hoti.
_aand_env = dotenv_values(os.path.expanduser("~/workspace/aand_bot/.env"))
API_ID = int(os.getenv("API_ID") or _aand_env.get("API_ID", "0"))
API_HASH = os.getenv("API_HASH") or _aand_env.get("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Userbot session (@Costombomber user account) — env se hi padho,
# kabhi file mein hardcode mat karo, kabhi print mat karo.
SESSION_STRING = os.getenv("SESSION_STRING", "")

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Bot API limit: 50 MB se badi file fetch nahi kar sakta
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB in bytes

app = Client("post_fetcher", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Userbot client (@Costombomber) — sirf tab banao jab SESSION_STRING ho.
# Ye private/protected channels ki posts fetch karta hai, kyunki ye
# account un channels ka member hai. Bot sirf file wapas bhejta hai.
userbot = None
if SESSION_STRING:
    userbot = Client(
        "post_fetcher_userbot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
    )

# ---------- Helpers ----------
# Message mein se pehli t.me link nikalo
LINK_RE = re.compile(r"(?:https?://)?t\.me/\S+", re.IGNORECASE)


def extract_link(text: str):
    """Message text mein se pehli t.me link nikalo. Na mile to None."""
    if not text:
        return None
    m = LINK_RE.search(text)
    if not m:
        return None
    return m.group(0)


def parse_telegram_link(link: str):
    """Telegram post link ko parse karo.

    Returns (chat, message_id):
      - chat: username (str) ya full chat ID (int, negative)
      - message_id: int
    Invalid link par (None, None).

    Supported forms:
      - https://t.me/<username>/<msg_id>
      - http://t.me/<username>/<msg_id>
      - t.me/<username>/<msg_id>
      - https://t.me/c/<chat_id>/<msg_id> (private channel/group;
        chat_id mein -100 prefix nahi hota, use jod kar full ID banta hai)
    Query params (?...) aur trailing slash hata diye jate hain.
    Message ID hamesha URL path ka aakhri numeric part hota hai.
    """
    if not link:
        return None, None

    # Query params (?...) aur fragment (#...) hatao, trailing slash hatao
    link = link.strip().split("?")[0].split("#")[0].rstrip("/")

    m = re.match(r"^(?:https?://)?t\.me/(.+)$", link, re.IGNORECASE)
    if not m:
        return None, None

    path = m.group(1)
    parts = [p for p in path.split("/") if p]

    # Private channel/group: t.me/c/<chat_id>/<msg_id>
    if len(parts) >= 3 and parts[0] == "c":
        chat_id_part, msg_id_part = parts[1], parts[2]
        if chat_id_part.isdigit() and msg_id_part.isdigit():
            # Full chat ID = -100 + chat_id
            return int("-100" + chat_id_part), int(msg_id_part)
        return None, None

    # Public: t.me/<username>/<msg_id>
    # (3 parts hone par bhi aakhri numeric part hi message ID hai)
    if len(parts) >= 2 and parts[-1].isdigit():
        return parts[0], int(parts[-1])

    return None, None


def sanitize_filename(name: str) -> str:
    """Filename mein allowed nahi hone wale characters hatao: / \\ : * ? " < > |"""
    name = re.sub(r'[/\\:*?"<>|]', "", name)
    name = name.strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name


def format_size(size_bytes: int) -> str:
    """Bytes ko readable size mein badlo (KB / MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def get_media_info(msg: Message):
    """Message se media ki jaankari nikalo.

    Returns (file_name, file_size):
      - file_name: original filename (photo ke liye banaya hua naam)
      - file_size: bytes mein size (None agar pata na chale)
    Koi media na ho to (None, None).
    """
    if msg.document:
        name = msg.document.file_name or f"file_{msg.id}"
        return name, msg.document.file_size
    if msg.video:
        name = msg.video.file_name or f"video_{msg.id}.mp4"
        return name, msg.video.file_size
    if msg.audio:
        name = msg.audio.file_name or f"audio_{msg.id}.mp3"
        return name, msg.audio.file_size
    if msg.animation:
        name = msg.animation.file_name or f"animation_{msg.id}.mp4"
        return name, msg.animation.file_size
    if msg.photo:
        # Photo ka koi filename nahi hota — naam bana lo
        return f"photo_{msg.id}.jpg", msg.photo.file_size
    if msg.voice:
        return f"voice_{msg.id}.ogg", msg.voice.file_size
    if msg.video_note:
        return f"video_note_{msg.id}.mp4", msg.video_note.file_size
    return None, None


# ---------- /start ----------
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 **Namaste! Main Post Link Fetcher Bot hun.**\n\n"
        "Mujhe kisi Telegram post ki **link** bhejo, main us post ki "
        "file nikaal kar tumhe bhej dunga.\n\n"
        "**Kaise kaam karta hai:**\n"
        "1️⃣ Kisi channel/group ki post ki link copy karo\n"
        "2️⃣ Yahan bhej do (jaise `https://t.me/channelname/123`)\n"
        "3️⃣ Bot us post ki file fetch karke tumhe bhej dega\n\n"
        "**Note:**\n"
        "• Public channels ki posts ke liye kaam karta hai\n"
        "• Private/protected (no-forward) channels ki posts bhi fetch kar leta hai — "
        "userbot account unka member hai, isliye bot ko add karne ki zaroorat nahi\n"
        "• Sirf **50 MB** tak ki files fetch kar sakta hai\n\n"
        "👉 Ab ek post ki link bhej kar try karo!"
    )


# ---------- /help ----------
@app.on_message(filters.command("help") & filters.private)
async def help_handler(client: Client, message: Message):
    await message.reply_text(
        "❓ **Help — Post Link Fetcher Bot**\n\n"
        "**Commands:**\n"
        "• /start — Bot shuru karo\n"
        "• /help — Ye help dekho\n\n"
        "**Link ke examples:**\n"
        "• `https://t.me/channelname/123`\n"
        "• `https://t.me/c/1234567890/123` (private channel)\n"
        "• `t.me/channelname/123`\n\n"
        "**Steps:**\n"
        "1. Post ki link bhejo\n"
        "2. Bot file fetch karega aur tumhe bhej dega\n\n"
        "**Agar error aaye:**\n"
        "• “Bot ko access nahi hai” — us channel mein userbot account member nahi hai; "
        "userbot account ko channel mein add karo\n"
        "• “Link galat hai” — link dobara check karo\n"
        "• “Post nahi mili” — post delete ho gayi hogi"
    )


# ---------- Post link aane par ----------
@app.on_message(filters.text & filters.private & ~filters.command(["start", "help"]))
async def link_handler(client: Client, message: Message):
    text = message.text or ""

    # Message mein se t.me link nikalo
    link = extract_link(text)
    if not link:
        await message.reply_text(
            "🔗 Kripya ek **Telegram post ki link** bhejo.\n\n"
            "Example: `https://t.me/channelname/123`\n"
            "Ya /help likh kar examples dekho."
        )
        return

    # Link parse karo
    chat, msg_id = parse_telegram_link(link)
    if chat is None or msg_id is None:
        await message.reply_text(
            "❌ **Link samajh nahi aayi.**\n\n"
            "Sahi format ye hai:\n"
            "• `https://t.me/channelname/123`\n"
            "• `https://t.me/c/1234567890/123`\n\n"
            "Link dobara check karo."
        )
        return

    status = await message.reply_text("🔍 Post dhoondh raha hun, rukho...")

    # Post fetch karo — pehle userbot se (private/protected channels ke liye),
    # userbot na ho to bot account se (public channels ke liye).
    # msg.download() usi client se hota hai jisne message fetch kiya.
    fetch_client = userbot if userbot is not None else client
    try:
        msg = await fetch_client.get_messages(chat, msg_id)
    except FloodWait as e:
        await status.edit_text(
            f"⏳ Telegram ne thoda rok lagayi hai. "
            f"{e.value} second baad dobara try karo."
        )
        return
    except (ChannelPrivate, ChannelInvalid):
        await status.edit_text(
            "🔒 **Us channel/group ka access nahi hai.**\n\n"
            "Userbot account ko us channel mein add karo (member banao), "
            "phir link bhejo."
        )
        return
    except (UsernameInvalid, UsernameNotOccupied, PeerIdInvalid):
        await status.edit_text(
            "❌ **Link galat hai — ye channel/username nahi mila.**\n\n"
            "Link dobara check karo. Private channel ke liye userbot "
            "account ko us channel ka member hona chahiye."
        )
        return
    except MessageIdInvalid:
        await status.edit_text(
            "❌ **Ye post nahi mili.**\n\n"
            "Message ID galat hai ya post delete ho gayi hai."
        )
        return
    except Exception as e:
        await status.edit_text(
            f"❌ **File fetch nahi ho payi.**\n\n"
            f"Reason: {e}"
        )
        return

    # Post mili ya nahi?
    if not msg or msg.empty:
        await status.edit_text(
            "❌ **Ye post nahi mili.**\n\n"
            "Post delete ho gayi hogi ya link galat hai."
        )
        return

    # Media hai ya nahi?
    file_name, file_size = get_media_info(msg)
    if not file_name:
        await status.edit_text(
            "📝 **Is post mein koi file nahi hai.**\n\n"
            "Ye post sirf text hai — ismein koi photo, video ya file nahi hai."
        )
        return

    # File size check (50 MB limit)
    if file_size and file_size > MAX_FILE_SIZE:
        await status.edit_text(
            f"❌ **File bahut badi hai ({format_size(file_size)}).**\n\n"
            f"Bot sirf **50 MB** tak ki file fetch kar sakta hai."
        )
        return

    # File download karo
    safe_name = sanitize_filename(file_name) or f"file_{msg_id}"
    tmp_path = os.path.join(
        DOWNLOADS_DIR, f"{message.from_user.id}_{msg_id}_{safe_name}"
    )
    await status.edit_text(
        f"⬇️ **File download ho rahi hai...**\n\n"
        f"📄 **Naam:** `{file_name}`\n"
        f"📏 **Size:** {format_size(file_size) if file_size else 'pata nahi'}"
    )
    try:
        await msg.download(file_name=tmp_path)
    except FloodWait as e:
        await status.edit_text(
            f"⏳ Telegram ne thoda rok lagayi hai. "
            f"{e.value} second baad dobara try karo."
        )
        return
    except Exception as e:
        await status.edit_text(f"❌ Download fail ho gaya: {e}")
        return

    # File user ko bhejo (document ke roop mein, original naam ke saath)
    caption = f"📦 **File mil gayi!**\n\n📄 **Naam:** `{file_name}`"
    if msg.caption:
        caption += f"\n\n💬 **Caption:** {msg.caption}"
    try:
        await client.send_document(
            message.chat.id,
            tmp_path,
            file_name=file_name,
            caption=caption,
        )
        await status.edit_text("✅ **Ho gaya! File upar bhej di.**")
    except FloodWait as e:
        await status.edit_text(
            f"⏳ Telegram ne thoda rok lagayi hai. "
            f"{e.value} second baad dobara try karo."
        )
    except Exception as e:
        await status.edit_text(f"❌ Bhejne mein error aaya: {e}")
    finally:
        # Temp file delete karo
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


# ---------- Run ----------
async def main():
    """Userbot (agar SESSION_STRING ho) aur bot — dono shuru karo.
    idle() ki jagah asyncio.Event().wait() use karte hain, taaki
    do clients ke saath event loop mein koi takraar na ho."""
    global userbot
    if userbot is not None:
        try:
            await userbot.start()
            me = await userbot.get_me()
            print(f"Userbot shuru ho gaya: @{me.username}")
        except Exception as e:
            # Userbot session kharab ho to bot-only mode mein chalao,
            # poora bot band mat karo.
            print(f"Userbot start nahi hua ({e}) — bot-only mode mein chal raha hai.")
            userbot = None
    await app.start()
    print("Post Link Fetcher Bot online hai.")
    # Dono clients background mein updates sunte rehte hain;
    # process ko zinda rakhne ke liye bas wait karo.
    await asyncio.Event().wait()


if __name__ == "__main__":
    print("Post Link Fetcher Bot shuru ho raha hai...")
    asyncio.run(main())
