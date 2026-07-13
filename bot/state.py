"""
Хранилища состояний диалогов (регистрация, РП, обучение и т.д.).

Раньше это были обычные dict'ы в памяти процесса — при рестарте бота
(деплой, падение, обновление) весь прогресс пользователей терялся:
не дописанная анкета, ход диалог-обучения, активная РП-сессия и т.д.

Теперь эти dict'ы автоматически:
  1) подгружаются из bot_state.db при старте (load_state),
  2) сохраняются в bot_state.db каждые AUTOSAVE_SECONDS секунд фоновым
     потоком и при штатной остановке процесса (SIGINT/SIGTERM/atexit).

Это не "тяжёлая" транзакционная система (специально, чтобы не усложнять),
а простой снапшот в SQLite — достаточно, чтобы штатный рестарт/деплой
не сбрасывал пользователей на middle of nowhere.
"""
import atexit
import json
import signal
import sqlite3
import threading
from collections import deque

from bot.config import STATE_DB, logger

AUTOSAVE_SECONDS = 15

# ---- собственно хранилища состояний ----
user_states = {}
temp_data = {}
temp_learning = {}
message_context = {}
story_parts = {}
story_tellers = {}
broadcast_data = {}
quiz_data = {}
dialogue_learning = {}
rp_sessions = {}
rp_pending = {}
session_contexts = {}

# Буфер истории диалогов для контекстных ответов — сознательно НЕ
# сохраняется на диск: это лёгкий кэш последних реплик, а не важное
# состояние, и deque плохо сериализуется. Восстановится сам по ходу чата.
chat_memory = {}

# Какие словари реально переживают рестарт
_PERSISTED = {
    'user_states': user_states,
    'temp_data': temp_data,
    'temp_learning': temp_learning,
    'message_context': message_context,
    'story_parts': story_parts,
    'story_tellers': story_tellers,
    'broadcast_data': broadcast_data,
    'quiz_data': quiz_data,
    'dialogue_learning': dialogue_learning,
    'rp_sessions': rp_sessions,
    'rp_pending': rp_pending,
    'session_contexts': session_contexts,
}

_save_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(STATE_DB, timeout=15)
    conn.execute("CREATE TABLE IF NOT EXISTS bot_state (namespace TEXT PRIMARY KEY, data TEXT)")
    return conn


def load_state():
    """Восстанавливает словари состояний из bot_state.db (вызывается один раз при старте)."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT namespace, data FROM bot_state").fetchall()
        for namespace, data in rows:
            target = _PERSISTED.get(namespace)
            if target is None:
                continue
            try:
                loaded = json.loads(data)
                # ключи словарей в JSON всегда строки — user_id хранится как int в коде,
                # поэтому приводим обратно там, где ключ выглядит как число
                fixed = {}
                for k, v in loaded.items():
                    key = int(k) if k.lstrip('-').isdigit() else k
                    fixed[key] = v
                target.update(fixed)
            except Exception as e:
                logger.error(f"Не удалось восстановить состояние '{namespace}': {e}")
        if rows:
            logger.info(f"♻️ Состояния восстановлены из bot_state.db ({len(rows)} разделов)")
    except Exception as e:
        logger.error(f"Не удалось загрузить bot_state.db: {e}")


def save_state():
    """Снимает снапшот всех отслеживаемых словарей в bot_state.db."""
    with _save_lock:
        try:
            with _connect() as conn:
                for namespace, d in _PERSISTED.items():
                    try:
                        payload = json.dumps(d, ensure_ascii=False, default=str)
                    except Exception as e:
                        logger.error(f"Не удалось сериализовать состояние '{namespace}': {e}")
                        continue
                    conn.execute(
                        "INSERT INTO bot_state (namespace, data) VALUES (?, ?) "
                        "ON CONFLICT(namespace) DO UPDATE SET data=excluded.data",
                        (namespace, payload)
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"Не удалось сохранить bot_state.db: {e}")


def _autosave_loop(stop_event):
    while not stop_event.wait(AUTOSAVE_SECONDS):
        save_state()


def start_autosave():
    """Запускает фоновое автосохранение + сохранение при остановке процесса."""
    stop_event = threading.Event()
    thread = threading.Thread(target=_autosave_loop, args=(stop_event,), daemon=True)
    thread.start()

    atexit.register(save_state)

    def _on_signal(signum, frame):
        logger.info(f"Получен сигнал {signum} — сохраняю состояние перед выходом...")
        save_state()
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
    except ValueError:
        # signal.signal доступен только в главном потоке — если бот запущен иначе, просто пропускаем
        pass

    return stop_event
