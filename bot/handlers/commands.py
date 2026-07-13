"""
Обработчики команд и текстовых кнопок главного меню, RP-меню, генераторов (NPC, локации,
квесты, загадки, пророчества и т.д.) и связанных callback-кнопок.
"""
import re
import random
import time
from telebot import types

from bot.config import bot, ADMIN_ID, CATEGORY_EMOJI
from bot.state import user_states, temp_learning, rp_sessions, quiz_data
from bot.db.database import execute_db, init_db, get_chat_context, get_wiki_by_category, check_achievements, notify_achievements
from bot.utils import ensure_player, format_profile, main_kb, rp_menu_kb
from bot.ai.ai_client import (ai_gm_suggest, ai_oracle_interpret, generate_location, generate_npc,
                              generate_npc_dialogue, generate_prophecy, generate_puzzle, generate_quest,
                              generate_random_encounter)
from bot.ai.rp import gm_narrate, stop_rp_session

@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id; chat_id = message.chat.id
    init_db()
    execute_db("INSERT OR IGNORE INTO broadcast_users (user_id, chat_id) VALUES (?, ?)", (uid, chat_id))
    ensure_player(uid, message.from_user.first_name)
    bot.send_message(
        chat_id,
        "🕯 <b>Библиотека Хуфы открыта</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 <b>ИИ-сортировка</b> — просто спроси «что ты знаешь про...»\n"
        "🎭 <b>РП-сессии</b> с AI-помощником\n"
        "📖 Истории, викторины, связи между статьями\n\n"
        "💰 <b>Экономика:</b> 🛒 магазин · 🎒 инвентарь · 🎁 ежедневный бонус\n"
        "💸 <code>/pay</code> — перевести валюту другому игроку\n"
        "⚒️ <code>/craft</code> — скрафтить новый предмет из двух\n"
        "🏆 топ игроков · 🏅 достижения\n\n"
        "🔖 <code>/bookmark ключ</code> — сохранить статью\n"
        "🔍 <code>/search запрос</code> — поиск по базе знаний\n"
        "🎲 <code>/roll d20</code>\n"
        "🧠 <code>/gm_suggest /npc /quest /oracle</code> — инструменты мастера",
        reply_markup=main_kb(uid), parse_mode="HTML"
    )

@bot.message_handler(commands=['create'])
def create_cmd(message):
    uid = message.from_user.id; chat_id = message.chat.id
    user_states[uid] = 'reg_name'
    bot.send_message(chat_id, "🎭 Как зовут героя?")

@bot.message_handler(commands=['roll'])
def handle_roll_cmd(message):
    args = message.text.replace('/roll', '').strip() or 'd20'
    match = re.match(r'(\d+)?d(\d+)([+-]\d+)?', args)
    if not match: bot.reply_to(message, "❌ Формат: /roll d20+5"); return
    count = int(match.group(1) or 1); dice = int(match.group(2)); modifier = int(match.group(3) or 0)
    rolls = [random.randint(1, dice) for _ in range(count)]
    total = sum(rolls) + modifier
    response = f"🎲 <b>{args}:</b> {total}"
    if dice == 20:
        if rolls[0] == 20: response += " ✨КРИТ!"
        elif rolls[0] == 1: response += " 💀ПРОВАЛ!"
    bot.send_message(message.chat.id, response, parse_mode="HTML")

@bot.message_handler(commands=['getid'])
def get_my_id(message):
    bot.reply_to(message, f"🆔 Ваш ID: <code>{message.from_user.id}</code>", parse_mode="HTML")

@bot.message_handler(commands=['users'])
def list_users_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    players = execute_db("SELECT user_id, name FROM players", (), True)
    if not players: bot.reply_to(message, "📭 Нет игроков!"); return
    response = "👥 <b>Игроки:</b>\n\n" + "\n".join([f"• {name} — <code>{uid}</code>" for uid, name in players])
    bot.send_message(message.from_user.id, response, parse_mode="HTML")

