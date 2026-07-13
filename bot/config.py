"""
Конфигурация бота: секреты, логирование, инстансы telebot/Groq, константы.
Всё остальное (обработчики, БД, ИИ) импортирует нужное отсюда.
"""
import os
import logging
import threading
import telebot
from groq import Groq

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(BASE_DIR, 'hupha_bot.db')
LORE_FILE = os.path.join(BASE_DIR, 'lore.txt')
STATE_DB = os.path.join(BASE_DIR, 'bot_state.db')  # постоянное хранилище состояний


# ============================================================
# ЗАГРУЗКА СЕКРЕТОВ ИЗ .env (токены больше не хранятся в коде)
# ============================================================
def _load_env(path):
    env = {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_env = _load_env(os.path.join(BASE_DIR, '.env'))
TOKEN = os.environ.get('BOT_TOKEN') or _env.get('BOT_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_KEY') or _env.get('GROQ_KEY')
ADMIN_ID = int(os.environ.get('ADMIN_ID') or _env.get('ADMIN_ID', 0))
SERPAPI_KEY = os.environ.get('SERPAPI_KEY') or _env.get('SERPAPI_KEY', '')

if not TOKEN or not GROQ_API_KEY or not ADMIN_ID:
    raise RuntimeError(
        "❌ Не найдены BOT_TOKEN / GROQ_KEY / ADMIN_ID.\n"
        "Заполни файл .env рядом с main.py, например:\n"
        "BOT_TOKEN=...\nGROQ_KEY=...\nADMIN_ID=123456789"
    )

# ============================================================
# ЛОГИРОВАНИЕ (вместо print — пишем в файл и в консоль)
# ============================================================
LOG_FILE = os.path.join(BASE_DIR, 'bot.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('hufa_bot')

bot = telebot.TeleBot(TOKEN)
client = Groq(api_key=GROQ_API_KEY)

# Глобальные блокировки для потокобезопасности
db_lock = threading.Lock()
memory_lock = threading.Lock()

CATEGORY_EMOJI = {
    'персонаж': '👤', 'локация': '🏛️', 'предмет': '💎',
    'фракция': '🎭', 'событие': '📜', 'организация': '🏢',
    'существо': '🐉', 'магия': '🔮', 'общее': '📚'
}

ALLOWED_COMMANDS = ['/getid', '/roll', '/create', '/start', '/gm_suggest', '/npc', '/location',
                     '/encounter', '/oracle', '/puzzle', '/prophecy', '/dialogue', '/quest',
                     '/rp_start', '/rp_narrate', '/rp_stop', '/rp_mode', '/users',
                     '/search', '/bookmark', '/recap', '/give', '/additem', '/ban', '/unban',
                     '/achievements', '/import_lore', '/pay', '/craft']

# Валюты, которые можно переводить/использовать в крафте
CURRENCIES = ('рубли', 'хуфа')
