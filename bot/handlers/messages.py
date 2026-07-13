"""
Главный обработчик всех входящих сообщений (текст/медиа), маршрутизация по user_states,
меню-кнопки не привязанные к конкретной команде, спец-триггеры ("что ты знаешь" и т.п.),
пошаговые сценарии (регистрация, обучение вики, загрузка историй).
"""
import re
import time
import random
from telebot import types

from bot.config import bot, ADMIN_ID, CATEGORY_EMOJI
from bot.state import (user_states, temp_data, temp_learning, story_parts, story_tellers,
                       broadcast_data, quiz_data, dialogue_learning, rp_sessions, rp_pending,
                       session_contexts)
from bot.db.database import (execute_db, is_blocked, get_all_users, broadcast_message,
                             add_wiki_link, get_links_text, get_wiki_by_category, get_categories_stats,
                             get_wiki_info, get_random_lore, generate_quiz, get_lore_stats,
                             check_lore_conflicts, edit_wiki_keyword, edit_wiki_photo,
                             find_story_by_name, get_story_parts, get_all_stories, delete_story,
                             get_story_count, save_story_part, set_chat_context, check_achievements,
                             notify_achievements)
from bot.utils import (clean_text, safe_send, ensure_player, main_kb, categories_kb, db_management_kb,
                       send_story_part)
from bot.ai.ai_client import (search_wiki_with_context, analyze_chat_history_for_context,
                              ai_categorize_keyword, auto_categorize_all, generate_wiki_description,
                              analyze_vs_battle, polish_full_story, extract_lore_from_story,
                              dialogue_learn_step)
from bot.ai.rp import start_rp_session, stop_rp_session, gm_narrate, gm_reply_to_player, process_rp_message

