"""
Новые фичи: перевод валюты между игроками (/pay) и крафт предметов (/craft).

Специально сделаны простыми — без отдельной таблицы рецептов:
- /pay работает с уже существующими валютами игрока (рубли, хуфа).
- /craft объединяет любые два предмета из инвентаря в новый ("алхимия"),
  используя уже существующие таблицы items/inventory — никаких новых
  сущностей в БД не потребовалось.
"""
from bot.config import bot, CURRENCIES
from bot.db.database import execute_db, check_achievements, notify_achievements, is_blocked
from bot.utils import clean_text, ensure_player


# ============================================================
# /pay — перевод рублей/хуфы другому игроку
# ============================================================
def _do_pay(chat_id, uid, target_id, amount, currency):
    if is_blocked(uid):
        return
    if target_id == uid:
        bot.send_message(chat_id, "❌ Нельзя перевести самому себе.")
        return
    if amount <= 0:
        bot.send_message(chat_id, "❌ Сумма должна быть положительным числом.")
        return
    if currency not in CURRENCIES:
        bot.send_message(chat_id, f"❌ Валюта должна быть одной из: {', '.join(CURRENCIES)}")
        return

    ensure_player(uid)
    ensure_player(target_id)

    balance = execute_db(f"SELECT {currency} FROM players WHERE user_id = ?", (uid,), True)
    current = balance[0][0] if balance else 0
    if current < amount:
        bot.send_message(chat_id, f"❌ Недостаточно средств! У тебя {current} {currency}, нужно {amount}.")
        return

    execute_db(f"UPDATE players SET {currency} = {currency} - ? WHERE user_id = ?", (amount, uid))
    execute_db(f"UPDATE players SET {currency} = {currency} + ? WHERE user_id = ?", (amount, target_id))

    bot.send_message(chat_id, f"✅ Перевод выполнен: {amount} {currency} → игроку <code>{target_id}</code>.", parse_mode="HTML")
    try:
        bot.send_message(target_id, f"💸 Тебе перевели {amount} {currency} от игрока <code>{uid}</code>!", parse_mode="HTML")
    except Exception:
        pass  # получатель мог не запускать бота лично — это не критично

    notify_achievements(chat_id, check_achievements(uid))


