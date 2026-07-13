"""
Вспомогательные функции общего назначения: очистка текста, безопасная
отправка сообщений, форматирование профиля, клавиатуры.
"""
import re
import html as html_module

from telebot import types

from bot.config import bot, ADMIN_ID, CATEGORY_EMOJI
from bot.state import message_context
from bot.db.database import execute_db


def clean_text(text, is_key=False):
    if not text: return ""
    text = text.strip()
    if is_key: text = re.sub(r'[\[\]\(\)\{\}\.\,\!\?\:\;\-\"\']', '', text)
    return text


def safe_html(text):
    safe = html_module.escape(text)
    for tag in ['b', 'i', 'u', 'code']:
        safe = safe.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return safe


def safe_send(chat_id, text, photo_id=None, keyword=None):
    if not text: return
    try:
        safe_text = safe_html(text)
        if photo_id:
            if len(safe_text) <= 1000:
                msg = bot.send_photo(chat_id, photo_id, caption=safe_text, parse_mode="HTML")
            else:
                bot.send_photo(chat_id, photo_id)
                msg = bot.send_message(chat_id, safe_text, parse_mode="HTML")
        else:
            msg = bot.send_message(chat_id, safe_text, parse_mode="HTML")
        if msg and keyword:
            if chat_id not in message_context: message_context[chat_id] = {}
            message_context[chat_id][msg.message_id] = keyword
        return msg
    except Exception as e:
        print(f"❌ Ошибка safe_send: {e}")
        try: bot.send_message(chat_id, text)
        except: pass


def ensure_player(uid, name="Безымянный"):
    if not execute_db("SELECT 1 FROM players WHERE user_id = ?", (uid,), True):
        execute_db("INSERT INTO players (user_id, name, bio, photo) VALUES (?, ?, ?, ?)", (uid, name, "", None))


def format_profile(user_id):
    res = execute_db('SELECT name, bio, photo, хуфа, рубли FROM players WHERE user_id = ?', (user_id,), True)
    if not res: return None, None
    p = res[0]
    caption = (
        f"🎴 <b>Карточка героя</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🎭 <b>Имя:</b> {p[0]}\n"
        f"🧪 <b>Хуфа:</b> {p[3]}   💰 <b>Рубли:</b> {p[4]}\n\n"
        f"📖 <b>Био:</b>\n{p[1]}"
    )
    return caption, p[2]


def send_story_part(chat_id, part, part_num, total_parts, story_name):
    content, content_type, file_id = part[1], part[2], part[3]
    safe_content = safe_html(content)
    header = f"📖 <b>{story_name}</b> [{part_num}/{total_parts}]\n\n"
    try:
        if content_type == 'text': bot.send_message(chat_id, header + safe_content, parse_mode="HTML")
        elif content_type == 'photo' and file_id: bot.send_photo(chat_id, file_id, caption=header + safe_content[:1000], parse_mode="HTML")
        elif content_type == 'video' and file_id: bot.send_video(chat_id, file_id, caption=header + safe_content[:1000], parse_mode="HTML")
        else: bot.send_message(chat_id, header + safe_content, parse_mode="HTML")
    except:
        try: bot.send_message(chat_id, f"📖 {story_name} [{part_num}/{total_parts}]\n\n{content[:4000]}")
        except: pass


def split_text_for_ai(text, max_chars=3000):
    if len(text) <= max_chars: return [text]
    parts, current = [], ""
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        if len(current) + len(sentence) < max_chars: current += sentence + " "
        else:
            if current: parts.append(current.strip())
            current = sentence + " "
    if current: parts.append(current.strip())
    return parts


# ============================================================
# КЛАВИАТУРЫ (обновлённый внешний вид: сгруппированы по смыслу,
# добавлены кнопки для перевода валюты и крафта)
# ============================================================
def main_kb(uid):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("👤 Мой профиль", "📚 База знаний")
    markup.add("🛒 Магазин", "🎒 Инвентарь")
    markup.add("💸 Перевод", "⚒️ Крафт")
    markup.add("🏆 Топ игроков", "🔖 Закладки")
    markup.add("🏅 Достижения")
    if uid == ADMIN_ID:
        markup.add("🎭 РП-сессия", "📜 Обучить ГМ-а")
        markup.add("📖 Истории", "📢 Рассылка")
        markup.add("🔗 Связи", "📊 Статистика Лора")
        markup.add("🎲 Случайный Лор", "❓ Викторина")
        markup.add("💬 Диалог-обучение", "📥 Импорт из Истории")
        markup.add("🤖 Авто-категоризация", "⚙️ Режим чата")
        markup.add("🔧 Управление БД", "🛠 Админ-панель")
    else:
        markup.add("🆔 Мой ID", "🎁 Ежедневный бонус")
    return markup


def admin_panel_kb():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("💾 Бэкап БД", "📈 Общая статистика")
    markup.add("🚫 Бан-лист", "💰 Выдать валюту")
    markup.add("🏷 Добавить товар", "📤 Экспорт вики")
    markup.add("📥 Импорт лора", "🔙 Назад")
    return markup


def shop_kb(items):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for item_id, name, price, currency, emoji in items:
        markup.add(types.InlineKeyboardButton(f"{emoji} {name} — {price} {currency}", callback_data=f"buy_{item_id}"))
    return markup


def rp_menu_kb():
    """Клавиатура для меню РП-сессий"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎭 Начать сессию", "🎭 Остановить сессию")
    markup.add("📖 Повествование", "📋 Контекст чата")
    markup.add("🤖 AI-Советник", "🎲 Оракул")
    markup.add("👤 Генератор NPC", "⚔️ Генератор Квестов")
    markup.add("🏛 Генератор Локаций", "🎲 Случайная Встреча")
    markup.add("🧩 Загадка", "🔮 Пророчество")
    markup.add("💬 Диалог NPC", "🔙 Назад")
    return markup


def categories_kb(categories):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for cat, count in categories:
        markup.add(types.InlineKeyboardButton(f"{CATEGORY_EMOJI.get(cat, '📚')} {cat.capitalize()} ({count})", callback_data=f"cat_{cat}"))
    return markup


def db_management_kb():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🔄 Изменить ключ", "🖼 Обновить фото")
    markup.add("🗑 Удалить фото", "📋 Просмотр записи")
    markup.add("🔙 Назад")
    return markup
