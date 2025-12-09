import os
from dotenv import load_dotenv
from typing import List
import logging
import asyncio
import subprocess
import tempfile
from pathlib import Path

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from telegram.error import BadRequest

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def search_music_itunes(query: str) -> List[dict]:
    """Ищет треки основному iTunes Search API."""
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": 5
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    tracks = []
    for item in data.get("results", [])[:5]:
        tracks.append({
            "title": item.get("trackName", "Без названия"),
            "artist": item.get("artistName", "Неизвестный исполнитель"),
            "link": item.get("trackViewUrl", "")
        })

    return tracks


def download_from_youtube_sync(artist: str, title: str) -> bytes:
    """Синхронная функция для загружки с YouTube через yt-dlp."""
    search_query = f"{artist} {title}"
    logger.info(f"🔍 Поиск YouTube: {search_query}")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "%(title)s.%(ext)s")
        
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "192",
            "-o", output_path,
            "--quiet",
            "--no-warnings",
            "ytsearch:" + search_query
        ]
        
        try:
            logger.info(f"⚡ Начинаю загружку YouTube...")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            logger.info(f"🔍 Ответ yt-dlp (return code: {result.returncode})")
            
            if result.returncode != 0:
                logger.error(f"❌ yt-dlp error stdout: {result.stdout}")
                logger.error(f"❌ yt-dlp error stderr: {result.stderr}")
                return None
            
            # Проверь какие файлы были скачаны
            files = os.listdir(tmpdir)
            logger.info(f"📄 Файлы в tmpdir: {files}")
            
            mp3_files = [f for f in files if f.endswith(".mp3")]
            
            if not mp3_files:
                logger.error("❌ MP3 файлы не найдены")
                return None
            
            file_path = os.path.join(tmpdir, mp3_files[0])
            file_size = os.path.getsize(file_path)
            logger.info(f"🎵 Мп3 скачан: {mp3_files[0]} ({file_size / 1024 / 1024:.2f} MB)")
            
            # Обычные данные аудиофайла
            with open(file_path, "rb") as f:
                audio_data = f.read()
            
            logger.info(f"🚀 Все готово! Протагружено {len(audio_data) / 1024 / 1024:.2f} MB")
            return audio_data
        
        except subprocess.TimeoutExpired:
            logger.error("❌ Таймаут yt-dlp (120 секунд)")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка yt-dlp: {type(e).__name__}: {e}")
            return None


async def download_from_youtube(artist: str, title: str) -> bytes:
    """Асинхронная загружка с YouTube."""
    return await asyncio.to_thread(download_from_youtube_sync, artist, title)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Привет! Отправь мне название песни, и я найду тае для тебя!\n\n"
        "🔍 Поиск со высокой релевантностью.\n\n"
        "🎶 Отправь: `linkin park numb` или `oxxxymiron город`\n\n"
        "⚡ Кликни — скачиваю полные MP3.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Просто напиши название песни.\n\n"
        "🔊 Кликни — бот скачиваю полную MP3."
    )


def build_tracks_keyboard(tracks: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, t in enumerate(tracks):
        text = f"{t['artist']} - {t['title']}" if t.get("artist") else t.get("title", "Трек")
        buttons.append([InlineKeyboardButton(text=text[:60], callback_data=f"download_{i}")])
    return InlineKeyboardMarkup(buttons)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    query = update.message.text.strip()
    if not query:
        return

    msg = await update.message.reply_text("🔍 Поиск треков...")
    logger.info(f"🔍 Поиск: {query}")

    try:
        tracks = await search_music_itunes(query)
    except Exception as e:
        logger.error(f"❌ Ошибка iTunes: {e}")
        await msg.edit_text("❌ Ошибка поиска.")
        return

    if not tracks:
        await msg.edit_text("🔍 Ничего не найдено.")
        return

    context.user_data["tracks"] = tracks

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный')} — {t.get('title', 'Без названия')}"
        text_lines.append(line)

    text_lines.append("\n🔊 Кликни для скачивания MP3.")

    await msg.edit_text(
        "\n".join(text_lines),
        reply_markup=build_tracks_keyboard(tracks),
    )


async def handle_download_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    track_index = int(query.data.split("_")[1])
    tracks = context.user_data.get("tracks", [])

    if track_index >= len(tracks):
        await query.edit_message_text("❌ Ошибка.")
        return

    track = tracks[track_index]
    logger.info(f"🎵 Начинаю скачивание: {track['artist']} - {track['title']}")
    
    await query.edit_message_text(
        f"🎵 {track['artist']} - {track['title']}\n\n⚡ Гружу с YouTube...\n(может зайти до 2-3 минут)"
    )

    try:
        logger.info("🎵 Ожидаю скачивание...")
        audio_data = await download_from_youtube(track["artist"], track["title"])

        if not audio_data:
            logger.error("❌ Не удалось скачать audio_data")
            await query.edit_message_text(
                f"❌ Не нашлась на YouTube.\n\nОткрыть в iTunes: {track['link']}"
            )
            return

        logger.info(f"🚀 Отправляю {len(audio_data) / 1024 / 1024:.2f} MB в Telegram")
        
        await query.message.reply_audio(
            audio=audio_data,
            title=track["title"],
            performer=track["artist"],
            caption=f"🎵 {track['artist']} - {track['title']}"
        )

        await query.edit_message_text(
            f"✔️ {track['artist']} - {track['title']}\nПесня в чате!"
        )
        logger.info("✔️ Успешно отправлена!")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {type(e).__name__}: {e}")
        await query.edit_message_text(
            f"❌ Ошибка на моем конце.\n{track['artist']} - {track['title']}"
        )


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_download_button, pattern=r"^download_\d+$"))

    logger.info("🚀 Бот запущен!")
    application.run_polling()


if __name__ == "__main__":
    main()
