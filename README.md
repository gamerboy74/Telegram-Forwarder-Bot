# 🔄 Telegram Hybrid Forwarder Bot

A production-ready, privacy-respecting Telegram message forwarding bot built using **Telethon** and **FastAPI**. Supports advanced forwarding features such as:

- 💬 Text-only messages
- 🖼️ Single media files (photo, video, audio, documents)
- 📚 Media groups (albums)
- 🔁 Dynamic channel management via admin commands
- 🔐 Secret-key-based API for secure data posting
- 🔧 Fully configurable with JSON and `.env` file

> All media are processed **locally**, and nothing is stored permanently.

---

## ✨ Features

### 🔁 Core Functionality

- **Auto-forward messages** from any source to multiple destinations
- **Supports media types:** photos, videos, audios, documents, albums
- **Remove mentions, links & credits** from text
- **Admin commands via Telegram** (add/remove source/dest channels, backup config, etc.)
- **Handles media size up to 45 MB**
- **Album grouping with debounce handling**

### 🛠 Tech Stack

- `Telethon` – Telegram client
- `FastAPI` – REST API backend
- `python-telegram-bot` – For bot server
- `Uvicorn` – ASGI server for FastAPI
- `dotenv`, `requests`, `base64`, `logging` – Utility libraries

---

## 🚀 Quick Start

### 🔧 Prerequisites

- Python 3.8+
- Telegram API credentials (`API_ID`, `API_HASH`, `BOT_TOKEN`)
- Git

### 📦 Installation

```bash
git clone https://github.com/your-username/telegram-hybrid-forwarder.git
cd telegram-hybrid-forwarder

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Use venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

### 📁 Environment Variables

Create a `.env` file in the root:

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
ADMIN_ID=your_telegram_id
FORWARD_SECRET=my_super_secret (BOTH FOR BOT SERVER AND FORWARDER)
FORWARD_URL=http://localhost:8000/forward
```

---

## ⚙️ Usage

### 1. 🔌 Start the Bot Forwarder

```bash
python user_forwarder.py
```

### 2. 🌐 Start the Bot Server (FastAPI)

```bash
uvicorn bot_server:app --host 0.0.0.0 --port 8000
```

---

## 💻 API Documentation

### 🔐 Endpoint: `POST /forward`

#### Request (JSON)

```json
{
  "secret_key": "my_super_secret",
  "text": "Example caption",
  "source_tag": "Source: ExampleChannel",
  "media_bytes": "base64-encoded string",
  "media_filename": "example.jpg",
  "media_type": "MessageMediaPhoto",
  "album": false
}
```

#### Media Group Support

```json
{
  "media_bytes_list": ["..."],
  "media_filename_list": ["image1.jpg", "image2.jpg"],
  "media_type_list": ["MessageMediaPhoto", "MessageMediaPhoto"],
  "album": true
}
```

---

## 🔧 Admin Commands

| Command                     | Description                   |
| --------------------------- | ----------------------------- |
| `/addsource <@username>`    | Add source channel            |
| `/removesource <@username>` | Remove source channel         |
| `/adddest <@username>`      | Add destination               |
| `/removedest <@username>`   | Remove destination            |
| `/setdest ch1,ch2`          | Set all destination channels  |
| `/start`                    | Resume forwarding             |
| `/stop`                     | Pause forwarding              |
| `/status`                   | Show current state            |
| `/showsource on/off`        | Toggle source tag             |
| `/addadmin <user_id>`       | Add admin                     |
| `/removeadmin <user_id>`    | Remove admin                  |
| `/backup`                   | Download config.json          |
| `/restore`                  | Upload config.json to restore |

---

## 🧪 Testing

Manual testing:

1. Add channels
2. Post messages in source
3. Verify delivery in destination

---

## 📁 Project Structure

```
telegram-hybrid-forwarder/
├── media/                   # Temp files
├── sessions/                # Telethon session
├── __pycache__/             # Python cache
├── .env                     # Environment variables
├── anon.session             # Telegram session
├── bot_server.py            # FastAPI server
├── user_forwarder.py        # Telethon bot
├── config.json              # Dynamic config
├── requirements.txt         # Dependencies
```

---

## 🔒 Security

- Secret key verification for `/forward`
- Media size limit (45MB)
- Admin control via bot
- Rate-limited album sending

---

## 🤝 Contributing

```bash
git clone <repo-url>
git checkout -b feature/your-feature
# Make changes
git commit -m "Added feature"
git push
```

Then submit a PR 🙌

## 🙏 Acknowledgements

- [Telethon](https://github.com/LonamiWebs/Telethon)
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [FastAPI](https://github.com/tiangolo/fastapi)
