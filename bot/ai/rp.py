"""
Логика РП-сессий: старт/стоп, пересылка сообщений игрок <-> ГМ, "литературная полировка" текста.
"""
import time

from bot.config import bot, client
from bot.state import rp_sessions, rp_pending, session_contexts
from bot.db.database import execute_db, get_chat_context, check_achievements, notify_achievements
from bot.utils import ensure_player

def start_rp_session(chat_id, gm_id, session_name="РП-сессия", context=None):
    if not context: context = get_chat_context(chat_id)
    if not context: return None, "no_context"
    execute_db("INSERT INTO rp_sessions (chat_id, gm_id, session_name, context) VALUES (?, ?, ?, ?)", (chat_id, gm_id, session_name, context))
    session_db_id = execute_db("SELECT last_insert_rowid()", (), True)[0][0]
    rp_sessions[chat_id] = {'gm_id': gm_id, 'name': session_name, 'context': [], 'active': True, 'thread_id': None, 'db_id': session_db_id, 'session_context': context}
    session_contexts[chat_id] = context
    execute_db("INSERT OR REPLACE INTO rp_channels (chat_id, mode) VALUES (?, 'active')", (chat_id,))
    ensure_player(gm_id)
    notify_achievements(gm_id, check_achievements(gm_id))
    return context, "ok"

def process_rp_message(chat_id, user_id, user_text, user_name):
    if chat_id not in rp_sessions or not rp_sessions[chat_id]['active']: return False
    gm_id = rp_sessions[chat_id]['gm_id']
    thread_info = f"\n📌 Тема: {rp_sessions[chat_id].get('thread_id')}" if rp_sessions[chat_id].get('thread_id') else ""
    context_info = f"\n📖 Контекст: {rp_sessions[chat_id].get('session_context', 'Не задан')[:200]}"
    gm_msg = f"🎭 <b>РП · {rp_sessions[chat_id]['name']}</b>{thread_info}{context_info}\n👤 <b>{user_name}</b> [ID: <code>{user_id}</code>]:\n{user_text}\n\n<i>Ответь на это сообщение, чтобы ответить игроку</i>"
    rp_sessions[chat_id]['context'].append({'user_id': user_id, 'user_name': user_name, 'text': user_text, 'timestamp': time.time()})
    sent_msg = bot.send_message(gm_id, gm_msg, parse_mode="HTML")
    rp_pending[chat_id] = rp_pending.get(chat_id, {})
    rp_pending[chat_id][sent_msg.message_id] = user_id
    return True

def gm_reply_to_player(gm_id, reply_text, original_msg_id):
    for chat_id, pending in rp_pending.items():
        if original_msg_id in pending:
            polished = polish_rp_text(reply_text)
            thread_id = rp_sessions[chat_id].get('thread_id')
            bot.send_message(chat_id, polished, parse_mode="HTML", message_thread_id=thread_id)
            return True
    return False

def gm_narrate(chat_id, gm_text):
    if chat_id not in rp_sessions: return False
    polished = polish_rp_text(gm_text)
    thread_id = rp_sessions[chat_id].get('thread_id')
    bot.send_message(chat_id, f"📖 {polished}", parse_mode="HTML", message_thread_id=thread_id)
    rp_sessions[chat_id]['context'].append({'user_id': 'gm', 'user_name': 'Мастер', 'text': gm_text, 'timestamp': time.time()})
    return True

def polish_rp_text(text):
    try:
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Ты — литературный редактор. Исправь грамматику, сделай текст атмосферным. НЕ добавляй действий за игроков. Ответь ТОЛЬКО текстом без HTML."}, {"role": "user", "content": f"ТЕКСТ:\n{text}\n\nОТРЕДАКТИРУЙ:"}], temperature=0.5, max_tokens=1000)
        return completion.choices[0].message.content.strip()
    except: return text

def stop_rp_session(chat_id):
    if chat_id in rp_sessions: rp_sessions[chat_id]['active'] = False
    execute_db("UPDATE rp_sessions SET status = 'finished' WHERE chat_id = ? AND status = 'active'", (chat_id,))
    execute_db("INSERT OR REPLACE INTO rp_channels (chat_id, mode) VALUES (?, 'silent')", (chat_id,))
    if chat_id in session_contexts: del session_contexts[chat_id]