@bot.message_handler(commands=['rp_start'])
def rp_start_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    chat_id = message.chat.id
    thread_id = message.message_thread_id if hasattr(message, 'message_thread_id') else None
    existing_context = get_chat_context(chat_id)
    if existing_context:
        user_states[uid] = 'rp_name'
        temp_learning[uid] = {'rp_chat_id': chat_id, 'thread_id': thread_id, 'context': existing_context}
        bot.send_message(uid, f"📖 <b>Найден контекст:</b>\n{existing_context[:600]}\n\n🎭 Введи название сессии (или /skip для авто-названия):")
    else:
        user_states[uid] = 'rp_context'
        temp_learning[uid] = {'rp_chat_id': chat_id, 'thread_id': thread_id}
        bot.send_message(uid, "📖 Контекст не найден.\n\nО чём будет сессия? Опиши тему (я поищу информацию в интернете):")

@bot.message_handler(commands=['rp_narrate'])
def rp_narrate_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    text = message.text.replace('/rp_narrate', '').strip()
    if not text: bot.reply_to(message, "❌ Напиши текст: /rp_narrate [текст]"); return
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if active_chat: gm_narrate(active_chat, text); bot.reply_to(message, "✅ Отправлено!")
    else: bot.reply_to(message, "❌ Нет активных сессий!")

@bot.message_handler(commands=['rp_stop'])
def rp_stop_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if active_chat:
        thread_id = rp_sessions[active_chat].get('thread_id')
        stop_rp_session(active_chat)
        bot.send_message(active_chat, "🎭 <b>РП-сессия завершена.</b>", parse_mode="HTML", message_thread_id=thread_id)
        bot.reply_to(message, "✅ Сессия остановлена!")
    else: bot.reply_to(message, "❌ Нет активных сессий!")

@bot.message_handler(commands=['rp_mode'])
def rp_mode_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    chat_id = message.chat.id
    args = message.text.split()
    if len(args) < 2:
        current = execute_db("SELECT mode FROM rp_channels WHERE chat_id = ?", (chat_id,), True)
        mode = current[0][0] if current else 'silent'
        bot.reply_to(message, f"⚙️ Режим: <b>{mode}</b>\n• active — бот в РП\n• silent — бот молчит\n• answer — отвечает", parse_mode="HTML")
        return
    mode = args[1].lower()
    if mode in ['active', 'silent', 'answer']:
        execute_db("INSERT OR REPLACE INTO rp_channels (chat_id, mode) VALUES (?, ?)", (chat_id, mode))
        bot.reply_to(message, f"✅ Режим изменён на: <b>{mode}</b>", parse_mode="HTML")

# ============================================================
# КОМАНДЫ ИИ-ИНСТРУМЕНТОВ ДЛЯ РП
# ============================================================

@bot.message_handler(commands=['gm_suggest'])
def gm_suggest_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if active_chat:
        bot.send_message(uid, "🤔 <i>ИИ анализирует ситуацию...</i>", parse_mode="HTML")
        ai_gm_suggest(uid, active_chat)
    else:
        bot.reply_to(message, "❌ Нет активных сессий!")

