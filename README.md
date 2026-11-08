# Post Link Fetcher Bot 📥

Telegram post ki link bhejo, us post ki file pao!

## Ye bot kya karta hai?

Tum kisi Telegram post ki link bhejte ho (jaise `https://t.me/channelname/123`),
bot us post ki file nikaal kar tumhe bhej deta hai. Un channels ke liye bhi
kaam karta hai jahan forwarding allowed nahi hai — **userbot** (@Costombomber
user account) un channels ka member hai, isliye file fetch kar leta hai;
bot ko channel mein add karne ki zaroorat nahi hai.

**Supported media:** document, photo, video, audio, animation (GIF),
voice, video note. Photo ko document ke roop mein bhejta hai taaki
quality bani rahe.

**Limit:** Sirf **50 MB** tak ki files fetch kar sakta hai (Bot API limit).

## Link ke examples

- `https://t.me/channelname/123` (public channel)
- `https://t.me/c/1234567890/123` (private channel/group)
- `t.me/channelname/123`

## Setup

### 1. .env banao

`.env.example` ko copy karke `.env` banao, aur apni values bharo:

```
BOT_TOKEN=apna_bot_token_yahan
API_ID=apna_api_id_yahan
API_HASH=apna_api_hash_yahan
SESSION_STRING=apna_session_string_yahan
```

- **BOT_TOKEN** — BotFather se naye bot ka token.
- **API_ID / API_HASH** — [my.telegram.org](https://my.telegram.org) se milega.
  (Agar Aand bot ki `.env` hai to bot khud wahan se padh lega,
  par Railway/hosting par ye variables set karna behtar hai.)
- **SESSION_STRING** — @Costombomber user account ka session string.
  Ye **userbot** chalata hai, jo private/protected (no-forward) channels
  ki posts fetch karta hai — un channels mein ye account member hona
  chahiye. Session string sirf **environment variable** (Railway Variables)
  mein daalo, kabhi file ya repo mein mat likho. Agar SESSION_STRING
  na ho to bot sirf bot account se fetch karega (public channels).

> ⚠️ `.env` mein real token kabhi GitHub par mat daalo. Sirf
> `.env.example` (placeholders) repo mein rakho.

### 2. Dependencies install karo

```
pip install -r requirements.txt
```

### 3. Bot chalao

```
python bot.py
```

## Kaise use karein?

1. Bot ko Telegram par kholo, `/start` bhejo.
2. Kisi post ki link copy karke bhejo.
3. Bot file fetch karke tumhe bhej dega.

## Agar error aaye

- **“Us channel/group ka access nahi hai”** — userbot account (@Costombomber)
  us channel ka member nahi hai; use channel mein add karo, phir link bhejo.
- **“Link galat hai”** — link dobara check karo.
- **“Post nahi mili”** — post delete ho gayi hogi.
- **“File bahut badi hai”** — 50 MB se badi file bot fetch nahi kar sakta.
- **“Is post mein koi file nahi hai”** — post sirf text hai.
