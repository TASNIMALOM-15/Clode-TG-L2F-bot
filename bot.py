import os
import re
import json
import uuid
import time
import html
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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

# Global Data Store
playlists = {}       # Generated playlists: {playlist_id: [video_data]}
user_sessions = {}   # Active playlist creation sessions: {user_id: [video_data]}


# =========================
# MODERN YOUTUBE-LIKE HTML TEMPLATE
# =========================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="bn">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__TITLE__ - Streamer Playlist</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.css" />
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 12px;
      display: flex;
      justify-content: center;
    }
    .container { width: 100%; max-width: 900px; }
    
    /* Video Box */
    .player-card {
      background: #1e293b;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
      position: relative;
    }
    .video-title {
      font-size: 1.1rem;
      font-weight: 600;
      color: #38bdf8;
      padding: 12px 15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .meta-bar {
      padding: 10px 15px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #1e293b;
      border-top: 1px solid #334155;
    }
    .size-info { font-size: 0.85rem; color: #94a3b8; }
    .btn-dl {
      display: inline-block;
      padding: 6px 14px;
      background: #0284c7;
      color: white;
      text-decoration: none;
      border-radius: 6px;
      font-weight: 600;
      font-size: 0.85rem;
    }

    /* Playlist Section UI */
    .playlist-section {
      margin-top: 15px;
      background: #1e293b;
      border-radius: 12px;
      padding: 12px;
      border: 1px solid #334155;
    }
    .playlist-header {
      font-size: 0.95rem;
      font-weight: bold;
      color: #f1f5f9;
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .playlist-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 380px;
      overflow-y: auto;
    }
    .playlist-item {
      display: flex;
      align-items: center;
      gap: 12px;
      background: #0f172a;
      padding: 8px;
      border-radius: 8px;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.2s;
    }
    .playlist-item:hover { background: #334155; }
    .playlist-item.active {
      border-color: #38bdf8;
      background: #1e293b;
    }
    .thumb-box {
      width: 70px;
      height: 45px;
      background: #000;
      border-radius: 6px;
      display: flex;
      justify-content: center;
      align-items: center;
      flex-shrink: 0;
      position: relative;
    }
    .thumb-box span { font-size: 1.2rem; }
    .item-details { flex-grow: 1; overflow: hidden; }
    .item-title {
      font-size: 0.85rem;
      font-weight: 500;
      color: #e2e8f0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .item-size {
      font-size: 0.75rem;
      color: #64748b;
      margin-top: 2px;
    }

    /* Overlay Overlay Button for Landscape/Fullscreen */
    .fs-playlist-btn {
      position: absolute;
      top: 15px;
      right: 15px;
      z-index: 10;
      background: rgba(15, 23, 42, 0.8);
      color: #38bdf8;
      border: 1px solid #38bdf8;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.8rem;
      cursor: pointer;
      backdrop-filter: blur(4px);
    }
  </style>
</head>
<body>
<div class="container">
  <div class="player-card">
    <button class="fs-playlist-btn" onclick="scrollToPlaylist()">📋 Playlist</button>
    <div class="video-title" id="main-title">__TITLE__</div>
    <video id="player" playsinline controls>
      <source src="__VIDEO_URL__" type="video/mp4" />
    </video>
    <div class="meta-bar">
      <div class="size-info" id="main-size">Size: __SIZE__</div>
      <a class="btn-dl" id="main-dl" href="__DL__" target="_blank">⬇️ Download</a>
    </div>
  </div>

  <div class="playlist-section" id="playlist-box">
    <div class="playlist-header">
      <span>🎬 Next Up / Playlist</span>
      <span id="pl-count" style="font-weight:normal; color:#94a3b8; font-size:0.85rem;"></span>
    </div>
    <div class="playlist-list" id="playlist-items"></div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.polyfilled.min.js"></script>
<script>
  var playlistData = __PLAYLIST_JSON__;
  var currentIndex = 0;
  var player;

  function initPlayer() {
    var el = document.getElementById('player');
    if (!el) return;

    player = new Plyr(el, {
      controls: ['play-large','play','progress','current-time','duration','mute','volume','settings','pip','fullscreen'],
      settings: ['speed'],
      speed: { selected: 3, options: [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4] },
      keyboard: { focused: true, global: true }
    });

    player.on('ready', function() { player.speed = 3; });

    player.on('ended', function() {
      if (playlistData && currentIndex < playlistData.length - 1) {
        playVideo(currentIndex + 1);
      }
    });

    renderPlaylistUI();
  }

  function renderPlaylistUI() {
    var container = document.getElementById('playlist-items');
    var countEl = document.getElementById('pl-count');
    if (!playlistData || playlistData.length === 0) {
      document.getElementById('playlist-box').style.display = 'none';
      return;
    }

    countEl.innerText = (currentIndex + 1) + " / " + playlistData.length;
    container.innerHTML = '';

    playlistData.forEach(function(item, idx) {
      var activeClass = (idx === currentIndex) ? 'active' : '';
      var html = '<div class="playlist-item ' + activeClass + '" onclick="playVideo(' + idx + ')">' +
                   '<div class="thumb-box"><span>▶️</span></div>' +
                   '<div class="item-details">' +
                     '<div class="item-title">' + (idx + 1) + '. ' + item.title + '</div>' +
                     '<div class="item-size">' + item.size + '</div>' +
                   '</div>' +
                 '</div>';
      container.innerHTML += html;
    });
  }

  function playVideo(idx) {
    if (!playlistData || !playlistData[idx]) return;
    currentIndex = idx;
    var item = playlistData[idx];

    player.source = {
      type: 'video',
      title: item.title,
      sources: [{ src: item.src, type: 'video/mp4' }]
    };

    document.getElementById('main-title').innerText = item.title;
    document.getElementById('main-size').innerText = "Size: " + item.size;
    document.getElementById('main-dl').href = item.download;

    renderPlaylistUI();
    player.play();
  }

  function scrollToPlaylist() {
    document.getElementById('playlist-box').scrollIntoView({ behavior: 'smooth' });
  }

  document.addEventListener('DOMContentLoaded', initPlayer);
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
    url = os.environ.get("SERVER_URL", "").strip().rstrip("/")
    if url:
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        return url
    
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        return f"https://{render_host}"
        
    return "https://your-app.onrender.com"


# =========================
# AIOHTTP SERVER HANDLERS
# =========================
async def handle_home(request):
    return web.Response(text="Bot Web Server is Running Online!", content_type="text/plain")


async def handle_stream(request):
    """অন-ডিম্যান্ড টেলিগ্রাম ডাইরেক্ট লিঙ্ক তৈরি করে স্ট্রিম রিডাইরেক্ট করা"""
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
    
    html_code = HTML_TEMPLATE.replace("__TITLE__", "Single Video Player")
    html_code = html_code.replace("__VIDEO_URL__", stream_url)
    html_code = html_code.replace("__DL__", stream_url)
    html_code = html_code.replace("__SIZE__", "Telegram Stream")
    html_code = html_code.replace("__PLAYLIST_JSON__", "null")

    return web.Response(text=html_code, content_type="text/html")


async def handle_playlist_player(request):
    pl_id = request.match_info.get("pl_id")
    
    if pl_id not in playlists:
        return web.Response(text="Playlist Not Found or Expired!", status=404)

    playlist_items = playlists[pl_id]
    first_item = playlist_items[0]
    json_playlist = json.dumps(playlist_items)

    html_code = HTML_TEMPLATE.replace("__TITLE__", html.escape(first_item["title"]))
    html_code = html_code.replace("__VIDEO_URL__", first_item["src"])
    html_code = html_code.replace("__DL__", first_item["download"])
    html_code = html_code.replace("__SIZE__", first_item["size"])
    html_code = html_code.replace("__PLAYLIST_JSON__", json_playlist)

    return web.Response(text=html_code, content_type="text/html")


# =========================
# TELEGRAM BOT HANDLERS
# =========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 <b>Welcome to Streamer Bot!</b>\n\n"
        "📹 <b>Single Video:</b> Send or forward any video to get Stream & Download links.\n\n"
        "🎶 <b>YouTube Style Playlist Mode:</b>\n"
        "1. Send <b>/playlist</b> to start playlist mode.\n"
        "2. Send or forward all videos.\n"
        "3. Send <b>/done</b> to generate a YouTube-like Playlist link!\n\n"
        "Commands:\n"
        "/playlist - Start Playlist Mode\n"
        "/done - Finish & Create Playlist\n"
        "/cancel - Cancel Session"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")


async def playlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = []
    
    await update.message.reply_text(
        "🎬 <b>Playlist Mode Started!</b>\n\n"
        "Now send or forward your videos (one by one or all together).\n"
        "When finished, send <b>/done</b> to get your playlist link.",
        parse_mode="HTML"
    )


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id

        if user_id not in user_sessions or not user_sessions[user_id]:
            await update.message.reply_text("❌ No active playlist session found! Send /playlist first.")
            return

        video_list = user_sessions[user_id]
        pl_id = f"pl_{uuid.uuid4().hex[:8]}"
        
        # Store in Memory Library
        playlists[pl_id] = video_list

        base_url = get_base_url()
        playlist_link = f"{base_url}/play/playlist/{pl_id}"

        reply_text = (
            f"🎉 <b>Playlist Created Successfully!</b>\n\n"
            f"🎵 <b>Total Videos:</b> {len(video_list)}\n"
            f"🔗 <b>Playlist Link:</b>\n{playlist_link}\n\n"
            f"⚡ <i>Supports YouTube-style UI, auto-play, manual shift & 3x speed!</i>"
        )
        
        await update.message.reply_text(reply_text, parse_mode="HTML")
        
        del user_sessions[user_id]
    except Exception as e:
        logger.error(f"Error in done_command: {e}")
        await update.message.reply_text(f"⚠️ Error creating playlist: {html.escape(str(e))}")


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

    # Playlist Mode
    if user_id in user_sessions:
        user_sessions[user_id].append({
            "title": file_name,
            "src": stream_link,
            "download": stream_link,
            "size": file_size
        })
        count = len(user_sessions[user_id])
        await message.reply_text(f"✅ Video #{count} added to Playlist! Send more or type <b>/done</b>.", parse_mode="HTML")
        return

    # Single Video
    safe_file_name = html.escape(file_name)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Watch Online", url=player_link),
            InlineKeyboardButton("⬇️ Direct Download", url=stream_link)
        ]
    ])

    await message.reply_text(
        f"🎬 <b>Video Received!</b>\n\n"
        f"📁 <b>Name:</b> {safe_file_name}\n"
        f"📦 <b>Size:</b> {file_size}\n\n"
        f"👇 Click below to stream or download:",
        reply_markup=keyboard,
        parse_mode="HTML"
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