@bot.message_handler(commands=['npc'])
def npc_generator_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    args = message.text.replace('/npc', '').strip()
    npc_type = args if args else "случайный"
    status_msg = bot.send_message(uid, f"🎭 <i>Генерирую {npc_type}...</i>", parse_mode="HTML")
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    context = rp_sessions[active_chat].get('session_context', '') if active_chat else ''
    npc = generate_npc(npc_type, context)
    bot.delete_message(uid, status_msg.message_id)
    if npc:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🎭 Ввести в игру", callback_data=f"npc_play_{uid}"),
            types.InlineKeyboardButton("🔄 Сгенерировать ещё", callback_data=f"npc_reroll_{npc_type}")
        )
        bot.send_message(uid, npc, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(uid, "⚠️ Ошибка генерации NPC")

@bot.message_handler(commands=['location'])
def location_generator_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    args = message.text.replace('/location', '').strip()
    parts = args.split('|')
    loc_type = parts[0].strip() if parts and parts[0].strip() else "таверна"
    mood = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "загадочная"
    status_msg = bot.send_message(uid, f"🏛 <i>Создаю {loc_type}...</i>", parse_mode="HTML")
    location = generate_location(loc_type, mood)
    bot.delete_message(uid, status_msg.message_id)
    if location:
        bot.send_message(uid, location, parse_mode="HTML")
    else:
        bot.send_message(uid, "⚠️ Ошибка генерации локации")

@bot.message_handler(commands=['encounter'])
def encounter_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    args = message.text.replace('/encounter', '').strip() or "лес|средний|день"
    parts = args.split('|')
    encounter = generate_random_encounter(
        environment=parts[0].strip() if len(parts) > 0 else "лес",
        party_level=parts[1].strip() if len(parts) > 1 else "средний",
        time_of_day=parts[2].strip() if len(parts) > 2 else "день"
    )
    if encounter:
        if active_chat:
            gm_narrate(active_chat, f"🎲 <b>Случайная встреча:</b>\n\n{encounter}")
            bot.send_message(uid, "✅ Событие отправлено в чат!")
        else:
            bot.send_message(uid, encounter, parse_mode="HTML")
    else:
        bot.send_message(uid, "⚠️ Ошибка генерации события")

@bot.message_handler(commands=['oracle'])
def oracle_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    text = message.text.replace('/oracle', '').strip()
    if not text: bot.reply_to(message, "🎲 Использование: /oracle [описание действия]"); return
    roll = random.randint(1, 20)
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    context = rp_sessions[active_chat].get('session_context', '') if active_chat else ''
    interpretation = ai_oracle_interpret(roll, text, context)
    if interpretation:
        crit_emoji = "✨" if roll == 20 else "💀" if roll == 1 else ""
        response = f"🎲 <b>Оракул:</b> [d20 = {roll}] {crit_emoji}\n\n📖 {interpretation}"
        if active_chat:
            gm_narrate(active_chat, response)
            bot.send_message(uid, "✅ Отправлено в чат!")
        else:
            bot.send_message(uid, response, parse_mode="HTML")

@bot.message_handler(commands=['puzzle'])
def puzzle_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    args = message.text.replace('/puzzle', '').strip()
    parts = args.split('|')
    difficulty = parts[0].strip() if parts and parts[0].strip() else "средняя"
    theme = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "магия"
    puzzle = generate_puzzle(difficulty, theme)
    if puzzle:
        quiz_data[f"puzzle_{uid}"] = {'puzzle': puzzle, 'hints_shown': 0}
        puzzle_parts = puzzle.split('🎯 ОТВЕТ:')
        display_text = puzzle_parts[0] + "\n\n<i>Используй кнопки для подсказок</i>"
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            types.InlineKeyboardButton("💡 Подсказка 1", callback_data=f"hint_1_{uid}"),
            types.InlineKeyboardButton("💡 Подсказка 2", callback_data=f"hint_2_{uid}"),
            types.InlineKeyboardButton("💡 Подсказка 3", callback_data=f"hint_3_{uid}")
        )
        bot.send_message(uid, display_text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(uid, "⚠️ Ошибка генерации загадки")

@bot.message_handler(commands=['prophecy'])
def prophecy_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    args = message.text.replace('/prophecy', '').strip()
    elements = args.split(',') if args else ["кровь", "луна", "возвращение"]
    elements = [e.strip() for e in elements if e.strip()]
    styles = ["туманное", "зловещее", "эпическое", "загадочное", "обнадёживающее"]
    style = random.choice(styles)
    prophecy = generate_prophecy(style, elements)
    if prophecy:
        parts = prophecy.split('📖 РАСШИФРОВКА ДЛЯ ГМ-а:')
        player_part = parts[0]
        gm_part = parts[1] if len(parts) > 1 else ""
        active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
        if active_chat:
            gm_narrate(active_chat, f"🔮 <b>Древнее пророчество:</b>\n\n{player_part}")
            if gm_part:
                bot.send_message(uid, f"📖 <b>Расшифровка для ГМ-а:</b>\n{gm_part}", parse_mode="HTML")
            bot.send_message(uid, "✅ Пророчество отправлено в чат!")
        else:
            bot.send_message(uid, prophecy, parse_mode="HTML")

@bot.message_handler(commands=['dialogue'])
def dialogue_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    text = message.text.replace('/dialogue', '').strip()
    parts = text.split('|')
    if len(parts) < 3:
        bot.reply_to(message, "📝 Использование: /dialogue [имя NPC] | [характер] | [вопрос игрока]")
        return
    npc_name = parts[0].strip()
    npc_personality = parts[1].strip()
    player_question = parts[2].strip()
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    context = rp_sessions[active_chat].get('session_context', '') if active_chat else ''
    response = generate_npc_dialogue(npc_name, npc_personality, player_question, context)
    if response:
        if active_chat:
            gm_narrate(active_chat, f"💬 <b>{npc_name}:</b> {response}")
            bot.send_message(uid, "✅ Ответ отправлен в чат!")
        else:
            bot.send_message(uid, f"💬 <b>{npc_name}:</b>\n{response}", parse_mode="HTML")

@bot.message_handler(commands=['quest'])
def quest_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    args = message.text.replace('/quest', '').strip()
    parts = args.split('|')
    quest_type = parts[0].strip() if parts and parts[0].strip() else "основной"
    difficulty = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "средний"
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    context = rp_sessions[active_chat].get('session_context', '') if active_chat else ''
    quest = generate_quest(quest_type, difficulty, context)
    if quest:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("📜 Дать квест игрокам", callback_data=f"quest_give_{uid}"),
            types.InlineKeyboardButton("🔄 Другой квест", callback_data=f"quest_reroll_{quest_type}_{difficulty}")
        )
        bot.send_message(uid, quest, reply_markup=markup, parse_mode="HTML")