@bot.message_handler(content_types=['text', 'photo', 'video', 'audio', 'voice', 'document', 'animation'])
def handle_all(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    state = user_states.get(uid)

    # 0. Заблокированные пользователи полностью игнорируются
    if uid != ADMIN_ID and is_blocked(uid):
        return

    # 1. Обработка РП-сообщений (если активна сессия) — ПРОВЕРЯЕМ ПЕРВЫМИ
    if chat_id in rp_sessions and rp_sessions[chat_id].get('active'):
        if uid != rp_sessions[chat_id]['gm_id']:
            if message.content_type == 'text' and message.text and not message.text.startswith('/'):
                process_rp_message(chat_id, uid, message.text, message.from_user.first_name)
                return
            elif message.content_type != 'text':
                # Игроки могут отправлять медиа в РП
                process_rp_message(chat_id, uid, f"[Отправил {message.content_type}]", message.from_user.first_name)
                return

    # 2. Обработка текстовых сообщений
    if message.content_type == 'text' and message.text:
        text = message.text
        text_lower = text.lower()

        # /cancel работает всегда, независимо от текущего состояния
        if text == '/cancel':
            if user_states.pop(uid, None):
                bot.send_message(chat_id, "❌ Отменено.", reply_markup=main_kb(uid))
            else:
                bot.send_message(chat_id, "Нечего отменять.", reply_markup=main_kb(uid))
            return

        # Команды уже обработаны декораторами — просто выходим
        if text.startswith('/'):
            return
        
        # Обработка состояний
        if handle_state_message(message, uid, chat_id, state):
            return
        
        # Обработка кнопок меню
        if handle_menu_buttons(message, uid, chat_id, text, text_lower):
            return
        
        # Обработка специальных текстовых триггеров
        if handle_special_triggers(message, uid, chat_id, text, text_lower):
            return
        
        # Ответ админа на РП сообщение (reply)
        if uid == ADMIN_ID and message.reply_to_message:
            for chat_id_pending, pending in rp_pending.items():
                if message.reply_to_message.message_id in pending:
                    gm_reply_to_player(uid, text, message.reply_to_message.message_id)
                    bot.send_message(uid, "✅ Ответ отправлен!")
                    return
        
        # Поиск по вики (основной функционал)
        if can_search(state):
            answer, photo, key = search_wiki_with_context(message)
            if answer:
                safe_send(chat_id, answer, photo, key)
                ensure_player(uid, message.from_user.first_name)
                execute_db("UPDATE players SET wiki_views = wiki_views + 1 WHERE user_id = ?", (uid,))
                notify_achievements(chat_id, check_achievements(uid))
                return
    
    # 3. Обработка фото и других медиа
    if handle_media_message(message, uid, state):
        return
    
    # 4. Если ничего не сработало
    if message.content_type == 'text' and message.text == "🔙 Назад":
        user_states.pop(uid, None)
        bot.send_message(chat_id, "🔙 Главное меню", reply_markup=main_kb(uid))


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ОБРАБОТЧИКА
# ============================================================

def can_search(state):
    """Проверяет, можно ли выполнять поиск по вики в текущем состоянии"""
    blocked_states = ['vs_mode', 'rp_name', 'rp_narrate_text', 'rp_context', 'edit_key', 'edit_desc']
    blocked_prefixes = ['story_', 'broadcast_', 'db_', 'reg_', 'learn_', 'link_']
    
    if state in blocked_states:
        return False
    if state and any(str(state).startswith(prefix) for prefix in blocked_prefixes):
        return False
    return True


def handle_state_message(message, uid, chat_id, state):
    """Обрабатывает сообщения в зависимости от текущего состояния пользователя"""
    if not state:
        return False
    
    text = message.text if message.content_type == 'text' and message.text else ""
    
    # Обработка состояний РП
    if uid == ADMIN_ID and state == 'rp_context':
        if message.content_type != 'text': return False
        context_topic = text
        status_msg = bot.send_message(uid, "🌐 Анализирую тему и ищу информацию...")
        context = analyze_chat_history_for_context(chat_id, context_topic)
        bot.delete_message(uid, status_msg.message_id)
        if context:
            temp_learning[uid]['context'] = context
            set_chat_context(chat_id, context)
            user_states[uid] = 'rp_name'
            bot.send_message(uid, f"📖 <b>Контекст создан:</b>\n{context[:600]}\n\n🎭 Введи название сессии (или /skip для авто-названия):")
        else:
            temp_learning[uid]['context'] = context_topic
            set_chat_context(chat_id, context_topic)
            user_states[uid] = 'rp_name'
            bot.send_message(uid, f"📖 Контекст: {context_topic[:300]}\n\n🎭 Введи название сессии (или /skip):")
        return True
    
    if uid == ADMIN_ID and state == 'rp_name':
        if message.content_type != 'text': return False
        if text == '/skip':
            text = temp_learning[uid].get('context', 'РП-сессия')[:50]
        context = temp_learning[uid].get('context', '')
        result, status = start_rp_session(temp_learning[uid]['rp_chat_id'], uid, text, context)
        if status == "no_context":
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Не удалось определить контекст. Нажмите «🎭 Начать сессию» и опишите тему.")
            return True
        rp_sessions[temp_learning[uid]['rp_chat_id']]['thread_id'] = temp_learning[uid].get('thread_id')
        user_states.pop(uid, None)
        bot.send_message(temp_learning[uid]['rp_chat_id'],
                        f"🎭 <b>РП-сессия началась!</b>\n«{text}»\n📖 Контекст: {context[:200]}",
                        parse_mode="HTML", message_thread_id=temp_learning[uid].get('thread_id'))
        bot.send_message(uid, f"✅ Сессия «{text}» запущена!\n📖 Контекст: {context[:300]}")
        return True
    
    if uid == ADMIN_ID and state == 'rp_narrate_text':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        active_chat = next((cid for cid, s in rp_sessions.items() if s['gm_id'] == uid and s['active']), None)
        if active_chat:
            gm_narrate(active_chat, text)
            bot.send_message(uid, "✅ Отправлено!", reply_markup=main_kb(uid))
        else:
            bot.send_message(uid, "❌ Нет активных сессий!")
        user_states.pop(uid, None)
        return True
    
    # Обработка состояний управления БД
    if uid == ADMIN_ID and state == 'db_view':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=db_management_kb())
            return True
        info = get_wiki_info(text)
        if info:
            msg = f"📋 <b>{info[0]}</b>\n\n📝 {info[1][:500]}\n\n{CATEGORY_EMOJI.get(info[3], '📚')} {info[3]}"
            links = get_links_text(info[0])
            if links: msg += f"\n\n🕸 Связи:\n{links}"
            safe_send(uid, msg, info[2])
        else:
            bot.send_message(uid, "❌ Не найдена!")
        user_states.pop(uid, None)
        return True
    
    if uid == ADMIN_ID and state == 'db_edit_key_old':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=db_management_kb())
            return True
        old_key = clean_text(text, is_key=True).lower()
        if not execute_db("SELECT keyword FROM wiki WHERE keyword = ?", (old_key,), True):
            bot.send_message(uid, "❌ Не найден!")
            return True
        temp_learning[uid] = {'old_key': old_key}
        user_states[uid] = 'db_edit_key_new'
        bot.send_message(uid, f"✏️ Новый ключ для <b>{old_key}</b>:")
        return True
    
    if uid == ADMIN_ID and state == 'db_edit_key_new':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=db_management_kb())
            return True
        success, msg = edit_wiki_keyword(temp_learning[uid]['old_key'], clean_text(text, is_key=True).lower())
        user_states.pop(uid, None)
        bot.send_message(uid, msg, reply_markup=db_management_kb())
        return True
    
    if uid == ADMIN_ID and state == 'db_update_photo_key':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=db_management_kb())
            return True
        keyword = clean_text(text, is_key=True).lower()
        if not execute_db("SELECT keyword FROM wiki WHERE keyword = ?", (keyword,), True):
            bot.send_message(uid, "❌ Не найден!")
            return True
        temp_learning[uid] = {'photo_key': keyword}
        user_states[uid] = 'db_update_photo_send'
        info = get_wiki_info(keyword)
        if info and info[2]:
            bot.send_photo(uid, info[2], caption="📸 Текущее фото\nОтправь НОВОЕ:")
        else:
            bot.send_message(uid, f"📸 Отправь фото для «{keyword}»:")
        return True
    
    if uid == ADMIN_ID and state == 'db_delete_photo':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=db_management_kb())
            return True
        success, msg = edit_wiki_photo(clean_text(text, is_key=True).lower(), None)
        user_states.pop(uid, None)
        bot.send_message(uid, msg, reply_markup=db_management_kb())
        return True
    
    # Обработка состояний связей
    if state == 'link_source':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        temp_learning[uid] = {'link_source': clean_text(text).lower()}
        user_states[uid] = 'link_target'
        bot.send_message(uid, f"🔗 Второй ключ для «{text}»:")
        return True
    
    if state == 'link_target':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        temp_learning[uid]['link_target'] = clean_text(text).lower()
        user_states[uid] = 'link_type'
        bot.send_message(uid, "🔗 Тип: враг | друг | союзник | находится_в | владеет | часть")
        return True
    
    if state == 'link_type':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        add_wiki_link(temp_learning[uid]['link_source'], temp_learning[uid]['link_target'], text.strip().lower())
        user_states.pop(uid, None)
        bot.send_message(uid, "✅ Связь добавлена!", reply_markup=main_kb(uid))
        return True
    
    # Обработка состояний рассылки
    if state and state.startswith('broadcast_'):
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        if state == 'broadcast_text':
            s, f = broadcast_message(uid, text)
            user_states.pop(uid, None)
            bot.send_message(uid, f"✅ {s}\n❌ {f}", reply_markup=main_kb(uid))
            return True
        if state == 'broadcast_photo':
            bot.send_message(uid, "❌ Отправь фото!")
            return True
    
    # Обработка VS битвы
    if state == 'vs_mode':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        for sep in [' vs ', ' против ']:
            if sep in text.lower():
                parts = text.lower().split(sep)
                if len(parts) == 2:
                    result = analyze_vs_battle(clean_text(parts[0]), clean_text(parts[1]))
                    if result:
                        safe_send(uid, result)
                    else:
                        bot.send_message(uid, "⚠️ Проверь имена.")
                    user_states.pop(uid, None)
                    bot.send_message(uid, "⚔️ Ещё?", reply_markup=main_kb(uid))
                    return True
        bot.send_message(uid, "❌ Формат: Имя1 vs Имя2")
        return True
    
    # Обработка состояний историй
    if state and state.startswith('story_'):
        if handle_story_state(message, uid, text, state):
            return True
    
    # Обработка состояний импорта
    if state == 'import_story':
        if message.content_type != 'text': return False
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        actual_name = find_story_by_name(text.strip())
        if not actual_name:
            bot.send_message(uid, "❌ История не найдена!")
            return True
        status_msg = bot.send_message(uid, "🤖 ИИ анализирует историю...", parse_mode="HTML")
        result = extract_lore_from_story(actual_name)
        bot.delete_message(uid, status_msg.message_id)
        user_states.pop(uid, None)
        if result:
            bot.send_message(uid, f"📥 <b>Найденные термины:</b>\n\n{result}", reply_markup=main_kb(uid))
        else:
            bot.send_message(uid, "⚠️ Не удалось извлечь лор.", reply_markup=main_kb(uid))
        return True
    
    # Обработка диалогового обучения
    if uid in dialogue_learning:
        if message.content_type != 'text': return False
        response = dialogue_learn_step(uid, text)
        if response:
            bot.send_message(uid, response, parse_mode="HTML")
        if uid not in dialogue_learning:
            bot.send_message(uid, "✅ Обучение завершено!", reply_markup=main_kb(uid))
        return True
    
    # Обработка обучения ГМ-а
    if uid == ADMIN_ID and state and state.startswith('learn_'):
        if handle_learn_state(message, uid, text, state):
            return True
    
    # Обработка регистрации
    if state and state.startswith('reg_'):
        if handle_reg_state(message, uid, state):
            return True
    
    # Обработка редактирования досье
    if uid == ADMIN_ID and state == 'edit_key':
        if message.content_type != 'text': return False
        clean_key = clean_text(text, is_key=True).lower()
        res = execute_db("SELECT description FROM wiki WHERE keyword = ?", (clean_key,), True)
        if res:
            temp_learning[uid] = {'key': clean_key}
            user_states[uid] = 'edit_desc'
            bot.send_message(uid, f"📝 Текущий:\n{res[0][0]}\n\nНовый текст:")
        else:
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Нет ключа.")
        return True
    
    if uid == ADMIN_ID and state == 'edit_desc':
        if message.content_type != 'text': return False
        execute_db("UPDATE wiki SET description = ? WHERE keyword = ?", (text, temp_learning[uid]['key']))
        user_states.pop(uid, None)
        bot.send_message(uid, "✅ Обновлено!", reply_markup=main_kb(uid))
        return True
    
    return False

print(">>> Модуль 5 загружен (коллбэки и часть обработчика)")

def handle_menu_buttons(message, uid, chat_id, text, text_lower):
    """Обрабатывает нажатия на кнопки клавиатуры"""
    
    # База знаний
    if "что ты знаешь" in text_lower or text_lower == "📚 база знаний":
        categories = get_categories_stats()
        if not categories:
            bot.send_message(chat_id, "🕸 Библиотека пуста...")
            return True
        bot.send_message(chat_id, f"📚 <b>База знаний Хуфы</b>\n📊 Записей: {sum(c for _, c in categories)}\n\n<b>Категории:</b>",
                        reply_markup=categories_kb(categories), parse_mode="HTML")
        return True
    
    # Админские кнопки
    if uid == ADMIN_ID:
        if text == "🤖 Авто-категоризация":
            msg = bot.send_message(uid, "🤖 Сортирую...")
            count = auto_categorize_all()
            bot.delete_message(uid, msg.message_id)
            bot.send_message(uid, f"✅ {count} записей!", reply_markup=main_kb(uid))
            return True
        
        if text == "🔧 Управление БД":
            bot.send_message(uid, "🔧 <b>Управление БД</b>", reply_markup=db_management_kb(), parse_mode="HTML")
            return True
        
        if text == "📋 Просмотр записи":
            keys = execute_db("SELECT keyword FROM wiki", (), True)
            if not keys:
                bot.send_message(uid, "📭 Пусто!")
                return True
            user_states[uid] = 'db_view'
            bot.send_message(uid, f"📋 Введи ключ:\n{', '.join([k[0] for k in keys])}")
            return True
        
        if text == "🔄 Изменить ключ":
            keys = execute_db("SELECT keyword FROM wiki", (), True)
            if not keys:
                bot.send_message(uid, "📭 Пусто!")
                return True
            user_states[uid] = 'db_edit_key_old'
            bot.send_message(uid, f"🔄 Старый ключ:\n{', '.join([k[0] for k in keys])}\n(/cancel)")
            return True
        
        if text == "🖼 Обновить фото":
            keys = execute_db("SELECT keyword FROM wiki", (), True)
            if not keys:
                bot.send_message(uid, "📭 Пусто!")
                return True
            user_states[uid] = 'db_update_photo_key'
            bot.send_message(uid, f"🖼 Ключ:\n{', '.join([k[0] for k in keys])}\n(/cancel)")
            return True
        
        if text == "🗑 Удалить фото":
            keys = execute_db("SELECT keyword, photo_id FROM wiki", (), True)
            keys_with_photos = [k[0] for k in keys if k[1]]
            if not keys_with_photos:
                bot.send_message(uid, "📭 Нет фото!")
                return True
            user_states[uid] = 'db_delete_photo'
            bot.send_message(uid, f"🗑 Ключ:\n{', '.join(keys_with_photos)}\n(/cancel)")
            return True
        
        if text == "🔙 Назад" and user_states.get(uid, '').startswith('db_'):
            user_states.pop(uid, None)
            bot.send_message(uid, "🔙 Главное меню", reply_markup=main_kb(uid))
            return True
        
        if text == "🔗 Связи":
            user_states[uid] = 'link_source'
            bot.send_message(uid, "🔗 Первый ключ:\n(/cancel)")
            return True
        
        if text == "📊 Статистика Лора":
            msg = get_lore_stats()
            conflicts = check_lore_conflicts()
            if conflicts:
                msg += "\n\n⚠️ " + "\n".join(conflicts)
            bot.send_message(uid, msg, parse_mode="HTML")
            return True
        
        if text == "🎲 Случайный Лор":
            lore = get_random_lore()
            if lore:
                safe_send(uid, f"🎲 <b>{lore[0].capitalize()}</b>\n\n{lore[1][:500]}", lore[2])
            else:
                bot.send_message(uid, "📭 Лор пуст!")
            return True
        
        if text == "❓ Викторина":
            quiz = generate_quiz()
            if quiz:
                quiz_data[uid] = quiz
                markup = types.InlineKeyboardMarkup()
                for i, opt in enumerate(quiz['options']):
                    markup.add(types.InlineKeyboardButton(opt.capitalize(), callback_data=f"quiz_{uid}_{i}_{quiz['correct']}"))
                bot.send_message(uid, quiz['question'], reply_markup=markup, parse_mode="HTML")
            else:
                bot.send_message(uid, "📭 Недостаточно знаний (нужно 4+)!")
            return True
        
        if text == "💬 Диалог-обучение":
            response = dialogue_learn_step(uid, None)
            bot.send_message(uid, response, parse_mode="HTML")
            return True
        
        if text == "📢 Рассылка":
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add("📢 Отправить текст", "🖼 Отправить с фото", "👥 Статистика", "🔙 Назад")
            bot.send_message(uid, "📢 <b>Рассылка</b>", reply_markup=markup, parse_mode="HTML")
            return True
        
        if text == "👥 Статистика":
            users = get_all_users()
            players = execute_db("SELECT COUNT(*) FROM players", (), True)
            wiki = execute_db("SELECT COUNT(*) FROM wiki", (), True)
            stories = get_all_stories()
            bot.send_message(uid, f"📊 Пользователей: {len(users)}\n🎭 Игроков: {players[0][0] if players else 0}\n📚 Знаний: {wiki[0][0] if wiki else 0}\n📖 Историй: {len(stories)}", parse_mode="HTML")
            return True
        
        if text == "📢 Отправить текст":
            user_states[uid] = 'broadcast_text'
            bot.send_message(uid, "📝 Текст:\n(/cancel)")
            return True
        
        if text == "🖼 Отправить с фото":
            user_states[uid] = 'broadcast_photo'
            bot.send_message(uid, "🖼 Фото с подписью:\n(/cancel)")
            return True
        
        if text == "⚔️ VS Битва":
            user_states[uid] = 'vs_mode'
            bot.send_message(uid, "⚔️ Имя1 vs Имя2\n(/cancel)")
            return True
        
        if text == "📖 Истории":
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add("📝 Создать историю", "📚 Список историй", "🗑 Удалить историю", "⏹ Закончить историю", "🤖 Обработать историю", "🔙 Назад")
            bot.send_message(uid, "📖 <b>Истории</b>", reply_markup=markup, parse_mode="HTML")
            return True
        
        if text == "📝 Создать историю":
            user_states[uid] = 'story_name'
            bot.send_message(uid, "📝 Название:\n(/cancel)")
            return True
        
        if text == "📚 Список историй":
            stories = get_all_stories()
            if stories:
                bot.send_message(chat_id, "📚 <b>Истории:</b>\n" + "\n".join([f"📜 {s[0]} ({s[1]} ч.)" for s in stories]), parse_mode="HTML")
            else:
                bot.send_message(chat_id, "📭 Нет историй.")
            return True
        
        if text == "🗑 Удалить историю":
            stories = get_all_stories()
            if not stories:
                bot.send_message(uid, "📭 Нечего удалять!")
                return True
            user_states[uid] = 'story_delete'
            bot.send_message(uid, f"🗑 Название:\n{chr(10).join([f'• {s[0]}' for s in stories])}\n(/cancel)")
            return True
        
        if text == "🤖 Обработать историю":
            stories = get_all_stories()
            if not stories:
                bot.send_message(uid, "📭 Нет историй!")
                return True
            user_states[uid] = 'story_polish'
            bot.send_message(uid, f"🤖 Название:\n{chr(10).join([f'• {s[0]}' for s in stories])}\n(/cancel)")
            return True
        
        if text == "📥 Импорт из Истории":
            stories = get_all_stories()
            if not stories:
                bot.send_message(uid, "📭 Нет историй!")
                return True
            user_states[uid] = 'import_story'
            bot.send_message(uid, f"📥 Выбери историю:\n{chr(10).join([f'• {s[0]}' for s in stories])}\n\nВведи название (/cancel):", parse_mode="HTML")
            return True
        
        if text == "✏️ Редактировать досье":
            keys = execute_db("SELECT keyword FROM wiki", (), True)
            if not keys:
                bot.send_message(uid, "Пусто!")
                return True
            user_states[uid] = 'edit_key'
            bot.send_message(uid, f"🔑 Ключ:\n{', '.join([k[0] for k in keys])}")
            return True
        
        if text == "📜 Обучить ГМ-а":
            user_states[uid] = 'learn_key'
            bot.send_message(uid, "🔑 Ключ:")
            return True
    
    return False


