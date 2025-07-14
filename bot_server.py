import os
import json
from fastapi import FastAPI, Request
from telegram import Bot, InputMediaPhoto, InputMediaDocument, InputMediaVideo, InputMediaAudio
from telegram.error import TelegramError
from dotenv import load_dotenv
import base64
import tempfile
import requests

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SECRET_KEY = os.getenv("FORWARD_SECRET", "my_super_secret")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # User ID or log channel ID

MAX_SIZE = 45 * 1024 * 1024  # 45 MB
bot = Bot(BOT_TOKEN)
app = FastAPI()
DEST_CHANNELS = []

def notify_admin(text):
    """Send error or log to admin/log channel."""
    if not ADMIN_CHAT_ID:
        print("[WARN] No ADMIN_CHAT_ID set for notifications!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text[:4000],  # Telegram max message length
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"[DM ADMIN ERROR] {e}")

def load_dest_channels():
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
            chans = config.get("destination_channels", [])
            new = []
            for c in chans:
                if isinstance(c, dict):
                    new.append(c)
                elif isinstance(c, str):
                    if c.lstrip("-").isdigit():
                        new.append({"id": int(c), "username": None})
                    else:
                        new.append({"id": None, "username": c.lstrip("@")})
            return new
    except Exception as e:
        print(f"[BOT ERROR] Failed to load config.json: {e}")
        notify_admin(f"⚠️ [BotServer] Failed to load config.json: {e}")
        return []

async def resolve_dest_channels(bot, dest_channels):
    resolved = []
    updated = False
    for ch in dest_channels:
        if ch.get("id"):
            resolved.append({"id": ch["id"], "username": ch.get("username")})
        elif ch.get("username"):
            uname = ch["username"]
            try:
                chat = await bot.get_chat(f"@{uname}")
                print(f"[DEST] Resolved @{uname} -> {chat.id}")
                resolved.append({"id": chat.id, "username": uname})
                ch["id"] = chat.id
                updated = True
            except TelegramError as e:
                print(f"[BOT ERROR] Could not resolve @{uname}: {e}")
                notify_admin(f"⚠️ [BotServer] Could not resolve @{uname}: {e}")
    if updated:
        try:
            with open("config.json", "r") as f:
                config = json.load(f)
            config["destination_channels"] = resolved
            with open("config.json", "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"[BOT ERROR] Failed to update config.json: {e}")
            notify_admin(f"⚠️ [BotServer] Failed to update config.json: {e}")
    return [c["id"] for c in resolved if c.get("id")]

@app.on_event("startup")
async def startup_event():
    global DEST_CHANNELS
    print("[BOT] Loading destination channels from config.json...")
    dest_objs = load_dest_channels()
    DEST_CHANNELS.clear()
    DEST_CHANNELS.extend(await resolve_dest_channels(bot, dest_objs))
    print(f"[BOT] Final destination channel IDs: {DEST_CHANNELS}")

def cleanup_files(file_list):
    for fp in file_list:
        try:
            os.remove(fp)
        except Exception:
            pass

@app.post("/forward")
async def forward(request: Request):
    data = await request.json()

    # --- SECRET KEY CHECK ---
    if data.get("secret_key") != SECRET_KEY:
        print("[SECURITY] Wrong secret key in /forward!")
        notify_admin("🚨 [BotServer] Unauthorized forward attempt!")
        return {"status": "unauthorized"}

    print(f"[BOT DEBUG] Data received: {str(data)[:350]}")
    text = data.get("text", "")
    tag = data.get("source_tag", "")
    caption = f"{text}\n\n{tag}".strip() if tag else text

    # Single media (not album)
    media_bytes = data.get("media_bytes")
    media_filename = data.get("media_filename")
    media_type = data.get("media_type")
    album = data.get("album", False)
    # Album (media group)
    media_bytes_list = data.get("media_bytes_list")
    media_filename_list = data.get("media_filename_list")
    media_type_list = data.get("media_type_list")

    for dest_id in DEST_CHANNELS:
        try:
            # ---- ALBUM (MEDIA GROUP) ----
            if album and media_bytes_list and media_type_list:
                media_group = []
                temp_files = []
                for idx, (file_b64, fname, typ) in enumerate(zip(media_bytes_list, media_filename_list, media_type_list)):
                    file_bytes = base64.b64decode(file_b64)
                    if len(file_bytes) > MAX_SIZE:
                        warn = f"[BOT ERROR] Album file {fname} too large, skipping."
                        print(warn)
                        notify_admin(warn)
                        continue
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fname)[1]) as tf:
                        tf.write(file_bytes)
                        tf.flush()
                        temp_files.append(tf.name)
                        cap = caption if idx == 0 else None
                        if typ == "MessageMediaPhoto":
                            media_group.append(InputMediaPhoto(media=tf.name, caption=cap))
                        elif typ == "MessageMediaVideo":
                            media_group.append(InputMediaVideo(media=tf.name, caption=cap))
                        elif typ == "MessageMediaAudio":
                            media_group.append(InputMediaAudio(media=tf.name, caption=cap))
                        else:
                            media_group.append(InputMediaDocument(media=tf.name, caption=cap))
                if media_group:
                    await bot.send_media_group(chat_id=dest_id, media=media_group)
                cleanup_files(temp_files)
            # ---- SINGLE MEDIA ----
            elif media_bytes and media_filename and media_type:
                file_bytes = base64.b64decode(media_bytes)
                if len(file_bytes) > MAX_SIZE:
                    warn = f"[BOT ERROR] Single file {media_filename} too large, skipping."
                    print(warn)
                    notify_admin(warn)
                    continue
                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(media_filename)[1]) as tf:
                    tf.write(file_bytes)
                    tf.flush()
                    temp_file = tf.name
                with open(temp_file, "rb") as f:
                    if media_type == "MessageMediaPhoto":
                        await bot.send_photo(chat_id=dest_id, photo=f, caption=caption)
                    elif media_type == "MessageMediaVideo":
                        await bot.send_video(chat_id=dest_id, video=f, caption=caption)
                    elif media_type == "MessageMediaAudio":
                        await bot.send_audio(chat_id=dest_id, audio=f, caption=caption)
                    else:
                        await bot.send_document(chat_id=dest_id, document=f, caption=caption or None)
                os.remove(temp_file)
            # ---- TEXT ONLY ----
            else:
                await bot.send_message(chat_id=dest_id, text=caption)
            print(f"[POSTED] To {dest_id}: {text[:40]}...")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[BOT ERROR] {e}")
            print(tb)
            notify_admin(f"⚠️ [BotServer Error]\nDest: {dest_id}\n{e}\n{tb[:1000]}")
    return {"status": "ok"}

# To run: uvicorn bot_server:app --host 0.0.0.0 --port 8000