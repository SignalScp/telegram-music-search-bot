import os
from dotenv import load_dotenv
from typing import List
import logging
import asyncio
import subprocess
import tempfile

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from telegram.error import BadRequest

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def search_music_itunes(query: str) -> List[dict]:
    """Ищет треки в iTunes Search API."""
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": 5,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    tracks = []
    for item in data.get("results", [])[:5]:
        tracks.append(
            {
                "title": item.get("trackName", "Без названия"),
                "artist": item.get("artistName", "Неизвестный исполнитель"),
                "link": item.get("trackViewUrl", ""),
            }
        )

    return tracks


def download_from_youtube_sync(artist: str, title: str) -> bytes:
    """Синхронная загружка с YouTube через yt-dlp."""
    search_query = f"{artist} {title}"
    logger.info(f"🔍 Ищу на YouTube: {search_query}")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "%(title)s.%(ext)s")

        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "128",
            "-o",
            output_path,
            f"ytsearch:{search_query}",
        ]

        logger.info("⚡ Запускаю yt-dlp...")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )

            logger.info(f"📊 yt-dlp return code: {result.returncode}")

            if result.returncode != 0:
                logger.error(f"❌ yt-dlp stderr: {result.stderr[:200]}")
                return None

            files = os.listdir(tmpdir)
            mp3_files = [f for f in files if f.endswith(".mp3")]

            if not mp3_files:
                logger.error("❌ MP3 файлы не найдены")
                return None

            file_path = os.path.join(tmpdir, mp3_files[0])
            file_size = os.path.getsize(file_path)
            logger.info(f"✅ MP3 найден: {file_size / 1024 / 1024:.2f} MB")

            with open(file_path, "rb") as f:
                audio_data = f.read()

            logger.info(f"✅ Готово {len(audio_data) / 1024 / 1024:.2f} MB")
            return audio_data

        except subprocess.TimeoutExpired:
            logger.error("❌ Timeout (120 сек)")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Привет! Отправь название песни.\n\n"
        "🔍 Поиск с высокой релевантностью.\n\n"
        "🎶 Например: `linkin park numb`\n\n"
        "⚡ Клик на трек = MP3!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Напиши название песни.\nКлик — скачиваю MP3."
    )


def build_tracks_keyboard(tracks: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, t in enumerate(tracks):
        text = (
            f"{t['artist']} - {t['title']}"
            if t.get("artist")
            else t.get("title", "Трек")
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=text[:60], callback_data=f"track_{i}"
                )
            ]
        )
    return InlineKeyboardMarkup(buttons)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    query = update.message.text.strip()
    if not query:
        return

    logger.info(f"🔍 Поиск: {query}")
    msg = await update.message.reply_text("🔍 Поиск...")

    try:
        tracks = await search_music_itunes(query)
    except Exception as e:
        logger.error(f"❌ iTunes ошибка: {e}")
        await msg.edit_text("❌ Ошибка.")
        return

    if not tracks:
        await msg.edit_text("🔍 Ничего.")
        return

    context.user_data["tracks"] = tracks

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный')} — {t.get('title', 'Без названия')}"
        text_lines.append(line)

    text_lines.append("\n🔊 Клик нля скачивания")

    await msg.edit_text(
        "\n".join(text_lines), reply_markup=build_tracks_keyboard(tracks)
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Universal callback handler."""
    query = update.callback_query
    
    logger.info(f"🔘 Callback: {query.data}")
    
    if not query.data.startswith("track_"):
        logger.warning(f"❌ Непознанные callback_data: {query.data}")
        await query.answer()
        return
    
    await query.answer()
    
    try:
        track_index = int(query.data.split("_")[1])
    except (IndexError, ValueError) as e:
        logger.error(f"❌ Парс ошибка: {e}")
        await query.edit_message_text("❌ Ошибка.")
        return

    tracks = context.user_data.get("tracks", [])

    if track_index >= len(tracks):
        logger.error(f"❌ Индекс вне диапазона")
        await query.edit_message_text("❌ Ошибка.")
        return

    track = tracks[track_index]
    logger.info(f"🎵 Начинаю: {track['artist']} - {track['title']}")

    await query.edit_message_text(
        f"🎵 {track['artist']} - {track['title']}\n\n⚡ Гружу...\n(1-3 мин)"
    )

    try:
        audio_data = await asyncio.to_thread(
            download_from_youtube_sync, track["artist"], track["title"]
        )

        if not audio_data:
            logger.error("❌ Нет audio_data")
            await query.edit_message_text(
                f"❌ Не нашел на YouTube."
            )
            return

        logger.info("📤 Отправляю...")
        
        await query.message.reply_audio(
            audio=audio_data,
            title=track["title"],
            performer=track["artist"],
        )

        await query.edit_message_text(f"✅ Готово!")
        logger.info("✅ Отправлено!")

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:50]}")


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # IMPORTANT: CallbackQueryHandler must be BEFORE MessageHandler for proper ordering
    application.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🚀 Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