def handle_special_triggers(message, uid, chat_id, text, text_lower):
    """Обрабатывает специальные текстовые триггеры"""
    
    # Расскажи историю
    if "расскажи историю" in text_lower:
        story_name = text_lower.replace("расскажи историю", "").strip()
        actual = find_story_by_name(story_name)
        if not actual:
            bot.send_message(chat_id, f"📭 «{story_name}» не найдена.")
            return True
        parts = get_story_parts(actual)
        if not parts:
            bot.send_message(chat_id, "📭 Пусто.")
            return True
        if chat_id not in story_tellers:
            story_tellers[chat_id] = {}
        story_tellers[chat_id][uid] = {'story_name': actual, 'current_part': 0, 'total_parts': len(parts)}
        send_story_part(chat_id, parts[0], 1, len(parts), actual)
        if len(parts) > 1:
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add("▶️ Продолжить историю", "⏹ Хватит")
            bot.send_message(chat_id, "▶️ «Продолжить историю»", reply_markup=markup)
        return True
    
    # Продолжить историю
    if text == "▶️ Продолжить историю":
        if chat_id not in story_tellers or uid not in story_tellers[chat_id]:
            return True
        info = story_tellers[chat_id][uid]
        next_part = info['current_part'] + 1
        parts = get_story_parts(info['story_name'])
        if next_part >= len(parts):
            bot.send_message(chat_id, "📖 Завершена!", reply_markup=main_kb(uid))
            del story_tellers[chat_id][uid]
            return True
        info['current_part'] = next_part
        send_story_part(chat_id, parts[next_part], next_part + 1, len(parts), info['story_name'])
        if next_part + 1 >= len(parts):
            del story_tellers[chat_id][uid]
        return True
    
    # Хватит истории
    if text == "⏹ Хватит":
        if chat_id in story_tellers and uid in story_tellers[chat_id]:
            del story_tellers[chat_id][uid]
        bot.send_message(chat_id, "📖 Прекратил.", reply_markup=main_kb(uid))
        return True
    
    # VS Битва (быстрый формат)
    if not text.startswith('/'):
        for sep in [' vs ', ' против ']:
            if sep in text.lower():
                parts = text.lower().split(sep)
                if len(parts) == 2:
                    result = analyze_vs_battle(clean_text(parts[0]), clean_text(parts[1]))
                    if result:
                        safe_send(chat_id, result)
                    else:
                        bot.send_message(chat_id, "⚠️ Проверь имена.")
                    return True
    
    # Ролевые сообщения (- * ")
    if text and text[0] in ['-', '*', '"'] and len(text) > 1:
        res = execute_db('SELECT name FROM players WHERE user_id = ?', (uid,), True)
        name = res[0][0] if res else "Странник"
        styles = {
            '-': f"<b>{name}</b>: — {text[1:]}",
            '*': f"<i>{name} {text[1:]}</i>",
            '"': f"💭 {name}: {text[1:]}"
        }
        try:
            bot.delete_message(chat_id, message.message_id)
        except:
            pass
        bot.send_message(chat_id, styles[text[0]], parse_mode="HTML")
        return True
    
    return False


def handle_media_message(message, uid, state):
    """Обрабатывает фото и другие медиа-сообщения"""
    
    # Отправка фото для рассылки
    if uid == ADMIN_ID and state == 'broadcast_photo':
        if message.photo:
            s, f = broadcast_message(uid, message.caption or "", message.photo[-1].file_id)
            user_states.pop(uid, None)
            bot.send_message(uid, f"✅ {s}\n❌ {f}", reply_markup=main_kb(uid))
            return True
    
    # Отправка фото для обновления фото в вики
    if uid == ADMIN_ID and state == 'db_update_photo_send':
        if message.photo:
            success, msg = edit_wiki_photo(temp_learning[uid]['photo_key'], message.photo[-1].file_id)
            user_states.pop(uid, None)
            bot.send_message(uid, msg, reply_markup=db_management_kb())
            if success:
                bot.send_photo(uid, message.photo[-1].file_id, caption=f"✅ Новое фото для «{temp_learning[uid]['photo_key']}»")
            return True
        else:
            bot.send_message(uid, "❌ Отправь фото!")
            return True
    
    # Фото для обучения (learn_photo)
    if uid == ADMIN_ID and state == 'learn_photo':
        if message.photo:
            p_id = message.photo[-1].file_id
            category = ai_categorize_keyword(temp_learning[uid]['key'], temp_learning[uid]['desc'])
            execute_db("INSERT OR REPLACE INTO wiki (keyword, description, photo_id, category) VALUES (?, ?, ?, ?)",
                      (temp_learning[uid]['key'], temp_learning[uid]['desc'], p_id, category))
            user_states.pop(uid, None)
            bot.send_message(uid, f"✅ Сохранено в «{category}»!", reply_markup=main_kb(uid))
            return True
    
    # Фото для регистрации
    if state == 'reg_photo':
        if message.photo:
            execute_db("INSERT INTO players (user_id, name, bio, photo) VALUES (?,?,?,?)",
                      (uid, temp_data[uid]['name'], temp_data[uid]['bio'], message.photo[-1].file_id))
            user_states.pop(uid, None)
            bot.send_message(uid, "✅ Герой создан!", reply_markup=main_kb(uid))
            notify_achievements(uid, check_achievements(uid))
            return True
    
    # Фото/видео для истории
    if state == 'story_collect' and uid == ADMIN_ID:
        content, content_type, file_id = "", "text", None
        if message.content_type == 'photo':
            content = message.caption or "📸"
            content_type = "photo"
            file_id = message.photo[-1].file_id
        elif message.content_type == 'video':
            content = message.caption or "🎥"
            content_type = "video"
            file_id = message.video.file_id
        else:
            return False
        
        story_parts[uid]['parts'].append({'content': content, 'type': content_type, 'file_id': file_id})
        bot.send_message(uid, f"✅ Часть {len(story_parts[uid]['parts'])} добавлена!")
        return True
    
    return False


