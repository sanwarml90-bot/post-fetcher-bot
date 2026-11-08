# ============================================================
# Post Link Fetcher Bot — Pyrogram bot + Telethon userbot
# Telegram post ki link bhejo, uski file pao!
#
# Features:
#  - /start, /help
#  - Post link bhejne par us post ki file fetch karke bhejta hai
#    (document, photo, video, audio, animation, voice, video note)
#  - Photo ko document ke roop mein bhejta hai (quality bani rahe)
#  - PUBLIC bot — koi bhi user use kar sakta hai
#  - 50 MB tak ki file bot bhejta hai; 50 MB se 500 MB tak ki file
#    Telethon userbot seedha user ko bhejta hai (Bot API ki 50 MB limit
#    ki wajah se)
#  - Link ke forms: t.me/<username>/<msg_id> aur t.me/c/<chat_id>/<msg_id>
#  - USERBOT (Telethon): SESSION_STRING se ek user account (dedicated
#    link-bot account, user id 6278892307, no username) bhi chalata hai —
#    private/protected channels ki posts fetch karne ke liye (jinmein bot
#    account ko access nahi hai). Userbot na ho to bot account se fetch
#    karta hai (public channels).
#
# KYUN TELETHON: Pyrogram ka userbot "Future attached to a different
# loop" se fail hota raha — Pyrogram ka sync wrapper saare userbot
# methods ko main loop par redirect kar deta hai, jabki userbot ka
# Session alag loop se bandha hota hai. Isliye get_messages, get_dialogs,
# join_chat sab fail hote the. Telethon natively async hai — koi sync
# wrapper nahi — isliye uska get_entity/get_messages seedha chalta hai.
# Test 2026-11-08: Telethon ne private channel -1004470825313 (title
# "Zenox bro ise kro kuchh") ka msg 2 fetch kiya — vote.zip (11882 bytes).
# Dono clients EK HI event loop (main loop) par chalte hain — app.run()
# ka loop — isliye koi loop conflict nahi.
# ============================================================

