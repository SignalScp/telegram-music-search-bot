# Telegram Music Search Bot

Бот ищет музыку по текстовому запросу и присылает ссылки на треки.

## Как запустить

1. Склонируй репозиторий:
   ```bash
   git clone https://github.com/SignalScp/telegram-music-search-bot.git
   cd telegram-music-search-bot
   ```
2. Создай виртуальное окружение и установи зависимости:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
3. Создай файл `.env` и добавь туда:
   ```env
   BOT_TOKEN=твой_телеграм_токен
   MUSIC_API_TOKEN=твой_токен_музыкального_API
   ```
4. Запусти бота:
   ```bash
   python bot.py
   ```
