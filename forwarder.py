import asyncio
import os
import re
import json
import time
import logging
import requests
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv
from collections import defaultdict
import base64

CONFIG_FILE = "config.json"
MEDIA_DIR = "media"
MAX_SIZE = 45 * 1024 * 1024  # 45 MB

# Load .env
load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
FORWARD_URL = os.getenv("FORWARD_URL", "http://localhost:8000/forward")
SECRET_KEY = os.getenv("FORWARD_SECRET", "my_super_secret")
default_admin = int(os.getenv("ADMIN_ID", "6100298605"))

logging.basicConfig(level=logging.WARNING, format='[%(asctime)s] %(levelname)s: %(message)s')

if not os.path.exists('sessions'):
    os.makedirs('sessions')
if not os.path.exists(MEDIA_DIR):
    os.makedirs(MEDIA_DIR)

# Clean up any old media files at startup
for f in os.listdir(MEDIA_DIR):
    try:
        os.remove(os.path.join(MEDIA_DIR, f))
    except Exception:
        pass

client = TelegramClient('sessions/forwarder_session', api_id, api_hash)
forwarding_enabled = True

def get_full_channel_id(entity):
    eid = int(entity.id)
    if abs(eid) > 1_000_000_000:
        return f"-100{abs(eid)}"
    return str(eid)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                sources = [dict(x) for x in data.get("source_channels", [])]
                dests = []
                for d in data.get("destination_channels", []):
                    if isinstance(d, dict):
                        dests.append(d)
                    elif isinstance(d, str):
                        if d.lstrip("-").isdigit():
                            dests.append({"id": str(d), "username": None})
                        else:
                            dests.append({"id": None, "username": d.lstrip("@")})
                admin_ids = set(int(x) for x in data.get("admin_ids", [default_admin]))
                show_source = data.get("show_source", True)
                return (
                    sources,
                    dests,
                    admin_ids,
                    show_source
                )
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
    return [], [], set([default_admin]), True

def save_config(source_channels, destination_channels, admin_ids, show_source):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "source_channels": [dict(x) for x in source_channels],
                "destination_channels": destination_channels,
                "admin_ids": list(admin_ids),
                "show_source": show_source
            }, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save config: {e}")

source_channels, destination_channels, admin_ids, show_source = load_config()

def reload_config():
    global source_channels, destination_channels, admin_ids, show_source
    source_channels, destination_channels, admin_ids, show_source = load_config()

def remove_mentions(text):
    if not text:
        return text
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def is_channel_allowed(cid):
    return any(str(cid) == str(sc['id']) for sc in source_channels)

album_buffer = defaultdict(list)
album_last_seen = {}

async def notify_admin_async(text):
    for admin_id in admin_ids:
        try:
            await client.send_message(admin_id, text[:4000])
        except Exception:
            pass

def send_to_bot_server(payload):
    payload["secret_key"] = SECRET_KEY
    for attempt in range(3):
        try:
            requests.post(FORWARD_URL, json=payload, timeout=30)
            print(f"[FORWARDED] {payload['text'][:40]}... to bot server.")
            break
        except Exception as e:
            logging.error(f"[HYBRID_ERROR] Failed to POST: {e}")
            time.sleep(2)
    else:
        asyncio.create_task(
            notify_admin_async("⚠️ [Forwarder ERROR] Failed to POST message to bot server after 3 tries.")
        )

