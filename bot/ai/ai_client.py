"""
ИИ-слой: обёртка над Groq API, генераторы контента (NPC, локации, квесты и т.д.),
контекстный поиск по вики и память диалогов.
"""
import re
import time
import random
import requests
from collections import Counter, deque

from bot.config import client, logger, bot, SERPAPI_KEY, memory_lock, CATEGORY_EMOJI
from bot.state import chat_memory, dialogue_learning, rp_sessions
from bot.db.database import execute_db, add_wiki_link, find_story_by_name, get_links_text, get_story_parts
from bot.utils import clean_text, split_text_for_ai

def save_to_memory(chat_id, user_message, bot_answer):
    with memory_lock:
        if chat_id not in chat_memory:
            chat_memory[chat_id] = deque(maxlen=10)
        chat_memory[chat_id].append({
            'user': user_message,
            'bot_answer': bot_answer,
            'timestamp': time.time()
        })

def get_memory_context(chat_id):
    with memory_lock:
        if chat_id not in chat_memory or not chat_memory[chat_id]:
            return ""
        recent = list(chat_memory[chat_id])[-5:]
        context_parts = []
        for entry in recent:
            context_parts.append(f"Игрок спросил: {entry['user'][:200]}\nБот ответил: {entry['bot_answer'][:200]}")
        return "\n".join(context_parts)

def search_wiki_with_context(message):
    query = message.text
    chat_id = message.chat.id
    
    memory_context = get_memory_context(chat_id)
    
    all_wiki = execute_db("SELECT keyword, description, photo_id FROM wiki", (), True)
    if not all_wiki: return None, None, None
    
    found_photo = target_data = current_key = None
    clean_query = clean_text(query, is_key=True).lower()
    
    clarifying_words = {"а", "но", "и", "или", "ещё", "тоже", "также", "тогда", "потом", "затем", "после"}
    query_words = set(clean_query.split())
    is_clarifying = bool(query_words & clarifying_words) or len(query_words) <= 2
    
    candidates = []
    for kw, desc, photo in all_wiki:
        weight = 0
        if kw.lower() in clean_query: weight = 100 + len(kw)
        else:
            kw_words = set(kw.lower().split())
            if kw_words & query_words: weight += len(kw_words & query_words) * 5
        if weight > 0: candidates.append((weight, kw, desc, photo))
    
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, current_key, target_data, found_photo = candidates[0]
    elif is_clarifying and memory_context:
        last_topic = None
        with memory_lock:
            if chat_id in chat_memory and chat_memory[chat_id]:
                last_bot_answer = chat_memory[chat_id][-1]['bot_answer']
                for kw, desc, photo in all_wiki:
                    if kw.lower() in last_bot_answer.lower():
                        current_key = kw
                        target_data = desc
                        found_photo = photo
                        break
    
    if not target_data: return None, None, None
    
    if len(target_data) > 30000: target_data = target_data[:30000] + "..."
    
    try:
        system_prompt = f"Ты — мудрый Хранитель знаний. Тема: {current_key}."
        if memory_context:
            system_prompt += f"\n\nПредыдущий разговор:\n{memory_context}"
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"ДОСЬЕ:\n{target_data}\n\nВОПРОС ИГРОКА: {query}\n\nОтветь на вопрос, учитывая контекст предыдущего разговора если это уточнение."}
            ],
            temperature=0.3,
            max_tokens=500
        )
        answer = completion.choices[0].message.content
        links_text = get_links_text(current_key)
        if links_text: answer += f"\n\n🕸 <b>Связи:</b>\n{links_text}"
        
        save_to_memory(chat_id, query, answer)
        
        return answer, found_photo, current_key
    except Exception as e:
        print(f"❌ Ошибка AI: {e}")
        if target_data:
            answer = f"📚 <b>{current_key.capitalize()}</b>\n\n{target_data[:1000]}{'...' if len(target_data) > 1000 else ''}"
            links_text = get_links_text(current_key)
            if links_text: answer += f"\n\n🕸 <b>Связи:</b>\n{links_text}"
            save_to_memory(chat_id, query, answer)
            return answer, found_photo, current_key
        return "🔮 Библиотека временно недоступна...", None, None

print(">>> Модуль 1 загружен (инициализация)")

