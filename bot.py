import os
import re
import json
import uuid
import time
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# CONFIGURATION
# =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8640151693:AAEh8_HnwbfqGnW9sVibRe8mIUJHvXPHeEs")
PORT = int(os.environ.get("PORT", 8080))
SERVER_URL = os.environ.get("SERVER_URL", "").strip().rstrip("/")

# Global Data Store
playlists = {}       # Generated playlists: {playlist_id: [video_data]}
user_sessions = {}   # Active playlist creation sessions: {user_id: [video_data]}


# =========================
# HTML TEMPLATE
# =========================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="bn">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.css" />
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      padding: 15px;
    }
    .wrap { width: 100%; max-width: 900px; }
    h1 {
      font-size: 1.25rem;
      margin-bottom: 12px;
      color: #38bdf8;
      text-overflow: ellipsis;
      overflow: hidden;
      white-space: nowrap;
    }
    .card {
      background: #1e293b;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }
    .plyr { border-radius: 12px 12px 0 0; }
    .meta {
      padding: 15px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #1e293b;
      border-top: 1px solid #334155;
    }
    .size-info { font-size: 0.9rem; color: #94a3b8; }
    .btn {
      display: inline-block;
      padding: 8px 16px;
      background: #0284c7;
      color: white;
      text-decoration: none;
      border-radius: 6px;
      font-weight: 600;
      font-size: 0.9rem;
      transition: background 0.2s;
    }
    .btn:hover { background: #0369a1; }
    .playlist-info {
      padding: 10px 15px;
      background: #0f172a;
      font-size: 0.85rem;
      color: #38bdf8;
      border-bottom: 1px solid #334155;
    }
  </style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="card">
    __PLAYLIST_HEADER__
    <video id="player" playsinline controls data-poster="__POSTER__">
      <source src="__VIDEO_URL__" type="video/mp4" />
    </video>
    <div class="meta">
      <div class="size-info">Size: __SIZE__</div>
      <a class="btn" href="__DL__" download>⬇️ Download</a>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.polyfilled.min.js"></script>
<script>
  var playlistData = __PLAYLIST_JSON__;
  var currentIndex = 0;

  var el = document.getElementById('player');
  if (el) {
    var player = new Plyr(el, {
      controls: ['play-large','play','progress','current-time','duration','mute','volume','settings','pip','fullscreen'],
      settings: ['speed'],
      speed: { selected: 3, options: [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4] },
      keyboard: { focused: true, global: true }
    });

    player.on('ready', function() {
      player.speed = 3;
    });

    if (playlistData && playlistData.length > 1) {
      player.on('ended', function() {
        if (currentIndex < playlistData.length - 1) {
          currentIndex++;
          var nextVideo = playlistData[currentIndex];
          
          player.source = {
            type: 'video',
            title: nextVideo.title,
            sources: [{ src: nextVideo.src, type: 'video/mp4' }]
          };
          
          document.querySelector('h1').innerText = nextVideo.title;
          var sizeEl = document.querySelector('.size-info');
          if (sizeEl) sizeEl.innerText = "Size: " + nextVideo.size;
          var dlEl = document.querySelector('.btn');
          if (dlEl) dlEl.href = nextVideo.download;
          var plHeader = document.querySelector('.playlist-info');
          if (plHeader) plHeader.innerText = "🎬 Playing: " + (currentIndex + 1) + " of " + playlistData.length;

          player.play();
        }
      });
    }
  }
</script>
</body>
</html>
"""


# =========================
# HELPER FUNCTIONS
# =========================
def format_size(size_bytes):
    if not size_bytes:
        return "Unknown"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def get_base_url():
    """Render বা জেনুইন সার্ভার URL পাওয়ার ব্যাকআপ লজিক"""
    url = os.environ.get("SERVER_URL", "").strip().rstrip("/")
    if url:
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        return url
    
    # Render-এর নিজস্ব ডিফল্ট এনভায়রনমেন্ট ভ্যারিয়েবল
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        return f"https://{render_host}"
        
    return "https://your-app.onrender.com"


# =========================
# AIOHTTP WEB SERVER HANDLERS
# =========================
async def handle_home(request):
    return web.Response(text="Bot Web Server is Running Online!", content_type="text/plain")


async def handle_stream(request):
    file_id = request.match_info.get("file_id")
    app = request.app["telegram_app"]

    try:
        tg_file = await app.bot.get_file(file_id)
        video_url = tg_file.file_path
        return web.HTTPFound(location=video_url)
    except Exception as e:
        logger.error(f"Error fetching stream file: {e}")
        return web.Response(text="Error streaming video file or Link Expired.", status=404)


async def handle_player(request):
    file_id = request.match_info.get("file_id")
    base_url = get_base_url()
    stream_url = f"{base_url}/stream/{file_id}"
    
    html = HTML_TEMPLATE.replace("__TITLE__", "Streamer Video Player")
    html = html.replace("__PLAYLIST_HEADER__", "")
    html = html.replace("__VIDEO_URL__", stream_url)
    html = html.replace("__DL__", stream_url)
    html = html.replace("__SIZE__", "Telegram Stream")
    html = html.replace("__POSTER__", "")
    html = html.replace("__PLAYLIST_JSON__", "null")

    return web.Response(text=html, content_type="text/html")


async def handle_playlist_player(request):
    pl_id = request.match_info.get("pl_id")
    
    if pl_id not in playlists:
        return web.Response(text="Playlist Not Found or Expired!", status=404)

    playlist_items = playlists[pl_id]
    first_item = playlist_items[0]

    header_html = f'<div class="playlist-info">🎬 Playing: 1 of {len(playlist_items)} (Auto-play Enabled)</div>'
    json_playlist = json.dumps(playlist_items)

    html = HTML_TEMPLATE.replace("__TITLE__", first_item["title"])
    html = html.replace("__PLAYLIST_HEADER__", header_html)
    html = html.replace("__VIDEO_URL__", first_item["src"])
    html = html.replace("__DL__", first_item["download"])
    html = html.replace("__SIZE__", first_item["size"])
    html = html.replace("__POSTER__", "")
    html = html.replace("__PLAYLIST_JSON__", json_playlist)

    return web.Response(text=html, content_type="text/html")


# =========================
# TELEGRAM BOT COMMANDS
# =========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **Welcome to Streamer Bot!**\n\n"
        "📹 **Single Video:** Send or forward any video to get instant Stream & Download links.\n\n"
        "🎶 **Playlist Mode:**\n"
        "1. Send **/playlist** to start playlist mode.\n"
        "2. Send all your videos one by one.\n"
        "3. Send **/done** to generate a single Playlist link!\n\n"
        "Commands:\n"
        "/playlist - Start Playlist Mode\n"
        "/done - Finish & Create Playlist\n"
        "/cancel - Cancel Playlist Creation"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def playlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = []
    
    await update.message.reply_text(
        "🎬 **Playlist Mode Started!**\n\n"
        "Now forward or send videos one by one.\n"
        "When finished, send **/done** to get your playlist link."
    )


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if user_id not in user_sessions or not user_sessions[user_id]:
            await update.message.reply_text("❌ No active playlist session found! Send /playlist first.")
            return

        video_list = user_sessions[user_id]
        pl_id = f"pl_{uuid.uuid4().hex[:8]}"
        
        # Store playlist in memory
        playlists[pl_id] = video_list

        base_url = get_base_url()
        playlist_link = f"{base_url}/play/playlist/{pl_id}"

        reply_text = (
            f"🎉 **Playlist Created Successfully!**\n\n"
            f"🎵 **Total Videos:** {len(video_list)}\n"
            f"🔗 **Playlist Link:** {playlist_link}\n\n"
            f"⚡ *All videos will play automatically one after another!*"
        )
        
        await update.message.reply_text(reply_text, parse_mode="Markdown")
        
        # Clear session
        del user_sessions[user_id]
    except Exception as e:
        logger.error(f"Error in done_command: {e}")
        await update.message.reply_text(f"⚠️ Error creating playlist: {str(e)}")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("🗑️ Playlist session cancelled.")
    else:
        await update.message.reply_text("❌ No active playlist session to cancel.")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.video:
        return

    video = message.video
    user_id = message.from_user.id
    file_id = video.file_id
    file_size = format_size(video.file_size)
    file_name = video.file_name or f"Video_{message.message_id}.mp4"

    base_url = get_base_url()
    stream_link = f"{base_url}/stream/{file_id}"
    player_link = f"{base_url}/play/{file_id}"

    # Check if user is in Playlist Mode
    if user_id in user_sessions:
        user_sessions[user_id].append({
            "title": file_name,
            "src": stream_link,
            "download": stream_link,
            "size": file_size
        })
        count = len(user_sessions[user_id])
        await message.reply_text(f"✅ Video #{count} added to Playlist! Send more or type **/done**.", parse_mode="Markdown")
        return

    # Single Video Output
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Watch Online", url=player_link),
            InlineKeyboardButton("⬇️ Direct Download", url=stream_link)
        ]
    ])

    await message.reply_text(
        f"🎬 **Video Received!**\n\n"
        f"📁 **Name:** `{file_name}`\n"
        f"📦 **Size:** {file_size}\n\n"
        f"👇 Click below to stream or download:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


# =========================
# MAIN APP RUNNER
# =========================
async def main():
    tg_app = Application.builder().token(BOT_TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start_command))
    tg_app.add_handler(CommandHandler("playlist", playlist_command))
    tg_app.add_handler(CommandHandler("done", done_command))
    tg_app.add_handler(CommandHandler("cancel", cancel_command))
    tg_app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()

    web_app = web.Application()
    web_app["telegram_app"] = tg_app

    web_app.router.add_get("/", handle_home)
    web_app.router.add_get("/stream/{file_id}", handle_stream)
    web_app.router.add_get("/play/{file_id}", handle_player)
    web_app.router.add_get("/play/playlist/{pl_id}", handle_playlist_player)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🤖 Bot & Web Server Started Successfully on Port {PORT}!")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