@client.on(events.NewMessage)
async def forward_message(event):
    global forwarding_enabled, show_source
    chat = await event.get_chat()
    uname = getattr(chat, "username", None)
    cid = get_full_channel_id(chat)
    print(f"[ALL_MSGS] username={uname}, id={cid}, text={event.message.text[:40] if event.message.text else None}")
    if not is_channel_allowed(cid):
        print(f"[SKIP] Message from {uname or cid} not in source_channels, skipping.")
        return
    if not forwarding_enabled:
        print("[SKIP] Forwarding paused.")
        return
    try:
        message = event.message
        source_name = getattr(chat, 'title', None) or uname or cid
        tag = f"Source: {source_name}"
        if message.grouped_id:
            group_id = (event.chat_id, message.grouped_id)
            album_buffer[group_id].append((event, tag))
            album_last_seen[group_id] = time.time()
            asyncio.create_task(debounce_album_send(group_id))
        else:
            clean_caption = remove_mentions(message.text) if message.text else ""
            caption_with_source = f"{clean_caption}\n\n{tag}".strip() if show_source else clean_caption
            payload = {
                "text": clean_caption,
                "source_tag": tag if show_source else "",
                "media_bytes": None,
                "media_filename": None,
                "media_type": None,
                "caption": caption_with_source,
                "album": False
            }
            if message.media:
                file_path = await message.download_media(file=MEDIA_DIR + "/")
                if os.path.getsize(file_path) > MAX_SIZE:
                    warn_msg = f"🚫 File too large to forward ({os.path.basename(file_path)}, {os.path.getsize(file_path)//1024//1024}MB)."
                    logging.warning(warn_msg)
                    await notify_admin_async(warn_msg)
                    os.remove(file_path)
                    return
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                    file_b64 = base64.b64encode(file_bytes).decode('utf-8')
                payload["media_bytes"] = file_b64
                payload["media_filename"] = os.path.basename(file_path)
                payload["media_type"] = type(message.media).__name__
                os.remove(file_path)
            send_to_bot_server(payload)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        await forward_message(event)
    except Exception as e:
        logging.error(f"Error in hybrid forward: {e}")
        await notify_admin_async(f"⚠️ [Forwarder ERROR] {e}")

async def debounce_album_send(group_id, debounce_sec=1.5):
    await asyncio.sleep(debounce_sec)
    last = album_last_seen.get(group_id)
    if last and time.time() - last >= debounce_sec:
        await process_album(group_id)
        album_last_seen.pop(group_id, None)

async def process_album(group_id):
    global show_source
    events_group = album_buffer.pop(group_id, [])
    if not events_group:
        return
    events_group.sort(key=lambda x: x[0].message.id)
    tag = events_group[0][1]
    clean_caption = remove_mentions(events_group[0][0].message.text) if events_group[0][0].message.text else ""
    caption_with_source = f"{clean_caption}\n\n{tag}".strip() if show_source else clean_caption

    file_b64s = []
    file_names = []
    media_types = []
    for e, _ in events_group:
        if e.message.media:
            fp = await e.message.download_media(file=MEDIA_DIR + "/")
            if os.path.getsize(fp) > MAX_SIZE:
                warn_msg = f"🚫 Album file too large to forward ({os.path.basename(fp)}, {os.path.getsize(fp)//1024//1024}MB)."
                logging.warning(warn_msg)
                await notify_admin_async(warn_msg)
                os.remove(fp)
                continue
            with open(fp, "rb") as f:
                file_bytes = f.read()
                file_b64s.append(base64.b64encode(file_bytes).decode('utf-8'))
                file_names.append(os.path.basename(fp))
                media_types.append(type(e.message.media).__name__)
            os.remove(fp)
    if not file_b64s:
        return
    payload = {
        "text": clean_caption,
        "source_tag": tag if show_source else "",
        "media_bytes_list": file_b64s,
        "media_filename_list": file_names,
        "media_type_list": media_types,
        "caption": caption_with_source,
        "album": True,
    }
    send_to_bot_server(payload)