# ============================================================
# ОБРАБОТЧИКИ КНОПОК МЕНЮ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def show_profile(message):
    caption, photo = format_profile(message.from_user.id)
    if not caption: bot.reply_to(message, "Создай героя: /create"); return
    safe_send(message.chat.id, caption, photo)

@bot.message_handler(func=lambda m: m.text == "🆔 Мой ID")
def show_my_id(message):
    bot.reply_to(message, f"🆔 <b>Ваш ID:</b> <code>{message.from_user.id}</code>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🎭 РП-сессия" and m.from_user.id == ADMIN_ID)
def rp_menu(message):
    bot.send_message(message.from_user.id, "🎭 <b>Меню РП-сессий</b>\n\nВыбери инструмент:", reply_markup=rp_menu_kb(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🎭 Начать сессию" and m.from_user.id == ADMIN_ID)
def rp_start_btn(message):
    uid = message.from_user.id; chat_id = message.chat.id
    thread_id = message.message_thread_id if hasattr(message, 'message_thread_id') else None
    existing_context = get_chat_context(chat_id)
    if existing_context:
        user_states[uid] = 'rp_name'
        temp_learning[uid] = {'rp_chat_id': chat_id, 'thread_id': thread_id, 'context': existing_context}
        bot.send_message(uid, f"📖 <b>Найден контекст:</b>\n{existing_context[:600]}\n\n🎭 Введи название сессии (или /skip для авто-названия):")
    else:
        user_states[uid] = 'rp_context'
        temp_learning[uid] = {'rp_chat_id': chat_id, 'thread_id': thread_id}
        bot.send_message(uid, "📖 Контекст не найден.\n\nО чём будет сессия? Опиши тему (я поищу информацию в интернете):")

@bot.message_handler(func=lambda m: m.text == "🎭 Остановить сессию" and m.from_user.id == ADMIN_ID)
def rp_stop_btn(message):
    uid = message.from_user.id
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if active_chat:
        thread_id = rp_sessions[active_chat].get('thread_id')
        stop_rp_session(active_chat)
        bot.send_message(active_chat, "🎭 <b>РП-сессия завершена.</b>", parse_mode="HTML", message_thread_id=thread_id)
        bot.send_message(uid, "✅ Сессия остановлена!", reply_markup=main_kb(uid))
    else: bot.send_message(uid, "❌ Нет активных сессий!", reply_markup=main_kb(uid))

@bot.message_handler(func=lambda m: m.text == "📖 Повествование" and m.from_user.id == ADMIN_ID)
def rp_narrate_btn(message):
    uid = message.from_user.id
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if not active_chat: bot.send_message(uid, "❌ Нет активных сессий!"); return
    user_states[uid] = 'rp_narrate_text'
    bot.send_message(uid, "📖 Введи текст повествования (/cancel):")

@bot.message_handler(func=lambda m: m.text == "📋 Контекст чата" and m.from_user.id == ADMIN_ID)
def show_context_btn(message):
    uid = message.from_user.id; chat_id = message.chat.id
    context = get_chat_context(chat_id)
    if context: bot.send_message(uid, f"📖 <b>Контекст чата:</b>\n\n{context}", parse_mode="HTML")
    else:
        user_states[uid] = 'rp_context'
        temp_learning[uid] = {'rp_chat_id': chat_id, 'thread_id': None}
        bot.send_message(uid, "📖 Контекст не найден.\n\nО чём будет сессия? Опиши тему:")

@bot.message_handler(func=lambda m: m.text == "⚙️ Режим чата" and m.from_user.id == ADMIN_ID)
def rp_mode_menu(message):
    chat_id = message.chat.id
    current = execute_db("SELECT mode FROM rp_channels WHERE chat_id = ?", (chat_id,), True)
    mode = current[0][0] if current else 'silent'
    markup = types.InlineKeyboardMarkup(row_width=3)
    for m in ['active', 'silent', 'answer']:
        icon = "✅ " if m == mode else ""
        markup.add(types.InlineKeyboardButton(f"{icon}{m}", callback_data=f"rpmode_{m}"))
    bot.send_message(chat_id, f"⚙️ Режим: <b>{mode}</b>", reply_markup=markup, parse_mode="HTML")

# ============================================================
# ОБРАБОТЧИКИ КНОПОК ИИ-ИНСТРУМЕНТОВ
# ============================================================

@bot.message_handler(func=lambda m: m.text in [
    "🤖 AI-Советник", "🎲 Оракул", "👤 Генератор NPC",
    "⚔️ Генератор Квестов", "🏛 Генератор Локаций",
    "🎲 Случайная Встреча", "🧩 Загадка", "🔮 Пророчество",
    "💬 Диалог NPC"
] and m.from_user.id == ADMIN_ID)
def rp_ai_tools(message):
    uid = message.from_user.id
    
    tools_map = {
        "🤖 AI-Советник": ("gm_suggest", "Получить 3 варианта развития сюжета от ИИ"),
        "🎲 Оракул": ("oracle", "Бросок d20 + нарративная интерпретация"),
        "👤 Генератор NPC": ("npc", "Создать персонажа: /npc [тип]"),
        "⚔️ Генератор Квестов": ("quest", "Создать квест: /quest [тип] | [сложность]"),
        "🏛 Генератор Локаций": ("location", "Создать локацию: /location [тип] | [настроение]"),
        "🎲 Случайная Встреча": ("encounter", "Случайное событие: /encounter [местность] | [уровень] | [время]"),
        "🧩 Загадка": ("puzzle", "Создать загадку: /puzzle [сложность] | [тема]"),
        "🔮 Пророчество": ("prophecy", "Создать пророчество: /prophecy [элемент1, элемент2]"),
        "💬 Диалог NPC": ("dialogue", "Ответ NPC: /dialogue [NPC] | [характер] | [вопрос]")
    }
    
    if message.text in tools_map:
        cmd, help_text = tools_map[message.text]
        bot.send_message(uid, f"📝 <b>{message.text}</b>\n\n{help_text}\n\nИспользуй команду /{cmd}", parse_mode="HTML")

print(">>> Модуль 4 загружен (клавиатуры и обработчики)")
# ============================================================
# CALLBACK ОБРАБОТЧИКИ
# ============================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith('rpmode_'))
def handle_rp_mode_callback(call):
    if call.from_user.id != ADMIN_ID: return
    execute_db("INSERT OR REPLACE INTO rp_channels (chat_id, mode) VALUES (?, ?)", (call.message.chat.id, call.data[7:]))
    bot.edit_message_text(f"✅ Режим: <b>{call.data[7:]}</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_callback(call):
    entries = get_wiki_by_category(call.data[4:])
    if not entries: bot.answer_callback_query(call.id, "Пусто"); return
    emoji = CATEGORY_EMOJI.get(call.data[4:], '📚')
    text = f"{emoji} <b>Категория: {call.data[4:].capitalize()}</b>\n\n"
    for i, (kw, desc, _) in enumerate(entries, 1):
        part = f"{i}. <b>{kw.capitalize()}</b>\n   {desc[:100]}{'...' if len(desc) > 100 else ''}\n\n"
        if len(text + part) > 3500:
            bot.send_message(call.message.chat.id, text, parse_mode="HTML")
            text = part
        else: text += part
    if text: bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('quiz_'))
def handle_quiz_callback(call):
    parts = call.data.split('_')
    quiz = quiz_data.get(int(parts[1]))
    if not quiz: bot.answer_callback_query(call.id, "⏰ Устарела!"); return
    if quiz['options'][int(parts[2])] == parts[3]:
        bot.answer_callback_query(call.id, "✅ Правильно!")
        uid = call.from_user.id
        ensure_player(uid, call.from_user.first_name)
        execute_db("UPDATE players SET хуфа = хуфа + 10, quiz_correct = quiz_correct + 1 WHERE user_id = ?", (uid,))
        notify_achievements(call.message.chat.id, check_achievements(uid))
    else: bot.answer_callback_query(call.id, f"❌ Ответ: {parts[3].capitalize()}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('hint_'))
def handle_hint_callback(call):
    uid = call.from_user.id
    if uid != ADMIN_ID: return
    
    parts = call.data.split('_')
    hint_level = int(parts[1])
    original_uid = int(parts[2])
    
    puzzle_data_key = f"puzzle_{original_uid}"
    puzzle_entry = quiz_data.get(puzzle_data_key, {})
    puzzle = puzzle_entry.get('puzzle', '') if isinstance(puzzle_entry, dict) else ''
    
    if not puzzle:
        bot.answer_callback_query(call.id, "⏰ Загадка устарела")
        return
    
    hint_marker = f"💡 ПОДСКАЗКА {hint_level}"
    answer_marker = "🎯 ОТВЕТ:"
    
    if hint_marker in puzzle:
        hint_start = puzzle.find(hint_marker)
        next_hint = puzzle.find("💡 ПОДСКАЗКА", hint_start + 1)
        if next_hint == -1:
            next_hint = puzzle.find(answer_marker, hint_start)
        
        hint = puzzle[hint_start:next_hint].strip() if next_hint != -1 else puzzle[hint_start:].strip()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎯 Показать ответ", callback_data=f"answer_{original_uid}"))
        
        bot.send_message(uid, hint, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id, "Подсказка отправлена!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('answer_'))
def handle_answer_callback(call):
    uid = call.from_user.id
    if uid != ADMIN_ID: return
    
    original_uid = int(call.data.split('_')[1])
    puzzle_data_key = f"puzzle_{original_uid}"
    puzzle_entry = quiz_data.get(puzzle_data_key, {})
    puzzle = puzzle_entry.get('puzzle', '') if isinstance(puzzle_entry, dict) else ''
    
    if not puzzle:
        bot.answer_callback_query(call.id, "⏰ Загадка устарела")
        return
    
    answer_marker = "🎯 ОТВЕТ:"
    consequences_marker = "🔮 ПОСЛЕДСТВИЯ:"
    
    if answer_marker in puzzle:
        answer_start = puzzle.find(answer_marker)
        answer_end = puzzle.find(consequences_marker, answer_start) if consequences_marker in puzzle else len(puzzle)
        answer_text = puzzle[answer_start:answer_end].strip()
        bot.send_message(uid, f"📖 <b>Ответ на загадку:</b>\n\n{answer_text}", parse_mode="HTML")
    
    bot.answer_callback_query(call.id, "Ответ отправлен!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('npc_play_'))
def handle_npc_play_callback(call):
    uid = call.from_user.id
    if uid != ADMIN_ID: return
    
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if active_chat:
        original_text = call.message.text or call.message.caption or ""
        gm_narrate(active_chat, f"🎭 <b>Новый NPC появился:</b>\n\n{original_text}")
        bot.send_message(uid, "✅ NPC введён в игру!")
        bot.answer_callback_query(call.id, "NPC в игре!")
    else:
        bot.answer_callback_query(call.id, "Нет активной сессии!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('npc_reroll_'))
def handle_npc_reroll_callback(call):
    uid = call.from_user.id
    if uid != ADMIN_ID: return
    
    npc_type = call.data.replace('npc_reroll_', '')
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    context = rp_sessions[active_chat].get('session_context', '') if active_chat else ''
    
    npc = generate_npc(npc_type, context)
    if npc:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🎭 Ввести в игру", callback_data=f"npc_play_{uid}"),
            types.InlineKeyboardButton("🔄 Сгенерировать ещё", callback_data=f"npc_reroll_{npc_type}")
        )
        bot.edit_message_text(npc, uid, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id, "Новый NPC!")
    else:
        bot.answer_callback_query(call.id, "Ошибка генерации")

@bot.callback_query_handler(func=lambda call: call.data.startswith('quest_give_'))
def handle_quest_give_callback(call):
    uid = call.from_user.id
    if uid != ADMIN_ID: return
    
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if active_chat:
        original_text = call.message.text or call.message.caption or ""
        gm_narrate(active_chat, f"⚔️ <b>Новое задание!</b>\n\n{original_text}")
        bot.send_message(uid, "✅ Квест выдан игрокам!")
        bot.answer_callback_query(call.id, "Квест в игре!")
    else:
        bot.answer_callback_query(call.id, "Нет активной сессии!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('quest_reroll_'))
def handle_quest_reroll_callback(call):
    uid = call.from_user.id
    if uid != ADMIN_ID: return
    
    parts = call.data.split('_')
    quest_type = parts[2] if len(parts) > 2 else "основной"
    difficulty = parts[3] if len(parts) > 3 else "средний"
    
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    context = rp_sessions[active_chat].get('session_context', '') if active_chat else ''
    
    quest = generate_quest(quest_type, difficulty, context)
    if quest:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("📜 Дать квест игрокам", callback_data=f"quest_give_{uid}"),
            types.InlineKeyboardButton("🔄 Другой квест", callback_data=f"quest_reroll_{quest_type}_{difficulty}")
        )
        bot.edit_message_text(quest, uid, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id, "Новый квест!")
    else:
        bot.answer_callback_query(call.id, "Ошибка генерации")


# ============================================================
# НОВЫЕ ФУНКЦИИ: ЭКОНОМИКА (магазин/инвентарь/бонус/топ)
# ============================================================

