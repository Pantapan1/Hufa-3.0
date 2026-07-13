"""
Экономика, магазин, достижения, бэкапы, модерация — обработчики команд/кнопок
администраторской и игровой панели.
"""
import os
import re
import time
import random

from bot.config import bot, ADMIN_ID, CATEGORY_EMOJI, DB_NAME, BASE_DIR, logger
from bot.state import rp_sessions
from bot.db.database import (execute_db, get_all_users, get_wiki_info, import_lore_from_file,
                             check_achievements, notify_achievements, ACHIEVEMENTS)
from bot.utils import clean_text, ensure_player, admin_panel_kb, shop_kb
from bot.ai.ai_client import groq_complete

@bot.message_handler(func=lambda m: m.text == "🛒 Магазин")
def shop_cmd(message):
    items = execute_db("SELECT id, name, price, currency, emoji FROM items ORDER BY price", (), True)
    if not items:
        bot.send_message(message.chat.id, "🛒 Магазин пока пуст. Загляни позже!")
        return
    bot.send_message(message.chat.id, "🛒 <b>Магазин</b>\n\nВыбери товар:", reply_markup=shop_kb(items), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def handle_buy_callback(call):
    uid = call.from_user.id
    item_id = int(call.data.split('_')[1])
    item = execute_db("SELECT id, name, price, currency, emoji FROM items WHERE id = ?", (item_id,), True)
    if not item:
        bot.answer_callback_query(call.id, "❌ Товар не найден"); return
    item_id, name, price, currency, emoji = item[0]
    ensure_player(uid, call.from_user.first_name)
    balance = execute_db(f"SELECT {currency} FROM players WHERE user_id = ?", (uid,), True)
    current = balance[0][0] if balance else 0
    if current < price:
        bot.answer_callback_query(call.id, f"❌ Не хватает {currency}! Нужно {price}, у тебя {current}", show_alert=True)
        return
    execute_db(f"UPDATE players SET {currency} = {currency} - ? WHERE user_id = ?", (price, uid))
    existing = execute_db("SELECT qty FROM inventory WHERE user_id = ? AND item_id = ?", (uid, item_id), True)
    if existing:
        execute_db("UPDATE inventory SET qty = qty + 1 WHERE user_id = ? AND item_id = ?", (uid, item_id))
    else:
        execute_db("INSERT INTO inventory (user_id, item_id, qty) VALUES (?, ?, 1)", (uid, item_id))
    bot.answer_callback_query(call.id, f"✅ Куплено: {emoji} {name}!", show_alert=True)
    notify_achievements(call.message.chat.id, check_achievements(uid))

@bot.message_handler(func=lambda m: m.text == "🎒 Инвентарь")
def inventory_cmd(message):
    uid = message.from_user.id
    rows = execute_db("""SELECT items.name, items.emoji, inventory.qty FROM inventory
                          JOIN items ON items.id = inventory.item_id WHERE inventory.user_id = ?""", (uid,), True)
    if not rows:
        bot.send_message(message.chat.id, "🎒 Инвентарь пуст. Загляни в 🛒 Магазин!")
        return
    text = "🎒 <b>Твой инвентарь:</b>\n\n" + "\n".join([f"{emoji} {name} × {qty}" for name, emoji, qty in rows])
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🎁 Ежедневный бонус")
def daily_bonus_cmd(message):
    uid = message.from_user.id
    ensure_player(uid, message.from_user.first_name)
    row = execute_db("SELECT last_daily FROM players WHERE user_id = ?", (uid,), True)
    last = row[0][0] if row else None
    now = time.time()
    if last:
        try:
            elapsed = now - float(last)
        except (TypeError, ValueError):
            elapsed = 999999
        if elapsed < 86400:
            remaining = int(86400 - elapsed)
            h, m = remaining // 3600, (remaining % 3600) // 60
            bot.send_message(message.chat.id, f"⏳ Бонус уже получен! Приходи через {h} ч {m} мин.")
            return
    reward = random.randint(20, 60)
    streak_row = execute_db("SELECT daily_streak FROM players WHERE user_id = ?", (uid,), True)
    prev_streak = (streak_row[0][0] or 0) if streak_row else 0
    # Если бонус забирали не позже 48 часов назад — серия продолжается, иначе начинается заново
    new_streak = prev_streak + 1 if (last and elapsed < 172800) else 1
    execute_db("UPDATE players SET рубли = рубли + ?, last_daily = ?, daily_streak = ?, daily_claims = daily_claims + 1 WHERE user_id = ?",
               (reward, now, new_streak, uid))
    bot.send_message(message.chat.id, f"🎁 Ежедневный бонус: +{reward} 💰 рублей!\n🔥 Серия дней подряд: {new_streak}")
    notify_achievements(message.chat.id, check_achievements(uid))

@bot.message_handler(commands=['achievements'])
@bot.message_handler(func=lambda m: m.text == "🏅 Достижения")
def achievements_cmd(message):
    uid = message.from_user.id
    ensure_player(uid, message.from_user.first_name)
    newly = check_achievements(uid)
    if newly:
        notify_achievements(message.chat.id, newly)
    unlocked = {row[0] for row in (execute_db("SELECT code FROM player_achievements WHERE user_id = ?", (uid,), True) or [])}
    lines = []
    for ach in ACHIEVEMENTS:
        mark = "✅" if ach['code'] in unlocked else "🔒"
        lines.append(f"{mark} {ach['emoji']} <b>{ach['name']}</b> — {ach['desc']} (+{ach['reward_amount']} {ach['reward_currency']})")
    text = f"🏅 <b>Достижения</b> ({len(unlocked)}/{len(ACHIEVEMENTS)})\n\n" + "\n".join(lines)
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(commands=['import_lore'])
@bot.message_handler(func=lambda m: m.text == "📥 Импорт лора" and m.from_user.id == ADMIN_ID)
def import_lore_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    added = import_lore_from_file()
    if added:
        bot.send_message(message.chat.id, f"📥 Импорт завершён: добавлено {added} новых статей из lore.txt в базу знаний.")
    else:
        bot.send_message(message.chat.id, "📥 Новых статей не найдено — база знаний уже содержит всё из lore.txt (или файл пуст).")

@bot.message_handler(func=lambda m: m.text == "🏆 Топ игроков")
def leaderboard_cmd(message):
    rows = execute_db("SELECT name, хуфа, рубли FROM players ORDER BY (хуфа * 10 + рубли) DESC LIMIT 10", (), True)
    if not rows:
        bot.send_message(message.chat.id, "🏆 Пока никто не набрал очков.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, huf, rub) in enumerate(rows):
        icon = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{icon} <b>{name}</b> — 🧪{huf} 💰{rub}")
    bot.send_message(message.chat.id, "🏆 <b>Топ игроков:</b>\n\n" + "\n".join(lines), parse_mode="HTML")

# ============================================================
# НОВЫЕ ФУНКЦИИ: ЗАКЛАДКИ ПО ВИКИ
# ============================================================

@bot.message_handler(commands=['bookmark'])
def bookmark_cmd(message):
    uid = message.from_user.id
    kw = clean_text(message.text.replace('/bookmark', ''), is_key=True).lower()
    if not kw:
        bot.reply_to(message, "❌ Формат: /bookmark ключевое_слово"); return
    if not get_wiki_info(kw):
        bot.reply_to(message, f"❌ «{kw}» не найдено в базе знаний."); return
    execute_db("INSERT OR IGNORE INTO bookmarks (user_id, keyword) VALUES (?, ?)", (uid, kw))
    bot.reply_to(message, f"🔖 «{kw}» добавлено в закладки!")
    notify_achievements(message.chat.id, check_achievements(uid))

@bot.message_handler(func=lambda m: m.text == "🔖 Закладки")
def bookmarks_cmd(message):
    uid = message.from_user.id
    rows = execute_db("SELECT keyword FROM bookmarks WHERE user_id = ?", (uid,), True)
    if not rows:
        bot.send_message(message.chat.id, "🔖 Закладок нет. Добавь: /bookmark ключ")
        return
    text = "🔖 <b>Твои закладки:</b>\n\n" + "\n".join([f"• {kw}" for (kw,) in rows])
    text += "\n\nПросто напиши название, чтобы узнать подробности!"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# ============================================================
# НОВЫЕ ФУНКЦИИ: АДМИН-ПАНЕЛЬ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель" and m.from_user.id == ADMIN_ID)
def admin_panel_cmd(message):
    bot.send_message(message.chat.id, "🛠 <b>Админ-панель</b>", reply_markup=admin_panel_kb(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "💾 Бэкап БД" and m.from_user.id == ADMIN_ID)
def backup_cmd(message):
    try:
        with open(DB_NAME, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=f"💾 Бэкап от {time.strftime('%Y-%m-%d %H:%M')}")
    except Exception as e:
        logger.error(f"Ошибка бэкапа: {e}")
        bot.send_message(message.chat.id, "❌ Не удалось создать бэкап.")

@bot.message_handler(func=lambda m: m.text == "📈 Общая статистика" and m.from_user.id == ADMIN_ID)
def full_stats_cmd(message):
    users_n = execute_db("SELECT COUNT(*) FROM players", (), True)[0][0]
    wiki_n = execute_db("SELECT COUNT(*) FROM wiki", (), True)[0][0]
    stories_n = execute_db("SELECT COUNT(DISTINCT story_name) FROM stories", (), True)[0][0]
    links_n = execute_db("SELECT COUNT(*) FROM wiki_links", (), True)[0][0]
    sessions_n = execute_db("SELECT COUNT(*) FROM rp_sessions", (), True)[0][0]
    blocked_n = execute_db("SELECT COUNT(*) FROM blocked_users", (), True)[0][0]
    items_n = execute_db("SELECT COUNT(*) FROM items", (), True)[0][0]
    total_broadcast = len(get_all_users())
    text = (f"📈 <b>Общая статистика бота</b>\n\n"
            f"👤 Игроков зарегистрировано: {users_n}\n"
            f"📨 Всего в рассылке: {total_broadcast}\n"
            f"🚫 В бан-листе: {blocked_n}\n"
            f"📚 Записей в вики: {wiki_n}\n"
            f"🔗 Связей: {links_n}\n"
            f"📖 Историй: {stories_n}\n"
            f"🎭 РП-сессий (всего): {sessions_n}\n"
            f"🛒 Товаров в магазине: {items_n}")
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🚫 Бан-лист" and m.from_user.id == ADMIN_ID)
def banlist_menu_cmd(message):
    rows = execute_db("SELECT user_id, reason FROM blocked_users", (), True)
    text = "🚫 <b>Бан-лист:</b>\n\n" + ("\n".join([f"• <code>{uid}</code> — {reason or 'без причины'}" for uid, reason in rows]) if rows else "пусто")
    text += "\n\nКоманды:\n/ban ID [причина]\n/unban ID"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(commands=['ban'])
def ban_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Формат: /ban ID [причина]"); return
    try:
        target_id = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ ID должен быть числом."); return
    reason = parts[2] if len(parts) > 2 else None
    execute_db("INSERT OR REPLACE INTO blocked_users (user_id, reason) VALUES (?, ?)", (target_id, reason))
    bot.reply_to(message, f"🚫 Пользователь <code>{target_id}</code> заблокирован.", parse_mode="HTML")

@bot.message_handler(commands=['unban'])
def unban_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ Формат: /unban ID"); return
    try:
        target_id = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ ID должен быть числом."); return
    execute_db("DELETE FROM blocked_users WHERE user_id = ?", (target_id,))
    bot.reply_to(message, f"✅ Пользователь <code>{target_id}</code> разблокирован.", parse_mode="HTML")

@bot.message_handler(commands=['give'])
def give_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 4 or parts[3] not in ('хуфа', 'рубли'):
        bot.reply_to(message, "❌ Формат: /give ID количество хуфа|рубли\nПример: /give 123456 50 рубли"); return
    try:
        target_id, amount = int(parts[1]), int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ ID и количество должны быть числами."); return
    currency = parts[3]
    ensure_player(target_id)
    execute_db(f"UPDATE players SET {currency} = {currency} + ? WHERE user_id = ?", (amount, target_id))
    bot.reply_to(message, f"✅ Выдано {amount} {currency} игроку <code>{target_id}</code>.", parse_mode="HTML")
    try:
        bot.send_message(target_id, f"🎉 Тебе начислено {amount} {currency} от Хранителя!")
    except Exception:
        pass

@bot.message_handler(func=lambda m: m.text == "💰 Выдать валюту" and m.from_user.id == ADMIN_ID)
def give_hint_cmd(message):
    bot.send_message(message.chat.id, "💰 Формат: /give ID количество хуфа|рубли\nПример: /give 123456 50 рубли")

@bot.message_handler(commands=['additem'])
def additem_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace('/additem', '').strip()
    parts = [p.strip() for p in text.split('|')]
    if len(parts) < 3:
        bot.reply_to(message, "❌ Формат: /additem Название | Цена | хуфа|рубли | Описание | Эмодзи\nПример: /additem Зелье силы | 30 | рубли | Даёт +2 к силе | 🧪")
        return
    name = parts[0]
    try:
        price = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ Цена должна быть числом."); return
    currency = parts[2] if parts[2] in ('хуфа', 'рубли') else 'рубли'
    description = parts[3] if len(parts) > 3 else ""
    emoji = parts[4] if len(parts) > 4 else "🎁"
    execute_db("INSERT OR REPLACE INTO items (name, description, price, currency, emoji) VALUES (?, ?, ?, ?, ?)",
               (name, description, price, currency, emoji))
    bot.reply_to(message, f"✅ Товар «{emoji} {name}» добавлен в магазин за {price} {currency}!")

@bot.message_handler(func=lambda m: m.text == "🏷 Добавить товар" and m.from_user.id == ADMIN_ID)
def additem_hint_cmd(message):
    bot.send_message(message.chat.id, "🏷 Формат: /additem Название | Цена | хуфа|рубли | Описание | Эмодзи\nПример: /additem Зелье силы | 30 | рубли | Даёт +2 к силе | 🧪")

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт вики" and m.from_user.id == ADMIN_ID)
def export_wiki_cmd(message):
    rows = execute_db("SELECT keyword, category, description FROM wiki ORDER BY category, keyword", (), True)
    if not rows:
        bot.send_message(message.chat.id, "📤 Вики пуста."); return
    lines = []
    for kw, cat, desc in rows:
        lines.append(f"### {kw} [{cat}]\n{desc}\n")
    export_path = os.path.join(BASE_DIR, 'wiki_export.txt')
    with open(export_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    with open(export_path, 'rb') as f:
        bot.send_document(message.chat.id, f, caption=f"📤 Экспорт вики ({len(rows)} записей)")

# ============================================================
# НОВЫЕ ФУНКЦИИ: ПОИСК И РЕКАП ДЛЯ РП
# ============================================================

@bot.message_handler(commands=['search'])
def search_cmd(message):
    query = message.text.replace('/search', '').strip()
    if not query:
        bot.reply_to(message, "❌ Формат: /search запрос"); return
    all_wiki = execute_db("SELECT keyword, category FROM wiki", (), True)
    q = query.lower()
    matches = [f"{CATEGORY_EMOJI.get(cat, '📚')} {kw}" for kw, cat in all_wiki if q in kw.lower()]
    if not matches:
        bot.reply_to(message, "🔍 Ничего не найдено."); return
    bot.reply_to(message, "🔍 <b>Найдено:</b>\n\n" + "\n".join(matches[:30]), parse_mode="HTML")

@bot.message_handler(commands=['recap'])
def recap_cmd(message):
    uid = message.from_user.id
    if uid != ADMIN_ID: return
    active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
    if not active_chat:
        bot.reply_to(message, "❌ Нет активных сессий!"); return
    session = rp_sessions[active_chat]
    recent = session['context'][-30:]
    if not recent:
        bot.reply_to(message, "📖 Пока нечего пересказывать."); return
    context_text = "\n".join([f"{'ГМ' if c['user_id'] == 'gm' else c['user_name']}: {c['text'][:200]}" for c in recent])
    summary = groq_complete(
        "Ты — летописец ролевой игры. Кратко перескажи последние события сессии (5-8 предложений), выдели ключевые моменты и текущую интригу. Пиши на русском.",
        context_text, temperature=0.4, max_tokens=500
    )
    if summary:
        bot.send_message(uid, f"📖 <b>Краткий пересказ сессии:</b>\n\n{summary}", parse_mode="HTML")
    else:
        bot.reply_to(message, "⚠️ Не удалось составить пересказ.")

# ============================================================
# ГЛАВНЫЙ ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ
# ============================================================