@client.on(events.NewMessage(pattern=r'^/'))
async def admin_commands(event):
    global forwarding_enabled, source_channels, destination_channels, admin_ids, show_source
    sender = int(event.sender_id)
    cmd = event.raw_text.strip()

    if sender not in admin_ids:
        return

    if cmd.startswith("/adddest "):
        ch = cmd.split(maxsplit=1)[1].strip().lstrip("@")
        try:
            entity = await client.get_entity(ch)
            resolved_id = get_full_channel_id(entity)
            resolved_username = getattr(entity, "username", None)
            resolved_title = getattr(entity, "title", None)
            if any(str(d.get("id")) == str(resolved_id) or (d.get("username") and d.get("username").lower() == (resolved_username or ch).lower()) for d in destination_channels):
                await event.reply(f"Channel {resolved_username or resolved_id} already in destination list.")
                return
            destination_channels.append({"id": resolved_id, "username": resolved_username or ch})
            save_config(source_channels, destination_channels, admin_ids, show_source)
            reload_config()
            await event.reply(f"✅ Added destination: {resolved_username or resolved_id} (ID: {resolved_id}, Title: {resolved_title}) (Saved to config.json!)")
        except Exception as e:
            await event.reply(f"❌ Could not resolve {ch}: {e}")
        return

    if cmd.startswith("/removedest "):
        ch = cmd.split(maxsplit=1)[1].strip().lstrip("@")
        before = len(destination_channels)
        destination_channels = [
            d for d in destination_channels
            if not (str(d.get("id")) == ch or (d.get("username") and d.get("username").lower() == ch.lower()))
        ]
        if len(destination_channels) < before:
            save_config(source_channels, destination_channels, admin_ids, show_source)
            reload_config()
            await event.reply(f"✅ Removed destination: {ch} (Saved to config.json!)")
        else:
            await event.reply(f"Channel {ch} not found in destination list.")
        return

    if cmd.startswith("/setdest "):
        chans = [c.strip().lstrip("@") for c in cmd.split(maxsplit=1)[1].split(",") if c.strip()]
        newdests = []
        for ch in chans:
            try:
                entity = await client.get_entity(ch)
                resolved_id = get_full_channel_id(entity)
                resolved_username = getattr(entity, "username", None)
                newdests.append({"id": resolved_id, "username": resolved_username or ch})
            except Exception:
                continue
        destination_channels[:] = newdests
        save_config(source_channels, destination_channels, admin_ids, show_source)
        reload_config()
        await event.reply(
            "✅ Destination channels set to: " +
            ", ".join(f"{d.get('username') or d.get('id')}" for d in destination_channels)
        )
        return

    if cmd.startswith("/addsource "):
        ch = cmd.split(maxsplit=1)[1].strip().lstrip("@")
        try:
            entity = await client.get_entity(ch)
            resolved_id = get_full_channel_id(entity)
            resolved_username = getattr(entity, "username", None)
            resolved_title = getattr(entity, "title", None)
            if any(sc['id'] == resolved_id for sc in source_channels):
                await event.reply(f"Channel {resolved_username or resolved_id} already in the source list.")
            else:
                source_channels.append({'id': resolved_id, 'username': resolved_username})
                save_config(source_channels, destination_channels, admin_ids, show_source)
                reload_config()
                await event.reply(f"✅ Added source: {resolved_username or resolved_id} (ID: {resolved_id}, Title: {resolved_title}) (Saved to config.json!)")
        except Exception as e:
            await event.reply(f"❌ Could not resolve {ch}: {e}")
        return

    if cmd.startswith("/removesource "):
        ch = cmd.split(maxsplit=1)[1].strip()
        before = len(source_channels)
        # match by id or username
        source_channels[:] = [
            sc for sc in source_channels
            if not (
                str(sc['id']) == ch or
                (sc.get('username') and sc.get('username').lower() == ch.lower())
            )
        ]
        if len(source_channels) < before:
            save_config(source_channels, destination_channels, admin_ids, show_source)
            reload_config()
            await event.reply(f"✅ Removed source channel: {ch} (Saved to config.json!)")
        else:
            await event.reply(f"Channel {ch} not found in source list.")
        return

    # --- Admin add/remove/backup/restore ---
    if cmd.startswith("/addadmin"):
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                new_admin = int(reply_msg.sender_id)
                if new_admin in admin_ids:
                    await event.reply(f"User ID {new_admin} is already an admin.")
                else:
                    admin_ids.add(new_admin)
                    save_config(source_channels, destination_channels, admin_ids, show_source)
                    reload_config()
                    await event.reply(f"✅ Added admin by reply: `{new_admin}` (Saved to config.json)")
        else:
            parts = cmd.split()
            if len(parts) == 2:
                try:
                    new_admin = int(parts[1])
                    if new_admin in admin_ids:
                        await event.reply(f"User ID {new_admin} is already an admin.")
                    else:
                        admin_ids.add(new_admin)
                        save_config(source_channels, destination_channels, admin_ids, show_source)
                        reload_config()
                        await event.reply(f"✅ Added admin: `{new_admin}` (Saved to config.json)")
                except Exception:
                    await event.reply("❌ Usage: /addadmin <user_id>")
            else:
                await event.reply("❌ Usage: /addadmin <user_id> or reply to user")
        return

    if cmd.startswith("/removeadmin"):
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                remove_admin = int(reply_msg.sender_id)
                if remove_admin not in admin_ids:
                    await event.reply(f"User ID {remove_admin} is not an admin.")
                elif len(admin_ids) == 1:
                    await event.reply("❌ At least one admin must remain.")
                else:
                    admin_ids.remove(remove_admin)
                    save_config(source_channels, destination_channels, admin_ids, show_source)
                    reload_config()
                    await event.reply(f"✅ Removed admin by reply: `{remove_admin}` (Saved to config.json)")
        else:
            parts = cmd.split()
            if len(parts) == 2:
                try:
                    remove_admin = int(parts[1])
                    if remove_admin not in admin_ids:
                        await event.reply(f"User ID {remove_admin} is not an admin.")
                    elif len(admin_ids) == 1:
                        await event.reply("❌ At least one admin must remain.")
                    else:
                        admin_ids.remove(remove_admin)
                        save_config(source_channels, destination_channels, admin_ids, show_source)
                        reload_config()
                        await event.reply(f"✅ Removed admin: `{remove_admin}` (Saved to config.json)")
                except Exception:
                    await event.reply("❌ Usage: /removeadmin <user_id>")
            else:
                await event.reply("❌ Usage: /removeadmin <user_id> or reply to user")
        return

    if cmd == "/backup":
        if os.path.exists(CONFIG_FILE):
            await event.reply("Here is your config.json backup ⬇️")
            await client.send_file(event.chat_id, CONFIG_FILE)
        else:
            await event.reply("No config.json found to backup.")
        return

    if cmd == "/restore":
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.file:
                await reply_msg.download_media(CONFIG_FILE)
                reload_config()
                await event.reply("✅ Config restored from uploaded file!")
            else:
                await event.reply("Please reply to a config.json file with /restore.")
        else:
            await event.reply("Reply to a config.json file with /restore.")
        return

    if cmd.startswith("/showsource"):
        parts = cmd.split()
        if len(parts) == 2 and parts[1] in ("on", "off"):
            show_source = (parts[1] == "on")
            save_config(source_channels, destination_channels, admin_ids, show_source)
            reload_config()
            await event.reply(f"✅ Source tag in forwarded messages is now {'ON' if show_source else 'OFF'}.")
        else:
            await event.reply("Usage: /showsource on  or  /showsource off")
        return

    if cmd == "/help":
        await event.reply(
            "Admin commands:\n"
            "/start - Enable forwarding\n"
            "/stop - Pause forwarding\n"
            "/status - Show if bot is forwarding\n"
            "/showconfig - Show current channels\n"
            "/addsource <channel username or id>\n"
            "/removesource <channel id or username>\n"
            "/adddest <channel username or id>\n"
            "/removedest <channel username or id>\n"
            "/setdest <ch1,ch2,...>  - full replace\n"
            "/showsource on|off - Toggle 'Source:' in forwards\n"
            "/addadmin <user_id> or reply to user\n"
            "/removeadmin <user_id> or reply to user\n"
            "/backup - Download config.json\n"
            "/restore (reply to file) - Restore config.json"
        )
    elif cmd == "/start":
        forwarding_enabled = True
        await event.reply("✅ Forwarding enabled!")
    elif cmd == "/stop":
        forwarding_enabled = False
        await event.reply("⛔ Forwarding paused. No messages will be forwarded.")
    elif cmd == "/status":
        status = "enabled ✅" if forwarding_enabled else "paused ⛔"
        await event.reply(f"Bot forwarding is currently *{status}*.")
    elif cmd == "/showconfig":
        pretty_sources = [
            f"{i+1}. {sc.get('username') or '[NO_USERNAME]'} (id: {sc['id']})"
            for i, sc in enumerate(source_channels)
        ]
        pretty_dests = [
            f"{i+1}. {d.get('username') or '[NO_USERNAME]'} (id: {d.get('id')})"
            for i, d in enumerate(destination_channels)
        ]
        await event.reply(
            "Sources:\n" + "\n".join(pretty_sources) +
            "\nDestinations:\n" + "\n".join(pretty_dests) +
            f"\nAdmins: {list(admin_ids)}\nShow source tag: {show_source}"
        )
    else:
        await event.reply("❓ Unknown command. Type /help.")

async def main():
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass