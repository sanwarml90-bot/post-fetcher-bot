# Unit tests for Post Link Fetcher Bot helpers — NO network, NO real
# sessions, NO tokens. Run: /tmp/lf-venv/bin/python test_bot_helpers.py
# (pyrogram/telethon only needed for the import; nothing connects.)
import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Dummy env vars so the Client() constructor is happy even without .env
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash1234567890")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

import bot as B
from telethon.tl import types as tele_types
from telethon.errors import (
    UsernameNotOccupiedError as TeleUsernameNotOccupied,
    UserNotParticipantError as TeleNotParticipant,
)

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


# --- parse_channel_link ---
print("parse_channel_link:")
check("bare username", B.parse_channel_link("https://t.me/somechannel") == "somechannel")
check("bare no scheme", B.parse_channel_link("t.me/somechannel") == "somechannel")
check("trailing slash", B.parse_channel_link("https://t.me/somechannel/") == "somechannel")
check("post link -> None", B.parse_channel_link("https://t.me/somechannel/123") is None)
check("private c/ link -> None", B.parse_channel_link("https://t.me/c/1234567890/123") is None)
check("invite + -> None", B.parse_channel_link("https://t.me/+AbCdEf123") is None)
check("invite joinchat -> None", B.parse_channel_link("https://t.me/joinchat/AbCdEf123") is None)
check("s/ preview -> None", B.parse_channel_link("https://t.me/s/somechannel/123") is None)
check("no link -> None", B.parse_channel_link("hello world") is None)
check("None -> None", B.parse_channel_link(None) is None)
check("query stripped", B.parse_channel_link("https://t.me/somechannel?x=1") == "somechannel")

# --- parse_telegram_link regression ---
print("parse_telegram_link (regression):")
check("public post", B.parse_telegram_link("https://t.me/channelname/123") == ("channelname", 123))
check("private post", B.parse_telegram_link("https://t.me/c/1234567890/123") == (-1001234567890, 123))
check("bare channel -> (None,None)", B.parse_telegram_link("https://t.me/channelname") == (None, None))

# --- split_text ---
print("split_text:")
check("short stays one", B.split_text("hello") == ["hello"])
check("exactly limit stays one", len(B.split_text("x" * 4000)) == 1)
long_nl = "line one\n" * 600  # 5400 chars with newlines
chunks = B.split_text(long_nl)
check("long splits, each <=4000", all(len(c) <= 4000 for c in chunks) and len(chunks) > 1)
check("long content preserved", "".join(chunks).replace("\n", "") == long_nl.replace("\n", ""))
long_nonl = "y" * 9000
chunks2 = B.split_text(long_nonl)
check("long no-newline splits", len(chunks2) == 3 and all(len(c) <= 4000 for c in chunks2))
check("long no-newline exact", "".join(chunks2) == long_nonl)

# --- userbot_tag ---
print("userbot_tag:")
B.TELE_ME = None
check("no userbot -> empty", B.userbot_tag() == "")
B.TELE_ME = {"username": "linkuserbot", "first_name": "Link", "id": 6278892307}
check("username shown", B.userbot_tag() == "\n\n🤖 via userbot: @linkuserbot")
B.TELE_ME = {"username": None, "first_name": "Link Bot", "id": 6278892307}
tag = B.userbot_tag()
check("no username -> name+ID", "Link Bot" in tag and "6278892307" in tag)

# --- build_file_caption ---
print("build_file_caption:")
c = B.build_file_caption("movie.mp4", "nice movie", "\n\n🤖 via userbot: @x")
check("short caption ok", "movie.mp4" in c and "nice movie" in c and "@x" in c)
long_cap = "z" * 5000
c2 = B.build_file_caption("f.mp4", long_cap, "\n\n🤖 via userbot: @x")
check("long caption capped <=1020", len(c2) <= 1020)
check("tag survives truncation", "@x" in c2)
c3 = B.build_file_caption("f.mp4", "", "")
check("no caption no tag", c3 == "📦 **File mil gayi!**\n\n📄 **Naam:** `f.mp4`")

# --- send_text_chunks (fake client) ---
print("send_text_chunks:")


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_to_message_id=None):
        self.sent.append((chat_id, text, reply_to_message_id))


async def _chunks():
    fc = FakeClient()
    await B.send_text_chunks(fc, 111, "short", reply_to_message_id=222)
    check("short: 1 msg as reply", len(fc.sent) == 1 and fc.sent[0][2] == 222)
    fc2 = FakeClient()
    await B.send_text_chunks(fc2, 111, "w" * 9000)
    check("long: 3 msgs, first not a reply", len(fc2.sent) == 3 and fc2.sent[0][2] is None)
    check("long: content joined", "".join(s[1] for s in fc2.sent) == "w" * 9000)


asyncio.run(_chunks())

# --- tele_join_public (fake telethon client) ---
print("tele_join_public:")


class FakeTele:
    def __init__(self, entity=None, get_entity_exc=None, member=False,
                 join_chats=None, join_exc=None):
        self.entity = entity
        self.get_entity_exc = get_entity_exc
        self.member = member
        self.join_chats = join_chats
        self.join_exc = join_exc
        self.join_called = False

    async def get_entity(self, username):
        if self.get_entity_exc:
            raise self.get_entity_exc
        return self.entity

    async def get_permissions(self, entity, user):
        if self.member:
            return MagicMock()
        raise TeleNotParticipant("not a participant")

    async def __call__(self, request):
        self.join_called = True
        if self.join_exc:
            raise self.join_exc
        m = MagicMock()
        m.chats = self.join_chats or []
        return m


def fake_channel():
    ch = MagicMock(spec=tele_types.Channel)
    ch.id = 123456789
    ch.title = "Test Channel"
    return ch


async def _join():
    # no userbot
    B.tele_client = None
    r = await B.tele_join_public("somechannel")
    check("no userbot -> error", r.get("ok") is False)
    # bad username
    B.tele_client = FakeTele(get_entity_exc=TeleUsernameNotOccupied("no such user"))
    r = await B.tele_join_public("nosuchchannel")
    check("unknown username -> error", r.get("ok") is False and "nahi mila" in r.get("error", ""))
    # user link, not channel
    usr = MagicMock(spec=tele_types.User)
    B.tele_client = FakeTele(entity=usr)
    r = await B.tele_join_public("someuser")
    check("user link -> error", r.get("ok") is False)
    # already member
    ch = fake_channel()
    B.tele_client = FakeTele(entity=ch, member=True)
    r = await B.tele_join_public("somechannel")
    check("already member", r.get("ok") is True and r.get("already") is True and r.get("title") == "Test Channel")
    # join success
    ch2 = fake_channel()
    B.tele_client = FakeTele(entity=ch2, member=False, join_chats=[ch2])
    r = await B.tele_join_public("somechannel")
    check("join ok", r.get("ok") is True and r.get("already") is False and r.get("chat_id") == -100123456789)
    # join returns nothing
    B.tele_client = FakeTele(entity=ch2, member=False, join_chats=[])
    r = await B.tele_join_public("somechannel")
    check("join empty -> error", r.get("ok") is False)


asyncio.run(_join())
B.tele_client = None

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
