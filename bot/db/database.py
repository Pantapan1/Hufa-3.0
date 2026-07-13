"""
Слой работы с БД (SQLite): все SQL-запросы бота живут здесь.
Обработчики и ИИ-модуль импортируют нужные функции отсюда, а не пишут SQL напрямую.
"""
import sqlite3
import re
import os
import time

from bot.config import DB_NAME, LORE_FILE, db_lock, bot, logger

def execute_db(query, params=(), is_select=False):
    try:
        with db_lock:
            with sqlite3.connect(DB_NAME, timeout=15) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                cursor = conn.cursor()
                cursor.execute(query, params)
                if is_select: return cursor.fetchall()
                conn.commit()
    except Exception as e:
        logger.error(f"Ошибка БД: {e} | Запрос: {query[:200]}")
        return []

def _ensure_column(table, col, decl):
    """Добавляет колонку в таблицу, если её ещё нет (SQLite не умеет ADD COLUMN IF NOT EXISTS)."""
    try:
        cols = [r[1] for r in execute_db(f"PRAGMA table_info({table})", (), True) or []]
        if col not in cols:
            execute_db(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception as e:
        logger.error(f"Не удалось добавить колонку {col} в {table}: {e}")

# ============================================================
# ИМПОРТ ЛОРА ИЗ lore.txt (БАГФИКС: раньше файл лежал рядом, но
# никогда не подгружался в таблицу wiki — библиотека была пустой)
# LORE_FILE уже корректно импортирован из bot.config выше — здесь его
# переопределять не нужно (раньше это делалось через необъявленный
# BASE_DIR и роняло бот ошибкой NameError при старте).
# ============================================================

def parse_lore_file(path=None):
    """Разбирает lore.txt на отдельные статьи.
    Поддерживает два формата, которые встречаются в файле:
      1. 'N. Название — Описание...' (одна строка на статью)
      2. 'Кто такой такой-то: ...' (многострочная статья до следующего маркера)
    Служебные строки вида '/start: /create' игнорируются.
    """
    path = path or LORE_FILE
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    def is_start(line):
        return bool(re.match(r'^\d+\.\s+\S', line)) or line.startswith('Кто такой ')

    blocks = []
    current = []
    for line in lines:
        if is_start(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    parsed = []
    for block in blocks:
        # выкидываем строки-мусор вроде "/start: /create"
        clean_lines = [l for l in block if not re.match(r'^\s*/\w+', l)]
        text = ''.join(clean_lines).strip()
        if not text:
            continue
        first_line = clean_lines[0].strip()
        if first_line.startswith('Кто такой'):
            m = re.match(r'Кто такой\s+([^:]+):', first_line)
            keyword = m.group(1).strip().lower() if m else None
        else:
            m = re.match(r'^\d+\.\s*([^—]+)—', first_line)
            keyword = m.group(1).strip().lower() if m else None
        if not keyword:
            continue
        parsed.append((keyword, text, 'существо'))
    return parsed

def import_lore_from_file(path=None, force=False):
    """Заливает статьи из lore.txt в таблицу wiki.
    force=False (по умолчанию) — добавляет только то, чего ещё нет, не трогая
    статьи, отредактированные вручную через админку/веб-редактор.
    force=True — принудительно перезаписывает содержимое по ключу из файла."""
    entries = parse_lore_file(path)
    added = 0
    for keyword, desc, category in entries:
        if force:
            execute_db("INSERT OR REPLACE INTO wiki (keyword, description, category) VALUES (?, ?, ?)", (keyword, desc, category))
            added += 1
        else:
            exists = execute_db("SELECT 1 FROM wiki WHERE keyword = ?", (keyword,), True)
            if not exists:
                execute_db("INSERT INTO wiki (keyword, description, category) VALUES (?, ?, ?)", (keyword, desc, category))
                added += 1
    return added

# ============================================================
# СИСТЕМА ДОСТИЖЕНИЙ (новая крупная фича)
# ============================================================
ACHIEVEMENTS = [
    {'code': 'first_step', 'name': 'Первый шаг', 'desc': 'Создай анкету героя (/create)',
     'emoji': '🌱', 'reward_currency': 'рубли', 'reward_amount': 20,
     'check': lambda s: s['has_photo']},
    {'code': 'librarian', 'name': 'Библиотекарь', 'desc': 'Сохрани 5 статей в закладки',
     'emoji': '🔖', 'reward_currency': 'рубли', 'reward_amount': 30,
     'check': lambda s: s['bookmarks'] >= 5},
    {'code': 'explorer', 'name': 'Исследователь Лора', 'desc': 'Узнай 10 статей из базы знаний',
     'emoji': '🔍', 'reward_currency': 'хуфа', 'reward_amount': 5,
     'check': lambda s: s['wiki_views'] >= 10},
    {'code': 'loremaster', 'name': 'Знаток Бестиария', 'desc': 'Узнай 25 статей из базы знаний',
     'emoji': '📖', 'reward_currency': 'хуфа', 'reward_amount': 15,
     'check': lambda s: s['wiki_views'] >= 25},
    {'code': 'collector', 'name': 'Коллекционер', 'desc': 'Собери 5 разных предметов в инвентаре',
     'emoji': '🎒', 'reward_currency': 'рубли', 'reward_amount': 50,
     'check': lambda s: s['inventory_items'] >= 5},
    {'code': 'huffa_magnate', 'name': 'Хуфа-магнат', 'desc': 'Накопи 100 хуфы',
     'emoji': '🧪', 'reward_currency': 'рубли', 'reward_amount': 20,
     'check': lambda s: s['хуфа'] >= 100},
    {'code': 'rich', 'name': 'Богач', 'desc': 'Накопи 1000 рублей',
     'emoji': '💰', 'reward_currency': 'хуфа', 'reward_amount': 10,
     'check': lambda s: s['рубли'] >= 1000},
    {'code': 'streak7', 'name': 'Постоянный гость', 'desc': 'Забирай ежедневный бонус 7 дней подряд',
     'emoji': '🔥', 'reward_currency': 'рубли', 'reward_amount': 100,
     'check': lambda s: s['daily_streak'] >= 7},
    {'code': 'game_master', 'name': 'Мастер Игры', 'desc': 'Проведи РП-сессию в роли ГМ-а',
     'emoji': '🎭', 'reward_currency': 'хуфа', 'reward_amount': 10,
     'check': lambda s: s['is_gm']},
    {'code': 'erudite', 'name': 'Эрудит', 'desc': 'Дай 5 верных ответов в викторине',
     'emoji': '🧠', 'reward_currency': 'рубли', 'reward_amount': 40,
     'check': lambda s: s['quiz_correct'] >= 5},
]

def _gather_achievement_stats(uid):
    prow = execute_db("SELECT хуфа, рубли, wiki_views, daily_streak, quiz_correct, photo FROM players WHERE user_id = ?", (uid,), True)
    хуфа = рубли = wiki_views = daily_streak = quiz_correct = 0
    has_photo = False
    if prow:
        хуфа, рубли, wiki_views, daily_streak, quiz_correct, photo = prow[0]
        хуфа = хуфа or 0; рубли = рубли or 0; wiki_views = wiki_views or 0
        daily_streak = daily_streak or 0; quiz_correct = quiz_correct or 0
        has_photo = bool(photo)
    bm = execute_db("SELECT COUNT(*) FROM bookmarks WHERE user_id = ?", (uid,), True)
    bookmarks = bm[0][0] if bm else 0
    inv = execute_db("SELECT COUNT(DISTINCT item_id) FROM inventory WHERE user_id = ?", (uid,), True)
    inventory_items = inv[0][0] if inv else 0
    gm = execute_db("SELECT COUNT(*) FROM rp_sessions WHERE gm_id = ?", (uid,), True)
    is_gm = bool(gm and gm[0][0] > 0)
    return {
        'хуфа': хуфа, 'рубли': рубли, 'wiki_views': wiki_views, 'daily_streak': daily_streak,
        'quiz_correct': quiz_correct, 'has_photo': has_photo, 'bookmarks': bookmarks,
        'inventory_items': inventory_items, 'is_gm': is_gm,
    }

def check_achievements(uid):
    """Проверяет условия всех ачивок для игрока, выдаёт награду за новые и возвращает их список."""
    stats = _gather_achievement_stats(uid)
    unlocked = {row[0] for row in (execute_db("SELECT code FROM player_achievements WHERE user_id = ?", (uid,), True) or [])}
    newly = []
    for ach in ACHIEVEMENTS:
        if ach['code'] in unlocked:
            continue
        try:
            if ach['check'](stats):
                execute_db("INSERT OR IGNORE INTO player_achievements (user_id, code) VALUES (?, ?)", (uid, ach['code']))
                execute_db(f"UPDATE players SET {ach['reward_currency']} = {ach['reward_currency']} + ? WHERE user_id = ?", (ach['reward_amount'], uid))
                newly.append(ach)
        except Exception as e:
            logger.error(f"Ошибка проверки достижения {ach['code']} для {uid}: {e}")
    return newly

def notify_achievements(chat_id, newly):
    for ach in newly:
        bot.send_message(
            chat_id,
            f"🏅 <b>Новое достижение!</b>\n{ach['emoji']} <b>{ach['name']}</b>\n{ach['desc']}\n\n"
            f"Награда: +{ach['reward_amount']} {ach['reward_currency']}",
            parse_mode="HTML"
        )

def init_db():
    # WAL значительно снижает блокировки при параллельных запросах
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
    except Exception as e:
        logger.warning(f"Не удалось включить WAL: {e}")

    execute_db('''CREATE TABLE IF NOT EXISTS players (user_id INTEGER PRIMARY KEY, name TEXT, bio TEXT, photo TEXT, хуфа INTEGER DEFAULT 0, рубли INTEGER DEFAULT 100, last_daily TIMESTAMP)''')
    execute_db('''CREATE TABLE IF NOT EXISTS wiki (keyword TEXT PRIMARY KEY, description TEXT, photo_id TEXT, category TEXT DEFAULT 'общее')''')
    execute_db('''CREATE TABLE IF NOT EXISTS wiki_links (id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL, target_key TEXT NOT NULL, link_type TEXT NOT NULL, UNIQUE(source_key, target_key, link_type))''')
    execute_db('''CREATE TABLE IF NOT EXISTS stories (id INTEGER PRIMARY KEY AUTOINCREMENT, story_name TEXT NOT NULL, part_number INTEGER NOT NULL, content TEXT NOT NULL, content_type TEXT DEFAULT 'text', file_id TEXT, original_content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    execute_db('''CREATE TABLE IF NOT EXISTS broadcast_users (user_id INTEGER, chat_id INTEGER, PRIMARY KEY (user_id, chat_id))''')
    execute_db('''CREATE TABLE IF NOT EXISTS rp_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gm_id INTEGER NOT NULL, session_name TEXT, context TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    execute_db('''CREATE TABLE IF NOT EXISTS rp_channels (chat_id INTEGER NOT NULL, mode TEXT DEFAULT 'silent', PRIMARY KEY (chat_id))''')
    execute_db('''CREATE TABLE IF NOT EXISTS chat_contexts (chat_id INTEGER PRIMARY KEY, context_summary TEXT, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # --- Новые таблицы: экономика, модерация, закладки ---
    execute_db('''CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT, price INTEGER DEFAULT 0, currency TEXT DEFAULT 'рубли', emoji TEXT DEFAULT '🎁')''')
    execute_db('''CREATE TABLE IF NOT EXISTS inventory (user_id INTEGER NOT NULL, item_id INTEGER NOT NULL, qty INTEGER DEFAULT 1, PRIMARY KEY (user_id, item_id))''')
    execute_db('''CREATE TABLE IF NOT EXISTS blocked_users (user_id INTEGER PRIMARY KEY, reason TEXT, blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    execute_db('''CREATE TABLE IF NOT EXISTS bookmarks (user_id INTEGER NOT NULL, keyword TEXT NOT NULL, PRIMARY KEY (user_id, keyword))''')
    # --- Достижения ---
    execute_db('''CREATE TABLE IF NOT EXISTS achievements (code TEXT PRIMARY KEY, name TEXT, description TEXT, emoji TEXT, reward_currency TEXT DEFAULT 'рубли', reward_amount INTEGER DEFAULT 0)''')
    execute_db('''CREATE TABLE IF NOT EXISTS player_achievements (user_id INTEGER NOT NULL, code TEXT NOT NULL, unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, code))''')
    # --- Индексы для ускорения частых запросов ---
    execute_db('''CREATE INDEX IF NOT EXISTS idx_wiki_category ON wiki(category)''')
    execute_db('''CREATE INDEX IF NOT EXISTS idx_stories_name ON stories(story_name)''')
    execute_db('''CREATE INDEX IF NOT EXISTS idx_players_хуфа ON players(хуфа)''')
    execute_db('''CREATE INDEX IF NOT EXISTS idx_players_рубли ON players(рубли)''')

    # --- Довесок колонок к players (для системы достижений) ---
    _ensure_column('players', 'wiki_views', 'INTEGER DEFAULT 0')
    _ensure_column('players', 'daily_streak', 'INTEGER DEFAULT 0')
    _ensure_column('players', 'daily_claims', 'INTEGER DEFAULT 0')
    _ensure_column('players', 'quiz_correct', 'INTEGER DEFAULT 0')

    # --- Сидирование достижений ---
    for ach in ACHIEVEMENTS:
        execute_db("INSERT OR IGNORE INTO achievements (code, name, description, emoji, reward_currency, reward_amount) VALUES (?, ?, ?, ?, ?, ?)",
                   (ach['code'], ach['name'], ach['desc'], ach['emoji'], ach['reward_currency'], ach['reward_amount']))

    # --- БАГФИКС: библиотека лора (lore.txt) перестала подгружаться в БД при обновлении.
    # Если таблица wiki пуста (например, после переноса на новую версию бота) — досыпаем
    # знания из lore.txt автоматически, ничего не перезаписывая поверх ручных правок.
    total_wiki = execute_db("SELECT COUNT(*) FROM wiki", (), True)
    if not total_wiki or total_wiki[0][0] == 0:
        added = import_lore_from_file()
        if added:
            logger.info(f"📥 Автоимпорт лора: добавлено {added} статей из lore.txt")


def migrate_db():
    migrations = [
        ("stories", "original_content", "TEXT"),
        ("wiki", "category", "TEXT DEFAULT 'общее'"),
        ("rp_sessions", "context", "TEXT"),
        ("players", "last_daily", "TIMESTAMP"),
    ]
    for table, column, col_type in migrations:
        try:
            columns = execute_db(f"PRAGMA table_info({table})", (), True)
            if column not in [col[1] for col in columns]: execute_db(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception as e:
            logger.warning(f"Миграция {table}.{column} пропущена: {e}")

def is_blocked(uid):
    return bool(execute_db("SELECT 1 FROM blocked_users WHERE user_id = ?", (uid,), True))


def get_all_users():
    users = set()
    for p in execute_db("SELECT user_id FROM players", (), True): users.add(p[0])
    for b in execute_db("SELECT DISTINCT user_id FROM broadcast_users", (), True): users.add(b[0])
    return list(users)

def broadcast_message(admin_id, text, photo_id=None):
    users = get_all_users()
    success = failed = 0
    for user_id in users:
        try:
            if photo_id: bot.send_photo(user_id, photo_id, caption=text, parse_mode="HTML")
            else: bot.send_message(user_id, text, parse_mode="HTML")
            success += 1; time.sleep(0.1)
        except: failed += 1
    return success, failed


def get_chat_context(chat_id):
    context = execute_db("SELECT context_summary FROM chat_contexts WHERE chat_id = ?", (chat_id,), True)
    if context: return context[0][0]
    return None

def set_chat_context(chat_id, context_text):
    execute_db("INSERT OR REPLACE INTO chat_contexts (chat_id, context_summary, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)", (chat_id, context_text))


def add_wiki_link(source, target, link_type):
    execute_db("INSERT OR IGNORE INTO wiki_links (source_key, target_key, link_type) VALUES (?, ?, ?)", (source.lower(), target.lower(), link_type))

def get_wiki_links(keyword):
    return execute_db("SELECT source_key, target_key, link_type FROM wiki_links WHERE source_key = ? OR target_key = ?", (keyword.lower(), keyword.lower()), True)

def get_links_text(keyword):
    links = get_wiki_links(keyword)
    if not links: return None
    link_types = {'враг': '⚔️ Враг', 'друг': '🤝 Друг', 'союзник': '🛡️ Союзник', 'находится_в': '📍 Находится в', 'владеет': '💎 Владеет', 'часть': '🧩 Часть'}
    lines = []
    for source, target, ltype in links:
        icon = link_types.get(ltype, '🔗')
        if source.lower() == keyword.lower(): lines.append(f"{icon} → {target.capitalize()}")
        else: lines.append(f"{icon} ← {source.capitalize()}")
    return "\n".join(lines)

def get_wiki_by_category(category):
    return execute_db("SELECT keyword, description, photo_id FROM wiki WHERE category = ? ORDER BY keyword", (category,), True)

def get_categories_stats():
    return execute_db("SELECT category, COUNT(*) as cnt FROM wiki GROUP BY category ORDER BY cnt DESC", (), True)

def get_wiki_info(keyword):
    result = execute_db("SELECT keyword, description, photo_id, category FROM wiki WHERE keyword = ?", (keyword.lower(),), True)
    return result[0] if result else None

def get_random_lore():
    all_wiki = execute_db("SELECT keyword, description, photo_id FROM wiki", (), True)
    return random.choice(all_wiki) if all_wiki else None

def generate_quiz():
    all_wiki = execute_db("SELECT keyword, description FROM wiki", (), True)
    if len(all_wiki) < 4: return None
    correct = random.choice(all_wiki)
    wrong = random.sample([k for k, d in all_wiki if k != correct[0]], min(3, len(all_wiki)-1))
    options = wrong + [correct[0]]
    random.shuffle(options)
    return {'question': f"❓ <b>Вопрос:</b> {correct[1][:200]}...\n\n<b>О ком/чём идёт речь?</b>", 'correct': correct[0], 'options': options}

def get_lore_stats():
    total = execute_db("SELECT COUNT(*) FROM wiki", (), True)
    links_count = execute_db("SELECT COUNT(*) FROM wiki_links", (), True)
    stats = f"📊 <b>Статистика:</b>\n📚 Знаний: {total[0][0] if total else 0}\n🔗 Связей: {links_count[0][0] if links_count else 0}\n\n<b>Категории:</b>\n"
    for cat, count in execute_db("SELECT category, COUNT(*) FROM wiki GROUP BY category ORDER BY COUNT(*) DESC", (), True):
        stats += f"{CATEGORY_EMOJI.get(cat, '📚')} {cat}: {count}\n"
    return stats

def check_lore_conflicts():
    all_wiki = execute_db("SELECT keyword, description FROM wiki", (), True)
    conflicts = []
    for kw1, desc1 in all_wiki:
        for kw2, desc2 in all_wiki:
            if kw1 >= kw2: continue
            links = get_wiki_links(kw1)
            if links and any(kw2.lower() in [l[0], l[1]] for l in links) and abs(len(desc1) - len(desc2)) > 1000:
                conflicts.append(f"⚠️ {kw1} и {kw2} связаны, но описания различаются")
    return conflicts[:5] if conflicts else None

def edit_wiki_keyword(old_keyword, new_keyword):
    try:
        if not execute_db("SELECT keyword FROM wiki WHERE keyword = ?", (old_keyword.lower(),), True): return False, f"❌ Ключ «{old_keyword}» не найден!"
        if execute_db("SELECT keyword FROM wiki WHERE keyword = ?", (new_keyword.lower(),), True): return False, f"❌ Ключ «{new_keyword}» уже существует!"
        execute_db("UPDATE wiki SET keyword = ? WHERE keyword = ?", (new_keyword.lower(), old_keyword.lower()))
        execute_db("UPDATE wiki_links SET source_key = ? WHERE source_key = ?", (new_keyword.lower(), old_keyword.lower()))
        execute_db("UPDATE wiki_links SET target_key = ? WHERE target_key = ?", (new_keyword.lower(), old_keyword.lower()))
        return True, f"✅ Ключ изменён: «{old_keyword}» → «{new_keyword}»"
    except: return False, "❌ Ошибка"

def edit_wiki_photo(keyword, new_photo_id=None):
    try:
        if not execute_db("SELECT keyword FROM wiki WHERE keyword = ?", (keyword.lower(),), True): return False, f"❌ Ключ «{keyword}» не найден!"
        if new_photo_id is None: execute_db("UPDATE wiki SET photo_id = NULL WHERE keyword = ?", (keyword.lower(),)); return True, "✅ Фото удалено!"
        execute_db("UPDATE wiki SET photo_id = ? WHERE keyword = ?", (new_photo_id, keyword.lower())); return True, "✅ Фото обновлено!"
    except: return False, "❌ Ошибка"

def find_story_by_name(story_name):
    for s in execute_db("SELECT DISTINCT story_name FROM stories", (), True):
        if s[0].lower() == story_name.lower(): return s[0]
    return None

def get_story_parts(story_name):
    actual = find_story_by_name(story_name)
    return execute_db("SELECT part_number, content, content_type, file_id FROM stories WHERE story_name = ? ORDER BY part_number", (actual,), True) if actual else []

def get_all_stories():
    return execute_db("SELECT DISTINCT story_name, COUNT(*) FROM stories GROUP BY story_name", (), True)

def delete_story(story_name):
    actual = find_story_by_name(story_name)
    if actual: execute_db("DELETE FROM stories WHERE story_name = ?", (actual,))

def get_story_count(story_name):
    actual = find_story_by_name(story_name)
    if not actual: return 0
    res = execute_db("SELECT COUNT(*) FROM stories WHERE story_name = ?", (actual,), True)
    return res[0][0] if res else 0

def save_story_part(story_name, part_number, content, content_type='text', file_id=None, original_content=None):
    try: execute_db("INSERT INTO stories (story_name, part_number, content, content_type, file_id, original_content) VALUES (?, ?, ?, ?, ?, ?)", (story_name, part_number, content, content_type, file_id, original_content))
    except: execute_db("INSERT INTO stories (story_name, part_number, content, content_type, file_id) VALUES (?, ?, ?, ?, ?)", (story_name, part_number, content, content_type, file_id))

