import os
from dotenv import load_dotenv
from typing import List

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

MUSIC_API_BASE_URL = "https://api.deezer.com/search"


async def search_music(query: str) -> List[dict]:
    """Ищет треки по текстовому запросу с помощью Deezer API."""
    params = {"q": query, "limit": 5}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(MUSIC_API_BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    tracks = []
    for item in data.get("data", [])[:5]:
        tracks.append(
            {
                "title": item.get("title"),
                "artist": item.get("artist", {}).get("name"),
                "link": item.get("link"),
                "preview": item.get("preview"),
            }
        )

    return tracks


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Отправь мне текст, а я попробую найти подходящую музыку.\n"
        "Например: `linkin park numb` или `русский реп мотивирующий`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Просто напиши, что ты хочешь найти: исполнителя, название песни или описание настроения."
    )


def build_tracks_keyboard(tracks: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for t in tracks:
        text = f"{t['artist']} - {t['title']}" if t.get("artist") else t.get("title", "Трек")
        buttons.append([InlineKeyboardButton(text=text[:60], url=t["link"])])
    return InlineKeyboardMarkup(buttons)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    query = update.message.text.strip()
    if not query:
        return

    msg = await update.message.reply_text("Ищу треки...")

    try:
        tracks = await search_music(query)
    except Exception:
        await msg.edit_text("Произошла ошибка при запросе к музыкальному сервису, попробуй позже.")
        return

    if not tracks:
        await msg.edit_text("Ничего не нашлось. Попробуй сформулировать запрос по‑другому.")
        return

    text_lines = []
    for i, t in enumerate(tracks, start=1):
        line = f"{i}. {t.get('artist', 'Неизвестный исполнитель')} — {t.get('title', 'Без названия')}"
        text_lines.append(line)

    text_lines.append("\nНажми на кнопку, чтобы открыть трек в браузере.")

    await msg.edit_text(
        "\n".join(text_lines),
        reply_markup=build_tracks_keyboard(tracks),
    )


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.run_polling()


if __name__ == "__main__":
    main()
