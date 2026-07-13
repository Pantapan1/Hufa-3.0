"""
Точка входа. Инициализирует БД, восстанавливает состояния диалогов из
bot_state.db (переживают рестарт бота), регистрирует все обработчики и
запускает бесконечный polling.

Структура проекта:
  config.py        — секреты, логирование, инстансы bot/Groq, константы
  state.py         — состояния диалогов (user_states и т.п.) + автосохранение
  utils.py         — общие хелперы, клавиатуры
  db/database.py   — весь SQL
  ai/ai_client.py  — обёртка над Groq + генераторы контента
  ai/rp.py         — логика РП-сессий
  handlers/        — обработчики команд, кнопок, callback'ов и сообщений
"""
import warnings

from bot.config import bot, logger
from bot.db.database import init_db, migrate_db
from bot.state import load_state, start_autosave
import bot.handlers  # noqa: F401  (регистрирует все @bot.message_handler/callback_query_handler)


def main():
    logger.info("БОТ ЗАПУСКАЕТСЯ...")

    logger.info("Инициализация базы данных...")
    init_db()
    logger.info("Миграция базы данных...")
    migrate_db()

    logger.info("Восстановление состояний диалогов...")
    load_state()
    start_autosave()

    logger.info("Все модули загружены успешно!")
    logger.info("🕯 Библиотека Хуфы готова к работе")
    logger.info("БОТ ЗАПУЩЕН!")

    warnings.filterwarnings("ignore")

    # Бесконечный опрос Telegram с авто-перезапуском при сбоях сети/API,
    # чтобы падение одного запроса не останавливало бота насовсем.
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Опрос Telegram упал: {e}. Перезапуск через 5 секунд...")


if __name__ == '__main__':
    main()
