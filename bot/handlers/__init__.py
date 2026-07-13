"""
Регистрация всех обработчиков бота.

ВАЖНО: pyTelegramBotAPI перебирает обработчики в порядке регистрации и
использует первый подходящий. handlers.messages содержит "catch-all"
обработчик (handle_all), который матчит любое текстовое/медиа-сообщение,
поэтому он должен импортироваться (регистрироваться) последним — иначе
он перехватит сообщения раньше более специфичных обработчиков команд/кнопок.
"""
from bot.handlers import commands  # noqa: F401
from bot.handlers import economy  # noqa: F401
from bot.handlers import trade  # noqa: F401
from bot.handlers import messages  # noqa: F401