def web_search(query, num_results=3):
    if not SERPAPI_KEY:
        try:
            completion = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "system", "content": "Ты — поисковый помощник. Найди информацию по запросу и ответь кратко на русском."}, {"role": "user", "content": f"Найди информацию: {query}"}],
                temperature=0.3, max_tokens=500
            )
            return completion.choices[0].message.content.strip()
        except: return None
    try:
        url = "https://serpapi.com/search"
        params = {"q": query, "hl": "ru", "gl": "ru", "num": num_results, "api_key": SERPAPI_KEY}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        results = []
        for r in data.get("organic_results", [])[:num_results]:
            results.append(f"• {r.get('title', '')}: {r.get('snippet', '')[:200]}")
        return "\n".join(results) if results else None
    except Exception as e:
        print(f"Ошибка поиска: {e}")
        return None


def analyze_chat_history_for_context(chat_id, admin_text=None):
    messages_for_analysis = []
    if admin_text: messages_for_analysis.append(admin_text)
    wiki_entries = execute_db("SELECT keyword, description FROM wiki", (), True)
    if wiki_entries:
        wiki_text = "\n".join([f"• {kw}: {desc[:200]}" for kw, desc in wiki_entries[:20]])
        messages_for_analysis.append(f"База знаний бота:\n{wiki_text}")
    chat_context = execute_db("SELECT context_summary FROM chat_contexts WHERE chat_id = ?", (chat_id,), True)
    if chat_context: messages_for_analysis.append(f"Предыдущий контекст: {chat_context[0][0]}")
    if not messages_for_analysis and not admin_text: return None
    combined_text = "\n\n".join(messages_for_analysis)
    try:
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Ты — аналитик ролевых игр. Проанализируй информацию и определи тему, сеттинг, ключевых персонажей и возможный сюжет для РП-сессии. Ответь кратко на русском языке, 3-5 предложений."}, {"role": "user", "content": f"Проанализируй для создания РП-сессии:\n{combined_text[:4000]}"}], temperature=0.4, max_tokens=300)
        context_summary = completion.choices[0].message.content.strip()
        if admin_text:
            search_results = web_search(admin_text)
            if search_results: context_summary += f"\n\n🌐 Из интернета:\n{search_results[:500]}"
        execute_db("INSERT OR REPLACE INTO chat_contexts (chat_id, context_summary, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)", (chat_id, context_summary))
        return context_summary
    except Exception as e:
        print(f"Ошибка анализа: {e}")
        if admin_text:
            execute_db("INSERT OR REPLACE INTO chat_contexts (chat_id, context_summary, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)", (chat_id, admin_text))
            return admin_text
        return None


def groq_complete(system_prompt, user_prompt, temperature=0.4, max_tokens=800, model="llama-3.1-8b-instant", retries=2):
    """Единая обёртка для вызовов Groq с ретраями и логированием ошибок."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    logger.error(f"Groq недоступен после {retries + 1} попыток: {last_err}")
    return None


def ai_categorize_keyword(keyword, description=""):
    if not description or len(description.strip()) < 10: description = keyword
    text_parts = split_text_for_ai(description, max_chars=2500)
    all_categories = []
    for part in text_parts:
        try:
            completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": f"Определи категорию:\nНАЗВАНИЕ: {keyword}\nТЕКСТ: {part[:3000]}\n\nКатегории: персонаж, локация, предмет, фракция, событие, организация, существо, магия, общее\n\nВерни ОДНО слово:"}], temperature=0.1, max_tokens=10)
            category = re.sub(r'[^а-яё]', '', completion.choices[0].message.content.strip().lower())
            all_categories.append(category if category in CATEGORY_EMOJI else 'общее')
            if len(text_parts) > 1: time.sleep(0.2)
        except: all_categories.append('общее')
    return Counter(all_categories).most_common(1)[0][0] if all_categories else 'общее'

def auto_categorize_all():
    count = 0
    for keyword, description in execute_db("SELECT keyword, description FROM wiki", (), True):
        category = ai_categorize_keyword(keyword, description)
        if category: execute_db("UPDATE wiki SET category = ? WHERE keyword = ?", (category, keyword.lower())); count += 1
        time.sleep(0.3)
    return count

def generate_wiki_description(keyword, context_hint=""):
    all_wiki = execute_db("SELECT keyword, description FROM wiki", (), True)
    wiki_context = "\n".join([f"• {kw}: {desc[:300]}" for kw, desc in random.sample(all_wiki, min(5, len(all_wiki)))]) if all_wiki else ""
    try:
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Ты — Хранитель знаний."}, {"role": "user", "content": f"Создай описание: «{keyword}»\n\n{wiki_context}"}], temperature=0.5, max_tokens=1500)
        return completion.choices[0].message.content.strip()
    except: return None

def analyze_vs_battle(char1, char2):
    all_wiki = execute_db("SELECT keyword, description FROM wiki", (), True)
    char1_data = char2_data = ""
    other_lore = []
    for kw, desc in all_wiki:
        if kw.lower() == char1.lower(): char1_data = f"• {kw}: {desc}"
        elif kw.lower() == char2.lower(): char2_data = f"• {kw}: {desc}"
        elif len(other_lore) < 5: other_lore.append(f"• {kw}: {desc[:200]}")
    try:
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Ты — Верховный Арбитр."}, {"role": "user", "content": f"Битва: {char1} vs {char2}\n\n{char1}: {char1_data or 'Нет данных'}\n{char2}: {char2_data or 'Нет данных'}\n\nЛОР:\n{chr(10).join(other_lore) if other_lore else 'Лор пуст.'}"}], temperature=0.6, max_tokens=2000)
        return completion.choices[0].message.content.strip()
    except: return None

def polish_story_text(raw_text, story_name, part_num):
    if len(raw_text) < 50: return raw_text
    try:
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Ты — летописец."}, {"role": "user", "content": f"{raw_text}\n\nОТРЕДАКТИРУЙ:"}], temperature=0.4, max_tokens=4000)
        return completion.choices[0].message.content.strip()
    except: return raw_text

def polish_full_story(story_name):
    actual_name = find_story_by_name(story_name)
    if not actual_name: return None
    parts = get_story_parts(actual_name)
    if not parts: return None
    text_parts = [(pn, c) for pn, c, ct, f in parts if ct == 'text' and len(c.strip()) > 10]
    if not text_parts: return None
    all_polished = []
    for i in range(0, len(text_parts), 3):
        group = text_parts[i:i+3]; group_text = "\n\n".join([f"[Часть {pn}]\n{txt}" for pn, txt in group])
        try:
            completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Ты — летописец."}, {"role": "user", "content": f"{group_text}\n\nСДЕЛАЙ КРАСИВЫЙ РАССКАЗ:"}], temperature=0.5, max_tokens=4000)
            all_polished.append(completion.choices[0].message.content.strip()); time.sleep(2)
        except: all_polished.append(group_text)
    return "\n\n---\n\n".join(all_polished) if all_polished else None

def extract_lore_from_story(story_name):
    parts = get_story_parts(story_name)
    if not parts: return None
    all_text = " ".join([c for _, c, ct, _ in parts if ct == 'text'])
    try:
        completion = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "system", "content": "Проанализируй текст и найди термины."}, {"role": "user", "content": f"История: {story_name}\n\nТекст:\n{all_text[:15000]}\n\nНайди все важные термины:"}], temperature=0.3, max_tokens=2000)
        return completion.choices[0].message.content.strip()
    except: return None

def dialogue_learn_step(uid, answer):
    if uid not in dialogue_learning:
        dialogue_learning[uid] = {'step': 'name'}
        return "📝 <b>Диалоговое обучение</b>\n\nО ком или о чём хочешь рассказать? (напиши имя)"
    step = dialogue_learning[uid]['step']
    if step == 'name': dialogue_learning[uid]['keyword'] = clean_text(answer).lower(); dialogue_learning[uid]['step'] = 'description'; return f"📝 Кто такой/что такое <b>{answer}</b>? Опиши подробнее..."
    elif step == 'description': dialogue_learning[uid]['desc'] = answer; dialogue_learning[uid]['category'] = ai_categorize_keyword(dialogue_learning[uid]['keyword'], answer); dialogue_learning[uid]['step'] = 'photo'; return f"📸 Отправь фото (или напиши /skip)\n\n🤖 ИИ определил категорию: {CATEGORY_EMOJI.get(dialogue_learning[uid]['category'], '📚')} {dialogue_learning[uid]['category']}"
    elif step == 'photo':
        if answer == '/skip': dialogue_learning[uid]['photo'] = None
        dialogue_learning[uid]['step'] = 'links'; return "🔗 Есть ли у этого связи? Напиши: враг Имярек\nИли /skip"
    elif step == 'links':
        if answer != '/skip':
            parts = answer.strip().split()
            if len(parts) >= 2: add_wiki_link(dialogue_learning[uid]['keyword'], parts[1], parts[0])
        dialogue_learning[uid]['step'] = 'confirm_category'
        return f"🏷 Подтверди категорию: {CATEGORY_EMOJI.get(dialogue_learning[uid]['category'], '📚')} {dialogue_learning[uid]['category']}\n\nИли напиши другую"
    elif step == 'confirm_category':
        d = dialogue_learning[uid]
        if answer.strip().lower() in CATEGORY_EMOJI: d['category'] = answer.strip().lower()
        execute_db("INSERT OR REPLACE INTO wiki (keyword, description, photo_id, category) VALUES (?, ?, ?, ?)", (d['keyword'], d['desc'], d.get('photo'), d['category']))
        del dialogue_learning[uid]
        return f"✅ Знание «{d['keyword']}» сохранено в категории «{d['category']}»!"

print(">>> Модуль 2 загружен (вспомогательные функции)")
# ============================================================
# ИИ-ФУНКЦИИ ДЛЯ РП-СЕССИЙ
# ============================================================

def ai_gm_suggest(gm_id, chat_id):
    """ИИ предлагает 3 варианта развития сюжета на основе контекста"""
    if chat_id not in rp_sessions or not rp_sessions[chat_id]['active']:
        return None
    
    session = rp_sessions[chat_id]
    recent_context = session['context'][-15:] if len(session['context']) > 15 else session['context']
    context_text = "\n".join([
        f"{'🎭 ГМ' if ctx['user_id'] == 'gm' else '👤 Игрок'}: {ctx['text'][:200]}"
        for ctx in recent_context
    ])
    
    session_context = session.get('session_context', 'Фэнтези мир')
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты — ИИ-помощник Гейм-Мастера. Предложи 3 ВАРИАНТА развития сюжета.
Каждый вариант должен быть КРАТКИМ (1-2 предложения) и создавать ИНТРИГУ.
Используй классические тропы: неожиданный поворот, появление NPC, опасность, загадку, моральный выбор.
Формат ответа:
1. 🔥 [Вариант 1]
2. 🌪 [Вариант 2]  
3. 💀 [Вариант 3]"""
            }, {
                "role": "user",
                "content": f"МИР: {session_context}\n\nПОСЛЕДНИЕ СОБЫТИЯ:\n{context_text}\n\nПредложи 3 варианта развития сюжета:"
            }],
            temperature=0.8,
            max_tokens=400
        )
        suggestions = completion.choices[0].message.content.strip()
        
        bot.send_message(
            gm_id,
            f"🎲 <b>AI-Советник:</b>\n\n{suggestions}\n\n<i>Выбери направление или используй как вдохновение</i>",
            parse_mode="HTML"
        )
        return suggestions
    except Exception as e:
        print(f"Ошибка AI GM: {e}")
        return None


def generate_npc(npc_type="случайный", context=""):
    """Генерирует NPC с уникальным характером и предысторией"""
    
    archetypes = {
        "торговец": "хитрый торговец с тёмным прошлым",
        "стражник": "уставший стражник, который видел слишком много",
        "маг": "эксцентричный маг, одержимый запретными знаниями",
        "бармен": "бармен, который знает все слухи в городе",
        "наёмник": "наёмник с кодексом чести и тёмной тайной",
        "жрец": "жрец, потерявший веру но скрывающий это",
        "вор": "благородный вор, грабящий только богатых",
        "учёный": "безумный учёный на грани великого открытия",
        "кузнец": "мастер-оружейник, хранящий секрет легендарного металла",
        "шпион": "двойной агент, не помнящий на чьей он стороне",
        "целитель": "травница с даром, за который её преследуют",
        "случайный": "уникальный персонаж со сложной судьбой"
    }
    
    archetype = archetypes.get(npc_type, npc_type)
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты — генератор NPC для RPG. Создай запоминающегося персонажа.
Формат ответа (строго соблюдай):
🎭 ИМЯ: [имя]
📋 АРХЕТИП: [архетип]
💬 МАНЕРА РЕЧИ: [2-3 характерные фразы или особенности речи]
🎯 МОТИВАЦИЯ: [чего хочет персонаж]
🔒 ТАЙНА: [что скрывает]
⚔️ ОСОБЫЕ ЧЕРТЫ: [2-3 уникальные особенности]
📖 КВЕСТ-КРЮЧОК: [как NPC может вовлечь игроков в приключение]"""
            }, {
                "role": "user",
                "content": f"Создай NPC: {archetype}\nКонтекст мира: {context[:500]}"
            }],
            temperature=0.9,
            max_tokens=500
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def generate_location(location_type="таверна", mood="мрачная"):
    """Генерирует детальное описание локации с элементами взаимодействия"""
    
    location_prompts = {
        "таверна": "таверна в фэнтези-мире, опиши завсегдатаев, особые напитки, тёмные углы",
        "лес": "древний лес полный магии и опасностей, опиши флору, звуки, скрытые тропы",
        "замок": "заброшенный замок с призраками прошлого, опиши архитектуру, ловушки, сокровища",
        "рынок": "шумный восточный базар, опиши торговцев, редкие товары, карманников",
        "подземелье": "тёмное подземелье с древними рунами, опиши опасности, головоломки, обитателей",
        "храм": "забытый храм забытого бога, опиши алтари, проклятия, благословения",
        "библиотека": "древняя библиотека с запретными фолиантами",
        "болото": "ядовитые топи, где обитают древние твари",
        "горы": "заснеженные пики с пещерами ледяных драконов",
        "порт": "пиратская гавань с контрабандистами и морскими легендами"
    }
    
    desc = location_prompts.get(location_type, location_type)
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты — генератор локаций для RPG. Создай АТМОСФЕРНОЕ описание.
Формат ответа:
🏛 НАЗВАНИЕ: [название локации]
👁 ПЕРВЫЙ ВЗГЛЯД: [что видят игроки входя]
👃 ЗАПАХИ И ЗВУКИ: [сенсорное описание]
🔍 ИНТЕРЕСНЫЕ ДЕТАЛИ: [3-4 элемента для исследования]
⚠️ ОПАСНОСТИ: [скрытые угрозы]
💎 НАГРАДЫ: [что можно найти]
📜 СЛУХИ: [местная легенда или сплетня]"""
            }, {
                "role": "user",
                "content": f"Создай локацию: {desc}\nНастроение: {mood}"
            }],
            temperature=0.8,
            max_tokens=600
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def generate_random_encounter(environment="лес", party_level="средний", time_of_day="ночь"):
    """Генерирует случайную встречу с учётом контекста"""
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты — генератор случайных встреч для RPG. Создай НЕОЖИДАННОЕ событие.
Оно должно быть НЕ БОЕВЫМ (или с возможностью избежать боя).
Формат ответа:
🎲 СОБЫТИЕ: [название]
📖 ОПИСАНИЕ: [2-3 предложения]
👥 УЧАСТНИКИ: [кто вовлечён]
⚡ ВАРИАНТЫ ДЕЙСТВИЙ:
  A) [первый вариант]
  B) [второй вариант]  
  C) [третий вариант]
🎁 ПОСЛЕДСТВИЯ: [что случится в зависимости от выбора]"""
            }, {
                "role": "user",
                "content": f"Создай случайную встречу:\nМестность: {environment}\nУровень группы: {party_level}\nВремя суток: {time_of_day}"
            }],
            temperature=0.9,
            max_tokens=500
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def ai_oracle_interpret(dice_result, action_description, context=""):
    """ИИ интерпретирует результат броска и создаёт нарративное описание"""
    
    result_type = "критический успех" if dice_result >= 20 else "успех" if dice_result >= 15 else "частичный успех" if dice_result >= 10 else "провал" if dice_result >= 5 else "критический провал"
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": f"""Ты — Оракул в RPG. Игрок выбросил {dice_result} (это {result_type}) при попытке: "{action_description}".
Опиши нарративно что происходит. Будь драматичным и кинематографичным.
При критическом успехе — добавь неожиданный бонус.
При критическом провале — добавь интересное осложнение (не просто "не получилось").
ОТВЕТЬ В 2-3 ПРЕДЛОЖЕНИЯХ, от лица рассказчика."""
            }, {
                "role": "user",
                "content": f"Действие: {action_description}\nРезультат броска: {dice_result} ({result_type})\nКонтекст: {context[:300]}"
            }],
            temperature=0.7,
            max_tokens=300
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def generate_puzzle(difficulty="средняя", theme="магия"):
    """Генерирует загадку с тремя уровнями подсказок"""
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты — создатель загадок для RPG. Придумай УНИКАЛЬНУЮ загадку.
Формат ответа:
🧩 ЗАГАДКА: [текст загадки или описание головоломки]
🎯 ОТВЕТ: [правильный ответ]
💡 ПОДСКАЗКА 1 (лёгкая): [общая подсказка]
💡 ПОДСКАЗКА 2 (средняя): [более прямая подсказка]  
💡 ПОДСКАЗКА 3 (почти ответ): [почти раскрывает ответ]
🔮 ПОСЛЕДСТВИЯ:
  ✅ Успех: [что получат игроки]
  ❌ Провал: [что случится при ошибке]"""
            }, {
                "role": "user",
                "content": f"Создай загадку:\nСложность: {difficulty}\nТема: {theme}"
            }],
            temperature=0.9,
            max_tokens=500
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def generate_prophecy(style="туманное", elements=["огонь", "корона", "падение"]):
    """Генерирует пророчество в выбранном стиле"""
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": f"""Ты — Древний Оракул. Создай ПРОРОЧЕСТВО.
Стиль: {style}
Включи элементы: {', '.join(elements)}
Пророчество должно быть:
- Туманным и допускающим множественные толкования
- Иметь зловещий или предостерегающий тон
- Содержать скрытый смысл, понятный ГМ-у

Формат ответа:
🔮 ПРОРОЧЕСТВО: [текст пророчества в 2-4 строки]
📖 РАСШИФРОВКА ДЛЯ ГМ-а: [что на самом деле значит пророчество]
🎭 КАК ОБЫГРАТЬ: [3 идеи как вплести в сюжет]"""
            }, {
                "role": "user",
                "content": f"Создай пророчество в стиле: {style}\nКлючевые элементы: {', '.join(elements)}"
            }],
            temperature=0.9,
            max_tokens=400
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def generate_npc_dialogue(npc_name, npc_personality, player_question, context=""):
    """Генерирует ответ NPC в соответствии с его характером"""
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": f"""Ты — {npc_name}. Твоя личность: {npc_personality}
Отвечай В ХАРАКТЕРЕ. Учитывай свою мотивацию, манеру речи, тайны.
Не говори того, что не знаешь. Можешь лгать, если это в твоём характере.
Используй речевые особенности, акцент, характерные фразы.
ОТВЕТЬ В 1-3 ПРЕДЛОЖЕНИЯХ."""
            }, {
                "role": "user",
                "content": f"Контекст: {context[:500]}\n\nИгрок спрашивает: {player_question}\n\nОтветь как {npc_name}:"
            }],
            temperature=0.8,
            max_tokens=300
        )
        return completion.choices[0].message.content.strip()
    except:
        return None


def generate_quest(quest_type="основной", difficulty="средний", context=""):
    """Генерирует квест с несколькими этапами и вариативностью"""
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты — генератор квестов для RPG. Создай увлекательный квест.
Формат ответа:
⚔️ НАЗВАНИЕ: [название квеста]
📜 ОПИСАНИЕ: [2-3 предложения завязки]
👤 ЗАКАЗЧИК: [кто даёт квест и почему]
🎯 ЦЕЛЬ: [что нужно сделать]
🗺 ЭТАПЫ:
  1) [первый этап]
  2) [второй этап]
  3) [третий этап]
⚡ РАЗВИЛКА: [моральный выбор или неожиданный поворот]
💎 НАГРАДА: [что получат игроки]
☠️ ОСЛОЖНЕНИЕ: [что может пойти не так]"""
            }, {
                "role": "user",
                "content": f"Создай {quest_type} квест сложности {difficulty}\nКонтекст мира: {context[:500]}"
            }],
            temperature=0.8,
            max_tokens=600
        )
        return completion.choices[0].message.content.strip()
    except:
        return None

print(">>> Модуль 3 загружен (ИИ-функции РП)")

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