def handle_story_state(message, uid, text, state):
    """Обрабатывает состояния работы с историями"""
    
    if state == 'story_name':
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        if get_story_count(clean_text(text)) > 0:
            bot.send_message(uid, "⚠️ Существует!")
            return True
        story_parts[uid] = {'name': clean_text(text), 'parts': []}
        user_states[uid] = 'story_collect'
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("📝 Создать историю", "📚 Список историй", "🗑 Удалить историю",
                   "⏹ Закончить историю", "🤖 Обработать историю", "🔙 Назад")
        bot.send_message(uid, f"📝 Собираю «{clean_text(text)}»\nПересылай сообщения. «⏹» когда готово.", reply_markup=markup)
        return True
    
    if state == 'story_delete':
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        delete_story(clean_text(text))
        user_states.pop(uid, None)
        bot.send_message(uid, "✅ Удалена!", reply_markup=main_kb(uid))
        return True
    
    if state == 'story_polish':
        if text == '/cancel':
            user_states.pop(uid, None)
            bot.send_message(uid, "❌ Отменено.", reply_markup=main_kb(uid))
            return True
        actual = find_story_by_name(clean_text(text))
        if not actual:
            bot.send_message(uid, "❌ Не найдена!")
            return True
        polished = polish_full_story(actual)
        if polished:
            save_story_part(actual, get_story_count(actual) + 1, polished)
            user_states.pop(uid, None)
            bot.send_message(uid, f"✅ Готово! Часть {get_story_count(actual)}.", reply_markup=main_kb(uid))
        else:
            bot.send_message(uid, "⚠️ Не удалось.", reply_markup=main_kb(uid))
        return True
    
    if state == 'story_collect' and uid == ADMIN_ID:
        if text == "⏹ Закончить историю":
            if story_parts[uid]['parts']:
                for i, part in enumerate(story_parts[uid]['parts'], 1):
                    save_story_part(story_parts[uid]['name'], i, part['content'], part['type'], part.get('file_id'))
                count = len(story_parts[uid]['parts'])
                del story_parts[uid]
                user_states.pop(uid, None)
                bot.send_message(uid, f"✅ Сохранена! ({count} ч.)", reply_markup=main_kb(uid))
            else:
                user_states.pop(uid, None)
                bot.send_message(uid, "❌ Нечего сохранять!", reply_markup=main_kb(uid))
            return True
        
        if text == "🔙 Назад":
            if uid in story_parts:
                del story_parts[uid]
            user_states.pop(uid, None)
            bot.send_message(uid, "🔙 Главное меню.", reply_markup=main_kb(uid))
            return True
        
        if message.content_type == 'text':
            story_parts[uid]['parts'].append({'content': text, 'type': 'text', 'file_id': None})
            bot.send_message(uid, f"✅ Часть {len(story_parts[uid]['parts'])} добавлена!")
            return True
    
    return False


