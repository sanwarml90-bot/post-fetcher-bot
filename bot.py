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
#  - USERBOT: SESSION_STRING se ek user account (dedicated link-bot
#    account, user id 6278892307, no username) bhi chalata hai —
#    private/protected channels ki posts fetch karne ke liye
#    (jinmein bot account ko access nahi hai). Userbot na ho to
#    bot account se fetch karta hai (public channels).
# ============================================================

import asyncio
import os
import re
import threading
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
    UserAlreadyParticipant,
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
SESSION_STRING = os.getenv("SESSION_STRING", "")

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Bot API limit: 50 MB se badi file fetch nahi kar sakta
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB in bytes

app = Client("post_fetcher", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- Userbot (alag thread mein) ----------
# Userbot ek ALAG thread + apne event loop mein chalta hai, taaki
# bot ka app.run() polling na toote. Pehle do clients ek hi loop
# mein hone se bot reply karna band kar deta tha. Ab bot (main
# thread, app.run) aur userbot (background thread) poori tarah
# alag hain — sirf plain data (file path, naam, size) share hota hai.
# Session: naya dedicated account (user id 6278892307, +91 8233779129)
# — Costombomber TTS ke liye hai, ye link bot ke liye hai.
#
# ZAROORI: userbot ka Client USI thread ke andar banao jismein
# uska loop chalega. Client module level (main thread) mein banane
# par uska loop main loop se bandh jata hai, aur thread ke loop
# mein use karne par "Future attached to a different loop" error
# aata hai — fetch fail hota hai aur error flood se logs bhar jate
# hain (Railway 500 logs/sec rate limit bhi lag jata hai).
userbot = None
userbot_loop = None
userbot_ready = threading.Event()


async def _userbot_main():
    """Userbot ke loop mein chalao: client banao, start karo, phir ruko."""
    global userbot
    userbot = Client(
        "post_fetcher_userbot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
    )
    await userbot.start()
    me = await userbot.get_me()
    print(f"Userbot shuru ho gaya: @{me.username}")
    userbot_ready.set()
    await asyncio.Event().wait()


def _run_userbot_thread():
    """Background thread: userbot ka apna event loop chalao."""
    global userbot_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    userbot_loop = loop
    try:
        # Poora lifecycle ek hi async function mein chalao, taaki
        # start() sahi async context mein await ho.
        loop.run_until_complete(_userbot_main())
    except Exception as e:
        print(f"Userbot start nahi hua ({e}) — bot-only mode mein chal raha hai.")
        userbot_ready.set()


def start_userbot_thread():
    """Userbot thread shuru karo (agar SESSION_STRING ho)."""
    if not SESSION_STRING:
        return False
    t = threading.Thread(target=_run_userbot_thread, daemon=True)
    t.start()
    # Userbot ke ready hone ka thoda wait karo (max 15 sec).
    userbot_ready.wait(timeout=15)
    return True


async def _userbot_sync_dialogs():
    """Userbot ke loop mein chalao: saare dialogs sync karo aur peer
    cache bharo. userbot.get_dialogs() wrapped Client method hai — sync
    wrapper use main_loop par redirect kar deta hai, isliye wo cache
    nahi bhar pata (yahi wajah thi jo pehle dialog refresh fail hota
    raha). Seedha raw messages.GetDialogs via session.invoke use karte
    hain (Session wrapped nahi hai), phir fetch_peers se saare chats
    cache mein dalte hain. Account jis bhi channel ka member hai, wo
    access_hash ke saath cache mein aa jata hai."""
    if getattr(userbot, "session", None) is None:
        return False
    try:
        r = await userbot.session.invoke(
            raw.functions.messages.GetDialogs(
                offset_date=0,
                offset_id=0,
                offset_peer=raw.types.InputPeerEmpty(),
                limit=100,
                hash=0,
            )
        )
        chats = getattr(r, "chats", []) or []
        users = getattr(r, "users", []) or []
        if chats:
            await userbot.fetch_peers(chats)
        if users:
            await userbot.fetch_peers(users)
        return True
    except Exception:
        print("Userbot sync_dialogs fail:")
        traceback.print_exc()
        return False


async def _userbot_fetch_and_download(chat, msg_id, tmp_path):
    """Userbot ke loop mein chalao: post fetch karo + file download karo.
    Sirf plain data wapas do (Pyrogram objects thread ke bahar mat bhejo).

    PeerIdInvalid aane par dialogs sync karke dobara try karo: Telegram
    ko channel fetch karne ke liye uska access_hash chahiye, jo sirf
    peer cache mein hota hai. Account channel ka member hone ke baad
    bhi cache mein channel na ho to get_messages fail hota hai —
    _userbot_sync_dialogs() (raw GetDialogs via session.invoke) cache
    bhar deta hai, phir retry kaam karta hai."""
    try:
        msg = await userbot.get_messages(chat, msg_id)
    except (PeerIdInvalid, KeyError, ValueError):
        try:
            await _userbot_sync_dialogs()
        except Exception:
            pass
        msg = await userbot.get_messages(chat, msg_id)
    if not msg or msg.empty:
        return {"ok": False, "error": "empty"}
    file_name, file_size = get_media_info(msg)
    if not file_name:
        return {"ok": True, "has_file": False, "caption": msg.caption}
    if file_size and file_size > MAX_FILE_SIZE:
        return {"ok": False, "error": "too_big", "file_name": file_name, "file_size": file_size}
    await msg.download(file_name=tmp_path)
    return {
        "ok": True,
        "has_file": True,
        "file_name": file_name,
        "file_size": file_size,
        "caption": msg.caption,
        "tmp_path": tmp_path,
    }


async def _userbot_join_and_resolve(invite_link):
    """Userbot ke loop mein chalao: invite link se join karo, channel ko
    peer cache mein daalo.

    ROOT CAUSE (traceback se mila, deploy 26633032, 22:04 IST):
    Pyrogram ke saare Client methods (join_chat, get_chat, invoke...)
    sync.py ke async_to_sync wrapper se bandhe hote hain. Wrapper mein
    ek GLOBAL main_loop hota hai (bot ke app.run() wala loop). Userbot
    thread se method call karne par wrapper coroutine ko main_loop par
    bhej deta hai (run_coroutine_threadsafe), jabki userbot ka Session
    userbot_loop se bandha hai. Phir session.send() ka run_in_executor
    userbot_loop ka Future banata hai, jise main_loop wala task await
    karta hai — "Future attached to a different loop" error. Isiliye
    get_chat bhi fail hua, join_chat bhi fail hua, chahe unhe userbot
    loop ke andar hi kyun na chalaya gaya ho.

    FIX: Client ke wrapped methods (join_chat/get_chat/invoke) use NAHI
    karte. Seedha userbot.session.invoke() use karte hain (Session class
    sync wrapper se bandha nahi hai — plain async method hai, current
    loop = userbot_loop par chalta hai). Phir fetch_peers() se channel
    ko cache mein dalte hain (fetch_peers Client par defined hai,
    Methods par nahi, isliye wrapped nahi hai).

    Returns dict: {ok, already, title, chat_id, error}."""
    if getattr(userbot, "session", None) is None:
        return {"ok": False, "error": "userbot session nahi mila"}
    m = re.search(r"t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)", invite_link)
    if not m:
        return {"ok": False, "error": "invite link samajh nahi aayi"}
    invite_hash = m.group(1)

    # 1) ImportChatInvite — join karne ki koshish karo.
    try:
        r = await userbot.session.invoke(
            raw.functions.messages.ImportChatInvite(hash=invite_hash)
        )
        # Result ke users/chats ko cache mein daalo.
        try:
            await userbot.fetch_peers(getattr(r, "users", []) or [])
            await userbot.fetch_peers(getattr(r, "chats", []) or [])
        except Exception:
            traceback.print_exc()
        chats = getattr(r, "chats", []) or []
        if chats:
            chat_obj = chats[0]
            title = getattr(chat_obj, "title", None) or "channel"
            if isinstance(chat_obj, raw.types.Channel):
                chat_id = utils.get_channel_id(chat_obj.id)
            elif isinstance(chat_obj, raw.types.Chat):
                chat_id = -chat_obj.id
            else:
                chat_id = getattr(chat_obj, "id", None)
            return {"ok": True, "already": False, "title": title, "chat_id": chat_id}
        return {"ok": True, "already": False, "title": "channel", "chat_id": None}
    except UserAlreadyParticipant:
        # Account pehle se member hai — join nahi hoga, par channel ko
        # cache mein dalna hai. Neeche CheckChatInvite se channel nikalo.
        pass
    except Exception:
        print("Userbot ImportChatInvite fail:")
        traceback.print_exc()
        return {"ok": False, "error": "join fail (ImportChatInvite error)"}

    # 2) Pehle se member hai — CheckChatInvite se channel ka object lo
    # aur fetch_peers se cache mein daalo.
    try:
        r = await userbot.session.invoke(
            raw.functions.messages.CheckChatInvite(hash=invite_hash)
        )
    except Exception:
        print("Userbot CheckChatInvite fail:")
        traceback.print_exc()
        return {"ok": False, "error": "invite link resolve nahi hui (CheckChatInvite fail)"}
    chat_obj = getattr(r, "chat", None)
    if chat_obj is None:
        return {"ok": False, "error": "invite link se channel nahi mila — link expire/galat ho sakti hai"}
    try:
        await userbot.fetch_peers([chat_obj])
    except Exception:
        print("Userbot fetch_peers fail:")
        traceback.print_exc()
    # CheckChatInvite ka channel min ho sakta hai (bina access_hash) —
    # isliye poora dialog sync bhi karo, taaki channel access_hash ke
    # saath cache mein aa jaye aur fetch turant kaam kare.
    try:
        await _userbot_sync_dialogs()
    except Exception:
        pass
    if isinstance(chat_obj, raw.types.Channel):
        chat_id = utils.get_channel_id(chat_obj.id)
    elif isinstance(chat_obj, raw.types.Chat):
        chat_id = -chat_obj.id
    else:
        chat_id = getattr(chat_obj, "id", None)
    title = getattr(chat_obj, "title", None) or "channel"
    return {"ok": True, "already": True, "title": title, "chat_id": chat_id}


async def userbot_join(invite_link, timeout=60):
    """Userbot ko invite link se channel/group mein join karwao.
    Returns dict: {ok, already, title, chat_id, error}.
    Apna coroutine userbot loop mein bhejta hai (fetch path jaisa) —
    Pyrogram calls andar hoti hain, isliye loop error nahi aata."""
    if userbot is None or userbot_loop is None:
        return {"ok": False, "error": "userbot nahi chal raha"}
    try:
        fut = asyncio.run_coroutine_threadsafe(
            _userbot_join_and_resolve(invite_link), userbot_loop
        )
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
    except Exception as e:
        print(f"Userbot join fail: {e}")
        traceback.print_exc()
        return {"ok": False, "error": f"join fail: {e}"}


async def userbot_fetch(chat, msg_id, tmp_path, timeout=120):
    """Bot ke handler se call karo: userbot thread par fetch+download
    karwao aur result lo. ASYNC hai — wrap_future se main loop block
    nahi hota (pehle fut.result() se loop 120 sec tak atak jata tha,
    isliye naye messages ka reply bhi late aata tha).
    Userbot na ho ya fail ho to None wapas do."""
    if userbot is None or userbot_loop is None:
        return None
    try:
        fut = asyncio.run_coroutine_threadsafe(
            _userbot_fetch_and_download(chat, msg_id, tmp_path), userbot_loop
        )
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
    except Exception as e:
        print(f"Userbot fetch fail: {e}")
        return None

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
        result = await userbot_join(link)
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

    # PEHLE userbot se try karo (alag thread mein; private channels ke liye).
    # Userbot fetch + download karke file path deta hai, bot use bhejta hai.
    if userbot is not None and userbot_loop is not None:
        ub_tmp = os.path.join(
            DOWNLOADS_DIR, f"{message.from_user.id}_{msg_id}_ub.tmp"
        )
        ub = await userbot_fetch(chat, msg_id, ub_tmp)
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
                f"Bot sirf **50 MB** tak ki file fetch kar sakta hai."
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
# Pyrogram ka built-in runner use karo (app.run) — ye event loop,
# startup aur polling sab khud sambhalta hai. Manual
# asyncio.run(main()) + app.start() + idle() se bot updates poll
# nahi kar pa raha tha (Telegram par 17 updates pending mile,
# chaar baar test karne par bhi koi reply nahi aaya).
# Userbot ALAG thread mein chalta hai (start_userbot_thread), taaki
# bot ka polling na toote — private channels ke liye.
if __name__ == "__main__":
    print("Post Link Fetcher Bot shuru ho raha hai...")
    # Pehle userbot thread shuru karo (agar SESSION_STRING ho),
    # phir bot ko app.run() se chalao.
    start_userbot_thread()
    # app.run() bot ko start karke polling chalata hai aur
    # band hone tak rukta hai.
    app.run()
