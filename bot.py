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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def search_music_itunes(query: str) -> List[dict]:
    """Ищет треки основному iTunes Search API высокой релевантности."""
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


async def download_from_youtube(artist: str, title: str) -> str:
    """Загружает полную песню с YouTube используя yt-dlp.
    Возвращает путь к файлу MP3 или не Ноне, если не найден."""
    search_query = f"{artist} {title}"
    
    # Утверждаю что yt-dlp запускается в виртуальном топнистек
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "%(title)s.%(ext)s")
        
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "192",
            "-o", output_path,
            "ytsearch:" + search_query
        ]
        
        try:
            # Прокарую asyncio.to_thread для блокирующего вызова
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                logger.error(f"Ошибка yt-dlp: {result.stderr}")
                return None
            
            # Находи скачанный файл
            files = os.listdir(tmpdir)
            mp3_files = [f for f in files if f.endswith(".mp3")]
            
            if not mp3_files:
                logger.error("Ошибка: MP3 файл не найден")
                return None
            
            file_path = os.path.join(tmpdir, mp3_files[0])
            
            # Обычные бинарные данные аудиофайла используются перед гружкой в Telegram
            with open(file_path, "rb") as f:
                audio_data = f.read()
            
            return audio_data
        
        except subprocess.TimeoutExpired:
            logger.error("Таймаут yt-dlp: залога численности")
            return None
        except Exception as e:
            logger.error(f"Ошибка yt-dlp: {e}")
            return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Привет! Отправь мне название песни, и я найду для тебя полную версию!\n\n"
        "🔍 Поиск со высокой релевантностью (работает в России).\n\n"
        "🎶 Отправь: `linkin park numb` или `oxxxymiron город под подошвой`\n\n"
        "⚡ Загруживается полная мп3 по клику.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎵 Просто напиши название песни и исполнителя.\n\n"
        "🔊 Кликни на трек — бот загружит полную MP3."
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

    msg = await update.message.reply_text("🔍 Поиск треков по релевантности...")

    try:
        tracks = await search_music_itunes(query)
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await msg.edit_text("❌ Ошибка при поиске.")
        return

    if not tracks:
        await msg.edit_text("🔍 Ничего не найдено. Попробуй другой запрос.")
        return

    # Сохрани треки в context для каллбека
    context.user_data["tracks"] = tracks

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный')} — {t.get('title', 'Без названия')}"
        text_lines.append(line)

    text_lines.append("\n🔊 Кликни для загружки полного MP3.")

    await msg.edit_text(
        "\n".join(text_lines),
        reply_markup=build_tracks_keyboard(tracks),
    )


async def handle_download_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # Получи индекс трека
    track_index = int(query.data.split("_")[1])
    tracks = context.user_data.get("tracks", [])

    if track_index >= len(tracks):
        await query.edit_message_text("❌ Ошибка.")
        return

    track = tracks[track_index]
    await query.edit_message_text(
        f"🔊 {track['artist']} - {track['title']}\n\nЗагружаю песню...\n(иногда 1-2 минуты)"
    )

    try:
        # Загружаю полную MP3
        audio_data = await download_from_youtube(track["artist"], track["title"])

        if not audio_data:
            await query.edit_message_text(
                f"❌ Не удалось найти песню на YouTube.\n\nОткрыть в iTunes: {track['link']}"
            )
            return

        # Отправь мп3
        await query.message.reply_audio(
            audio=audio_data,
            title=track["title"],
            performer=track["artist"],
            caption=f"🎵 {track['artist']}\n{track['title']}"
        )

        await query.edit_message_text(
            f"✔️ {track['artist']} - {track['title']}\nПесня отправлена!"
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await query.edit_message_text(
            f"❌ Ошибка при загружке.\n\n{track['artist']} - {track['title']}"
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