@bot.message_handler(commands=['pay'])
def pay_cmd(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    ensure_player(uid, message.from_user.first_name)
    args = message.text.split()[1:]

    reply_target = message.reply_to_message.from_user.id if message.reply_to_message else None

    usage = ("💸 <b>Перевод валюты</b>\n"
             "Ответь на сообщение игрока: <code>/pay сумма [рубли|хуфа]</code>\n"
             "Или явно: <code>/pay id сумма [рубли|хуфа]</code>")
    try:
        if reply_target:
            if not args:
                bot.send_message(chat_id, usage, parse_mode="HTML"); return
            amount = int(args[0])
            currency = args[1].lower() if len(args) > 1 else 'рубли'
            target_id = reply_target
        else:
            if len(args) < 2:
                bot.send_message(chat_id, usage, parse_mode="HTML"); return
            target_id = int(args[0])
            amount = int(args[1])
            currency = args[2].lower() if len(args) > 2 else 'рубли'
    except ValueError:
        bot.send_message(chat_id, "❌ ID и сумма должны быть числами.")
        return

    _do_pay(chat_id, uid, target_id, amount, currency)


@bot.message_handler(func=lambda m: m.text == "💸 Перевод")
def pay_hint_cmd(message):
    bot.send_message(
        message.chat.id,
        "💸 <b>Перевод валюты другому игроку</b>\n\n"
        "Ответь на его сообщение командой:\n<code>/pay сумма [рубли|хуфа]</code>\n\n"
        "Или укажи ID напрямую:\n<code>/pay id сумма [рубли|хуфа]</code>\n\n"
        "Свой ID можно узнать кнопкой 🆔 Мой ID.",
        parse_mode="HTML"
    )


# ============================================================
# /craft — объединить два предмета из инвентаря в новый
# ============================================================
def _get_owned_item(uid, name):
    row = execute_db(
        """SELECT items.id, items.name, items.price, items.currency, items.emoji, items.description, inventory.qty
           FROM inventory JOIN items ON items.id = inventory.item_id
           WHERE inventory.user_id = ? AND LOWER(items.name) = LOWER(?)""",
        (uid, name), True
    )
    return row[0] if row else None


def _craft_help(chat_id, uid):
    rows = execute_db(
        """SELECT items.name, items.emoji, inventory.qty FROM inventory
           JOIN items ON items.id = inventory.item_id WHERE inventory.user_id = ?""",
        (uid,), True
    )
    text = "⚒️ <b>Крафт</b>\nОбъединяет два предмета из инвентаря в новый.\nНапиши: <code>/craft предмет1, предмет2</code>\n\n"
    if rows:
        text += "🎒 Твои предметы:\n" + "\n".join(f"{emoji} {name} × {qty}" for name, emoji, qty in rows)
    else:
        text += "🎒 Инвентарь пуст — загляни в 🛒 Магазин!"
    bot.send_message(chat_id, text, parse_mode="HTML")


@bot.message_handler(commands=['craft'])
def craft_cmd(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    ensure_player(uid, message.from_user.first_name)

    args = message.text.replace('/craft', '', 1).strip()
    if not args:
        _craft_help(chat_id, uid)
        return

    parts = [clean_text(p) for p in args.split(',')]
    if len(parts) != 2 or not all(parts):
        bot.send_message(chat_id, "❌ Укажи ровно два предмета через запятую: <code>/craft меч, щит</code>", parse_mode="HTML")
        return

    name1, name2 = parts
    item1 = _get_owned_item(uid, name1)
    item2 = _get_owned_item(uid, name2)
    if not item1 or not item2:
        missing = name1 if not item1 else name2
        bot.send_message(chat_id, f"❌ У тебя нет предмета «{missing}» в инвентаре.")
        return
    if item1[0] == item2[0] and item1[6] < 2:
        bot.send_message(chat_id, "❌ Нужно два разных предмета — или два экземпляра одного и того же.")
        return

    # списываем ингредиенты (по 1 штуке каждого; если предмет один и тот же — спишется 2 штуки)
    consume = {}
    for item in (item1, item2):
        consume[item[0]] = consume.get(item[0], 0) + 1
    for item_id, need in consume.items():
        row = execute_db("SELECT qty FROM inventory WHERE user_id = ? AND item_id = ?", (uid, item_id), True)
        have = row[0][0] if row else 0
        remaining = have - need
        if remaining <= 0:
            execute_db("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (uid, item_id))
        else:
            execute_db("UPDATE inventory SET qty = ? WHERE user_id = ? AND item_id = ?", (remaining, uid, item_id))

    result_name = f"{item1[1]} + {item2[1]}"
    result_price = max(1, int((item1[2] + item2[2]) * 0.7))
    result_currency = item1[3] or item2[3] or 'рубли'
    result_emoji = '✨'
    result_desc = f"Создано крафтом из «{item1[1]}» и «{item2[1]}»."

    existing = execute_db("SELECT id FROM items WHERE name = ?", (result_name,), True)
    if existing:
        result_id = existing[0][0]
    else:
        execute_db("INSERT INTO items (name, description, price, currency, emoji) VALUES (?, ?, ?, ?, ?)",
                    (result_name, result_desc, result_price, result_currency, result_emoji))
        result_id = execute_db("SELECT id FROM items WHERE name = ?", (result_name,), True)[0][0]

    inv_row = execute_db("SELECT qty FROM inventory WHERE user_id = ? AND item_id = ?", (uid, result_id), True)
    if inv_row:
        execute_db("UPDATE inventory SET qty = qty + 1 WHERE user_id = ? AND item_id = ?", (uid, result_id))
    else:
        execute_db("INSERT INTO inventory (user_id, item_id, qty) VALUES (?, ?, 1)", (uid, result_id))

    bot.send_message(chat_id, f"⚒️ Готово! Ты создал: {result_emoji} <b>{result_name}</b>\n{result_desc}", parse_mode="HTML")
    notify_achievements(chat_id, check_achievements(uid))


@bot.message_handler(func=lambda m: m.text == "⚒️ Крафт")
def craft_hint_btn(message):
    _craft_help(message.chat.id, message.from_user.id)