import asyncio
import os
import re
import traceback
from dotenv import load_dotenv, dotenv_values
from pyrogram import Client, filters, raw, utils
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
from telethon import TelegramClient as TelethonClient
from telethon.sessions import StringSession as TelethonStringSession
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl import types as tele_types
from telethon.errors import (
    FloodWaitError as TeleFloodWait,
    ChannelPrivateError as TeleChannelPrivate,
    InviteHashExpiredError as TeleInviteExpired,
    InviteHashInvalidError as TeleInviteInvalid,
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
# Sirf owner (Sanji) userbot ko kisi channel mein join karwa sakta hai
# (invite link bhej kar). Owner ID = admin chat 7966027018.
OWNER_ID = int(os.getenv("OWNER_ID", "7966027018"))
# Userbot session (dedicated link-bot account, user id 6278892307) —
# env se hi padho, kabhi file mein hardcode mat karo, kabhi print mat karo.
# ZAROORI: ye ab TELETHON StringSession hai (Pyrogram string nahi chalegi).
# Pyrogram session se convert kiya gaya (convert_pyro_to_telethon.py) —
# same auth key, sirf encoding alag hai.
SESSION_STRING = os.getenv("SESSION_STRING", "")

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Bot API limit: bot account sirf 50 MB tak bhej sakta hai (Telegram ki
# hard limit — code se nahi badh sakti). Isliye 50 MB se badi file ko
# Telethon userbot seedha user ko bhejta hai (user account 2 GB tak
# bhej sakta hai). Fetch ki limit 500 MB hai.
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB in bytes
BOT_SEND_LIMIT = 50 * 1024 * 1024  # 50 MB — Bot API send limit

app = Client("post_fetcher", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- Userbot (Telethon, main loop par) ----------
# Telethon userbot — dedicated link-bot account (user id 6278892307).
# Pyrogram userbot ko hata diya: uska sync wrapper saare methods ko
# main loop par redirect karta tha, jabki Session alag loop se bandha
# tha — get_messages/get_dialogs/join_chat sab "Future attached to a
# different loop" se fail hote the. Telethon natively async hai, koi
# sync wrapper nahi, isliye get_entity/get_messages seedha chalte hain.
# Dono clients (Pyrogram bot + Telethon userbot) EK HI event loop par
# chalte hain — app.run() ka main loop — isliye koi loop conflict nahi.
# Telethon client ko start_telethon() se main loop par start karte hain,
# phir app.run() usi loop par bot chalata hai.
tele_client = None


async def start_telethon():
    """Telethon userbot ko main loop par start karo. SESSION_STRING ek
    Telethon StringSession honi chahiye (Pyrogram string nahi chalegi)."""
    global tele_client
    if not SESSION_STRING:
        print("Telethon SESSION_STRING nahi hai — bot-only mode.")
        return False
    tele_client = TelethonClient(
        TelethonStringSession(SESSION_STRING),
        API_ID,
        API_HASH,
    )
    await tele_client.start()
    me = await tele_client.get_me()
    print(f"Telethon userbot shuru ho gaya: @{me.username} ID:{me.id}")
    return True


async def tele_fetch(chat, msg_id, tmp_path, timeout=120):
    """Bot ke handler se call karo: Telethon userbot se post fetch karo
    + file download karo. Returns dict: {ok, has_file, file_name,
    file_size, caption, tmp_path} — ya None (userbot na ho / fail ho).
    chat: username (str) ya full chat ID (int, negative).

    50 MB se badi file DOWNLOAD NAHI karte — uska Telethon message
    wapas dete hain ("msg"), taaki handler use seedha reference se bhej
    sake (send_file with msg.media). Download+upload ka round-trip
    badi file ke liye bahut slow hota hai, aur temp naam se file bhej
    jati hai; reference se bhejne par file turant jati hai aur original
    naam bhi bana rehta hai."""
    if tele_client is None:
        return None
    try:
        entity = await tele_client.get_entity(chat)
        msg = await tele_client.get_messages(entity, ids=msg_id)
        if not msg:
            return {"ok": False, "error": "empty"}
        f = msg.file
        if f is None:
            return {"ok": True, "has_file": False, "caption": msg.message}
        file_name = f.name or f"file_{msg.id}"
        file_size = f.size
        if file_size and file_size > MAX_FILE_SIZE:
            return {
                "ok": False,
                "error": "too_big",
                "file_name": file_name,
                "file_size": file_size,
            }
        # Badi file (Bot API 50 MB limit se upar): download mat karo,
        # message hi wapas do — handler reference se bhejega.
        if file_size and file_size > BOT_SEND_LIMIT:
            return {
                "ok": True,
                "has_file": True,
                "file_name": file_name,
                "file_size": file_size,
                "caption": msg.message,
                "msg": msg,
                "send_by_ref": True,
            }
        await tele_client.download_media(msg, file=tmp_path)
        return {
            "ok": True,
            "has_file": True,
            "file_name": file_name,
            "file_size": file_size,
            "caption": msg.message,
            "tmp_path": tmp_path,
        }
    except Exception as e:
        print(f"Telethon fetch fail: {e}")
        return None


async def tele_join(invite_link, timeout=60):
    """Telethon userbot ko invite link se channel/group mein join karwao.
    Returns dict: {ok, already, title, chat_id, error}.
    CheckChatInviteRequest se pehle check karte hain — account pehle se
    member ho to ChatInviteAlready milta hai (galat "link expire" nahi)."""
    if tele_client is None:
        return {"ok": False, "error": "userbot nahi chal raha"}
    m = re.search(r"t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)", invite_link)
    if not m:
        return {"ok": False, "error": "invite link samajh nahi aayi"}
    invite_hash = m.group(1)
    try:
        res = await tele_client(CheckChatInviteRequest(invite_hash))
        if isinstance(res, tele_types.ChatInviteAlready):
            chat = res.chat
            return {"ok": True, "already": True, **_tele_chat_info(chat)}
        updates = await tele_client(ImportChatInviteRequest(invite_hash))
        chats = getattr(updates, "chats", None) or []
        if not chats:
            return {"ok": False, "error": "join ke baad channel nahi mila"}
        return {"ok": True, "already": False, **_tele_chat_info(chats[0])}
    except Exception as e:
        print(f"Telethon join fail: {e}")
        traceback.print_exc()
        return {"ok": False, "error": f"join fail: {e}"}


def _tele_chat_info(chat):
    """Telethon chat object se {title, chat_id} nikalo.
    Channel ke liye full ID -100... hota hai, group ke liye -id."""
    title = getattr(chat, "title", None) or "channel"
    if isinstance(chat, tele_types.Channel):
        return {"title": title, "chat_id": int("-100" + str(chat.id))}
    return {"title": title, "chat_id": -chat.id}

# ---------- Helpers ----------
# Message mein se pehli t.me link nikalo
LINK_RE = re.compile(r"(?:https?://)?t\.me/\S+", re.IGNORECASE)
# Invite link: t.me/+XXXX ya t.me/joinchat/XXXX (https:// bhi)
INVITE_RE = re.compile(
    r"(?:https?://)?t\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+", re.IGNORECASE
)


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
        "• **500 MB** tak ki files fetch kar sakta hai (50 MB se badi file "
        "userbot se seedha bhejta hai)\n\n"
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

    # INVITE LINK: owner userbot ko kisi private channel mein join karwana
    # chahta hai (uske baad us channel ki posts fetch ho jayengi).
    # Sirf owner (Sanji) ke liye — baaki users ko mana kar do.
    invite = INVITE_RE.search(text)
    if invite:
        if message.from_user.id != OWNER_ID:
            await message.reply_text(
                "🔒 Ye feature sirf bot ke owner ke liye hai."
            )
            return
        link = invite.group(0)
        status = await message.reply_text("🔗 Userbot ko channel mein join karwa raha hun, rukho...")
        result = await tele_join(link)
        if result.get("ok"):
            if result.get("already"):
                await status.edit_text(
                    f"✅ **Account pehle se hi is channel mein hai: {result.get('title')}**\n\n"
                    "Ab us channel ki post ki link bhejo, file fetch ho jayegi."
                )
            else:
                await status.edit_text(
                    f"✅ **Userbot join ho gaya: {result.get('title')}**\n\n"
                    "Ab us channel ki post ki link bhejo, file fetch ho jayegi."
                )
        else:
            await status.edit_text(
                f"❌ **Join nahi ho paya.**\n\nReason: {result.get('error')}"
            )
        return

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

    # PEHLE Telethon userbot se try karo (main loop par; private channels ke liye).
    # Telethon fetch + download karke file path deta hai, bot use bhejta hai.
    if tele_client is not None:
        ub_tmp = os.path.join(
            DOWNLOADS_DIR, f"{message.from_user.id}_{msg_id}_ub.tmp"
        )
        ub = await tele_fetch(chat, msg_id, ub_tmp)
        if ub is not None and ub.get("ok"):
            if not ub.get("has_file"):
                await status.edit_text(
                    "📝 **Is post mein koi file nahi hai.**\n\n"
                    "Ye post sirf text hai — ismein koi photo, video ya file nahi hai."
                )
                return
            ub_name = ub["file_name"]
            ub_size = ub.get("file_size")
            ub_caption = ub.get("caption") or ""
            caption = f"📦 **File mil gayi!**\n\n📄 **Naam:** `{ub_name}`"
            if ub_caption:
                caption += f"\n\n💬 **Caption:** {ub_caption}"
            # 50 MB se badi file Bot API se nahi bhej sakte (Telegram ki
            # hard limit) — isliye Telethon userbot use seedha user ko
            # bhejta hai (user account 2 GB tak bhej sakta hai).
            # REFERENCE se bhejte hain (msg.media) — file download/upload
            # nahi hoti, isliye turant jati hai aur original naam bhi
            # bana rehta hai (temp naam "…_ub.tmp" nahi aata).
            if ub_size and ub_size > BOT_SEND_LIMIT:
                try:
                    await tele_client.send_file(
                        message.from_user.id,
                        ub["msg"].media,
                        caption=caption,
                    )
                    await status.edit_text(
                        "✅ **Ho gaya! File bhej di (userbot se, kyunki "
                        "file 50 MB se badi hai).**"
                    )
                except TeleFloodWait as e:
                    await status.edit_text(
                        f"⏳ Telegram ne thoda rok lagayi hai. "
                        f"{e.seconds} second baad dobara try karo."
                    )
                except Exception as e:
                    await status.edit_text(
                        "❌ **Badi file userbot se bhi nahi bhej paya.**\n\n"
                        f"Reason: {e}\n\n"
                        "Userbot account (+91 8233779129) ko pehle message "
                        "karo, phir try karo."
                    )
                return
            try:
                await client.send_document(
                    message.chat.id,
                    ub["tmp_path"],
                    file_name=ub_name,
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
                try:
                    if os.path.exists(ub["tmp_path"]):
                        os.remove(ub["tmp_path"])
                except OSError:
                    pass
            return
        if ub is not None and ub.get("error") == "too_big":
            await status.edit_text(
                f"❌ **File bahut badi hai ({format_size(ub.get('file_size'))}).**\n\n"
                f"Bot sirf **500 MB** tak ki file fetch kar sakta hai."
            )
            return
        # Userbot fail (jaise account channel ka member nahi) —
        # neeche bot account se try karo (public channels ke liye).

    # Post fetch karo — bot account se (public channels ke liye).
    # msg.download() usi client se hota hai jisne message fetch kiya.
    fetch_client = client
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

    # File size check (Bot API 50 MB limit — bot account se zyada nahi bhej sakte)
    if file_size and file_size > BOT_SEND_LIMIT:
        await status.edit_text(
            f"❌ **File bahut badi hai ({format_size(file_size)}).**\n\n"
            f"Bot account se sirf **50 MB** tak ki file bhej sakta hai. "
            f"Badi file ke liye userbot wala rasta use hota hai."
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
# Pyrogram ka built-in runner use karo (app.run) — ye event loop,
# startup aur polling sab khud sambhalta hai. Manual
# asyncio.run(main()) + app.start() + idle() se bot updates poll
# nahi kar pa raha tha (Telegram par 17 updates pending mile,
# chaar baar test karne par bhi koi reply nahi aaya).
# Telethon userbot ko app.run() se PEHLE main loop par start karte
# hain: app.run() asyncio.get_event_loop() use karta hai — wahi loop
# jismein Telethon start hua — isliye dono clients ek hi loop par
# chalte hain aur koi loop conflict nahi hota.
if __name__ == "__main__":
    print("Post Link Fetcher Bot shuru ho raha hai...")
    # Pehle Telethon userbot start karo (agar SESSION_STRING ho),
    # phir bot ko app.run() se chalao.
    if SESSION_STRING:
        asyncio.get_event_loop().run_until_complete(start_telethon())
    # app.run() bot ko start karke polling chalata hai aur
    # band hone tak rukta hai.
    app.run()