def handle_learn_state(message, uid, text, state):
    """Обрабатывает состояния обучения ГМ-а"""
    
    if state == 'learn_key':
        temp_learning[uid] = {'key': clean_text(text, is_key=True).lower()}
        user_states[uid] = 'learn_desc_or_generate'
        bot.send_message(uid, f"📝 Описание для «{text}» или /generate\n(/skip для фото)")
        return True
    
    if state == 'learn_desc_or_generate':
        if text == '/generate':
            generated = generate_wiki_description(temp_learning[uid]['key'])
            if generated:
                temp_learning[uid]['desc'] = generated
                user_states[uid] = 'learn_photo'
                bot.send_message(uid, f"🤖 {generated}\n\n📸 Фото или /skip:")
            else:
                bot.send_message(uid, "⚠️ Не удалось.")
            return True
        if text == '/skip':
            temp_learning[uid]['desc'] = ""
            user_states[uid] = 'learn_photo'
            bot.send_message(uid, "📸 Фото или /skip:")
            return True
        temp_learning[uid]['desc'] = text
        user_states[uid] = 'learn_photo'
        bot.send_message(uid, "📸 Фото или /skip:")
        return True
    
    if state == 'learn_photo':
        if text == '/skip':
            category = ai_categorize_keyword(temp_learning[uid]['key'], temp_learning[uid]['desc'])
            execute_db("INSERT OR REPLACE INTO wiki (keyword, description, photo_id, category) VALUES (?, ?, ?, ?)",
                      (temp_learning[uid]['key'], temp_learning[uid]['desc'], None, category))
            user_states.pop(uid, None)
            bot.send_message(uid, f"✅ Сохранено в «{category}»!", reply_markup=main_kb(uid))
            return True
        return False  # Ждём фото
    
    return False


def handle_reg_state(message, uid, state):
    """Обрабатывает состояния регистрации игрока"""
    
    if message.content_type != 'text':
        return False
    
    text = message.text
    
    if state == 'reg_name':
        temp_data[uid] = {'name': clean_text(text)}
        user_states[uid] = 'reg_bio'
        bot.send_message(uid, "📖 Био:")
        return True
    
    if state == 'reg_bio':
        temp_data[uid]['bio'] = text
        user_states[uid] = 'reg_photo'
        bot.send_message(uid, "📸 Фото:")
        return True
    
    return False

