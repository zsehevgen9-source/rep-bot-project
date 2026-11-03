"""Проверка сценариев бота без внешних сервисов.

Запуск из папки проекта:  python tests/test_bot.py
MAX, YandexGPT, базу и Redis заменяют заглушки, поэтому интернет и Docker не нужны.
"""
import asyncio, json, logging, os, shutil, sys, tempfile, types
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
logging.basicConfig(level=logging.ERROR)
logging.getLogger("app.services.profiles").setLevel(logging.CRITICAL)
logging.getLogger("app.services.audio").setLevel(logging.CRITICAL)

def mod(name, **a):
    m = types.ModuleType(name); m.__dict__.update(a); sys.modules[name] = m; return m

# ---- заглушки внешних библиотек ----
class BaseModel:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)
    @property
    def is_callback(self): return getattr(self, "payload", None) is not None
mod('pydantic', BaseModel=BaseModel, Field=lambda *a, **k: None)
mod('sqlalchemy', select=lambda model: ("select", model)); mod('sqlalchemy.ext'); mod('sqlalchemy.ext.asyncio', AsyncSession=object)
class StudentProfile:
    def __init__(self, user_id): self.user_id = user_id; self.diagnostic_status = "none"
    def __getattr__(self, k): return None
mod('app.models'); mod('app.models.profile', StudentProfile=StudentProfile)
sys.modules['sdamgia'] = None

# ---- имитация базы ----
class FakeDB:
    fail = False
    def __init__(self): self.rows = {}
    async def get(self, model, key):
        if self.fail: raise ConnectionError("db down")
        return self.rows.get(key)
    def add(self, obj): self.rows[obj.user_id] = obj
    async def commit(self):
        if self.fail: raise ConnectionError("db down")
    async def rollback(self): pass
    async def delete(self, obj): self.rows.pop(obj.user_id, None)
    async def execute(self, query):
        if self.fail: raise ConnectionError("db down")
        rows = list(self.rows.values())
        return types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(all=lambda: rows))

OGG_OPUS = b"OggS" + b"\x00" * 24 + b"OpusHead" + b"\x00" * 64   # заголовок OGG/Opus: перекодировать не нужно
tmp = tempfile.mkdtemp()
# Сценарии бота проверяем на примерной программе; программы школы (КТП) — отдельно в test_school_programs
os.makedirs(os.path.join(tmp, "programs"))
for _f in os.listdir(os.path.join(PROJECT, "data/programs")):
    if "school" not in _f:
        shutil.copy(os.path.join(PROJECT, "data/programs", _f), os.path.join(tmp, "programs"))
os.makedirs(os.path.join(tmp, "images"))
shutil.copy(os.path.join(PROJECT, "data/diagnostic_4.csv"), tmp)
shutil.copy(os.path.join(PROJECT, "data/diagnostic_5.csv"), tmp)
shutil.copy(os.path.join(PROJECT, "data/diagnostic_7.csv"), tmp)
json.dump([], open(os.path.join(tmp, "tasks.json"), "w"))
ADMIN = 1
settings = types.SimpleNamespace(TASKS_FILE=os.path.join(tmp, "tasks.json"), ALLOW_AI_TASKS=True, admin_ids={ADMIN},
                                 IMAGE_CARDS=False, VOICE_MODE="all", TTS_CACHE=False,
                                 CONSENT_REQUIRED=False, TEACHER_APPROVAL=False, VIA_DAILY_LIMIT=5,
                                 REDIS_URL="redis://redis:6379/0")   # согласие проверяет test_consent   # карточки-картинки проверяет test_visual
mod('app.core.config', settings=settings)

WIDE = []   # ряды кнопок, которые не влезли бы на экран телефона
class FakeApi:
    def __init__(self): self.sent = []; self.voices = 0
    async def send_message(self, chat_id=None, user_id=None, text="", keyboard=None, attachments=None):
        from app.handlers.tutor.common import _vis_len, ROW_WIDTH, BUTTON_MAX
        for row in (keyboard or {}).get("buttons", []):       # каждая кнопка должна влезать целиком
            sizes = [_vis_len(b["text"]) for b in row]
            assert sizes, keyboard                              # пустой ряд кнопок MAX может не принять
            # одна кнопка в ряду — до 24 знаков; несколько — каждая до 12 (половина экрана), а 3 в ряд — до 8
            limit = BUTTON_MAX if len(row) == 1 else (12 if len(row) == 2 else 8)
            if max(sizes) > limit or any(b["text"].endswith("…") for b in row):   # и ничего не обрезано
                WIDE.append([b["text"] for b in row])
        self.sent.append({"text": text, "keyboard": keyboard, "attachments": attachments}); return {}
    async def send_voice(self, chat_id, audio_bytes): self.voices += 1; return {}
    async def upload_image(self, *a, **k): return "TOK"
    files = {}
    async def download_image(self, url, max_bytes=0):
        return await self.download_file(url, max_bytes)
    async def download_file(self, url, max_bytes=0):
        if url in self.files: return self.files[url]
        return None if url == "broken" else OGG_OPUS
mod('app.services.api_client', MaxApiClient=FakeApi)

class FakeRedis:
    def __init__(self): self.d = {}
    async def set_state(self, key, value, ex=None): self.d[str(key)] = value; return True
    async def get_state(self, key): return self.d.get(str(key))
    async def delete_state(self, key): self.d.pop(str(key), None); return True
mod('app.services.redis_client', RedisClient=FakeRedis)

calls = {"judge": [], "summary": [], "gen": [], "speech": []}
AI_VERDICTS = {"А. С. Пушкин": True, "арктическая пустыня": True,
               "чтобы не опоздать снова": True, "потому что там живут олени": False}
class FakeAI:
    summary_fail = False
    async def judge_answer(self, task, ans, ref=None):
        calls["judge"].append((ans, ref)); return AI_VERDICTS.get(ans, False)
    async def diagnostic_summary(self, name, grade, results):
        calls["summary"].append((name, grade, results))
        return "Ошибка сети." if self.summary_fail else f"ИТОГ для {name}"
    async def generate_task(self, grade, subject, topic=None, avoid=None, with_options=False, level=2, textbook=None):
        calls.setdefault("textbook", []).append(textbook)
        calls["gen"].append((grade, subject, topic)); calls.setdefault("with_options", []).append(with_options)
        calls.setdefault("level", []).append(level)
        return (f"AI TASK {subject}", "1", {"topic": topic or "Тема от ИИ", "options": ["0", "1", "2"] if with_options else []})
    async def check_answer(self, *a, **k):
        calls.setdefault("check_known", []).append(k.get("known")); return (False, "hint")
    async def chat_with_mentor(self, history, context=None): return "CHAT"
    ocr_text = "Пн | Вт\n1 Математика | Окр. мир\n2 Русский | Англ.\n3 Музыка |"
    async def recognize_image_text(self, data, mime):
        calls.setdefault("ocr", []).append(mime); return self.ocr_text
    async def homework_tasks(self, raw, grade, titles):
        calls.setdefault("hw", []).append((raw, grade))
        tasks = [{"text": "Найди 3/4 от числа 20.", "subject": "Математика", "topic": "Дроби"}]
        if "№2" in raw: tasks.append({"text": "Подчеркни главные члены предложения: «Наступила осень».",
                                      "subject": "Русский язык", "topic": "Главные члены предложения"})
        return [] if "кот" in raw else tasks
    async def solve(self, task, options=None):
        calls.setdefault("solve", []).append(task); return "15" if "3/4" in task else "осень наступила"
    async def schedule_lines(self, raw, titles):
        calls.setdefault("sched_ai", []).append(raw)
        if "Пн" not in raw: return ""
        return "Пн: Математика, Русский язык, Музыка\nВт: Окружающий мир, Английский язык"
    async def explain_topic(self, grade, subject, topic, context=None, task=None, reference=None, detailed=False, textbook=None):
        calls.setdefault("explain", []).append((grade, subject, topic, task, detailed))
        calls.setdefault("explain_textbook", []).append(textbook); return f"ОБЪЯСНЕНИЕ {topic}"
    async def flashcards(self, grade, subject, topic, count=8):
        calls.setdefault("fc", []).append((grade, subject, topic))
        return [{"front": f"{topic}: вопрос {i}", "back": f"ответ {i}"} for i in range(1, 6)]
    async def explain_via(self, grade, subject, topic, world, details):
        calls.setdefault("via", []).append((grade, subject, topic, world)); return f"ЧЕРЕЗ {world}: {topic}"
    voice = True
    async def synthesize_speech(self, text, voice=None):
        calls["speech"].append(text); calls.setdefault("voice_arg", []).append(voice); return b"ogg" if self.voice else None
    test_fail = False
    usage = {}
    def count_usage(self, key, n=1): FakeAI.usage[key] = FakeAI.usage.get(key, 0) + n
    def usage_report(self): return ["💰 Расход Yandex Cloud сегодня"]
    async def build_test(self, grade, subject, count, n_options, level=2, focus_topics=None, scope="default", textbook=None,
                         on_progress=None):
        if on_progress and not self.test_fail: await on_progress("generated", count + 1, count)
        calls.setdefault("test_textbook", []).append(textbook)
        calls.setdefault("tests", []).append((grade, subject, count, n_options, level, focus_topics))
        calls.setdefault("scope", []).append(scope)
        if self.test_fail: return []
        opts = ["A1", "B2", "C3", "D4"][:n_options]
        n = len(calls["tests"])   # у каждого теста свои вопросы (как у настоящего ИИ)
        return [{"q": f"Вопрос теста {i} по предмету {subject}" + (f" (вариант {n})" if n > 1 else ""),
                 "options": list(opts), "answer": "B2",
                 "topic": f"Тема {i}"} for i in range(1, count + 1)]
    heard = ""
    async def recognize_speech(self, audio): calls.setdefault("stt", []).append(audio); return self.heard
mod('app.services.ai_client', ai_client=FakeAI())

from app.services.programs import programs as _programs
_programs.data_dir, _programs.loaded = os.path.join(tmp, "programs"), False
from app.handlers.user import UserHandler, SUBJECTS
from app.schemas.payloads import NormalizedMessage
from app.services.task_bank import task_bank

fallbacks = []
class Catch(logging.Handler):
    def emit(self, r):
        if "Не удалось обработать кнопку" in r.getMessage(): fallbacks.append(r.getMessage())
logging.getLogger("app.handlers.user").addHandler(Catch()); logging.getLogger("app.handlers.user").setLevel(logging.WARNING)

api, redis, db = FakeApi(), FakeRedis(), FakeDB()
def msg(uid, text=None, payload=None, audio_url=None):
    return NormalizedMessage(chat_id=uid, user_id=uid, message_id="m", text=text, payload=payload, audio_url=audio_url)
async def speak(uid, heard, url="https://files.max/voice.ogg"):
    FakeAI.heard = heard
    n = len(api.sent); await UserHandler(api, db, redis).handle_message(msg(uid, audio_url=url)); return api.sent[n:]
async def say(uid, t):
    n = len(api.sent); await UserHandler(api, db, redis).handle_message(msg(uid, text=t)); return api.sent[n:]
AUTO_TZ = True   # вопрос о часовом поясе после выбора класса: в старых сценариях отвечаем «Екатеринбург» автоматически
async def press(uid, p):
    n = len(api.sent); await UserHandler(api, db, redis).handle_callback(msg(uid, payload=p)); out = api.sent[n:]
    if AUTO_TZ and p.startswith("grade_") and any(q.startswith("tz_5_") for _, q in btns(out)):
        tz = next(q for _, q in btns(out) if q.startswith("tz_5_"))
        n = len(api.sent); await UserHandler(api, db, redis).handle_callback(msg(uid, payload=tz))
        out = api.sent[n + 1:]   # без строки «Запомнил: …»
    return out
def btns(out):
    return [(b["text"], b.get("payload") or b.get("url")) for m in out for row in (m["keyboard"] or {}).get("buttons", []) for b in row]
PRAISE_STARTS = ("✅ Верно", "✅ Правильно", "✅ Точно")
MODE_PREFIXES = ("Играйка 🎈 · ", "Тренер 💪 · ", "Стратег 🎯 · ")
def last(out):
    """Текст последнего сообщения без строки статуса (очки, уровень) над главным меню."""
    text = out[-1]["text"]
    if text.startswith(MODE_PREFIXES) and "\n\n" in text:
        text = text.split("\n\n", 1)[1]
    return text
def status(out):
    text = out[-1]["text"]
    return text.split("\n\n", 1)[0] if text.startswith(MODE_PREFIXES) else ""

async def main():
    global AUTO_TZ
    U = 1
    task_bank.load()
    assert len(task_bank.diagnostic_questions(4, [s for _, s in SUBJECTS[4]])) == 15
    assert [s for _, s in SUBJECTS[1]] == ["math", "rus", "read", "world"]
    for g in (2, 3, 4): assert ("Английский язык", "eng") in SUBJECTS[g]
    assert task_bank.pick("regular", 4, "math") is None   # вопросы диагностики не попадают в обычные задания

    # 1–2. Новый ученик: имя -> класс -> предложение диагностики
    assert "как тебя зовут" in last(await say(U, "/start"))
    out = await say(U, "Ваня"); assert [p for _, p in btns(out)] == [f"grade_{i}" for i in range(1, 12)]
    assert db.rows[U].name == "Ваня"
    out = await press(U, "grade_4"); t = last(out)
    assert "входной тест по пройденным темам" in t and "🗓 Расписание уроков" in t and "голосом" not in t, t   # без диагностики
    assert ("Математика", "subj_math_4") in btns(out) and ("🎮 Игры", "games") in btns(out)
    assert db.rows[U].grade == 4
    print("1. Новый ученик: имя → класс → сразу меню (диагностика — входным тестом в предмете): OK")

    # 3–4. Вопросы, голос, «Я не знаю»
    calls["speech"].clear()
    out = await press(U, "diag_start_4"); t = last(out)
    assert t.startswith("Вопрос 1 из 15 · Математика") and "3 400 + 2 650" in t
    assert btns(out)[-2:] == [("🤷 Я не знаю", "diag_idk"), ("⏸ Продолжу потом", "diag_pause")]
    assert sorted(b[0].split(" ", 1)[1] for b in btns(out)[:-2]) == ["5050", "6050", "6150"] and "Выбери ответ кнопкой" in last(out)
    spoken = " ".join(calls["speech"]); assert "Варианты ответа: первый — " in spoken and "3 400 плюс 2 650" in spoken, spoken
    assert calls["speech"] and calls["speech"][0].startswith("Вопрос 1"), calls["speech"]   # голос — только сам вопрос
    assert db.rows[U].diagnostic_status == "in_progress"
    assert last(await say(U, "6050")).startswith("✅ Записал!\n\nВопрос 2 из 15 · Математика")
    assert last(await say(U, "200")).startswith("✅ Записал!\n\nВопрос 3 из 15")
    assert last(await press(U, "diag_idk")).startswith("👌 Хорошо, идём дальше.\n\nВопрос 4 из 15 · Русский язык")
    assert calls["judge"] == []    # во время теста ИИ не вызывается — ничего не ждём
    FakeAI.voice = False   # дальше голос не нужен (в боте пауза 1 сек между голосовыми — тест был бы долгим)
    print("2. Вопросы по очереди с голосом, «Записал!», «Я не знаю», проверка не тормозит тест: OK")

    # 5–6. Пауза и обычный чат во время паузы
    out = await press(U, "diag_pause")
    assert "ты ответил на 3 из 15" in last(out) and btns(out)[0] == ("▶️ Продолжить тест", "diag_resume")
    assert ("📊 Прогресс", "progress_4") in btns(out)
    assert last(await say(U, "а что такое глагол?")) == "CHAT"
    print("3. «Продолжу потом»: прогресс сохранён, кнопка «Продолжить диагностику», чат работает: OK")

    # 7. Возвращение с незаконченным тестом (Redis очищен — данные из базы и сохранённого состояния)
    out = await say(U, "/start")
    assert "С возвращением, Ваня" in last(out) and "3 из 15" in last(out)
    assert btns(out) == [("▶️ Продолжить", "diag_resume"), ("Позже", "subjects_4")]
    out = await press(U, "diag_resume"); assert last(out).startswith("Вопрос 4 из 15")
    for ans in ["журчат", "Предложный", "умываться", "басня", "А. С. Пушкин", "чтобы не опоздать снова",
                "арктическая пустыня", "меркурий"]:
        out = await say(U, ans)
    assert last(out).startswith("✅ Записал!\n\nВопрос 12 из 15 · Окружающий мир")
    out = await press(U, "diag_idk")
    assert last(out).startswith("👌 Хорошо, идём дальше.\n\nВопрос 13 из 15 · Английский язык")
    for ans in ["Cat", "is", "likes"]: out = await say(U, ans)
    print("4. «С возвращением» + «Продолжить» с того же места: OK")

    # 8. Итоги
    assert out[0]["text"] == "Подвожу итоги... ⏳" and last(out).startswith("ИТОГ для Ваня")
    assert ("▶️ Продолжить тест", "diag_resume") not in btns(out) and ("Математика", "subj_math_4") in btns(out)
    judged = [a for a, _ in calls["judge"]]
    assert sorted(judged) == sorted(["200", "А. С. Пушкин", "чтобы не опоздать снова", "арктическая пустыня"]), judged   # «is» — другой вариант, ИИ не нужен
    name, grade, results = calls["summary"][-1]
    st = {(r["subject"], r["level"]): r["status"] for r in results}
    assert st[("Английский язык", 2)] == "неверно"
    assert st[("Математика", 1)] == "верно" and st[("Математика", 2)] == "неверно" and st[("Математика", 3)] == "не знаю"
    assert st[("Русский язык", 2)] == "верно"                      # «Предложный» = «предложный»
    assert st[("Литературное чтение", 2)] == "верно" and st[("Литературное чтение", 3)] == "верно"
    assert st[("Окружающий мир", 2)] == "верно" and st[("Окружающий мир", 3)] == "не знаю"
    p = db.rows[U]
    assert p.diagnostic_status == "done" and p.diagnostic_grade == 4
    scores = json.loads(p.diagnostic_scores); weak = json.loads(p.weak_topics)
    assert scores["Математика"] == {"correct": 1, "total": 3} and scores["Русский язык"] == {"correct": 3, "total": 3}
    assert scores["Английский язык"] == {"correct": 2, "total": 3}      # «Cat» верно, «is» неверно, «likes» верно
    assert {w["topic"] for w in weak} == {"Задачи в несколько действий", "Задачи на движение", "Природные зоны", "Глагол to be"}, weak
    assert "diag_state_1" not in redis.d and "diag_mode_1" not in redis.d
    print("5. Итоги: ответы проверены в конце, баллы и слабые темы сохранены в базе, меню после итогов: OK")

    # 9. Мой прогресс
    out = await press(U, "progress_4"); t = last(out)
    assert "• Математика: 1 из 3" in t and "Задачи на движение (Математика)" in t and "ИТОГ для Ваня" in t
    assert btns(out) == [("🔄 Диагностика заново", "diag_start_4"), ("◀️ Назад", "subjects_4")]
    # 10. Тест по предмету тренирует слабую тему
    out = await press(U, "subjtask_math_4")
    assert calls["gen"][-1][2] in ("Задачи в несколько действий", "Задачи на движение") and "🎯 Тренируем тему" in last(out)
    from app.services.programs import programs
    out = await press(U, "subjtask_rus_4"); assert "🎯" not in last(out)
    assert calls["gen"][-1][2] in programs.topics(4, "rus"), calls["gen"][-1]        # тема — из программы 4 класса
    out = await press(U, "subj_eng_3"); assert "Английский язык" in last(out)
    out = await press(U, "exam_vpr_4"); assert ("Английский", "exsub_vpr_4_eng") in btns(out)
    assert ("Окруж. мир", "exsub_vpr_4_world") in btns(out) and ("Лит. чтение", "exsub_vpr_4_read") in btns(out)
    print("   ВПР 4 класса: математика, русский, литературное чтение, окружающий мир, английский: OK")
    print("6. «Мой прогресс» и тренировка слабых тем в «Тесте по предмету»: OK")

    # 11. Возвращение: без повторного теста; Redis очищен (как после пересоздания контейнера)
    redis.d.clear()
    out = await say(U, "/start")
    assert last(out).startswith("С возвращением, Ваня! 👋 Готов продолжить учёбу?") and ("Математика", "subj_math_4") in btns(out)
    out = await press(U, "grade_4"); assert "диагностику" not in last(out)        # тот же класс — не предлагаем снова
    out = await press(U, "grade_5"); assert "входной тест" in last(out)              # 5 класс: меню, тест — в предмете
    out = await press(U, "grade_6"); assert "диагностику" not in last(out)        # для 6 класса вопросов пока нет
    out = await press(U, "progress_6"); assert "(4 класс)" in last(out) and btns(out) == [("◀️ Назад", "subjects_6")]
    print("7. Повторный заход без теста, данные пережили очистку Redis (из базы): OK")

    # Голосовой ввод (для тех, кто ещё не умеет писать)
    Y = 6
    await say(Y, "/start")
    out = await speak(Y, "Мила"); assert out[0]["text"] == "🎤 Я услышал: «Мила»" and "Мила" in last(out)
    await press(Y, "grade_4"); await press(Y, "diag_start_4")
    out = await speak(Y, "6050")
    assert out[0]["text"] == "🎤 Я услышал: «6050»" and last(out).startswith("✅ Записал!\n\nВопрос 2 из 15")
    out = await speak(Y, "")                       # не расслышали — вопрос не пропускается
    assert len(out) == 1 and "не расслышал" in last(out)
    out = await speak(Y, "что-то", url="broken")   # файл не скачался
    assert "не расслышал" in last(out)
    out = await say(Y, "208"); assert last(out).startswith("✅ Записал!\n\nВопрос 3 из 15")
    st = json.loads(redis.d["diag_state_6"]); assert [a["answer"] for a in st["answers"]] == ["6050", "208"]
    out = await say(Y, "   "); assert "Напиши мне сообщение" in last(out)
    print("   Голосовой ввод: имя и ответы голосом, «Я услышал: …», не расслышал — переспрашивает: OK")

    # Настройки: сменить имя, удалить данные и пройти всё как новый ученик
    out = await press(U, "subjects_4"); assert ("⚙️ Настройки", "settings_4") in btns(out)
    out = await press(U, "settings_4"); assert "Имя: Ваня" in last(out) and "Класс: 4" in last(out)
    assert [p for _, p in btns(out)] == ["set_name", "set_class", "notify_settings", "sched", "voice_toggle", "interests", "tutorial", "parent_code", "sc_my", "diag_start_4", "privacy", "delete_me", "subjects_4"]
    await press(U, "set_name"); out = await say(U, "Иван")
    assert "Приятно познакомиться заново, Иван" in last(out) and db.rows[U].name == "Иван"
    assert db.rows[U].diagnostic_status == "done"                              # смена имени не сбрасывает прогресс
    await say(U, "/name"); await press(U, "subjects_4")                       # передумал — кнопка отменяет ввод имени
    out = await say(U, "как дела"); assert last(out) == "CHAT" and db.rows[U].name == "Иван"
    out = await press(U, "set_class"); assert [p for _, p in btns(out)][:3] == ["grade_1", "grade_2", "grade_3"]
    out = await say(U, "/delete_me"); assert btns(out)[0] == ("Да, удалить", "delete_me_yes")
    out = await press(U, "delete_me_yes"); assert "данные удалены" in last(out)
    assert U not in db.rows and not [k for k in redis.d if k.endswith("_1") or k == "1"], redis.d.keys()
    out = await say(U, "/start"); assert "как тебя зовут" in last(out)        # теперь — как новый ученик
    await say(U, "Ваня"); out = await press(U, "grade_4"); assert "входной тест" in last(out)
    print("   Настройки: смена имени и класса, удаление данных, после удаления — сценарий нового ученика: OK")

    # Кнопки с вариантами ответа
    def press_option(out, label):
        return next(p for txt, p in btns(out) if p.startswith("ans_") and txt.split(" ", 1)[1] == label)
    Z = 7
    await say(Z, "/start"); await say(Z, "Тима"); await press(Z, "grade_4")
    out = await press(Z, "diag_start_4")
    out = await press(Z, press_option(out, "6050")); assert last(out).startswith("✅ Записал!\n\nВопрос 2 из 15")
    out = await press(Z, press_option(out, "198")); assert last(out).startswith("✅ Записал!\n\nВопрос 3 из 15")
    again = await press(Z, "diag_pause"); out = await press(Z, "diag_resume")      # после паузы варианты те же
    first = [b for b in btns(out) if b[1].startswith("ans_")]
    out2 = await press(Z, "diag_pause"); out2 = await press(Z, "diag_resume")
    assert first == [b for b in btns(out2) if b[1].startswith("ans_")]
    st = json.loads(redis.d["diag_state_7"]); assert [a["answer"] for a in st["answers"]] == ["6050", "198"]
    # задание 3 класса от ИИ — с вариантами; 7 класса — без
    await press(Z, "diag_pause")
    out = await press(Z, "subjtask_math_3"); assert calls["with_options"][-1] is True
    assert "Выбери ответ кнопкой" in last(out) and [p for _, p in btns(out)][:3] == ["ans_0", "ans_1", "ans_2"]
    out = await press(Z, press_option(out, "2")); assert last(out) == "hint"            # неверно — подсказка, варианты остались
    assert [p for _, p in btns(out)][:3] == ["ans_0", "ans_1", "ans_2"] and ("➡️ Ещё задание", "more") not in btns(out)
    out = await press(Z, press_option(out, "1")); assert last(out).startswith(PRAISE_STARTS) and btns(out)[0] == ("➡️ Ещё задание", "more")
    out = await press(Z, "ans_0"); assert "уже закончилось" in last(out)                 # старая кнопка
    out = await press(Z, "subjtask_hist_7"); assert calls["with_options"][-1] is False and "ans_0" not in [p for _, p in btns(out)]
    print("   Кнопки вариантов: в диагностике и заданиях 1–4 классов, неверный — подсказка, старые кнопки: OK")

    # Прогресс по темам и интервальное повторение
    import app.services.progress as prog
    from datetime import datetime, timedelta, timezone
    T0 = datetime.now(timezone.utc)
    progress = json.loads(db.rows[U].topic_progress) if U in db.rows and db.rows[U].topic_progress else None
    Q = 8
    await say(Q, "/start"); await say(Q, "Лиза"); await press(Q, "grade_4"); await press(Q, "diag_start_4")
    for _ in range(15): await press(Q, "diag_idk")
    pr = json.loads(db.rows[Q].topic_progress); assert pr and all(e["status"] == "weak" for e in pr.values())
    out = await press(Q, "subjtask_math_4"); assert "🎯 Тренируем тему «" in last(out)
    topic = calls["gen"][-1][2]
    out = await press(Q, press_option(out, "1"))                                         # верно с первой попытки
    e = json.loads(db.rows[Q].topic_progress)[prog.topic_key(4, "math", topic)]
    assert (e["status"], e["box"]) == ("learning", 1)
    out = await press(Q, "progress_4"); t = last(out)
    assert "📚 Темы:" in t and "Закреплено слабых тем: 0 из" in t and "🔁 Пора повторить:" in t
    # «Перематываем» время: тема возвращается на повторение через 3 дня и закрепляется после повторений
    for days in (3.1, 10.2, 25.3):
        prog.now = lambda d=days: T0 + timedelta(days=d)
        out = await press(Q, "subjtask_math_4")
        for _ in range(5):                         # сначала бот даёт другие слабые темы — решаем и их
            if calls["gen"][-1][2] == topic:
                break
            assert "🎯 Тренируем тему" in last(out) or "🔁 Повторяем тему" in last(out)
            await press(Q, press_option(out, "1")); out = await press(Q, "subjtask_math_4")
        assert calls["gen"][-1][2] == topic and "🔁 Повторяем тему" in last(out), (days, last(out))
        await press(Q, press_option(out, "1"))
    e = json.loads(db.rows[Q].topic_progress)[prog.topic_key(4, "math", topic)]
    assert e["status"] == "mastered" and e["box"] == 4, e
    out = await press(Q, "progress_4")
    mastered = sum(1 for x in json.loads(db.rows[Q].topic_progress).values() if x["was_weak"] and x["status"] == "mastered")
    assert mastered >= 1 and f"Закреплено слабых тем: {mastered} из" in last(out)
    prog.now = lambda: datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)   # среда прошлой недели — не мешает «перемотке» ниже
    print("   Прогресс по темам: слабые → повторение через 1/3/7/14 дней → «закреплено», экран прогресса: OK")

    # ---------- Этап 2: ежедневные занятия ----------
    from datetime import datetime, timedelta, timezone
    import app.services.progress as prog
    from app.services.notifier import notify_once
    D = 9
    await say(D, "/start"); await say(D, "Даша"); await press(D, "grade_4"); await press(D, "diag_start_4")
    for _ in range(15): await press(D, "diag_idk")                                # все темы — слабые
    assert "Твой ID в MAX: 9" in last(await say(D, "/whoami"))
    assert "только администратору" in last(await say(D, "/stats"))
    # задание на день (через /today) — 3 задания для 4 класса, сначала слабые темы
    async def today(uid):
        out = await say(uid, "/today")
        if "мини-теста недели" in last(out):          # если тест запущен в пятницу — берём обычный план
            out = await press(uid, "plan_regular")
        return out
    async def next_task(uid, payload="plan_next"):
        """Слабая тема: сначала объяснение (один раз), потом задание."""
        out = await press(uid, payload)
        if ("✅ Понятно, к заданию", "plan_next") in btns(out):
            assert last(out).startswith("📖 ") and "ОБЪЯСНЕНИЕ" in last(out), last(out)
            out = await press(uid, "plan_next")
        return out
    out = await today(D); t = last(out)
    assert t.startswith("Привет, Даша! 📚 Задание на сегодня — 3 задания:") and t.count("(подтянем)") == 3, t
    assert btns(out) == [("▶️ Начать", "plan_next"), ("⏰ Позже", "subjects_4")]
    out = await press(D, "subjects_4"); assert ("📅 Что сегодня · 0/3", "today") in btns(out)
    n_explain = len(calls.get("explain", []))
    out = await press(D, "plan_next"); t = last(out)                               # слабая тема — сначала объяснение
    assert "непросто — сначала коротко разберём" in out[0]["text"] and "ОБЪЯСНЕНИЕ" in t, t
    assert btns(out) == [("✅ Понятно, к заданию", "plan_next"), ("🎨 Объясни по-другому", "via"), ("⏰ Позже", "subjects_4")]
    out = await press(D, "plan_next"); t = last(out)
    assert "📅 Задание 1 из 3 на сегодня" in t and "🎯 Тема «" in t and ("⏭ Пропустить", "plan_skip") in btns(out)
    assert ("📖 Объясни тему", "explain") in btns(out)
    out = await press(D, "explain"); assert "ОБЪЯСНЕНИЕ" in last(out) and calls["explain"][-1][3]   # с текстом задания
    # «🎨 Объясни по-другому»: через увлечение, из кеша — бесплатно, «↩️ К заданию» возвращает задание
    assert ("🎨 Объясни по-другому", "via") in btns(out)
    await press(D, "interests"); out = await press(D, "int_foot"); assert ("✅ ⚽ Футбол", "int_foot") in btns(out)
    out = await press(D, "via"); assert btns(out)[0] == ("⭐ ⚽ Футбол", "via_foot")                    # увлечения первыми
    n_via = len(calls.get("via", [])); out = await press(D, "via_foot")
    assert "ЧЕРЕЗ футбол" in last(out) and len(calls["via"]) == n_via + 1 and btns(out)[0] == ("↩️ К заданию", "via_task")
    out = await press(D, "via_foot"); assert len(calls["via"]) == n_via + 1                           # из кеша
    out = await press(D, "via_task"); assert "✍️ Задание" in last(out) and [p for _, p in btns(out)][:3] == ["ans_0", "ans_1", "ans_2"]
    settings.VIA_DAILY_LIMIT = 1
    out = await press(D, "via_space"); assert "закончились" in last(out) and len(calls["via"]) == n_via + 1
    settings.VIA_DAILY_LIMIT = 5
    out = await press(D, "via_task")
    assert [p for _, p in btns(out)][:3] == ["ans_0", "ans_1", "ans_2"]                 # задание продолжается
    out = await press(D, press_option(out, "1"))
    assert btns(out)[0] == ("➡️ Дальше (ещё 2)", "plan_next")
    out = await next_task(D); out = await press(D, press_option(out, "2"))     # неверно: подсказка
    assert ("⏭ Пропустить", "plan_skip") in btns(out) and ("➡️ Ещё задание", "more") not in btns(out)
    out = await next_task(D, "plan_skip"); assert "📅 Задание 3 из 3 на сегодня" in last(out)
    assert len(calls["explain"]) - n_explain == 4                                   # 3 темы + кнопка «Объясни тему»
    out = await press(D, press_option(out, "1")); assert btns(out)[0] == ("🏁 Завершить на сегодня", "plan_next")
    out = await press(D, "plan_next"); t = last(out)
    assert t.startswith("🎉 Все задания на сегодня выполнены! Решено: 2 из 3, с первой попытки: 2."), t
    out = await press(D, "subjects_4"); assert not [b for b in btns(out) if b[1] == "plan_next"]
    day = json.loads(db.rows[D].activity)
    assert list(day.values())[0] == {"solved": 2, "first_try": 2, "points": 12}   # диагностика 5 + 2×2 за задания + 3 за план
    assert db.rows[D].streak_days == 1
    print("   План на день: 3 задания по слабым темам, «Пропустить», «Следующее», итог дня, статистика дня: OK")

    # вечерний отчёт и /demo для администратора
    t = last(await say(ADMIN, "/demo_report")); assert "Эта команда" not in t
    out = await today(D); assert "Задание на сегодня" in last(out)              # новый план после завершения
    # настройки уведомлений
    out = await press(D, "settings_4"); out = await press(D, "notify_settings")
    assert "Уведомления включены" in last(out) and ("✅ 📚 16:00", "dtime_1600") in btns(out) and ("✅ 🌙 19:30", "rtime_1930") in btns(out)
    out = await press(D, "dtime_1700"); assert ("✅ 📚 17:00", "dtime_1700") in btns(out) and db.rows[D].daily_time == "17:00"
    out = await press(D, "rtime_2000"); assert db.rows[D].report_time == "20:00"
    out = await press(D, "notify_toggle"); assert "Уведомления выключены" in last(out) and db.rows[D].notify_enabled is False
    await press(D, "notify_toggle"); assert db.rows[D].notify_enabled is True
    await press(D, "dtime_1600"); await press(D, "rtime_1930")
    print("   Настройки уведомлений: вкл/выкл, время задания и отчёта: OK")

    # Рассылка по расписанию (время «перематываем»; ученик в UTC+5)
    class Sess:
        async def __aenter__(self): return db
        async def __aexit__(self, *a): return False
    def make_msg(p): return msg(p["user_id"])
    def make_handler(session): return UserHandler(api, db, redis)
    for uid in list(db.rows):
        if uid != D: db.rows[uid].notify_enabled = False                              # проверяем на одном ученике
    def at(day, hh, mm):  # 2026-11-02 — понедельник; время местное (UTC+5)
        return datetime(2026, 11, day, hh, mm, tzinfo=timezone.utc) - timedelta(hours=5)
    db.rows[D].last_active_at = at(2, 9, 0); db.rows[D].last_daily_date = None; db.rows[D].last_report_date = None
    async def tick(day, hh, mm):
        prog.now = lambda: at(day, hh, mm)
        n = len(api.sent); sent = await notify_once(lambda: Sess(), make_handler, make_msg); return sent, api.sent[n:]
    assert (await tick(2, 15, 59))[0] == 0                                             # рано
    sent, out = await tick(2, 16, 1); assert sent == 1 and "Задание на сегодня" in last(out)
    assert (await tick(2, 16, 30))[0] == 0                                             # второй раз не шлёт
    sent, out = await tick(2, 19, 31); assert sent == 1 and "сегодня позаниматься не получилось" in last(out)
    assert (await tick(2, 20, 0))[0] == 0
    assert (await tick(7, 16, 1))[0] == 0                                              # суббота
    db.rows[D].last_active_at = at(2, 16, 5)
    sent, out = await tick(5, 16, 1); assert sent == 1 and "Задание на сегодня" in last(out)   # чт: 2 учебных дня тишины
    sent, out = await tick(6, 16, 1); assert sent == 1 and "Всё в порядке?" in last(out)       # пт: 3 дня — один вопрос
    assert db.rows[D].notify_paused is True
    assert (await tick(6, 19, 31))[0] == 0                                             # после вопроса бот молчит, отчёта нет
    assert (await tick(9, 16, 1))[0] == 0                                              # пн: на паузе — молчит
    redis.d.pop(f"seen_{D}", None); await say(D, "привет, я тут")                      # ученик вернулся
    assert db.rows[D].notify_paused is False
    sent, out = await tick(10, 16, 1); assert sent == 1 and "Задание на сегодня" in last(out)
    db.rows[D].tz_offset = 120                                                         # другой часовой пояс (UTC+2)
    assert (await tick(11, 16, 1))[0] == 0                                             # там ещё 13:01
    prog.now = lambda: datetime.now(timezone.utc)
    print("   Рассылка: будни, 16:00/19:30, без повторов, «Всё в порядке?» после 3 дней тишины и пауза, часовой пояс: OK")

    out = await say(ADMIN, "/stats"); t = last(out)
    assert t.startswith("📈 Статистика бота") and "Учеников:" in t and "Слабых тем закреплено:" in t, t
    print("   /stats для администратора: OK")

    # ---------- Обучение «как пользоваться ботом» ----------
    K = 10
    await say(K, "/start"); await say(K, "Петя")
    calls["speech"].clear(); FakeAI.voice = True
    out = await press(K, "grade_1")                                        # первоклассник: обучение запускается само
    assert last(out).startswith("👋 Привет! Я — твой помощник") and btns(out) == [("Дальше ▶️", "tut_1"), ("✖️ Закрыть", "tut_end")]
    assert calls["speech"] and "Я умею говорить" in " ".join(calls["speech"])  # всё озвучено
    FakeAI.voice = False
    out = await press(K, "tut_1"); assert btns(out)[0] == ("👆 Нажми меня", "tut_tap_1") and ("◀️ Назад", "tut_0") in btns(out)
    out = await press(K, "tut_tap_1"); assert last(out).startswith("👍 Молодец!") and "Сколько будет 1 + 1?" in last(out)
    assert [p for _, p in btns(out)][:3] == ["tut_q_2_0", "tut_q_2_1", "tut_q_2_2"]
    out = await press(K, "tut_q_2_2"); assert "Посчитай на пальчиках" in last(out) and btns(out)[1][1] == "tut_q_2_1"   # «3» — подсказка
    out = await press(K, "tut_q_2_1"); assert last(out).startswith("🎉 Правильно!")
    out = await press(K, "tut_3"); out = await press(K, "tut_4"); assert "Для взрослых" in last(out)
    assert btns(out)[0] == ("✅ Всё понятно!", "tut_end")
    out = await press(K, "tut_end")                                         # диагностики для 1 класса пока нет → меню
    assert "Если что-то забудешь" in last(out) and ("⚙️ Настройки", "settings_1") in btns(out)
    assert db.rows[K].tutorial_done is True
    out = await press(K, "grade_1"); assert not last(out).startswith("👋")    # второй раз само не запускается
    out = await press(K, "tutorial"); assert last(out).startswith("👋 Привет!")  # а по кнопке — пожалуйста
    out = await press(K, "tut_end"); assert "Если что-то забудешь" in last(out)
    # 7 класс: короткое обучение из 5 шагов, /help
    out = await press(K, "grade_7"); assert "🎓 Обучение»" in last(out)
    out = await say(K, "/help"); assert last(out).startswith("🎓 Как со мной заниматься · 1 из 5")
    for i in range(1, 5):
        out = await press(K, f"tut_{i}")
    assert "⌨️ Команды · 5 из 5" in last(out) and "/today" in last(out) and btns(out)[0] == ("✅ Всё понятно!", "tut_end")
    out = await press(K, "tut_end"); assert ("⚙️ Настройки", "settings_7") in btns(out)
    # 4 класс, новый ученик: обучение не мешает, сразу предложение диагностики со ссылкой на обучение
    L = 11
    await say(L, "/start"); await say(L, "Оля")
    out = await press(L, "grade_4"); assert "входной тест" in last(out) and "🎓 Обучение»" in last(out)
    for bad in ("tut_99", "tut_q_2_7", "tut_q_0_0"):
        out = await press(K, bad); assert out, bad
    assert last(await say(12, "/help")).startswith("Сначала напиши /start")
    print("   Обучение: 1–2 класс — само, голосом, с тренировкой кнопок и ответа; 3–11 — 5 шагов; кнопка и /help: OK")

    # ---------- Этап 3: режимы и награды ----------
    from app.services.rewards import WEEKLY_GOAL
    R = 13                                                                   # Играйка (4 класс)
    await say(R, "/start"); await say(R, "Рома"); await press(R, "grade_4"); await press(R, "diag_skip_4")
    out = await press(R, "subjtask_math_4")
    out = await press(R, press_option(out, "1")); t = out[-1]["text"]
    assert "+2 ⭐ · всего 2 ⭐" in t and "🏆 Новое достижение: 🌱 Первый шаг!" in t, t
    out = await press(R, "more"); await press(R, press_option(out, "0")); await press(R, press_option(out, "2"))
    out = await press(R, press_option(out, "1")); t = out[-1]["text"]
    assert "+1 ⭐ · всего 3 ⭐" in t and "🧗 Упорство" in t, t                  # верно с 3-й попытки: +1 и «Упорство»
    out = await press(R, "subjects_4"); s = status(out)
    assert s.startswith("Играйка 🎈 · 3 ⭐ · Звёздочка ✨") and "неделя: 2/10" in s, s
    assert ("🏆 Награды", "rewards_4") in btns(out)
    for _ in range(8):                                                       # 10 заданий, 19 ⭐
        out = await press(R, "more"); out = await press(R, press_option(out, "1"))
    assert "🧠 10 заданий" in out[-1]["text"] and "🥇 Цель недели" in out[-1]["text"] and "Новый уровень" not in out[-1]["text"]
    out = await press(R, "more"); out = await press(R, press_option(out, "1"))   # 21 ⭐ — новый уровень
    assert "+2 ⭐ · всего 21 ⭐" in out[-1]["text"] and "🎉 Новый уровень: Искорка 💫!" in out[-1]["text"], out[-1]["text"]
    out = await press(R, "rewards_4"); t = last(out)
    assert "уровень: Искорка 💫" in t, t
    assert "Цель недели: ▓▓▓▓▓▓▓▓▓▓ 10/10 заданий" in t and "✅ 🌱 Первый шаг" in t and "🔒 🧭 Разведчик — пройди диагностику" in t
    assert "✅ 🥇 Цель недели" in t, t
    # Тренер (7 класс): очки опыта ⚡
    C = 14
    await say(C, "/start"); await say(C, "Костя"); await press(C, "grade_7")
    out = await press(C, "subjtask_hist_7"); out = await say(C, "1")           # FakeAI: эталон «1»
    assert "+20 ⚡ · всего 20 ⚡" in out[-1]["text"], out[-1]["text"]
    out = await press(C, "subjects_7"); assert status(out).startswith("Тренер 💪 · 20 ⚡ · уровень «Новичок»"), status(out)
    # Стратег (11 класс): без звёзд, цель недели и точность
    S = 15
    await say(S, "/start"); await say(S, "Саша"); await press(S, "grade_11")
    out = await press(S, "subjtask_phys_11"); out = await say(S, "1")
    t = out[-1]["text"]; assert "⭐" not in t and "⚡" not in t and f"Неделя: 1/{WEEKLY_GOAL['strategy']} заданий" in t, t
    out = await press(S, "subjects_11"); assert status(out).startswith("Стратег 🎯 · точность за неделю 100%"), status(out)
    out = await press(S, "rewards_11"); assert "Точность с первой попытки за неделю: 100%" in last(out)
    # награда за обучение (первоклассник) — только в первый раз
    T = 16
    await say(T, "/start"); await say(T, "Тоня"); await press(T, "grade_1")
    out = await press(T, "tut_end"); assert "+1 ⭐" in last(out) and "🎓 Всё понятно" in last(out)
    await press(T, "tutorial"); out = await press(T, "tut_end"); assert "⭐" not in last(out)
    print("   Режимы и награды: Играйка ⭐ и уровни, Тренер ⚡, Стратег — цель недели и точность, достижения: OK")

    # ---------- Диагностика 5–11: математика и русский обязательно + 2–3 на выбор ----------
    P = 17
    await say(P, "/start"); await say(P, "Паша")
    await press(P, "grade_5"); out = await press(P, "diag_start_5"); t = last(out)   # общая диагностика — по желанию
    assert "Обязательно: Математика, Русский язык." in t and "Выбери ещё 2–3 предмета" in t, t
    assert [b for b in btns(out) if b[1].startswith("diag_pick_")] == [("▫️ Литература", "diag_pick_lit"),
        ("▫️ История", "diag_pick_hist"), ("▫️ Биология", "diag_pick_bio"),
        ("▫️ География", "diag_pick_geo"), ("▫️ Английский", "diag_pick_eng"), ("▫️ Информатика", "diag_pick_inf")]
    out = await press(P, "diag_go"); assert last(out).startswith("Выбери ещё 2 — и начнём!")
    await press(P, "diag_pick_hist"); out = await press(P, "diag_pick_bio")
    assert "Выбрано: История, Биология · 12 вопросов, около 7 минут." in last(out)
    assert ("🚀 Начать (выбрано 2)", "diag_go") in btns(out) and ("✅ История", "diag_pick_hist") in btns(out)
    await press(P, "diag_pick_geo"); out = await press(P, "diag_pick_eng")
    assert last(out).startswith("Можно выбрать не больше 3") and ("▫️ Английский", "diag_pick_eng") in btns(out)
    out = await press(P, "diag_pick_bio"); assert "Выбрано: История, География" in last(out)   # сняли галочку
    out = await press(P, "diag_go"); assert last(out).startswith("Вопрос 1 из 12 · Математика")
    for _ in range(12): out = await press(P, "diag_idk")
    scores = json.loads(db.rows[P].diagnostic_scores)
    assert sorted(scores) == ["География", "История", "Математика", "Русский язык"], scores
    # предмет, который не проверяли, — короткая проверка при первом открытии
    out = await press(P, "subj_hist_5"); assert not [b for b in btns(out) if b[1].startswith("entry_")]
    out = await press(P, "subj_eng_5"); assert btns(out)[0] == ("🧭 Входной тест", "entry_eng_5")
    assert last(out).startswith("🧭 Давай начнём с короткого входного теста")
    out = await press(P, "diag_subj_eng_5"); assert last(out).startswith("Вопрос 1 из 3 · Английский язык")
    out = await press(P, press_option(out, "are")); out = await press(P, press_option(out, "children"))
    out = await press(P, press_option(out, "go"))
    texts = [m["text"] for m in out]
    assert any(x.startswith("Готово! Английский язык: 2 из 3. Похоже, стоит повторить: Past Simple.") for x in texts), texts
    assert "Какую тему вы сейчас проходите?" in last(out) and not [b for b in btns(out) if b[1].startswith("entry_")]
    scores = json.loads(db.rows[P].diagnostic_scores); assert scores["Английский язык"] == {"correct": 2, "total": 3}
    assert len(scores) == 5 and db.rows[P].diagnostic_summary.startswith("ИТОГ для Паша")   # итоги сохранились
    out = await press(P, "subjtask_eng_5"); assert "🎯 Тренируем тему «Past Simple»" in last(out)
    # «пройти заново» у старших — снова через выбор предметов
    out = await press(P, "diag_start_5"); assert "Обязательно: Математика, Русский язык." in last(out)
    # пропустил диагностику — каждый предмет предлагает быструю проверку
    W2 = 18
    await say(W2, "/start"); await say(W2, "Вера"); await press(W2, "grade_5"); await press(W2, "diag_skip_5")
    out = await press(W2, "subj_math_5"); assert btns(out)[0] == ("🧭 Входной тест", "entry_math_5")
    # 7 класс: «Математика» = алгебра + геометрия по 2 задания, результаты — отдельно
    M = 19
    await say(M, "/start"); await say(M, "Марк")
    await press(M, "grade_7"); out = await press(M, "diag_start_7"); t = last(out)
    assert "Обязательно: Математика (алгебра и геометрия — по 2 задания), Русский язык." in t, t
    optional = [b[0] for b in btns(out) if b[1].startswith("diag_pick_")]
    assert "▫️ Геометрия" not in optional and "▫️ Физика" in optional, optional
    out = await press(M, "diag_pick_geo_math"); assert out                    # обязательную геометрию выбрать нельзя
    await press(M, "diag_pick_phys"); out = await press(M, "diag_pick_hist")
    assert "13 вопросов" in last(out), last(out)                              # 2 + 2 + 3 + 3 + 3
    out = await press(M, "diag_go"); assert last(out).startswith("Вопрос 1 из 13 · Математика · Алгебра"), last(out)
    out = await press(M, press_option(out, "5"))                              # 3x + 5 = 20 → 5 (уровень 1)
    assert last(out).startswith("✅ Записал!\n\nВопрос 2 из 13 · Математика · Алгебра") and "y = 2x − 1" in last(out)
    out = await press(M, press_option(out, "9"))                              # уровень 3 — неверно
    assert "Вопрос 3 из 13 · Математика · Геометрия" in last(out) and "смежных" in last(out)
    for _ in range(11): out = await press(M, "diag_idk")
    scores = json.loads(db.rows[M].diagnostic_scores)
    assert scores["Алгебра"] == {"correct": 1, "total": 2} and scores["Геометрия"] == {"correct": 0, "total": 2}, scores
    assert sorted(scores) == ["Алгебра", "Геометрия", "История", "Русский язык", "Физика"], scores
    out = await press(M, "subj_geo_math_7"); assert not [b for b in btns(out) if b[1].startswith("entry_")]
    out = await press(M, "subj_bio_7"); assert btns(out)[0][1] == "entry_bio_7"
    out = await press(M, "diag_subj_bio_7"); assert last(out).startswith("Вопрос 1 из 3 · Биология")
    print("   Диагностика 5–11: обязательные математика и русский + 2–3 на выбор, быстрая проверка предмета: OK")
    print("   7–11 классы: «Математика» = алгебра 2 + геометрия 2, результаты отдельно: OK")

    # ---------- Предметы и слова «экзамен» / «ВПР» по классам ----------
    G = 20
    await say(G, "/start"); await say(G, "Гоша")
    texts = {}
    for g in (2, 3, 4, 5, 6, 7, 9, 10, 11):
        await press(G, f"grade_{g}"); await press(G, f"diag_skip_{g}") if g in (4, 5, 7) else None
        out = await press(G, f"subjects_{g}"); texts[g] = (last(out), btns(out) + (btns(await press(G, f"subjlist_{g}")) if g >= 5 else []))
    for g in (2, 3):
        assert texts[g][0].startswith("Выбери предмет, и начнём заниматься") and "экзам" not in texts[g][0] and "ВПР" not in texts[g][0]
    for g in (4, 6, 10):
        assert "«📝 Подготовка к ВПР»" in texts[g][0] and "экзам" not in texts[g][0], (g, texts[g][0])
    for g in (9, 11):
        assert "подготовки к экзамену" in texts[g][0], (g, texts[g][0])
    names = lambda g: [b[0] for b in texts[g][1]]
    assert "Общество" not in names(6) and "Общество" in names(9) and "ОБЗР" in names(9) and "ОБЗР" in names(11)
    out = await press(G, "subjlist_7"); assert "Общество" not in [b[0] for b in btns(out)] and "Литература" in [b[0] for b in btns(out)]
    out = await press(G, "subjlist_8"); assert "ОБЗР" in [b[0] for b in btns(out)] and "Общество" not in [b[0] for b in btns(out)]
    out = await press(G, "exam_oge_9"); assert ("ОБЗР", "exsub_oge_9_obzr") not in btns(out) and ("Общество", "exsub_oge_9_soc") in btns(out)
    for g in (5, 6, 7):                                                              # информатика 5–7 и в ВПР
        assert "Информатика" in names(g), (g, names(g))
        out = await press(G, f"exam_vpr_{g}"); assert ("Информатика", f"exsub_vpr_{g}_inf") in btns(out)
    out = await press(G, "subj_obzr_10"); assert "ОБЗР" in last(out)
    # обучение: строка про подготовку — по классу
    for g, expected in ((3, None), (7, "«Подготовка к ВПР» — задания в формате ВПР"), (11, "«Подготовка к ЕГЭ» — задания в формате экзамена")):
        await press(G, f"subjects_{g}"); out = await press(G, "tutorial")
        if expected:
            assert expected in last(out), (g, last(out))
        else:
            assert "Подготовка" not in last(out), last(out)
    print("   Предметы по классам (обществознание 9–11, ОБЗР 8–11), «экзамен» только в 9 и 11, в остальных — ВПР: OK")

    # 12. Пропуск и повторное прохождение
    V = 2
    await say(V, "/start"); await say(V, "Аня")
    await press(V, "grade_4")
    out = await press(V, "diag_skip_4"); assert "📊 Прогресс" in last(out)
    out = await press(V, "grade_4"); assert "диагностику" not in last(out)
    out = await press(V, "progress_4"); assert btns(out)[0] == ("🚀 Пройти диагностику", "diag_start_4")
    out = await press(V, "diag_start_4"); assert last(out).startswith("Вопрос 1 из 15")
    print("8. «Пропустить», повторное предложение не надоедает, пройти можно из «Мой прогресс»: OK")

    # 13. База недоступна — бот работает на Redis
    W = 3; FakeDB.fail = True
    await say(W, "/start"); await say(W, "Петя")
    out = await press(W, "grade_4"); assert "входной тест" in last(out)
    await press(W, "diag_start_4")
    for _ in range(15): out = await press(W, "diag_idk")
    FakeAI.summary_fail = True
    assert True
    FakeAI.summary_fail = False
    assert "ИТОГ для Петя" in last(out)
    out = await say(W, "/start"); assert "С возвращением, Петя" in last(out)
    out = await press(W, "progress_4"); assert "• Математика: 0 из 3" in last(out)
    FakeDB.fail = False
    print("9. Без базы: профиль и итоги живут в Redis, бот не падает: OK")

    # 14. Запасные итоги, если ИИ не ответил
    X = 4; FakeAI.summary_fail = True
    await say(X, "/start"); await say(X, "Оля"); await press(X, "grade_4"); await press(X, "diag_start_4")
    await say(X, "6050")
    for _ in range(14): out = await press(X, "diag_idk")
    FakeAI.summary_fail = False
    t = last(out); assert t.startswith("Оля, диагностика пройдена!") and "Похоже, стоит повторить" in t, t
    print("10. Если ИИ не ответил на итоги — понятный запасной текст: OK")

    # 15. Старые/битые кнопки диагностики не роняют бота
    out = await press(5, "diag_idk"); assert out and fallbacks and "диагностика не начата" in fallbacks[-1]
    for p in ("diag_start_99", "diag_skip_x", "progress_0"):
        out = await press(1, p); assert "Выбери предмет" in last(out), p
    print("11. Старые/битые кнопки диагностики: OK")

    # ---------- Сгенерированный тест по предмету ----------
    G = 30
    await say(G, "/start"); await say(G, "Гоша"); await press(G, "grade_6"); await press(G, "diag_skip_6")
    out = await press(G, "subj_math_6"); assert "составлю для тебя новый тест" in last(out)
    out = await press(G, "subjtest_math_6")
    assert out[0]["text"].startswith("✍️ Составляю для тебя новый тест") and calls["tests"][-1][:5] == (6, "Математика", 7, 4, 2)
    assert out[1]["text"].startswith("🔍 Вопросы составлены (8)")                     # видно, что бот не завис
    assert "7 вопросов, уровень — повышенный" in out[2]["text"] and last(out).startswith("Вопрос 1 из 7")
    out = await press(G, "subjects_6"); assert btns(out)[0] == ("📅 Что сегодня", "today")
    out = await press(G, "today"); assert "Задание на сегодня" in last(out) or "мини-теста" in last(out)
    out = await press(G, "subjtest_math_6")
    def test_option(out, label):
        return next(p for txt, p in btns(out) if p.startswith("sta_") and txt.split(" ", 1)[1] == label)
    assert [p for _, p in btns(out)][:4] == ["sta_0_0", "sta_0_1", "sta_0_2", "sta_0_3"] and ("⏹ Закончить тест", "st_stop") in btns(out)
    assert "Варианты ответа: первый —" in calls["speech"][-1]
    out = await press(G, test_option(out, "B2")); assert last(out).startswith("✅ Записал!\n\nВопрос 2 из 7")
    out = await press(G, "sta_0_1"); assert "уже закончился" in last(out)        # старая кнопка
    out = await press(G, "subjtest_math_6")                                       # новый тест
    out = await press(G, test_option(out, "A1"))                                   # 1 — неверно
    st = json.loads(redis.d[f"stest_{G}"]); number = st["questions"][1]["options"].index("B2") + 1
    out = await say(G, str(number))                                                # номер варианта текстом
    assert json.loads(redis.d[f"stest_{G}"])["answers"][1] == {"chosen": "B2", "correct": True}
    out = await say(G, "что это?"); assert "номер варианта" in last(out)          # не вариант — переспрашивает
    for _ in range(5): out = await press(G, test_option(out, "B2"))
    t = out[-1]["text"]
    assert "Тест готов! Математика: 6 из 7." in t and "1. ❌ Тема 1" in t and "3. ✅ Тема 3" in t, t
    assert "B2" not in t and "Правильные ответы не показываю" in t, t                # ответы не раскрываются
    assert ("🔧 Разобрать вопрос 1", "st_fix_0") in btns(out) and ("🎲 Ещё тест", "subjtest_math_6") in btns(out)
    assert "+" in t and "⚡" in t                                                     # очки за верные ответы
    pr = json.loads(db.rows[G].topic_progress); assert pr[prog.topic_key(6, "math", "Тема 1")]["status"] == "learning"
    assert json.loads(redis.d[f"levels_{G}"])["math"]["level"] == 3                  # 6 из 7 — сложнее
    assert "уровень «высокий»" in t
    out = await press(G, "st_fix_0"); t = last(out)
    assert "Разбираем вопрос 1 из теста" in t and "Вопрос теста 1" in t and [p for _, p in btns(out)][:4] == ["ans_0", "ans_1", "ans_2", "ans_3"]
    out = await press(G, press_option(out, "A1")); assert last(out) == "hint"
    out = await press(G, press_option(out, "B2")); assert last(out).startswith(PRAISE_STARTS)
    out = await press(G, "subjtest_math_6"); assert calls["tests"][-1][4] == 3        # следующий тест — уровень 3
    out = await press(G, "st_stop"); assert "Какую тему вы сейчас проходите" in last(out)   # без ответов — просто выход
    out = await press(G, "subjtest_math_6"); out = await press(G, test_option(out, "A1"))
    out = await press(G, "st_stop"); assert "Тест остановлен! Математика: 0 из 1." in out[-1]["text"]
    FakeAI.test_fail = True
    out = await press(G, "subjtest_math_6"); assert "Не получилось составить тест" in last(out)
    assert btns(out)[0] == ("🔄 Попробовать ещё раз", "subjtest_math_6")
    FakeAI.test_fail = False
    # 1–4 классы: 5 вопросов, 3 варианта, слабые темы из диагностики попадают в тест
    Q2 = 32
    await say(Q2, "/start"); await say(Q2, "Даша"); await press(Q2, "grade_4"); await press(Q2, "diag_start_4")
    for _ in range(15): await press(Q2, "diag_idk")
    out = await press(Q2, "subjtest_math_4"); assert calls["tests"][-1][2:4] == (5, 3) and calls["tests"][-1][5], calls["tests"][-1]
    assert "🎯 Добавил темы, которые пора повторить" in out[2]["text"]
    out = await press(Q2, "subj_math_4"); assert any(p == "subjtask_math_4" for _, p in btns(out))   # «Повторить: тема»
    # Уровень одиночных заданий: 2 ошибки подряд — проще, 3 верных подряд — сложнее
    L = 31
    await say(L, "/start"); await say(L, "Лёва"); await press(L, "grade_7"); await press(L, "diag_skip_7")
    for _ in range(2):
        await press(L, "subjtask_hist_7"); await say(L, "неверно")
    assert json.loads(redis.d[f"levels_{L}"])["hist"]["level"] == 1
    for _ in range(3):
        await press(L, "subjtask_hist_7"); await say(L, "1")
    assert json.loads(redis.d[f"levels_{L}"])["hist"]["level"] == 2
    await press(L, "subjtask_hist_7"); assert calls["level"][-1] == 2
    print("   Тест по предмету: новый тест от ИИ, ответы кнопками/номером, итог без ответов, разбор ошибок, уровень: OK")

    # Выбор голоса на слух (только администратор)
    out = await say(L, "/voices"); assert "только администратору" in last(out)
    FakeAI.voice = True; n = api.voices
    out = await say(ADMIN, "/voices"); assert "8. Филипп" in last(out) and api.voices - n == 8
    assert calls["voice_arg"][-1] == {"model": "", "voice": "filipp", "role": ""}
    await say(ADMIN, "/voice 2"); assert json.loads(redis.d["tts_voice"])["voice"] == "anton"
    await say(L, "/start"); assert calls["voice_arg"][-1]["voice"] == "anton"        # голос для всех учеников
    await say(ADMIN, "/voice 0"); assert "tts_voice" not in redis.d
    out = await say(ADMIN, "/voice 99"); assert "номер голоса от 1 до 8" in last(out)
    FakeAI.voice = False
    print("   Голос: /voices — образцы, /voice N — выбрать для всех, /voice 0 — как в .env: OK")

    # ---------- Этап 1: входной тест в предмете, урок по теме, расписание ----------
    from datetime import datetime as _dt, timezone as _tz
    real_now = prog.now
    E = 40
    await say(E, "/start"); await say(E, "Ева"); await press(E, "grade_4")
    out = await press(E, "subj_math_4"); assert btns(out)[0] == ("🧭 Входной тест", "entry_math_4")
    out = await press(E, "entry_math_4")
    assert calls["scope"][-1] == "passed" and "🧭 Входной тест по предмету «Математика»" in out[2]["text"]
    for label in ["A1", "B2", "B2", "C3", "B2"]: out = await press(E, test_option(out, label))
    t = out[-1]["text"]
    assert "Входной тест готов! Математика: 3 из 5." in t and "Запомнил темы, которые стоит подтянуть: «Тема 1», «Тема 4»" in t, t
    assert json.loads(db.rows[E].diagnostic_scores)["Математика"] == {"correct": 3, "total": 5}
    pr = json.loads(db.rows[E].topic_progress)
    assert pr[prog.topic_key(4, "math", "Тема 1")]["status"] == "weak" and pr[prog.topic_key(4, "math", "Тема 2")]["status"] == "known"
    out = await press(E, "subj_math_4"); assert not [b for b in btns(out) if b[1].startswith("entry_")]
    out = await press(E, "subj_rus_4"); assert btns(out)[0] == ("🧭 Входной тест", "entry_rus_4")   # другой предмет — свой тест
    # урок по теме: объяснение → пробный пример → задания на оценку → оценка
    await press(E, "subj_math_4"); out = await say(E, "Дроби")
    assert calls["explain"][-1][2] == "Дроби" and calls["explain"][-1][4] is True       # подробный урок
    assert last(out).startswith("📘 Урок · Математика · Дроби") and "ОБЪЯСНЕНИЕ Дроби" in last(out)
    assert btns(out)[:2] == [("✍️ Пробный пример", "lesson_trial"), ("📝 Сразу на оценку", "lesson_graded")]
    before = json.loads(db.rows[E].topic_progress)
    out = await press(E, "lesson_trial"); assert "✍️ Пробный пример — не на оценку" in last(out)
    out = await press(E, press_option(out, "2")); assert last(out) == "hint"            # ошибка — подсказка
    out = await press(E, press_option(out, "1")); t = last(out)
    assert "Пробный пример решён" in t and btns(out)[0] == ("📝 Задания на оценку", "lesson_graded")
    assert prog.topic_key(4, "math", "Дроби") not in json.loads(db.rows[E].topic_progress) or \
        json.loads(db.rows[E].topic_progress)[prog.topic_key(4, "math", "Дроби")].get("wrong", 0) == \
        before.get(prog.topic_key(4, "math", "Дроби"), {}).get("wrong", 0)                # пробные ошибки не считаются
    n_tests = len(calls["tests"])
    out = await press(E, "lesson_graded")
    assert calls["scope"][-1] == "topic" and calls["tests"][-1][2] == 4 and calls["tests"][-1][5] == ["Дроби"]
    assert len(calls["tests"]) == n_tests + 1                # одна фоновая подготовка с начала урока, не новая генерация
    assert not any("Составляю" in m["text"] for m in out)
    assert any("📝 Задания на оценку по теме «Дроби»" in m["text"] for m in out)
    for label in ["B2", "B2", "A1", "B2"]: out = await press(E, test_option(out, label))
    t = out[-1]["text"]
    assert "Задания на оценку выполнены! Математика: 3 из 4." in t and "📒 Оценка за урок «Дроби»: 4. Хорошо!" in t, t
    assert ("🔄 Ещё раз на оценку", "lesson_graded") in btns(out)
    assert json.loads(db.rows[E].lesson_marks)[-1]["mark"] == 4
    out = await press(E, "progress_4"); assert "📒 Оценки за уроки:\n• Математика: 4" in last(out)
    # расписание: кнопками и текстом
    out = await press(E, "sched"); assert "🗓 Расписание уроков" in last(out) and ("Пн", "sch_d_0") in btns(out)
    await press(E, "sch_d_0"); await press(E, "sch_a_0_math"); await press(E, "sch_a_0_rus")
    out = await press(E, "sch_a_0_read"); assert "1. Математика\n2. Русский язык\n3. Литературное чтение" in last(out)
    out = await press(E, "sch_u_0"); assert "3. Литературное чтение" not in last(out)
    await press(E, "sched"); await press(E, "sch_text")
    out = await say(E, "Вт: русский, математика, физкультура\nСреда\n1. Окружающий мир\n2. Английский")
    t = last(out)
    assert "✅ Записал расписание! Пропустил уроки без заданий или незнакомые: физкультура." in t, t
    assert "Пн: Математика, Русский язык" in t and "Вт: Русский язык, Математика" in t and "Ср: Окружающий мир, Английский язык" in t
    # занятие по расписанию: в понедельник — уроки понедельника
    prog.now = lambda: _dt(2026, 11, 2, 11, 5, tzinfo=_tz.utc)                       # понедельник, 16:05
    await redis.delete_state(f"daily_plan_{E}")
    out = await say(E, "/today"); t = last(out)
    assert "Сегодня по расписанию: Математика, Русский язык" in t, t
    assert btns(out)[:2] == [("Математика", "lesson_ask_math"), ("Русский", "lesson_ask_rus")]
    assert ("🔁 Повторение", "plan_regular") in btns(out)
    out = await press(E, "lesson_ask_math"); assert "Какую тему сегодня проходили на уроке?" in last(out)
    now = programs.current_topics(4, "math", _dt(2026, 11, 2).date())
    assert now[0] == "Письменное сложение и вычитание многозначных чисел" and f"1. {now[0]}" in last(out), last(out)
    pick = next(p for txt, p in btns(out) if txt == "Дроби")                        # недавняя тема — после программы
    out = await press(E, pick); assert last(out).startswith("📘 Урок · Математика · Дроби")
    await press(E, "lesson_ask_math"); out = await press(E, "lesson_pick_0")
    assert last(out).startswith(f"📘 Урок · Математика · {now[0]}")
    await press(E, "lesson_ask_rus"); out = await say(E, "Падежи")
    assert last(out).startswith("📘 Урок · Русский язык · Падежи")
    out = await press(E, "plan_regular"); assert "📚 Задание на сегодня" in last(out)
    prog.now = real_now
    print("   Этап 1: входной тест в предмете, урок (объяснение → пробный → на оценку → оценка), расписание, занятия по нему: OK")

    # ---------- Этап 2: программа 1–4 классов и учебник ----------
    prog.now = lambda: _dt(2026, 11, 2, 11, 5, tzinfo=_tz.utc)                       # понедельник, начало 2 четверти
    B = 41
    await say(B, "/start"); await say(B, "Боря"); await press(B, "grade_4")
    now_math = programs.current_topics(4, "math", _dt(2026, 11, 2).date())[0]
    out = await press(B, "subj_math_4"); t = last(out)
    assert f"📚 Сейчас по программе: «{now_math}»" in t and "📗 Учебник: не выбран" in t, t
    assert ("📘 Урок по программе", "lesson_now_math") in btns(out)
    assert ("📚 Программа", "prog_math") in btns(out) and ("📗 Учебник", "tb_math") in btns(out)
    out = await press(B, "prog_math"); t = last(out)
    assert t.startswith("📚 Программа · Математика · 4 класс") and "1 четверть:" in t and "4 четверть:" in t
    assert f"👉 {now_math} — сейчас по программе" in t and "▫️ Деление на трёхзначное число" in t, t
    out = await press(B, "tb_math"); assert "• Петерсон (Учусь учиться)" in last(out)
    assert ("Моро", "tb_set_math_0") in btns(out) and ("Рудницкая", "tb_set_math_3") in btns(out)
    out = await press(B, "tb_set_math_0")
    assert "Запомнил: Математика — учебник «Моро (Школа России)»" in out[0]["text"] and "📗 Учебник: Моро (Школа России)" in last(out)
    await press(B, "tb_rus"); await press(B, "tb_other_rus"); out = await say(B, "канакина")
    assert json.loads(db.rows[B].textbooks) == {"math": "Моро (Школа России)", "rus": "Канакина, Горецкий (Школа России)"}
    await press(B, "tb_eng"); await press(B, "tb_other_eng"); await say(B, "Комарова")
    assert json.loads(db.rows[B].textbooks)["eng"] == "Комарова"                       # учебник не из списка — как написали
    # входной тест — по темам, пройденным по программе; задания и урок — с учётом учебника
    out = await press(B, "entry_math_4")
    passed = programs.passed_topics(4, "math", _dt(2026, 11, 2).date())
    assert calls["tests"][-1][5] == passed and passed[-1] == now_math and calls["test_textbook"][-1] == "Моро (Школа России)"
    await press(B, "st_stop")
    out = await press(B, "lesson_now_math"); assert last(out).startswith(f"📘 Урок · Математика · {now_math}")
    assert calls["explain_textbook"][-1] == "Моро (Школа России)"
    for key in [k for k in redis.d if k.startswith("bank:4:math")]: redis.d.pop(key)   # банк пуст — задание составит ИИ
    await press(B, "lesson_trial"); assert calls["textbook"][-1] == "Моро (Школа России)"
    out = await press(B, "subj_hist_5"); assert ("📚 Программа", "prog_hist") in btns(out)   # 5 класс — программа есть
    out = await press(B, "subj_chem_10"); assert ("📚 Программа", "prog_chem") in btns(out)      # программы есть для всех классов
    prog.now = real_now
    print("   Этап 2: программа 1–4 по четвертям, «сейчас по программе», учебник по каждому предмету, тест по пройденному: OK")

    # ---------- Этап 3: расписание фото, скриншотом или файлом ----------
    import io, zipfile
    def file_msg(uid, url, name, kind):
        return NormalizedMessage(chat_id=uid, user_id=uid, message_id="m", text=None, payload=None,
                                 file_url=url, file_name=name, file_kind=kind)
    async def send_file(uid, url, name, kind):
        n = len(api.sent); await UserHandler(api, db, redis).handle_message(file_msg(uid, url, name, kind)); return api.sent[n:]
    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40_000
    FakeApi.files["https://img/sched.jpg"] = JPEG
    F = 42
    await say(F, "/start"); await say(F, "Фая"); await press(F, "grade_4")
    out = await press(F, "sched"); assert ("📷 Фото, файл или текст", "sch_text") in btns(out)
    await press(F, "sch_text")
    out = await send_file(F, "https://img/sched.jpg", "photo.jpg", "image"); t = last(out)
    assert out[0]["text"].startswith("Смотрю расписание") and calls["ocr"][-1] == "image/jpeg"
    assert "📷 Вот что я понял:" in t and "Пн: Математика, Русский язык" in t and "Вт: Окружающий мир, Английский язык" in t, t
    assert "Пропустил уроки без заданий: музыка" in t and btns(out) == [("✅ Всё верно", "sch_ok"), ("✏️ Исправить", "sch_fix")]
    assert not db.rows[F].schedule                                                   # пока не подтвердили — не сохраняем
    out = await press(F, "sch_ok"); assert "✅ Записал расписание!" in last(out)
    assert json.loads(db.rows[F].schedule) == {"0": ["math", "rus"], "1": ["world", "eng"]}
    # фото без режима расписания — бот спрашивает, расписание ли это
    out = await send_file(F, "https://img/sched.jpg", "photo.jpg", "image")
    assert "Я получил фото" in last(out) and ("🗓 Это расписание", "sch_file") in btns(out)
    assert btns(out)[0] == ("📸 Задание — помоги", "hw_file")
    out = await press(F, "sch_file"); assert "📷 Вот что я понял:" in last(out)
    out = await press(F, "sch_fix"); assert "поправь нужный день" in last(out)
    # файл Word с таблицей: читаем без OCR
    doc = io.BytesIO()
    with zipfile.ZipFile(doc, "w") as z:
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        cell = lambda t: f"<w:tc><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>"
        rows = "".join(f"<w:tr>{cell(a)}{cell(b)}</w:tr>" for a, b in [("Пн", "Вт"), ("Математика", "Чтение")])
        z.writestr("word/document.xml", f"<w:document {W}><w:body><w:p><w:r><w:t>Расписание 4А</w:t></w:r></w:p>"
                                        f"<w:tbl>{rows}</w:tbl></w:body></w:document>")
    FakeApi.files["https://files/sched.docx"] = doc.getvalue()
    n_ocr = len(calls["ocr"])
    await press(F, "sched"); await press(F, "sch_text")
    out = await send_file(F, "https://files/sched.docx", "Расписание.docx", "document")
    assert len(calls["ocr"]) == n_ocr and "Расписание 4А\nПн | Вт\nМатематика | Чтение" in calls["sched_ai"][-1]
    assert "📷 Вот что я понял:" in last(out)
    # не получилось распознать — понятный ответ
    FakeAI.ocr_text = ""
    await press(F, "sched"); await press(F, "sch_text")
    out = await send_file(F, "https://img/sched.jpg", "photo.jpg", "image"); assert "Не получилось прочитать расписание" in last(out)
    FakeApi.files["https://img/tiny.webp"] = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 800
    await press(F, "sched"); await press(F, "sch_text")
    out = await send_file(F, "https://img/tiny.webp", "photo.jpg", "image")
    assert "Фото пришло ко мне совсем маленьким" in last(out) and "как файл" in last(out)      # превью — просим файлом
    FakeAI.ocr_text = "Привет, это фото кота"
    out = await send_file(F, "https://img/sched.jpg", "photo.jpg", "image"); assert "не нашёл в нём расписания" in last(out)
    print("   Этап 3: расписание фото/скриншотом (OCR) или файлом Word/Excel → «Вот что я понял» → подтверждение: OK")

    # ---------- Помощь с домашкой: фото или текст задания → подсказки без ответа ----------
    H = 43
    await say(H, "/start"); await say(H, "Гена"); out = await press(H, "grade_5")
    assert ("📸 Домашка", "hw") in btns(out)
    out = await press(H, "hw"); assert "Сфотографируй задание" in last(out)
    FakeAI.ocr_text = "Упр. 5. Найди 3/4 от числа 20"
    out = await send_file(H, "https://img/sched.jpg", "photo.jpg", "image"); t = last(out)
    assert out[0]["text"].startswith("Читаю задание") and calls["hw"][-1] == ("Упр. 5. Найди 3/4 от числа 20", 5)
    assert "📸 Задание с фото" in t and "Найди 3/4 от числа 20." in t and "15" not in t, t        # ответ не показываем
    assert redis.d[f"ref_answer_{H}"] == "15" and json.loads(redis.d[f"task_ctx_{H}"])["subject_id"] == "math"
    out = await say(H, "12"); assert last(out) == "hint"                                    # неверно — подсказка
    out = await say(H, "15"); assert last(out).startswith(PRAISE_STARTS)
    # несколько заданий на фото — выбор
    await press(H, "hw"); FakeAI.ocr_text = "№1 Найди 3/4 от 20. №2 Подчеркни главные члены"
    out = await send_file(H, "https://img/sched.jpg", "photo.jpg", "image")
    assert "Я нашёл несколько заданий" in last(out) and btns(out)[0] == [("Задание 1", "hw_pick_0"), ("Задание 2", "hw_pick_1")][0]
    out = await press(H, "hw_pick_1"); assert "Подчеркни главные члены" in last(out)
    assert json.loads(redis.d[f"task_ctx_{H}"])["subject_id"] == "rus"
    # задание текстом и фото без режима
    await press(H, "hw"); out = await say(H, "Найди 3/4 от 20"); assert "📸 Задание с фото" in last(out)
    FakeAI.ocr_text = "Найди 3/4 от 20"
    out = await send_file(H, "https://img/sched.jpg", "photo.jpg", "image"); out = await press(H, "hw_file")
    assert "📸 Задание с фото" in last(out)
    await press(H, "hw"); FakeAI.ocr_text = "фото кота"
    out = await send_file(H, "https://img/sched.jpg", "photo.jpg", "image"); assert "не нашёл здесь задания" in last(out)
    print("   Помощь с домашкой: фото или текст → условие → подсказки без ответа, несколько заданий — выбор: OK")

    # ---------- Режим для родителя: код, отчёт, расписание за ребёнка ----------
    out = await press(E, "parent_code"); code = redis.d[f"pcode_of_{E}"]
    assert f"Код: {code}" in last(out) and f"/parent {code}" in last(out) and "Пока никто не подключён" in last(out)
    PA = 50
    out = await say(PA, "/start")
    assert btns(out) == [("🎒 Я ученик", "role_student"), ("👨‍👩‍👧 Я родитель", "parent_start"), ("👩‍🏫 Я учитель", "role_teacher")]
    out = await press(PA, "parent_start"); assert "введите код из 6 цифр" in last(out)
    out = await say(PA, "111111"); assert "Код не подошёл" in last(out)
    n = len(api.sent); out = await say(PA, code)
    assert "✅ Готово! Вы подключены к занятиям: Ева." in last(out) and ("📊 Отчёт · Ева", f"parent_rep_{E}") in btns(out)
    assert any("подключился родитель" in m["text"] for m in api.sent[n:])               # ребёнок узнаёт о подключении
    assert json.loads(db.rows[E].parents)[0]["user_id"] == PA and not db.rows[PA].grade     # родитель — не ученик
    out = await say(PA, "/start"); assert "👨‍👩‍👧 Родительский раздел" in last(out)
    redis.d.pop(f"children_{PA}", None)                                                    # Redis очищен — найдём по базе
    out = await say(PA, "/start"); assert "👨‍👩‍👧 Родительский раздел" in last(out)
    out = await press(PA, f"parent_rep_{E}"); t = last(out)
    assert t.startswith("📊 Ева, 4 класс — неделя") and "📒 Оценки за уроки:" in t and "«Дроби» — 4" in t, t
    out = await press(PA, f"parent_sch_{E}"); assert "🗓 Расписание: Ева" in last(out)
    out = await say(PA, "Чт: математика, физкультура"); assert "Вот что я понял:" in last(out) and "Чт: Математика" in last(out)
    n = len(api.sent); out = await press(PA, "parent_sch_ok")
    assert "✅ Расписание сохранено." in last(out) and json.loads(db.rows[E].schedule)["3"] == ["math"]
    assert any("Родитель внёс твоё расписание" in m["text"] for m in api.sent[n:])
    out = await press(51, f"parent_rep_{E}"); assert "введите код" in last(out)          # чужой отчёт не покажем
    # недельный отчёт по воскресеньям
    import app.services.daily as dr
    sunday = _dt(2026, 11, 8, 13, 30, tzinfo=_tz.utc)                                  # 18:30 по Екатеринбургу
    prof = {"parents": db.rows[E].parents, "tz_offset": 300, "last_parent_report": None}
    assert dr.parent_report_due(prof, sunday) and not dr.parent_report_due({**prof, "parents": "[]"}, sunday)
    assert not dr.parent_report_due(prof, _dt(2026, 11, 7, 13, 30, tzinfo=_tz.utc))        # суббота
    assert not dr.parent_report_due({**prof, "last_parent_report": "2026-11-08"}, sunday)
    n = len(api.sent); await UserHandler(api, db, redis).run_scheduled(msg(E), "parent_report")
    assert any(m["text"].startswith("📊 Ева") for m in api.sent[n:]) and db.rows[E].last_parent_report
    out = await say(ADMIN, "/demo_parent"); assert last(out).startswith("📊 ")
    # ребёнок может отключить родителя
    out = await press(E, "parent_code"); t = last(out)
    assert "Сейчас подключено родителей: 1" in t and "1. Родитель — подключён" in t, t
    assert ("🔕 Отключить 1", f"parent_unlink_{PA}") in btns(out) and not any(p == "parent_unlink_all" for _, p in btns(out))
    # второй родитель: у ребёнка — кнопка у каждого и «Отключить всех»
    PB = 52
    await press(E, "parent_code"); code = redis.d[f"pcode_of_{E}"]
    await say(PB, "/parent " + code)
    out = await press(E, "parent_code")
    assert "Сейчас подключено родителей: 2" in last(out) and ("🔕 Отключить всех", "parent_unlink_all") in btns(out)
    # ребёнок отключает одного (второй остаётся), родитель узнаёт об этом
    n = len(api.sent); out = await press(E, f"parent_unlink_{PB}")
    assert [p["user_id"] for p in json.loads(db.rows[E].parents)] == [PA] and any("Родитель отключён" in m["text"] for m in out)
    assert any(m["text"].endswith("отключил(а) ваш родительский доступ.") for m in api.sent[n:])
    out = await say(PB, "/parent"); assert "введите код" in last(out)
    # родитель сам отключается от ребёнка (с подтверждением)
    out = await press(PA, "parent_menu"); assert ("🚫 Отключить · Ева", f"parent_leave_{E}") in btns(out)
    out = await press(PA, f"parent_leave_{E}"); assert "Отключиться от занятий: Ева?" in last(out)
    assert json.loads(db.rows[E].parents)                                                  # без подтверждения — не отключаем
    n = len(api.sent); out = await press(PA, f"parent_leave_yes_{E}")
    assert json.loads(db.rows[E].parents) == [] and "Вы отключились от занятий: Ева" in last(out)
    assert any("Родитель отключился" in m["text"] for m in api.sent[n:])
    out = await press(51, f"parent_leave_yes_{E}"); assert "введите код" in last(out)       # чужой не отключит
    # «Отключить всех»
    await press(E, "parent_code"); code = redis.d[f"pcode_of_{E}"]; await say(PA, "/parent " + code)
    out = await press(E, "parent_unlink_all"); assert json.loads(db.rows[E].parents) == []
    out = await say(PA, "/parent"); assert "введите код" in last(out)
    # ребёнок удалил данные и зарегистрировался заново — у родителя он больше не появляется
    K, PK = 53, 54
    await say(K, "/start"); await say(K, "Коля"); await press(K, "grade_3")
    await press(K, "parent_code"); await say(PK, "/parent " + redis.d[f"pcode_of_{K}"])
    out = await say(PK, "/start"); assert "Коля" in last(out)
    n = len(api.sent); await press(K, "delete_me_yes")
    assert any("удалил(а) свои данные" in m["text"] for m in api.sent[n:])
    await say(K, "/start"); await say(K, "Новенький"); await press(K, "grade_5")
    out = await say(PK, "/start"); assert "Новенький" not in last(out) and "Родительский раздел" not in last(out), last(out)
    # даже если в Redis остался старый список детей — проверяем по профилю ребёнка
    await redis.set_state(f"children_{PK}", json.dumps([K]))
    out = await say(PK, "/parent"); assert "введите код" in last(out)
    # родитель удалил свои данные — у ребёнка он отключается
    await press(K, "parent_code"); await say(PK, "/parent " + redis.d[f"pcode_of_{K}"])
    await press(PK, "delete_me_yes"); assert json.loads(db.rows[K].parents) == []
    print("   Родитель: код на сутки, отчёт за неделю (и по воскресеньям сам), расписание за ребёнка, отключение: OK")

    # ---------- Демо-ученик для презентации ----------
    assert "только администратору" in last(await say(E, "/demo_student"))
    out = await say(ADMIN, "/demo_student 7"); assert "⚠️ Твои настоящие прогресс" in last(out)
    assert btns(out)[0] == ("✅ Да, заполнить", "demo_yes_7")
    out = await press(ADMIN, "demo_yes_7"); assert "🎭 Готово!" in last(out) and ("📚 Предметы", "subjlist_7") in btns(out)
    p1 = db.rows[ADMIN]
    assert p1.grade == 7 and len(json.loads(p1.lesson_marks)) == 8 and json.loads(p1.schedule)["0"][:2] == ["math", "rus"]
    assert len(json.loads(p1.diagnostic_scores)) == len(SUBJECTS[7]) and json.loads(p1.textbooks)["math"] == "Макарычев"
    out = await press(ADMIN, "progress_7"); t = last(out)
    assert "📚 Темы:" in t and "📒 Оценки за уроки:" in t and "Закреплено слабых тем:" in t, t
    out = await press(ADMIN, "rewards_7"); assert "✅ 🌱 Первый шаг" in last(out) and "✅ 🧠 10 заданий" in last(out)
    out = await say(ADMIN, "/demo_parent"); assert "📒 Оценки за уроки:" in last(out) and "🎯 Стоит подтянуть:" in last(out)
    out = await press(ADMIN, "subjects_7"); assert status(out).startswith("Тренер 💪 · "), status(out)
    out = await press(51, "demo_yes_7"); assert 51 not in db.rows or not db.rows[51].lesson_marks   # не админ — не заполняем
    print("   Демо-ученик: история за 2 недели (темы, оценки, награды, серия, расписание) — только администратору: OK")

    # ---------- Свой банк заданий, подготовка заранее, лимит ИИ ----------
    X1, X2, X3 = 60, 61, 62
    for u, nm in ((X1, "Ксюша"), (X2, "Яша"), (X3, "Юра")):
        await say(u, "/start"); await say(u, nm); await press(u, "grade_10")
    n = len(calls["tests"])
    out = await press(X1, "subjtest_phys_10"); assert len(calls["tests"]) == n + 1      # банк пуст — составляет ИИ
    out = await press(X2, "subjtest_phys_10")
    assert len(calls["tests"]) == n + 1 and not any("Составляю" in m["text"] for m in out)   # из банка — сразу
    assert last(out).startswith("Вопрос 1 из 7")
    out = await press(X2, "subjtest_phys_10"); assert len(calls["tests"]) == n + 2      # своё уже видел — новый тест
    # объяснение темы тоже кешируется
    k = len(calls["explain"])
    await press(X1, "subj_chem_10"); await say(X1, "Алканы")
    await press(X2, "subj_chem_10"); out = await say(X2, "алканы")
    assert len(calls["explain"]) == k + 1 and "ОБЪЯСНЕНИЕ Алканы" in last(out)
    # лимит «тяжёлых» запросов к ИИ в день
    settings.AI_DAILY_LIMIT = 2
    await press(X3, "subjtest_bio_10"); await press(X3, "subjtest_geo_10")
    out = await press(X3, "subjtest_inf_10"); assert "На сегодня новых заданий от меня хватит" in last(out)
    assert ("🔁 Повторение", "plan_regular") in btns(out)
    out = await press(X3, "subjtest_soc_10"); assert "хватит" in last(out)
    await press(X3, "subj_hist_10"); out = await say(X3, "Холодная война"); assert "хватит" in last(out)
    await press(X2, "subjtest_soc_10"); out = await press(X3, "subjtest_soc_10")       # из банка — можно и после лимита
    assert last(out).startswith("Вопрос 1 из 7"), last(out)
    out = await say(ADMIN, "/stats"); t = last(out)
    assert "🤖 Запросов к ИИ сегодня" in t and "лимит на ученика: 2 в день" in t and "📚 В своём банке проверенных заданий:" in t
    settings.AI_DAILY_LIMIT = 0
    print("   Свой банк: тест второму ученику — сразу из банка, объяснения кешируются, лимит ИИ в день, /stats: OK")

    # ---------- Часовой пояс: город словами или кнопка со временем ----------
    AUTO_TZ = False
    TZ = 33
    await say(TZ, "/start"); await say(TZ, "Тимур")
    out = await press(TZ, "grade_5"); t = last(out)
    assert "напиши свой город" in t.lower() or "Напиши свой город" in t, t
    labels = [x for x, _ in btns(out)]
    assert any(x.endswith("Москва") for x in labels) and ("🤷 Не знаю", "tz_x_g5") in btns(out)
    assert any(p == "tz_3_g5" for _, p in btns(out)) and any(p == "tz_12_g5" for _, p in btns(out))
    out = await say(TZ, "деревня Простоквашино"); assert "не узнал такой город" in last(out)
    out = await say(TZ, "я из Казани")
    assert out[0]["text"].startswith("Запомнил: Москва (UTC+3)") and "входной тест" in last(out), [m["text"][:80] for m in out]
    assert db.rows[TZ].tz_offset == 180 and "awaiting_tz_33" not in redis.d
    out = await press(TZ, "grade_5"); assert not any(p.startswith("tz_") for _, p in btns(out))   # второй раз не спрашиваем
    out = await press(TZ, "notify_settings"); assert "Часовой пояс: Москва (UTC+3)" in last(out) and ("🕐 Часовой пояс", "tzset") in btns(out)
    out = await press(TZ, "tzset"); out = await press(TZ, "tz_7_n")
    assert db.rows[TZ].tz_offset == 420 and "Часовой пояс: Новосибирск (UTC+7)" in last(out)
    out = await press(TZ, "tzset"); out = await say(TZ, "Санкт-Петербург"); assert db.rows[TZ].tz_offset == 180
    TZ2 = 34
    await say(TZ2, "/start"); await say(TZ2, "Ира"); await press(TZ2, "grade_4")
    out = await press(TZ2, "tz_x_g4"); assert "Пока считаю" in out[0]["text"] and db.rows[TZ2].tz_offset == 300
    assert "входной тест" in last(out)                                                 # дальше — меню
    AUTO_TZ = True
    print("   Часовой пояс: спрашиваем после выбора класса — город словами или кнопка со временем, смена в настройках: OK")

    # ---------- Пятница: мини-тест недели вместо обычного плана ----------
    from datetime import datetime as _dt, timezone as _tz
    real_now = prog.now
    prog.now = lambda: _dt(2026, 11, 6, 11, 5, tzinfo=_tz.utc)                       # пятница, 16:05 по Екатеринбургу
    out = await say(G, "/today"); t = last(out)
    assert "Сегодня пятница — время мини-теста недели" in t and "«Математика»" in t, t
    assert btns(out) == [("🎯 Начать мини-тест", "weektest"), ("📅 Обычные задания", "plan_regular"), ("⏰ Позже", "subjects_6")]
    out = await press(G, "plan_regular"); assert "📚 Задание на сегодня" in last(out)   # можно выбрать обычный план
    await redis.delete_state(f"daily_plan_{G}")
    out = await say(G, "/today"); out = await press(G, "weektest")
    assert "🎯 Мини-тест недели" in out[2]["text"] and calls["tests"][-1][5] and "Тема 1" in calls["tests"][-1][5]
    for _ in range(7): out = await press(G, test_option(out, "B2"))
    assert "Тест готов! Математика: 7 из 7." in out[-1]["text"] and "Всё по плану" not in "" and f"weektest_{G}" not in redis.d
    prog.now = lambda: _dt(2026, 11, 4, 11, 5, tzinfo=_tz.utc)                       # среда — обычный план
    await redis.delete_state(f"daily_plan_{G}")
    out = await say(G, "/today"); assert "📚 Задание на сегодня" in last(out)
    prog.now = real_now
    # /demo_friday — мини-тест недели в любой день (для презентации), только администратору
    assert "только администратору" in last(await say(G, "/demo_friday"))
    await say(ADMIN, "/start")
    out = await say(ADMIN, "/demo_friday"); t = last(out)
    assert "время мини-теста недели" in t and btns(out)[0] == ("🎯 Начать мини-тест", "weektest"), t
    out = await press(ADMIN, "weektest"); assert "🎯 Мини-тест недели" in out[2]["text"] and last(out).startswith("Вопрос 1 из")
    await press(ADMIN, "st_stop")
    print("   Пятница: мини-тест недели по темам недели (или обычный план на выбор), в среду — обычный план: OK")

    # 16. Обход всех кнопок меню (кроме diag_*) — каждая отвечает
    START = [f"grade_{g}" for g in range(1, 12)]
    fallbacks.clear(); seen = set(); queue = list(START)
    while queue:
        p = queue.pop(0)
        if p in seen or p.startswith("diag_") or p.startswith("grade_") and p not in START:
            seen.add(p); continue
        seen.add(p); out = await press(1, p); assert out, p
        queue += [q for _, q in btns(out) if q not in seen and not q.startswith(("ans_", "sta_", "st_fix_", "explain"))]
    assert not fallbacks, fallbacks
    print(f"12. Обход кнопок: {len(seen)} кнопок, все отвечают: OK")
    assert not WIDE, sorted({tuple(r) for r in WIDE})
    if os.environ.get("DUMP_KB"):                    # DUMP_KB=1 — показать все ряды кнопок, которые видел бот
        for row in sorted({tuple(b["text"] for b in r) for m in api.sent for r in (m["keyboard"] or {}).get("buttons", [])}):
            print("   KB", " | ".join(row))
    # Длинные варианты ответа: на кнопке — сокращённо, в сообщении — полностью
    from app.handlers.tutor.common import _option_rows, _options_text, _short, _fit_rows, _btn
    long_opts = ["потому что весной тает снег и реки разливаются", "из-за дождей", "из-за ветра", "из-за морозов"]
    rows = _option_rows(long_opts)
    assert [[b["text"] for b in r] for r in rows] == [["1️⃣", "2️⃣", "3️⃣", "4️⃣"]], rows      # длинные — только номера
    assert "1️⃣ потому что весной тает снег и реки разливаются" in _options_text(long_opts)
    rus8 = ["К счастью, дождь закончился", "Дождь, к счастью закончился", "К счастью дождь, закончился"]
    assert [b["text"] for b in _option_rows(rus8)[0]] == ["1️⃣", "2️⃣", "3️⃣"] and "1️⃣ К счастью, дождь закончился" in _options_text(rus8)
    assert _options_text(["12", "15", "18"]) == "" and len(_option_rows(["12", "15", "18"])) == 1
    assert [[b["text"] for b in r] for r in _option_rows(["арктическая пустыня", "тундра", "тайга"])] == [["1️⃣", "2️⃣", "3️⃣"]]
    assert [[b["text"] for b in r] for r in _option_rows(["Past Simple", "Present Simple", "Future"])] == \
        [["1️⃣ Past Simple"], ["2️⃣ Present Simple"], ["3️⃣ Future"]]
    assert _short("Литературное чтение", 28) == "Литературное чтение" and _short("а " * 40, 10).endswith("…")
    subj = _fit_rows([_btn(n, n) for n in ["Математика", "Русский язык", "Литературное чтение", "Окружающий мир"]], max_per_row=2)
    assert [[b["text"] for b in r] for r in subj] == [["Математика", "Русский язык"], ["Литературное чтение"], ["Окружающий мир"]], subj
    from app.handlers.tutor.common import _options_voice
    assert _options_voice(["жираф", "жыраф"]).startswith("Посмотри на варианты на экране")      # вслух звучат одинаково
    assert _options_voice(["солнце", "сонце"]).startswith("Посмотри") and _options_voice(["собака", "сабака", "сабако"]).startswith("Посмотри")
    assert _options_voice(["Заяц", "Волк", "Лиса"]) == "Варианты ответа: первый — Заяц; второй — Волк; третий — Лиса."
    assert _options_voice(["москва", "город", "улица"]).startswith("Варианты ответа")
    assert _options_voice(["5050", "6050", "6150"]).startswith("Варианты ответа")                # числа читаем как обычно
    print("   Кнопки: текст влезает целиком; длинные варианты — на кнопках номера, сами варианты — в сообщении: OK")

def test_progress_rules():
    from datetime import datetime, timedelta, timezone
    import app.services.progress as prog
    t0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    p = {}
    prog.mark_diagnostic(p, 4, [
        {"subject_id": "math", "subject": "Математика", "topic": "Дроби", "status": "неверно"},
        {"subject_id": "math", "subject": "Математика", "topic": "Дроби", "status": "верно"},
        {"subject_id": "rus", "subject": "Русский язык", "topic": "Падежи", "status": "верно"},
    ], t0)
    assert p[prog.topic_key(4, "math", "Дроби")]["status"] == "weak"          # хоть одна ошибка по теме — слабая
    assert p[prog.topic_key(4, "rus", "падежи")]["status"] == "known"
    assert [e["topic"] for e in prog.due_topics(p, 4, moment=t0)] == ["Дроби"]
    e = prog.record_answer(p, 4, "math", "Математика", "Дроби", "correct", t0)
    assert (e["status"], e["box"]) == ("learning", 1) and prog.due_topics(p, 4, "math", t0 + timedelta(days=2)) == []
    assert prog.due_topics(p, 4, "math", t0 + timedelta(days=3, seconds=1))
    prog.record_answer(p, 4, "math", "Математика", "Дроби", "hinted", t0)
    assert p[prog.topic_key(4, "math", "Дроби")]["box"] == 1                  # верно после подсказок — уровень не растёт
    for _ in range(2): prog.record_answer(p, 4, "math", "Математика", "Дроби", "correct", t0)
    assert p[prog.topic_key(4, "math", "Дроби")]["status"] == "mastered"
    prog.record_answer(p, 4, "math", "Математика", "Дроби", "wrong", t0)
    e = p[prog.topic_key(4, "math", "Дроби")]; assert (e["status"], e["box"]) == ("learning", 0)
    s = prog.summary(p, 4, t0); assert s["was_weak"] == 1 and s["total"] == 2
    print("   Правила интервального повторения: OK")


def test_ai_task_parsing():
    import importlib
    sys.modules.pop("app.services.ai_client", None)
    real = types.ModuleType("aiohttp"); sys.modules.setdefault("aiohttp", real)
    sys.modules["app.core.config"].settings.YANDEX_API_KEY = "k"
    sys.modules["app.core.config"].settings.YANDEX_FOLDER_ID = "f"
    sys.modules["app.core.config"].settings.YANDEX_GPT_MODEL = "m"
    ai = importlib.import_module("app.services.ai_client")
    assert isinstance(ai.ai_client, ai.AIClient)          # бот импортирует готовый экземпляр ai_client
    client = ai.AIClient()
    async def run(raw, opts):
        async def fake(*a, **k): return raw
        client._request = fake
        return await client.generate_task(3, "Математика", None, with_options=opts)
    r = asyncio.run(run("ЗАДАНИЕ: 3 + 4 = ?\nВАРИАНТЫ: 6 | 7 | 8\nОТВЕТ: 7\nТЕМА: Сложение", True))
    assert r == ("3 + 4 = ?", "7", {"topic": "Сложение", "options": ["6", "7", "8"]}), r
    r = asyncio.run(run("ЗАДАНИЕ: 3 + 4 = ?\nВАРИАНТЫ: 6 | 8 | 9\nОТВЕТ: 7\nТЕМА: Сложение", True))
    assert r[2]["options"] == []                                              # верного нет среди вариантов — без кнопок
    r = asyncio.run(run("ЗАДАНИЕ: Решите уравнение 2x = 10.\nОТВЕТ: 5\nТЕМА: Уравнения", False))
    assert r == ("Решите уравнение 2x = 10.", "5", {"topic": "Уравнения", "options": []})
    # «Окружающий мир» 1 класса: в запросе — ориентир по окружающему миру, а не по математике; арифметику отбраковываем
    prompts = []
    replies = iter(["ЗАДАНИЕ: Сколько будет 7 + 5?\nВАРИАНТЫ: 11 | 12 | 13\nОТВЕТ: 12\nТЕМА: Сложение",
                    "ЗАДАНИЕ: Какое время года наступает после зимы?\nВАРИАНТЫ: лето | весна | осень\nОТВЕТ: весна\nТЕМА: Времена года"])
    async def fake(system_prompt, *a, **k):
        if "ИТОГ" in system_prompt:     # повторное решение задания для проверки ответа
            return "Решаю по шагам... ИТОГ: " + {"Какое": "весна", "Как п": "cat"}.get(k.get("user_prompt", "")[:5], "?")
        prompts.append(system_prompt); return next(replies)
    client._request = fake
    r = asyncio.run(client.generate_task(1, "Окружающий мир", None, with_options=True))
    assert r[0] == "Какое время года наступает после зимы?" and r[2]["topic"] == "Времена года", r
    assert "времена года" in prompts[0] and "числа в пределах 20" not in prompts[0] and "именно по предмету «Окружающий мир»" in prompts[0]
    assert "задачей на вычисления" in prompts[1]
    # Английский во 2 классе: условие по-русски; задание целиком на английском отбраковывается
    prompts.clear()
    replies = iter(["ЗАДАНИЕ: Read the sentence and choose the correct word: My cat is very big and it likes milk.\n"
                    "ВАРИАНТЫ: cat | dog | cow\nОТВЕТ: cat\nТЕМА: Animals",
                    "ЗАДАНИЕ: Как по-английски «кошка»?\nВАРИАНТЫ: dog | cat | cow\nОТВЕТ: cat\nТЕМА: Животные"])
    r = asyncio.run(client.generate_task(2, "Английский язык", None, with_options=True))
    assert r[0] == "Как по-английски «кошка»?" and r[2]["options"] == ["dog", "cat", "cow"], r
    assert "ПО-РУССКИ" in prompts[0] and "почти целиком на английском" in prompts[1]
    assert ai.too_much_english("Вставь пропущенное слово: I ... a cat", "Английский язык", 2) is False
    assert ai.too_much_english("Read the text. Tom has got a big red ball and a small dog.", "Английский язык", 3) is True
    assert ai.too_much_english("Read the text. Tom has got a big red ball and a small dog.", "Английский язык", 9) is False
    # Проверка ответа повторным решением: эталон не подтвердился — задание выбрасывается и просится новое
    prompts.clear()
    replies = iter(["ЗАДАНИЕ: Поезд прошёл 240 км за 3 ч. Найди скорость.\nОТВЕТ: 70\nТЕМА: Движение",
                    "ЗАДАНИЕ: Поезд прошёл 180 км за 2 ч. Найди скорость.\nОТВЕТ: 90\nТЕМА: Движение"])
    async def fake2(system_prompt, *a, **k):
        if "ИТОГ" in system_prompt:
            return "240 : 3 = 80\nИТОГ: 80" if "240" in k.get("user_prompt", "") else "180 : 2 = 90\nИТОГ: 90 км/ч"
        if "строгий и точный проверяющий" in system_prompt:
            return "ВЕРНО" if "«90 км/ч»" in k.get("user_prompt", "") else "НЕВЕРНО"   # «90 км/ч» и «90» — один ответ
        prompts.append(system_prompt); return next(replies)
    client._request = fake2
    r = asyncio.run(client.generate_task(5, "Математика", "Движение", level=3))
    assert r[0].startswith("Поезд прошёл 180") and r[1] == "90", r
    assert "неверным или неоднозначным" in prompts[1] and "ВЫСОКИЙ" in prompts[0]
    # Тест: разбор, отбраковка плохих вопросов и вопросов, ответ на которые не подтвердился
    assert ai.resolve_option("Б", ["10", "20", "30"]) == "20" and ai.resolve_option("В) 30", ["10", "20", "30"]) == "30"
    assert ai.resolve_option("2", ["1", "2", "3"]) == "2" and ai.resolve_option("40", ["10", "20"]) is None
    raw_test = (
        "ВОПРОС 1: Сколько минут в 2 часах 15 минутах?\nВАРИАНТЫ: 135 | 215 | 125 | 145\nОТВЕТ: 135\nТЕМА: Время\n\n"
        "ВОПРОС 2: Посмотри на рисунок и найди площадь фигуры.\nВАРИАНТЫ: 12 | 14 | 16 | 18\nОТВЕТ: 12\nТЕМА: Площадь\n\n"
        "ВОПРОС 3: Чему равна 1/4 от 96?\nВАРИАНТЫ: А) 24 | Б) 32 | В) 48 | Г) 12\nОТВЕТ: А\nТЕМА: Доли\n\n"
        "ВОПРОС 4: Какое число делится на 9?\nВАРИАНТЫ: 333 | 334 | 335 | 336\nОТВЕТ: 334\nТЕМА: Делимость\n\n"
        "ВОПРОС 5: Сколько будет 7 · 8?\nВАРИАНТЫ: 54 | 56 | 58\nОТВЕТ: 64\nТЕМА: Умножение"
    )
    async def fake3(system_prompt, *a, **k):
        if "Реши каждый вопрос теста" in system_prompt:
            return "1. 120 + 15\nОТВЕТ 1: 135\nОТВЕТ 2: 24\nОТВЕТ 3: 333"   # вопрос 4 у ИИ был с ошибкой
        return raw_test
    client._request = fake3
    parsed = ai.parse_test_reply(raw_test)
    assert len(parsed) == 5 and parsed[2]["options"] == ["24", "32", "48", "12"] and parsed[2]["answer"] == "24"
    good = asyncio.run(client.generate_test(4, "Математика", 5, 4))
    assert [q["topic"] for q in good] == ["Время", "Доли", "Делимость"], good   # рисунок и «нет верного» — отброшены
    kept = asyncio.run(client.verify_test(good))
    assert [q["topic"] for q in kept] == ["Время", "Доли"], kept                 # 334 не делится на 9 — отброшен
    stages = []
    async def on_progress(stage, done, total): stages.append((stage, done, total))
    built = asyncio.run(client.build_test(4, "Математика", 2, 4, on_progress=on_progress))
    assert stages[0] == ("generated", 3, 2) and stages[-1] == ("verified", 2, 2) and len(built) == 2, (stages, built)
    async def fake4(system_prompt, *a, **k):
        return ("ЗАДАНИЕ 1: Найди 3/4 от числа 20.\nПРЕДМЕТ: Математика\nТЕМА: Дроби\n\n"
                "ЗАДАНИЕ 2: Реши уравнение 2x + 3 = 11.\nПРЕДМЕТ: Математика\nТЕМА: Уравнения")
    client._request = fake4
    hw = asyncio.run(client.homework_tasks("упр 5 ...", 5, ["Математика", "Русский язык"]))
    assert hw == [{"text": "Найди 3/4 от числа 20.", "subject": "Математика", "topic": "Дроби"},
                  {"text": "Реши уравнение 2x + 3 = 11.", "subject": "Математика", "topic": "Уравнения"}], hw
    async def fake5(system_prompt, *a, **k): return "НЕТ"
    client._request = fake5
    assert asyncio.run(client.homework_tasks("кот", 5, ["Математика"])) == []
    sys.modules.pop("app.services.ai_client", None)
    print("   Задания от ИИ перепроверяются повторным решением, тест: разбор, отбраковка, проверка ответов: OK")
    print("   Разбор задания от ИИ (варианты, ответ, тема), задание строго по своему предмету: OK")
    print("   Английский во 2–4 классах: условие по-русски, английский — только проверяемые слова: OK")


def test_daily_rules():
    from datetime import date, datetime, timezone
    import app.services.daily as d
    assert [d.tasks_per_day(g) for g in (1, 4, 5, 9, 10, 11)] == [3, 3, 5, 5, 6, 6]
    assert d.school_days_between(date(2026, 10, 30), date(2026, 11, 3)) == 1      # пт → вт: только пн
    assert d.update_streak("2026-10-30", 4, date(2026, 11, 2)) == 5                  # пт → пн: серия не прервалась
    assert d.update_streak("2026-10-28", 4, date(2026, 11, 2)) == 1
    assert d.update_streak("2026-11-02", 5, date(2026, 11, 2)) == 5
    p = {"grade": 4, "daily_time": "16:00", "report_time": "19:30", "tz_offset": 300}
    utc = lambda hh, mm, day=2: datetime(2026, 11, day, hh - 5, mm, tzinfo=timezone.utc)
    assert d.plan_actions(p, utc(16, 0)) == ["daily"]
    assert d.plan_actions(p, utc(20, 0)) == []                                     # время задания прошло — не догоняем вечером
    assert d.plan_actions({**p, "last_daily_date": "2026-11-02"}, utc(19, 30)) == ["report"]
    assert d.plan_actions({**p, "last_daily_date": "2026-11-02", "last_report_date": "2026-11-02"}, utc(20, 0)) == []
    assert d.plan_actions({**p, "notify_enabled": False}, utc(16, 0)) == []
    assert d.plan_actions(p, utc(22, 0)) == [] and d.plan_actions(p, utc(16, 0, day=8)) == []   # поздно / воскресенье
    progress = {"4|math|дроби": {"grade": 4, "subject_id": "math", "subject": "Математика", "topic": "Дроби",
                                 "status": "weak", "box": 0, "next_review": "2026-01-01T00:00:00+00:00"},
                "4|math|углы": {"grade": 4, "subject_id": "math", "subject": "Математика", "topic": "Углы",
                                "status": "learning", "box": 1, "next_review": "2026-01-01T00:00:00+00:00"},
                "4|math|доли": {"grade": 4, "subject_id": "math", "subject": "Математика", "topic": "Доли",
                                "status": "weak", "box": 0, "next_review": "2026-01-01T00:00:00+00:00"}}
    import random
    plan = d.build_daily_plan(progress, 4, ["math", "rus", "read", "world", "eng"], 3, random.Random(1))
    assert [(i["subject_id"], i["reason"]) for i in plan][:2] == [("math", "weak"), ("math", "weak")]
    assert plan[2]["subject_id"] != "math" and plan[2]["reason"] == "new"            # не больше 2 заданий одного предмета
    plan = d.build_daily_plan({}, 7, ["math", "rus"], 5, random.Random(1))
    assert len(plan) == 4 and all(i["reason"] == "new" for i in plan)               # всего 2 предмета × 2
    print("   Правила расписания и личного плана: OK")


def test_rewards_rules():
    from datetime import date
    import app.services.rewards as r
    assert [r.mode_for_grade(g) for g in (1, 4, 5, 9, 10, 11)] == ["play", "play", "coach", "coach", "strategy", "strategy"]
    assert r.level(0, "play") == ("Звёздочка ✨", 20) and r.level(55, "play") == ("Комета ☄️", 45)
    assert r.level(250, "coach") == ("Эксперт", None) and r.level(10, "strategy") == (None, None)
    rewards = {}
    assert r.add_points(rewards, "first_try") == 2 and r.add_points(rewards, "hinted") == 1
    assert rewards == {"points": 3, "solved": 2, "first_try": 1}
    assert r.new_achievements(rewards, {"streak": 3}) == ["first_task", "streak_3"]
    assert r.new_achievements(rewards, {"streak": 3}) == []                     # повторно не выдаются
    act = {"2026-11-02": {"solved": 3, "first_try": 2}, "2026-11-04": {"solved": 1, "first_try": 1},
           "2026-10-30": {"solved": 5, "first_try": 5}}                          # прошлая неделя не считается
    assert r.week_stats(act, date(2026, 11, 5)) == {"solved": 4, "first_try": 3, "days": 2}
    assert r.status_line({}, "play", 0, {"solved": 0, "first_try": 0, "days": 0}) == ""
    print("   Правила наград и уровней: OK")


def test_diagnostic_files():
    """Все data/diagnostic_*.csv: каждый предмет класса — 3 уровня, у 1–4 классов варианты, ничего не отброшено."""
    import csv, glob, io, re
    from app.services.task_bank import TaskBank
    from app.services.task_utils import needs_picture
    data_dir = os.path.join(PROJECT, "data")
    bank = TaskBank(path=os.path.join(data_dir, "tasks.json"), csv_path=os.path.join(data_dir, "tasks.csv"))
    bank.load()
    files = sorted(glob.glob(os.path.join(data_dir, "diagnostic_*.csv")),
                   key=lambda f: int(re.search(r"diagnostic_(\d+)\.csv$", f).group(1)))
    assert files, "нет файлов диагностики"
    checked = []
    for f in files:
        grade = int(re.search(r"diagnostic_(\d+)\.csv$", f).group(1))
        rows = list(csv.DictReader(io.StringIO(open(f, encoding="utf-8-sig").read()), delimiter=";"))
        questions = bank.diagnostic_questions(grade, [s for _, s in SUBJECTS[grade]])
        assert len(questions) == len(rows), f"{f}: бот принял {len(questions)} из {len(rows)} строк (смотри лог)"
        by_subject = {}
        for q in questions:
            by_subject.setdefault(q.subject, []).append(q.level)
            assert not needs_picture(q.text), (f, q.text)
            assert q.topic, (f, q.text)
            if grade <= 4:
                assert q.options, f"{f}: у вопроса нет вариантов: {q.text}"
            if q.options:
                from app.services.task_utils import normalize_answer
                assert len({normalize_answer(o) for o in q.options}) == len(q.options), f"{f}: варианты совпадают: {q.options}"
        expected = {sid for _, sid in SUBJECTS[grade]}
        assert set(by_subject) == expected, f"{f}: предметы {sorted(by_subject)} вместо {sorted(expected)}"
        for sid, levels in by_subject.items():
            assert sorted(levels) == [1, 2, 3], f"{f}: {sid} — уровни {levels}"
        checked.append(f"{grade} кл.: {len(questions)}")
    total = sum(int(c.split(": ")[1]) for c in checked)
    print(f"   Файлы диагностики, всего {total} вопросов (" + ", ".join(checked) + "): все предметы, 3 уровня, варианты: OK")


def test_speakable():
    from app.handlers.tutor.core import speakable
    def clean(x):  # как бот готовит текст к озвучке
        import re
        return re.sub(r"\s+", " ", re.sub(r'[^\w\s,.\-!?:;—+=]', '', speakable(x))).strip()
    assert clean("Вычисли: −8 + 3 − (−5).") == "Вычисли: минус 8 плюс 3 минус минус 5."
    assert clean("Сколько будет 3 × 4?") == "Сколько будет 3 умножить на 4?"
    assert clean("Какая дробь больше: 3/7 или 5/7?") == "Какая дробь больше: 3 дробь 7 или 5 дробь 7?"
    assert clean("x² − 5x + 6 = 0") == "x в квадрате минус 5x плюс 6 равно 0"
    assert clean("У подножия +18 °C") == "У подножия плюс 18 градусов" and clean("в 7:30") == "в 7:30"
    print("   Озвучка формул: минус, умножить, дробь, в квадрате, градусы: OK")


def test_normalize_voice():
    from app.handlers.normalize import normalize_update
    upd = {"update_type": "message_created", "message": {"sender": {"user_id": 7}, "recipient": {"chat_id": 9},
           "body": {"mid": "x", "attachments": [{"type": "audio", "payload": {"url": "https://v.ogg", "token": "t"}}]}}}
    n = normalize_update(upd); assert n.audio_url == "https://v.ogg" and not n.text and not n.is_callback
    upd["message"]["body"]["attachments"] = [{"type": "file", "payload": {"url": "https://f", "filename": "rec.ogg"}}]
    assert normalize_update(upd).audio_url == "https://f"
    upd["message"]["body"]["attachments"] = [{"type": "image", "payload": {"url": "https://img"}}]
    assert normalize_update(upd).audio_url is None
    print("   Голосовое из MAX распознаётся как голосовое сообщение: OK")


def test_audio_ffmpeg():
    """Перекодирование звука — только если на компьютере есть ffmpeg (в Docker-образе он есть)."""
    import subprocess
    from app.services.audio import to_ogg_opus, is_ogg_opus
    if shutil.which("ffmpeg") is None:
        print("   ffmpeg не найден — проверка перекодирования пропущена"); return
    mp3 = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                          "sine=frequency=440:duration=40", "-f", "mp3", "pipe:1"], capture_output=True).stdout
    out = asyncio.run(to_ogg_opus(mp3)); assert out and is_ogg_opus(out) and len(out) < 1_000_000
    assert asyncio.run(to_ogg_opus(OGG_OPUS)) == OGG_OPUS
    print("   Голосовое в другом формате перекодируется в OGG/Opus: OK")


def test_voice():
    import base64 as b64
    from app.services import voice as v
    t = v.prepare_for_speech("🎉 Верно!!\nТы молодец\n• шаг первый\n2) т.е. 3/4 от 100%\n«Ответ» (подумай)")
    assert t == "Верно! Ты молодец. шаг первый. 2. то есть 3 дробь 4 от 100 процентов. Ответ, подумай.", t
    long = " ".join(f"Предложение номер {i}, в нём есть немного слов." for i in range(30))
    chunks = v.split_for_tts(long)
    assert all(len(c) <= v.TTS_CHUNK for c in chunks) and " ".join(chunks) == long and len(chunks) > 3
    assert v.split_for_tts("Слово " * 100)[0].endswith("Слово")                    # длинное предложение — по словам
    raw = (json.dumps({"result": {"audioChunk": {"data": b64.b64encode(b"AB").decode()}}}) + "\n"
           + json.dumps({"result": {"audioChunk": {"data": b64.b64encode(b"CD").decode()}}})).encode()
    assert v.parse_tts_stream(raw) == b"ABCD"
    assert len(v.prepare_for_speech("а" * 5000)) <= v.MAX_SPEECH_CHARS
    # Озвучка: основной голос недоступен — запасной; LiveTTS без скорости
    import importlib
    sys.modules.pop("app.services.ai_client", None)
    cfg = sys.modules["app.core.config"].settings
    cfg.TTS_VOICE, cfg.TTS_ROLE, cfg.TTS_SPEED, cfg.YANDEX_GPT_FALLBACK_MODEL = "denis", "", 1.0, "yandexgpt-5-pro"
    ai = importlib.import_module("app.services.ai_client")
    client = ai.AIClient()
    requests = []
    async def fake_tts(text, model, voice, role, raw_pcm):
        requests.append((model, voice, role, raw_pcm))
        return (b"", 400) if voice == "denis" else (b"OGG", 200)
    client._tts_request = fake_tts
    audio = asyncio.run(client.synthesize_speech("Привет. Как дела?"))
    assert audio == b"OGG" and requests == [("livetts", "denis", "", False), ("", "ermil", "good", False)]
    requests.clear(); asyncio.run(client.synthesize_speech("Ещё раз."))
    assert requests == [("", "ermil", "good", False)]                               # час не дёргаем недоступный голос
    requests.clear(); asyncio.run(client.synthesize_speech("Ещё раз.", voice={"model": "", "voice": "anton", "role": "good"}))
    assert requests[0] == ("", "anton", "good", False)
    # Маячок — мальчик: старый женский голос (из .env или /voice) заменяется голосом Маячка
    client._tts_primary_broken_until = 0
    requests.clear(); asyncio.run(client.synthesize_speech("Ещё раз.", voice={"model": "", "voice": "masha", "role": "friendly"}))
    assert requests[0] == ("livetts", "denis", "", False), requests                 # женский /voice — как в .env
    client._tts_primary_broken_until = 0
    cfg.TTS_VOICE, cfg.TTS_ROLE = "irina", "narrator"
    requests.clear(); asyncio.run(client.synthesize_speech("Ещё раз."))
    assert requests[0] == ("", "alexander", "good", False), requests
    if shutil.which("ffmpeg"):
        async def fake_pcm(text, model, voice, role, raw_pcm):
            return (v.silence(0.2) if raw_pcm else b""), 200
        client._tts_request = fake_pcm
        from app.services.audio import is_ogg_opus
        audio = asyncio.run(client.synthesize_speech(long, voice={"voice": "anton", "role": "good"}))
        assert is_ogg_opus(audio), audio[:40]                                        # куски склеены в одно голосовое
    # Дешёвая модель для простых действий; если её нет в каталоге — простые действия идут на основную
    cfg.YANDEX_GPT_LIGHT_MODEL, cfg.YANDEX_GPT_MODEL = "yandexgpt-lite", "aliceai-llm"
    client = ai.AIClient()
    used, broken = [], set()
    class Resp:
        def __init__(self, status, data): self.status, self._data = status, data
        async def json(self): return self._data
        async def text(self): return json.dumps(self._data)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class Session:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            model = json["modelUri"].rsplit("/", 1)[-1]; used.append(model)
            if model in broken: return Resp(404, {"error": "model not found"})
            return Resp(200, {"result": {"alternatives": [{"message": {"text": "ВЕРНО"}}]}})
    real_session, real_timeout = getattr(ai.aiohttp, "ClientSession", None), getattr(ai.aiohttp, "ClientTimeout", None)
    ai.aiohttp.ClientSession, ai.aiohttp.ClientTimeout = Session, (lambda **k: None)
    try:
        assert asyncio.run(client.judge_answer("2+2", "4", "4")) is True and used == ["yandexgpt-lite"]
        used.clear(); asyncio.run(client._request("x", "y")); assert used == ["aliceai-llm"]        # сложное — основной
        ok, text = asyncio.run(client.check_answer("2+2", "4", "4")); assert ok and text in ai.PRAISE   # похвала без ИИ
        used.clear(); asyncio.run(client.check_answer("2+2", "5", "4", known=False))
        assert used == ["aliceai-llm"]                                                   # вердикт известен: только подсказка
        broken.add("yandexgpt-lite"); used.clear()
        assert asyncio.run(client.judge_answer("2+2", "4", "4")) is True and used == ["yandexgpt-lite", "aliceai-llm"]
        used.clear(); asyncio.run(client.judge_answer("2+2", "4", "4")); assert used == ["aliceai-llm"]   # больше не пробует
        report = "\n".join(client.usage_report())
        assert "yandexgpt-lite" in report and "aliceai-llm" in report and "Озвучка" in report
    finally:
        ai.aiohttp.ClientSession = real_session
        if real_timeout: ai.aiohttp.ClientTimeout = real_timeout
    sys.modules.pop("app.services.ai_client", None)
    print("   Голос: текст как живая речь (паузы, сокращения, формулы), одно голосовое, запасной голос: OK")


def test_timezones():
    from datetime import datetime, timezone
    from app.services import timezones as tzs
    import app.services.daily as d
    cases = {"Москва": 180, "я из Казани": 180, "в Перми": 300, "Нижний Тагил": 300, "нижний новгород": 180,
             "Санкт-Петербург": 180, "Чита": 540, "читаю книгу": None, "Уфа": 300, "Новосибирск": 420,
             "Южно-Сахалинск": 660, "Калининград": 120, "Петропавловск-Камчатский": 720, "Простоквашино": None}
    for text, expected in cases.items():
        assert tzs.detect_city(text) == expected, (text, tzs.detect_city(text))
    assert tzs.detect_time("у меня 14:58", 11, 58) == 180 and tzs.detect_time("19.00", 14, 0) == 300
    assert tzs.zone_name(None) == "Екатеринбург (UTC+5)" and tzs.zone_name(420) == "Новосибирск (UTC+7)"
    wed_13utc = datetime(2026, 11, 4, 13, 5, tzinfo=timezone.utc)                   # 16:05 в Москве, 18:05 в Екатеринбурге
    assert d.plan_actions({"grade": 5, "tz_offset": 180}, wed_13utc) == ["daily"]
    assert d.plan_actions({"grade": 5, "tz_offset": 720}, wed_13utc) == []           # на Камчатке уже ночь
    print("   Часовые пояса: город (в любом падеже) или время → пояс, задания по местному времени: OK")


def test_schedule_rules():
    from app.services import schedule as sch
    ids4 = [sid for _, sid in SUBJECTS[4]]
    parsed, unknown = sch.parse_schedule("Понедельник: Математика, Русский язык, Лит. чтение, Музыка\n"
                                         "вт - окр. мир, англ. яз, физ-ра\nПятница\n1) Русский\n2. Литературное чтение", ids4)
    assert parsed == {"0": ["math", "rus", "read"], "1": ["world", "eng"], "4": ["rus", "read"]}, parsed
    assert unknown == ["музыка", "физ-ра"], unknown
    ids8 = [sid for _, sid in SUBJECTS[8]]
    p8, _ = sch.parse_schedule("Ср: алгебра, геометрия, физика, физкультура, химия, ОБЖ", ids8)
    assert p8 == {"2": ["math", "geo_math", "phys", "chem", "obzr"]}, p8
    assert sch.parse_schedule("математика, русский", ids4)[0] == {}                 # без дня недели — не понимаем
    assert sch.lessons_on({"0": ["math", "rus", "math"]}, 0) == ["math", "rus"] and sch.lessons_on({}, 3) == []
    assert [sch.mark_for(r, 4) for r in (4, 3, 2, 1)] == [5, 4, 3, 2] and sch.mark_for(9, 10) == 5
    print("   Расписание: разбор текста (строкой и столбиком, сокращения), уроки без заданий пропускаются, оценки 2–5: OK")


def test_programs():
    from datetime import date
    from app.services.programs import programs, quarter_position
    programs.load()
    for g in range(1, 12):
        for _, sid in SUBJECTS[g]:
            q = programs.quarters(g, sid)
            if q is None and sid == "lit":
                continue   # литературы в примерной программе нет — она есть в программах школы
            assert q and len(q) == 4 and all(q), (g, sid)
            assert programs.authors_for(g, sid), (g, sid)
            assert len(set(programs.topics(g, sid))) == len(programs.topics(g, sid)), (g, sid)   # темы не повторяются
    assert programs.quarters(12, "math") is None and programs.authors_for(9, "soc")[0] == "Боголюбов"
    assert programs.topics(11, "math")[0] == "Производная" and programs.authors_for(10, "phys")[0].startswith("Мякишев")
    assert programs.authors_for(7, "math")[0] == "Макарычев" and programs.authors_for(6, "math")[0] == "Виленкин, Жохов"
    assert programs.authors_for(7, "hist")[0].startswith("Юдовская") and programs.authors_for(7, "geo_math")[0] == "Атанасян"
    assert quarter_position(date(2026, 9, 1)) == (0, 0.0) and quarter_position(date(2026, 7, 10)) == (3, 1.0)
    assert quarter_position(date(2027, 2, 14))[0] == 2
    assert programs.passed_topics(1, "math", date(2026, 9, 1)) == ["Счёт предметов"]
    assert len(programs.passed_topics(1, "math", date(2027, 6, 1))) == len(programs.topics(1, "math"))
    total = sum(len(programs.topics(g, sid)) for g in range(1, 12) for _, sid in SUBJECTS[g])
    print(f"   Программы 1–11 классов: {total} тем по четвертям, учебники по каждому предмету: OK")


def test_school_programs():
    """Рабочие программы школы: поурочное планирование с датами заменяет примерную программу."""
    from datetime import date
    from app.services.programs import Programs, by_date
    p = Programs(os.path.join(PROJECT, "data/programs")); p.load()
    covered = 0
    for g in range(1, 12):
        for _, sid in SUBJECTS[g]:
            q = p.quarters(g, sid)
            assert q and len(q) == 4 and all(q), (g, sid)
            topics = p.topics(g, sid)
            assert len(set(topics)) == len(topics), (g, sid)                 # темы не повторяются
            if p.is_school(g, sid):
                assert all(not t.startswith(("Контрольная", "Повторение", "http")) and "edsoo" not in t for t in topics), (g, sid)
                covered += 1
                assert p.authors_for(g, sid), (g, sid)
    assert covered >= 85, covered
    assert p.is_school(5, "math") and "Виленкин" in p.authors_for(5, "math")[0] and "учебник школы" in p.authors_for(5, "math")[0]
    assert len(p.authors_for(5, "math")) > 1                                   # остальные учебники остались для выбора
    assert not p.is_school(5, "inf")                                            # нет КТП — примерная программа
    # Тема по дате урока из КТП
    t5 = p.topics(5, "math")
    assert p.current_topics(5, "math", date(2026, 9, 1))[0] == t5[0] == "Десятичная система счисления. Ряд натуральных чисел"
    assert p.passed_topics(5, "math", date(2026, 9, 1)) == [t5[0]]
    q, i = p.position(5, "math", date(2026, 11, 16)); assert q == 1 and 0 < i < len(t5) - 1
    assert p.position(5, "math", date(2027, 2, 10))[0] == 2 and p.position(5, "math", date(2027, 4, 20))[0] == 3
    assert len(p.passed_topics(5, "math", date(2027, 7, 1))) == len(t5)      # лето — пройдено всё
    assert by_date(["09-01", "09-15", "01-12", None, "05-20"], date(2026, 12, 30)) == 1
    assert by_date(["09-01", "09-15", "01-12", None, "05-20"], date(2027, 1, 12)) == 2
    assert by_date(["09-05"], date(2026, 9, 1)) == 0
    # «📚 Программа» в боте: рабочая программа школы, текущая четверть вокруг сегодняшней темы, не длиннее 4000 знаков
    import app.services.progress as prog
    from datetime import datetime as _dt, timezone as _tz
    real_now, real_dir = prog.now, _programs.data_dir
    _programs.data_dir, _programs.loaded = os.path.join(PROJECT, "data/programs"), False
    try:
        prog.now = lambda: _dt(2027, 2, 10, 9, 0, tzinfo=_tz.utc)
        async def scenario():
            R = 9601
            await say(R, "/start"); await say(R, "Рома"); await press(R, "grade_3")
            for sid in ("read", "rus", "math", "world"):
                out = await press(R, f"prog_{sid}"); t = last(out)
                assert t.startswith("📚 Рабочая программа школы") and "👉" in t and "Темы и даты — из КТП учителя" in t, t[:200]
                assert len(t) < 4000 and "3 четверть:" in t and "1 четверть:" in t and "— пройдено" in t, (sid, len(t))
        asyncio.run(scenario())
    finally:
        prog.now, _programs.data_dir, _programs.loaded = real_now, real_dir, False
    total = sum(len(p.topics(g, sid)) for g in range(1, 12) for _, sid in SUBJECTS[g] if p.is_school(g, sid))
    print(f"   Программы школы (КТП): {covered} предметов, {total} тем с датами уроков, тема — по дате урока: OK")


def test_documents():
    import io, zipfile
    from app.services.documents import xlsx_text, document_text, image_mime
    from app.handlers.normalize import normalize_update
    x = io.BytesIO()
    with zipfile.ZipFile(x, "w") as z:
        S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        z.writestr("xl/sharedStrings.xml", f"<sst {S}><si><t>Пн</t></si><si><t>Математика</t></si><si><t>Русский</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {S}><sheetData><row><c t="s"><v>0</v></c></row>'
                   f'<row><c t="s"><v>1</v></c><c t="s"><v>2</v></c><c><v>5</v></c></row></sheetData></worksheet>')
    assert xlsx_text(x.getvalue()) == "Пн\nМатематика | Русский | 5", xlsx_text(x.getvalue())
    assert document_text(x.getvalue(), "r.xlsx").startswith("Пн")
    assert document_text("Пн: математика".encode("cp1251"), "r.txt") == "Пн: математика"
    assert document_text(b"%PDF-1.4", "r.pdf") is None and image_mime(b"%PDF-1.4") == "application/pdf"
    assert image_mime(b"\x89PNG\r\n\x1a\n...") == "image/png" and image_mime(b"GIF89a") is None
    upd = {"update_type": "message_created", "message": {"sender": {"user_id": 7}, "recipient": {"chat_id": 9},
           "body": {"mid": "x", "attachments": [{"type": "image", "payload": {"url": "https://i.jpg"}}]}}}
    n = normalize_update(upd); assert (n.file_url, n.file_kind) == ("https://i.jpg", "image") and n.audio_url is None
    upd["message"]["body"]["attachments"] = [{"type": "file", "payload": {"url": "https://f", "filename": "Расписание.docx"}}]
    n = normalize_update(upd); assert (n.file_url, n.file_name, n.file_kind) == ("https://f", "Расписание.docx", "document")
    upd["message"]["body"]["attachments"] = [{"type": "file", "payload": {"url": "https://f", "filename": "game.exe"}}]
    assert normalize_update(upd).file_url is None
    print("   Файлы: Word и Excel читаются без библиотек, фото/PDF — на распознавание, вложения MAX: OK")


def test_download_reads_whole_file():
    """Файл скачивается целиком, даже если сервер отдаёт его кусками (раньше получались обрезанные 873 байта)."""
    import importlib.util
    fake = sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
    for name in ("ClientSession", "ClientTimeout", "FormData"):
        setattr(fake, name, getattr(fake, name, object))
    cfg = sys.modules["app.core.config"].settings
    cfg.MAX_BOT_TOKEN = types.SimpleNamespace(get_secret_value=lambda: "t")
    cfg.MAX_API_URL, cfg.MAX_SSL_VERIFY = "https://max", True
    spec = importlib.util.spec_from_file_location("real_api_client", os.path.join(PROJECT, "app/services/api_client.py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class Content:
        def __init__(self, parts): self.parts = parts
        async def read(self, n=-1): return self.parts[0]            # как aiohttp: только то, что уже пришло
        async def iter_chunked(self, size):
            for part in self.parts: yield part
    parts = [b"\xff\xd8\xff" + b"a" * 870] + [b"b" * 65536] * 5
    response = types.SimpleNamespace(content=Content(parts))
    data = asyncio.run(module.MaxApiClient._read_all(response, 10 * 1024 * 1024))
    assert len(data) == 873 + 5 * 65536, len(data)
    assert asyncio.run(module.MaxApiClient._read_all(types.SimpleNamespace(content=Content(parts)), 1000)) is None
    print("   Скачивание фото и файлов: файл читается целиком, а не первым куском: OK")


def test_task_cache():
    from app.services.task_cache import TaskCache
    r = FakeRedis(); bank = TaskCache(r)
    qs = [{"q": f"Вопрос {i}", "options": ["a", "b", "c", "d"], "answer": "a", "topic": "Дроби" if i < 3 else "Проценты"}
          for i in range(6)]
    assert asyncio.run(bank.add(5, "math", qs, "question", 2)) == 6
    assert asyncio.run(bank.add(5, "math", qs[:2], "question", 2)) == 0                 # повторы не сохраняем
    assert asyncio.run(bank.add(5, "math", [{"q": "Без ответа", "topic": "Дроби"}], "question", 2)) == 0
    assert asyncio.run(bank.size()) == 6
    got = asyncio.run(bank.take(1, 5, "math", "question", 4, n_options=4))
    assert len(got) == 4 and {g["topic"] for g in got} == {"Дроби", "Проценты"}          # темы вперемешку
    assert len(asyncio.run(bank.take(1, 5, "math", "question", 10, topics=["дроби"]))) == 3
    assert asyncio.run(bank.take(1, 5, "math", "question", 4, n_options=3)) == []        # другие варианты — нет
    asyncio.run(bank.mark_seen(1, [g["id"] for g in got]))
    assert len(asyncio.run(bank.take(1, 5, "math", "question", 10))) == 2                 # уже виденные не даём
    assert len(asyncio.run(bank.take(2, 5, "math", "question", 10))) == 6                 # другому ученику — все
    assert asyncio.run(bank.take(1, 5, "math", "task", 5)) == []
    print("   Банк заданий: без повторов, по темам, вперемешку, ученик не видит одно и то же дважды: OK")


async def test_visual():
    """Оформление: HTML-разметка сообщений и картинки-карточки (прогресс, тест, грамота, отчёт родителю)."""
    from app.services.formatting import to_html, bar
    from app.services import cards
    assert to_html("📘 Урок · Дроби\n\n3 < 5 & 2") == "<b>📘 Урок · Дроби</b>\n\n3 &lt; 5 &amp; 2"
    assert to_html("Привет!\n📗 Учебник: Моро") == "Привет!\n<b>📗 Учебник:</b> Моро"
    assert to_html("Темы:\n• тема «Дроби»") == "<b>Темы:</b>\n• тема «<i>Дроби</i>»"
    assert to_html("Сколько будет 2 + 2?") == "Сколько будет 2 + 2?" and to_html("") == ""
    assert bar(0.5, 4) == "▰▰▱▱"
    if not cards.available():
        print("   Оформление: разметка OK (Pillow не установлен — картинки не проверены)"); return
    pngs = [
        cards.progress_card("Очень-очень длинное имя ученика для проверки", 7, "уровень «Знаток»", "1 240", 5, 9, 15,
                            [("Математика", 5, 2, 1), ("Информатика", 0, 0, 0)] * 8),
        cards.progress_card("Маша", 1, "Играйка", "0", 0, 0, 10, []),
        cards.result_card("Урок: Дроби", "Математика · 5 класс", 7, 10, [True] * 7 + [False] * 3, 4),
        cards.result_card("Тест", "Математика · 5 класс", 0, 3, [False] * 3),
        cards.certificate_card("Маша", "Достижение «Снайпер»", "ответь верно с первой попытки 10 раз"),
        cards.parent_card("Ева", 4, "02.11–08.11", 5, 42, 81, [("Математика · Дроби", 5)] * 9, ["Русский: Падежи"] * 7),
        cards.parent_card("Ева", None, "02.11–08.11", 0, 0, None, [], []),
        cards.welcome_card(),
        cards.chest_card("🦊", "Лис-хитрец", "common", "Альбом: 3 из 30"), cards.chest_card("💡", "Очень длинный факт " * 20, "fact"),
        cards.chest_card("🏅", "Звездочёт", "нет такой"),
        cards.lightning_card("Очень длинное имя ученика, 11 класс", "🇬🇧 Слова", 128, 130, False, "Повелитель молний ⚡⚡⚡", 128, 3),
        *[cards.lighthouse_card("Маша", n, 30, n % 2 == 0, __import__("app.services.lighthouse", fromlist=["x"]).MILESTONES)
          for n in (0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45)],
    ]
    from app.services.mascot import mascot, avatar
    assert mascot(200, "cheer").size == (150, 200) and mascot(100, "нет такого").mode == "RGBA"
    assert avatar(256).size == (256, 256)
    assert cards.plain("Комета ☄️") == "Комета"
    assert all(p.startswith(b"\x89PNG") and len(p) > 5000 for p in pngs)
    settings.IMAGE_CARDS = True
    try:
        out = await say(9301, "/start")                                     # знакомство: картинка Маячка
        assert out[0]["attachments"] and "Я Маячок" in last(out)
        g = await UserHandler(api, db, redis)._get_grade(ADMIN)
        out = await press(ADMIN, f"progress_{g}")
        assert out[0]["attachments"] == [{"type": "image", "payload": {"token": "TOK"}}], out[0]
        assert out[-1]["text"] and not out[-1]["attachments"]          # текст прогресса — следом
        await redis.set_state(f"cert_{ADMIN}", json.dumps([["🎯 Снайпер", "10 раз подряд"]]))
        out = await press(ADMIN, "back_to_menu")
        assert out[-1]["attachments"] and await redis.get_state(f"cert_{ADMIN}") is None   # грамота — после сообщения
        out = await say(ADMIN, "/demo_parent")
        assert out[0]["attachments"] and out[-1]["text"].startswith("📊 ")
    finally:
        settings.IMAGE_CARDS = False
    print("   Оформление: жирные заголовки (HTML), карточки прогресса, теста, грамоты и отчёта родителю: OK")


def test_html_messages():
    """Настоящий MaxApiClient: текст уходит с format=html, а если MAX разметку не принял — обычным текстом."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("real_api_client2", os.path.join(PROJECT, "app/services/api_client.py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    client = module.MaxApiClient(token="t", base_url="https://max")
    calls = []
    async def fake_request(method, endpoint, params=None, json_body=None):
        calls.append(json_body)
        if json_body.get("format") and fail[0]:
            raise module.MaxApiError(400, {"code": "bad.format"})
        return {"ok": True}
    client._request = fake_request
    fail = [False]
    asyncio.run(client.send_message(user_id=1, text="📘 Урок\n3 < 5"))
    assert calls[-1] == {"text": "<b>📘 Урок</b>\n3 &lt; 5", "format": "html"}, calls
    asyncio.run(client.send_message(user_id=1, text="просто 3 < 5"))
    assert calls[-1] == {"text": "просто 3 < 5"}                          # без разметки — как есть
    fail[0] = True
    asyncio.run(client.send_message(user_id=1, text="📘 Урок\nтекст"))
    assert calls[-1] == {"text": "📘 Урок\nтекст"} and calls[-2].get("format") == "html"
    print("   Сообщения с разметкой: HTML, а если MAX не принял — обычный текст: OK")


async def test_miniapp():
    """Мини-приложение: подпись MAX, данные ученика и родителя, расписание, тренажёр, действия в чате, веб-сервер."""
    import time as _time
    from contextlib import asynccontextmanager
    from urllib.parse import quote
    from app.webapp.auth import sign, validate_init_data
    from app.webapp.api import ApiError, MiniAppAPI

    # 1) Подпись initData — как в документации MAX
    def init_data(uid, token="TOKEN", age=0):
        fields = {"auth_date": str(int(_time.time()) - age), "query_id": "q1",
                  "user": json.dumps({"id": uid, "first_name": "Маша"}, ensure_ascii=False)}
        h = sign(fields, token)
        return "&".join(f"{k}={quote(v)}" for k, v in fields.items()) + f"&hash={h}"
    good = init_data(77)
    assert validate_init_data(good, "TOKEN")["user"]["id"] == 77
    assert validate_init_data(good, "OTHER") is None                                  # чужой токен
    assert validate_init_data(good.replace("q1", "q2"), "TOKEN") is None              # подделка
    assert validate_init_data(init_data(77, age=3 * 86400), "TOKEN") is None          # старые данные
    assert validate_init_data("", "TOKEN") is None and validate_init_data("hash=1&hash=2", "TOKEN") is None
    assert validate_init_data("WebAppData=" + quote(good), "TOKEN")["user"]["id"] == 77

    @asynccontextmanager
    async def session_factory():
        yield db
    api_ = MiniAppAPI(lambda session: UserHandler(api, session, redis), session_factory, "mayachok_bot")

    # 2) Ученик: всё о нём одним запросом
    M = 9401
    await say(M, "/start"); await say(M, "Маша"); await press(M, "grade_5")
    st = await api_.state(M)
    assert st["role"] == "student" and st["grade"] == 5 and st["name"] == "Маша" and st["bot_link"] == "https://max.ru/mayachok_bot"
    assert [s["id"] for s in st["subjects"]] == [sid for _, sid in SUBJECTS[5]] and len(st["week"]) == 7
    assert st["week"][-1]["today"] and st["achievements"] and st["unit"] == "⚡" and st["points"] == 0
    new = await api_.state(9499); assert new["role"] == "new" and "subjects" not in new

    # 3) Расписание: чужие предметы и лишние дни отбрасываются
    sch = await api_.schedule(M); assert sch["days"][0] == "Пн" and {"id": "math", "title": "Математика"} in sch["subjects"]
    saved = await api_.save_schedule(M, {"0": ["math", "chem", "rus"], "4": []})
    assert saved["schedule"] == {"0": ["math", "rus"]} and (await api_.schedule(M))["schedule"] == {"0": ["math", "rus"]}
    for bad in ([], {"9": ["math"]}, {"0": "math"}):
        try: await api_.save_schedule(M, bad); assert False
        except ApiError as e: assert e.status == 400

    # 4) Тренажёр: тест в приложении, ответы не раскрываются, итог как в чате
    t = await api_.train_start(M, "math")
    assert t["subject"] == "Математика" and len(t["questions"]) == 7 and "answer" not in t["questions"][0]
    r = None
    for i, q in enumerate(t["questions"]):
        r = await api_.train_answer(M, i, "B2" if i else "A1")
        if i < 6: assert r["done"] is False
    assert r["done"] and r["summary"]["right"] == 6 and r["summary"]["wrong"] == [0] and r["summary"]["reward"]
    assert r["summary"]["level_change"] == 1                                            # 6 из 7 — уровень выше
    for call in (api_.train_answer(M, 7, "B2"), api_.train_answer(M, 0, "B2")):
        try: await call; assert False
        except ApiError as e: assert e.status == 409
    st = await api_.state(M)
    assert st["points"] > 0 and st["week"][-1]["solved"] == 6 and any(s["topics"] for s in st["subjects"] if s["id"] == "math")
    try: await api_.train_start(M, "chem"); assert False
    except ApiError as e: assert e.status == 400

    # 5) Действия в чате: разбор ошибки и урок по теме приходят сообщением от бота
    n = len(api.sent)
    await api_.chat_action(M, "fix", {"n": 0})
    for _ in range(20): await asyncio.sleep(0)
    assert any("Разбираем вопрос 1" in m["text"] for m in api.sent[n:]), [m["text"][:50] for m in api.sent[n:]]
    n = len(api.sent)
    await api_.chat_action(M, "lesson", {"subject_id": "math", "topic": "Дроби"})
    for _ in range(40): await asyncio.sleep(0)
    assert any("Урок" in m["text"] and "Дроби" in m["text"] for m in api.sent[n:])
    for action, params in (("rm -rf", {}), ("lesson", {"subject_id": "chem", "topic": "x"}), ("lesson", {"subject_id": "math"})):
        try: await api_.chat_action(M, action, params); assert False
        except ApiError as e: assert e.status == 400

    # 6) Родитель видит только своего ребёнка
    P, X = 9402, 9403
    h = UserHandler(api, db, redis)
    await h._save_profile(M, parents=json.dumps([{"user_id": P, "name": "Мама"}]))
    await redis.delete_state(f"children_{P}")
    ps = await api_.state(P)
    assert ps["role"] == "parent" and ps["children"] == [{"id": M, "name": "Маша", "grade": 5}]
    view = await api_.state(P, child=M)
    assert view["role"] == "parent_view" and view["report"]["solved"] == 6 and view["name"] == "Маша"
    await api_.save_schedule(P, {"1": ["eng"]}, child=M)
    assert (await api_.schedule(P, child=M))["schedule"] == {"1": ["eng"]}
    for call in (api_.state(X, child=M), api_.schedule(X, child=M), api_.save_schedule(X, {}, child=M)):
        try: await call; assert False
        except ApiError as e: assert e.status == 403

    # Кнопка «📱 Открыть приложение» в меню — только когда включена в .env
    assert not any(p and p.startswith("https://max.ru/") for _, p in btns(await press(M, "back_to_menu")))
    settings.MINIAPP_BUTTON = True
    await redis.set_state("bot_username", "mayachok_bot")
    out = await press(M, "back_to_menu")
    assert ("📱 Открыть приложение", "https://max.ru/mayachok_bot?startapp=home") in btns(out)
    out = await press(M, "progress_5")
    assert ("📱 Подробно в приложении", "https://max.ru/mayachok_bot?startapp=progress") in btns(out)
    settings.MINIAPP_BUTTON = False

    # 7) Веб-сервер: без подписи MAX — 401, с подписью — данные (вместо aiohttp — простая заглушка)
    web = types.ModuleType("aiohttp.web")
    class Response:
        def __init__(self, body=None, text=None, status=200, content_type=None, headers=None):
            self.body, self.text, self.status, self.headers = body, text, status, dict(headers or {})
    class HTTPException(Exception): pass
    class HTTPNotFound(HTTPException): pass
    class Router:
        def __init__(self): self.routes = {}
        def add_get(self, path, fn): self.routes[("GET", path)] = fn
        def add_post(self, path, fn): self.routes[("POST", path)] = fn
        def add_static(self, prefix, path): self.static = path
        def add_route(self, method, path, fn): self.routes[(method, path)] = fn
    class Application:
        def __init__(self, middlewares=(), client_max_size=0): self.middlewares, self.router = middlewares, Router()
    web.Response, web.HTTPException, web.HTTPNotFound, web.Application = Response, HTTPException, HTTPNotFound, Application
    web.json_response = lambda data, status=200, dumps=json.dumps: Response(text=dumps(data), status=status)
    web.middleware = lambda fn: fn
    web.FileResponse = lambda path: Response(text=open(path, encoding="utf-8").read())
    web.Request = web.AppRunner = web.TCPSite = web.StreamResponse = object
    sys.modules["aiohttp"] = types.ModuleType("aiohttp"); sys.modules["aiohttp"].web = web; sys.modules["aiohttp.web"] = web
    sys.modules.pop("app.webapp.server", None)
    from app.webapp.server import create_app
    app = create_app(api_, "TOKEN")
    class Req:
        def __init__(self, path, headers=None, query=None, data=None, match=None):
            self.path, self.headers, self.query, self._data, self.match_info = path, headers or {}, query or {}, data, match or {}
        async def json(self):
            if self._data is None: raise ValueError("no body")
            return self._data
    async def call(method, path, uid=None, **kw):
        headers = {"X-Init-Data": init_data(uid)} if uid else {}
        response = await app.middlewares[0](Req(path, headers=headers, **kw), app.router.routes[(method, path)])
        return response.status, (json.loads(response.text) if response.text and path.startswith("/api") else response)
    status, data = await call("GET", "/api/state"); assert status == 401 and data["code"] == "auth"
    resp = await app.middlewares[0](Req("/api/state", headers={"X-Init-Data": init_data(M)}), app.router.routes[("GET", "/api/state")])
    assert resp.headers["Access-Control-Allow-Origin"] == "*" and "X-Init-Data" in resp.headers["Access-Control-Allow-Headers"]
    assert (await app.router.routes[("OPTIONS", "/api/{tail:.*}")](Req("/api/state"))).status == 204
    status, data = await call("GET", "/api/state", uid=M); assert status == 200 and data["name"] == "Маша"
    status, data = await call("GET", "/api/state", uid=X, query={"child": str(M)}); assert status == 403
    status, data = await call("GET", "/api/state", uid=P, query={"child": "abc"}); assert status == 400
    status, data = await call("POST", "/api/train/answer", uid=M, data={"index": "0"}); assert status == 400
    status, data = await call("POST", "/api/schedule", uid=M); assert status == 400            # без тела запроса
    status, data = await call("POST", "/api/schedule", uid=M, data={"schedule": {"2": ["rus"]}})
    assert status == 200 and data["schedule"] == {"2": ["rus"]}
    status, page = await call("GET", "/"); assert status == 200 and "max-web-app.js" in page.text and "static/config.js" in page.text
    status, img = await call("GET", "/img/{name}.png", match={"name": "cheer"}); assert img.body.startswith(b"\x89PNG")
    try: await call("GET", "/img/{name}.png", match={"name": "../secret"}); assert False
    except HTTPNotFound: pass
    print("   Мини-приложение: подпись MAX, ученик и родитель, расписание, тренажёр, действия в чате, сервер: OK")


async def test_savings():
    """Экономия Yandex Cloud: голос по настройке, меню без озвучки, кеш озвучки, ответ кнопкой без проверки у ИИ."""
    FakeAI.voice = True
    S5, S2 = 9501, 9502
    for uid, g in ((S5, "grade_7"), (S2, "grade_2")):
        await say(uid, "/start"); await say(uid, "Тест"); await press(uid, g)
    settings.VOICE_MODE = "young"
    h = UserHandler(api, db, redis)
    assert not await h._voice_enabled(S5) and await h._voice_enabled(S2)          # 7 класс — без голоса, 2 — с голосом
    n = api.voices
    await press(S5, "today"); assert api.voices == n                               # старшему не озвучиваем
    await press(S2, "today"); assert api.voices > n
    # ученик сам выбирает: сразу → по кнопке → выключен
    out = await press(S5, "settings_7"); assert ("🔇 Голос: выключен", "voice_toggle") in btns(out)
    out = await press(S5, "voice_toggle"); assert "Голос — сразу" in out[0]["text"] and ("🔊 Голос: сразу", "voice_toggle") in btns(out)
    assert await h._voice_mode(S5) == "auto"
    out = await press(S5, "voice_toggle"); assert ("🔈 Голос: по кнопке", "voice_toggle") in btns(out)
    await press(S5, "voice_toggle"); assert await h._voice_mode(S5) == "off"
    settings.VOICE_MODE = "off"; assert not await h._voice_enabled(S2)
    # по кнопке: голосовое не делается, пока ученик не нажмёт «🔊 Послушать»
    settings.VOICE_MODE = "button"
    calls["speech"].clear(); n = api.voices
    out = await press(S2, "today")
    listen = [p for t, p in btns(out) if t == "🔊 Послушать"]
    assert listen and api.voices == n and not calls["speech"]
    out = await press(S2, listen[-1]); assert api.voices == n + 1 and calls["speech"]
    out = await press(S2, "say_000000000000"); assert "устарело" in last(out)
    out = await press(S2, "settings_2"); assert not any(t == "🔊 Послушать" for t, _ in btns(out))   # у меню кнопки нет
    settings.VOICE_MODE = "all"
    # меню, настройки, прогресс и награды — без озвучки
    for p in ("settings_2", "progress_2", "rewards_2", "sched"):
        n = api.voices; await press(S2, p); assert api.voices == n, p
    # кеш: одна и та же фраза озвучивается один раз
    settings.TTS_CACHE = True
    calls["speech"].clear(); FakeAI.usage.clear()
    msg2 = msg(S2)
    await h._send_with_voice(msg2, "Привет, это Маячок!"); await h._send_with_voice(msg2, "Привет, это Маячок!")
    assert len(calls["speech"]) == 1 and FakeAI.usage.get("tts_cached") == 1
    settings.TTS_CACHE = False
    # ответ кнопкой: неверный вариант ясен без запроса к ИИ (known=False)
    await redis.set_state(f"current_task_{S2}", "2 + 2 = ?"); await redis.set_state(f"ref_answer_{S2}", "4")
    await redis.set_state(f"options_{S2}", json.dumps(["3", "4", "5"])); await redis.set_state(f"task_active_{S2}", "true")
    calls.pop("check_known", None)
    await h._check_answer(msg2, "5"); assert calls["check_known"] == [False]
    await h._check_answer(msg2, "четыре наверное"); assert calls["check_known"][-1] is None   # свой текст — проверяет ИИ
    out_n = len(api.sent); await h._check_answer(msg2, "4")
    assert api.sent[out_n]["text"].startswith(PRAISE_STARTS) and len(calls["check_known"]) == 2   # верно — без ИИ
    # /stats показывает расход
    out = await say(ADMIN, "/stats"); assert "💰 Расход" in last(out)
    FakeAI.voice = False
    print("   Экономия: голос по классу и настройке, меню без озвучки, кеш голосовых, ответ кнопкой без ИИ, расход в /stats: OK")


async def test_consent():
    """Согласие на обработку данных: до него бот не запоминает имя и не обращается к нейросети."""
    settings.CONSENT_REQUIRED = True
    try:
        C = 9701
        out = await say(C, "/start"); assert "Кто ты?" in last(out)                       # сначала — кто ты
        assert [p for _, p in btns(out)] == ["role_student", "parent_start", "role_teacher"]
        out = await press(C, "role_student"); t = last(out)
        assert "🔒 Прежде чем начать" in t and "меньше 14 лет" in t, t
        assert [p for _, p in btns(out)] == ["consent_yes", "privacy"]
        n_gen = len(calls["gen"])
        out = await say(C, "Маша"); assert "🔒 Прежде чем начать" in last(out)          # имя без согласия не берём
        assert not db.rows.get(C) or not db.rows[C].name
        out = await say(C, "реши мне задачу 2+2"); assert "🔒 Прежде чем начать" in last(out) and len(calls["gen"]) == n_gen
        out = await press(C, "privacy"); t = last(out)
        assert t.startswith("📄 Политика конфиденциальности") and "Yandex Cloud" in t and "14 лет" in t
        assert ("✅ Согласен", "consent_yes") in btns(out)
        out = await press(C, "consent_yes"); assert "как тебя зовут?" in last(out)
        out = await press(C, "role_student"); assert "как тебя зовут?" in last(out)        # уже согласился — сразу имя
        assert db.rows[C].consent_at and "v2026-11" in db.rows[C].consent_at
        out = await say(C, "Маша"); assert db.rows[C].name == "Маша"
        await press(C, "grade_6")
        out = await say(C, "/privacy"); assert ("🗑 Удалить мои данные", "delete_me") in btns(out)
        # Redis очищен — согласие берём из базы
        redis.d.pop(f"consent_{C}", None)
        out = await say(C, "/start"); assert "С возвращением" in last(out)
        # ученик, который был до появления согласия, — один раз спрашиваем при /start
        O = 9702
        settings.CONSENT_REQUIRED = False
        await say(O, "/start"); await say(O, "Олег"); await press(O, "grade_5")
        settings.CONSENT_REQUIRED = True
        out = await say(O, "/start"); assert "Мы обновили правила" in last(out) and "parent_start" not in [p for _, p in btns(out)]
        out = await press(O, "consent_yes"); assert "Спасибо, Олег!" in last(out)
        out = await say(O, "/start"); assert "С возвращением" in last(out)
        # родитель подключается без согласия ученика — согласие даёт он сам при вводе кода
        P = 9703
        await say(P, "/start"); out = await press(P, "parent_start"); assert "/privacy" in last(out)
        await press(C, "parent_code"); out = await say(P, redis.d[f"pcode_of_{C}"])
        assert "Вы подключены к занятиям: Маша" in last(out)
        # удаление данных стирает и согласие
        await press(C, "delete_me_yes"); await say(C, "/start"); out = await say(C, "Маша")
        assert "🔒 Прежде чем начать" in last(out)                                         # написал имя — значит ученик
        # учитель без подтверждения (по умолчанию): кабинет сразу, администратору — уведомление
        TF = 9705
        await say(TF, "/start"); n = len(api.sent); out = await press(TF, "role_teacher")
        assert "Какие классы вы ведёте?" in last(out)
        assert any("Новый учитель в боте" in m["text"] and f"/del_teacher {TF}" in m["text"] for m in api.sent[n:])
        out = await say(TF, f"/del_teacher {TF}"); assert "только администратору" in last(out)
        out = await say(ADMIN, f"/del_teacher {TF}"); assert "закрыт" in last(out)
        out = await say(TF, "/teacher"); assert ("👩‍🏫 Я учитель", "role_teacher") in btns(out)
        # учитель с подтверждением (TEACHER_APPROVAL=true): запрос администратору одной кнопкой
        settings.TEACHER_APPROVAL = True
        TQ = 9704
        await say(TQ, "/start"); n = len(api.sent); out = await press(TQ, "role_teacher")
        assert "Отправил запрос администратору" in last(out)
        req = [m for m in api.sent[n:] if "Запрос на кабинет учителя" in m["text"]]
        assert req and ("✅ Открыть", f"t_ok_{TQ}") in btns(req)
        out = await press(TQ, "role_teacher"); assert "Запрос уже у администратора" in last(out)
        out = await press(TQ, f"t_ok_{TQ}"); assert "только для администратора" in last(out) or "для администратора" in last(out)
        n = len(api.sent); out = await press(ADMIN, f"t_ok_{TQ}")
        assert any("теперь учитель" in m["text"] for m in out)
        out = await press(TQ, "role_teacher"); assert "Какие классы вы ведёте?" in last(out)
    finally:
        settings.CONSENT_REQUIRED = False
        settings.TEACHER_APPROVAL = False
    print("   Согласие: до него имя не сохраняется и ИИ не вызывается, /privacy, старым ученикам — один раз, родитель: OK")


async def test_teacher():
    """Кабинет учителя: класс по коду, ученики, слабые темы класса, задание классу и его результаты."""
    import app.services.progress as prog
    T, S1, S2, S3, X = 9801, 9802, 9803, 9804, 9805
    out = await say(T, "/teacher"); assert ("👩‍🏫 Я учитель", "role_teacher") in btns(out)              # не учитель — кабинета нет
    out = await say(X, f"/add_teacher {X}"); assert "только администратору" in last(out)
    n = len(api.sent); out = await say(ADMIN, f"/add_teacher {T}"); assert any("теперь учитель" in m["text"] for m in out)
    assert any(m["text"].startswith("👩‍🏫 Вам открыт кабинет учителя") for m in api.sent[n:])
    # первый вход: начальные классы или предметник → предметы → имя для учеников
    out = await say(T, "/start"); assert "Какие классы вы ведёте?" in last(out)
    assert btns(out) == [("🧸 Начальные классы", "t_kind_primary"), ("📐 Учитель-предметник", "t_kind_subject")]
    out = await press(T, "t_kind_subject"); assert "Какие предметы вы ведёте?" in last(out) and ("▫️ Геометрия", "t_subj_geo_math") in btns(out)
    out = await press(T, "t_subj_done"); assert "Отметьте хотя бы один предмет" in last(out)
    out = await press(T, "t_subj_math"); assert ("✅ Математика", "t_subj_math") in btns(out) and ("👍 Готово (выбрано 1)", "t_subj_done") in btns(out)
    out = await press(T, "t_subj_done"); assert "Как к вам обращаться" in last(out)
    out = await say(T, "Мария  Ивановна"); t = last(out)
    assert "✅ Готово, Мария Ивановна!" in t and "Мария Ивановна · Математика" in t and "Классов пока нет" in t, t
    out = await press(T, "t_add"); assert ("🔑 Подключиться по коду", "t_link") in btns(out)
    out = await press(T, "t_new"); assert ("5 класс", "t_new_5") in btns(out) and ("4 класс", "t_new_4") not in btns(out)
    out = await press(T, "t_new_7"); assert ("7А", "t_name_7_7А") in btns(out)
    out = await press(T, "t_name_7_7А"); assert "✅ Класс «7А» создан." in last(out)        # название кнопкой
    code7 = json.loads(redis.d[f"tclasses_{T}"])[0]
    out = await press(T, f"t_asg_{code7}"); topic_btn = [p for _, p in btns(out) if p.startswith("t_asgt_")]   # один предмет — сразу темы
    assert topic_btn                                                                      # тема по программе кнопкой
    if topic_btn:
        out = await press(T, topic_btn[0]); assert "✅ Задание отправлено ученикам: 0" in last(out), last(out)
    await press(T, f"t_delyes_{code7}")
    out = await press(T, "t_new"); out = await press(T, "t_new_5"); assert ("5А", "t_name_5_5А") in btns(out)
    assert json.loads(redis.d[f"tclasses_{T}"]) == []
    out = await say(T, "5 «Б»  класс")
    code = json.loads(redis.d[f"tclasses_{T}"])[0]
    assert f"✅ Класс «5 «Б» класс» создан." in last(out) and f"/join {code}" in last(out) and len(code) == 6
    # ученики вступают по коду
    for uid, name, grade in ((S1, "Аня", "grade_5"), (S2, "Петя", "grade_6"), (S3, "Оля", "grade_5")):
        await say(uid, "/start"); await say(uid, name); await press(uid, grade)
    n = len(api.sent); out = await say(S1, f"/join {code}")
    assert "✅ Ты в классе «5 «Б» класс»!" in last(out) and any("вступил(а): Аня" in m["text"] for m in api.sent[n:])
    out = await say(S2, f"/join {code}"); assert "Этот код — для 5 класса, а у тебя выбран 6" in last(out)
    out = await say(S2, "/join ZZZZZZ"); assert "Код класса не подошёл" in last(out)
    out = await press(S3, "settings_5"); assert ("🏫 Учитель", "sc_my") in btns(out)
    out = await press(S3, "sc_my"); assert "Напиши код класса" in last(out)
    out = await say(S3, code.lower()); assert "✅ Ты в классе" in last(out)
    out = await press(S3, "sc_my"); assert "Ты в классе «5 «Б» класс»" in last(out) and ("🚪 Выйти из класса", "sc_leave") in btns(out)
    # учитель видит класс и учеников
    out = await press(T, f"t_cls_{code}"); assert "Учеников: 2" in last(out)
    out = await press(T, f"t_stu_{code}"); t = last(out)
    assert "• Аня: дней 0/7, решено 0" in t and "• Оля" in t and ("Аня", f"t_rep_{code}_{S1}") in btns(out)
    out = await press(T, f"t_rep_{code}_{S1}"); assert last(out).startswith("📊 Аня, 5 класс")
    # чужой учитель и ученик не видят класс
    out = await press(X, f"t_cls_{code}"); assert "Кабинет учителя — для учителей" in last(out)
    out = await press(S1, f"t_stu_{code}"); assert "Кабинет учителя — для учителей" in last(out)
    # слабые темы класса
    h = UserHandler(api, db, redis)
    for uid in (S1, S3):
        progress = await h._load_progress(uid)
        prog.mark_diagnostic(progress, 5, [{"topic": "Дроби", "subject_id": "math", "subject": "Математика",
                                            "status": "неверно"}])
        await h._save_progress(uid, progress)
    out = await press(T, f"t_weak_{code}"); assert "Математика: «Дроби» — у 2 уч." in last(out)
    # задание классу по слабой теме → ученики получают кнопку
    n = len(api.sent); out = await press(T, f"t_asgw_{code}_0")
    assert "✅ Задание отправлено ученикам: 2" in last(out)
    got = [m for m in api.sent[n:] if m["text"].startswith("📝 Задание от учителя")]
    assert len(got) == 2 and "тема «Дроби»" in got[0]["text"] and "Задание от учителя — Мария Ивановна" in got[0]["text"]
    start = [p for m in got for _, p in btns([m])][0]; assert start == f"asg_{code}_1"
    out = await press(S2, start); assert "уже недоступно" in last(out)                   # не из класса
    # ученик решает задание (тест по теме), результат уходит учителю
    out = await press(S1, start)
    for _ in range(10):
        if "Вопрос" not in last(out): break
        out = await say(S1, "B2")
    done_text = "\n".join(m["text"] for m in out)
    assert "📨 Результат отправлен учителю." in done_text and "📸 Последний шаг" in done_text, done_text[-300:]
    # фото решения: на фото нет записей — просим переснять; потом принимаем и учитель его видит
    def photo_msg(uid, url):
        return NormalizedMessage(chat_id=uid, user_id=uid, message_id="m", text=None, payload=None,
                                 file_url=url, file_name="photo.jpg", file_kind="image")
    async def send_photo(uid, url="https://img/solution.jpg"):
        n = len(api.sent); await UserHandler(api, db, redis).handle_message(photo_msg(uid, url)); return api.sent[n:]
    FakeApi.files["https://img/solution.jpg"] = b"\xff\xd8\xff\xe0" + b"\x00" * 40_000
    FakeAI.ocr_text = "кот"
    out = await send_photo(S1); assert "Не вижу на фото записей" in last(out)
    out = await press(T, f"t_resd_{code}_1"); assert "• Аня: 5 (5 из 5) · ⏳ ждём фото" in last(out), last(out)
    FakeAI.ocr_text = "Задача 1. 3/8 + 2/8 = 5/8. Задача 2. 2/3 > 3/5, т.к. 10/15 > 9/15"
    out = await send_photo(S1); assert "✅ Фото решения получил" in last(out)
    out = await press(S1, start); assert "Ты уже выполнил это задание" in last(out) and "Фото решения учитель тоже получил" in last(out)
    out = await send_photo(S1); assert "📎 Что это?" in last(out)                       # больше фото не ждём
    # результаты, напоминание тем, кто не сделал
    out = await press(T, f"t_res_{code}"); assert "выполнили 1 из 2, ср. оценка 5.0, с фото решения: 1" in last(out)
    out = await press(T, f"t_resd_{code}_1"); t = last(out)
    assert "• Аня: 5 (5 из 5) · 📸 фото есть" in t and "Ещё не выполнили: Оля" in t, t
    assert ("📸 Аня", f"t_ph_{code}_1_{S1}") in btns(out)
    out = await press(T, f"t_ph_{code}_1_{S1}")                                          # учитель смотрит фото
    assert out[-1]["attachments"] == [{"type": "image", "payload": {"token": "TOK"}}] and "📸 Аня — задание 1" in out[-1]["text"]
    out = await press(X, f"t_ph_{code}_1_{S1}"); assert not out[-1]["attachments"]       # чужой не увидит
    n = len(api.sent); out = await press(T, f"t_rem_{code}_1")
    assert any(m["text"].startswith("🔔 Напоминание") for m in api.sent[n:]) and "Напомнил: 1" in "\n".join(m["text"] for m in out)
    # задание своей темой (текстом)
    out = await press(T, f"t_asg_{code}"); assert "Напишите тему задания" in last(out)    # у предметника — сразу тема
    out = await press(T, f"t_asgs_{code}_rus"); assert "Кабинет учителя" in last(out)     # чужой предмет — нельзя
    out = await press(T, f"t_asg_{code}"); out = await say(T, "Десятичные дроби")
    assert "✅ Задание отправлено ученикам: 2" in last(out)
    # второй учитель-предметник (русский) подключается к тому же классу по коду — ученикам вступать не нужно
    T2 = 9806
    await say(ADMIN, f"/add_teacher {T2}")
    await say(T2, "/teacher"); await press(T2, "t_kind_subject"); await press(T2, "t_subj_rus"); await press(T2, "t_subj_lit")
    await press(T2, "t_subj_done"); await say(T2, "Ольга Петровна")
    out = await press(T2, "t_add"); out = await press(T2, "t_link"); out = await say(T2, "XXXXXX"); assert "Класса с таким кодом нет" in last(out)
    await press(T2, "t_link"); out = await say(T2, code.lower())
    t = last(out); assert "✅ Вы подключились к классу" in t and "Учеников: 2" in t and "Ольга Петровна (Русский язык, Литература)" in t, t
    assert ("🚪 Выйти", f"t_unlink_{code}") in btns(out)                                  # удалить класс может только создатель
    out = await press(T2, f"t_asg_{code}"); assert [p for _, p in btns(out) if p.startswith("t_asgs_")] == [f"t_asgs_{code}_rus", f"t_asgs_{code}_lit"]
    out = await press(T2, f"t_res_{code}"); assert "Заданий пока не было" in last(out)    # чужие задания не видны
    out = await press(T2, f"t_weak_{code}"); assert "Дроби" not in last(out)               # слабые темы — по своим предметам
    out = await press(T2, f"t_resd_{code}_1"); assert "Кабинет учителя" in last(out)
    out = await press(T2, f"t_del_{code}"); assert "Кабинет учителя" in last(out) and f"tclass_{code}" in redis.d
    out = await press(T2, f"t_unlink_{code}"); assert "Вы отключились от класса" in last(out)
    assert str(T2) not in json.loads(redis.d[f"tclass_{code}"])["teachers"]
    # учитель начальных классов: классы 1–4, предмет — при каждом задании
    T3 = 9807
    await say(ADMIN, f"/add_teacher {T3}")
    await say(T3, "/teacher"); out = await press(T3, "t_kind_primary"); assert "Как к вам обращаться" in last(out)
    out = await say(T3, "Анна Сергеевна"); assert "начальные классы" in last(out)
    out = await press(T3, "t_new"); assert [p for _, p in btns(out) if p.startswith("t_new_")] == ["t_new_1", "t_new_2", "t_new_3", "t_new_4"]
    await press(T3, "t_new_2"); out = await press(T3, "t_name_2_2А"); c2 = json.loads(redis.d[f"tclasses_{T3}"])[0]
    out = await press(T3, f"t_asg_{c2}"); assert "Выберите предмет" in last(out) and ("Математика", f"t_asgs_{c2}_math") in btns(out)
    assert not any(p.endswith("_eng") for _, p in btns(out))                             # английский — у отдельного учителя
    # учитель английского: 2–11 классы, к 2А подключается по коду, задания только по английскому
    T4 = 9808
    await say(ADMIN, f"/add_teacher {T4}")
    await say(T4, "/teacher"); await press(T4, "t_kind_subject"); await press(T4, "t_subj_eng"); await press(T4, "t_subj_done")
    await say(T4, "Елена Викторовна")
    out = await press(T4, "t_new"); grades_btn = [p for _, p in btns(out) if p.startswith("t_new_")]
    assert grades_btn == [f"t_new_{g}" for g in range(2, 12)], grades_btn
    await press(T4, "t_link"); out = await say(T4, c2); assert "✅ Вы подключились к классу «2А»" in last(out)
    assert "Анна Сергеевна (начальные классы); Елена Викторовна (Английский язык)" in last(out), last(out)
    out = await press(T4, f"t_asg_{c2}"); assert "Английский язык" in last(out) and "Напишите тему задания" in last(out)
    # учитель математики 5–11 не видит 1–4 классы
    out = await press(T, "t_new"); assert ("4 класс", "t_new_4") not in btns(out)
    out = await press(T3, "t_prof"); assert ("🔄 Начальные/предметник", "t_kind") in btns(out)
    # ученик выходит из класса; удаление данных
    n = len(api.sent); out = await press(S3, "sc_leave")
    assert "Ты вышел из класса" in out[0]["text"] or any("вышел из класса" in m["text"] for m in out)
    assert any("Оля вышел(ла) из класса" in m["text"] for m in api.sent[n:])
    out = await press(T, f"t_cls_{code}"); assert "Учеников: 1" in last(out)
    await press(S1, "delete_me_yes"); out = await press(T, f"t_cls_{code}"); assert "Учеников: 0" in last(out)
    assert all(str(S1) not in a["results"] for a in json.loads(redis.d[f"tclass_{code}"])["assignments"])   # и фото тоже
    # удаление класса
    out = await press(T, f"t_del_{code}"); assert "Удалить класс" in last(out)
    out = await press(T, f"t_delyes_{code}"); assert "Класс удалён" in last(out) and f"tclass_{code}" not in redis.d
    assert not WIDE, sorted({tuple(r) for r in WIDE})                                  # кнопки помещаются
    print("   Кабинет учителя: класс по коду, ученики и отчёты, слабые темы, задание классу и оценки, напоминание: OK")


async def test_lighthouse():
    """«🗼 Мой маяк»: серия, заморозка раз в неделю, этапы стройки и картинка маяка."""
    from datetime import date
    from app.services import lighthouse as L
    mon, tue, wed, thu = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)
    assert L.stage(0) == 0 and L.stage(5) == 4 and L.stage(100) == len(L.MILESTONES)
    assert L.next_milestone(5) == (7, "огонь маяка") and L.next_milestone(30) is None
    assert L.reached(4, 5) == "флаг на крыше" and L.reached(5, 6) is None and L.reached(0, 1) == "фундамент"
    assert L.next_streak(None, 0, mon, None) == (1, False)
    assert L.next_streak("2026-10-30", 4, mon, None) == (5, False)                  # пт → пн: выходные не в счёт
    assert L.next_streak("2026-11-02", 5, mon, None) == (5, False)                  # второй раз за день
    assert L.next_streak("2026-11-02", 5, wed, None) == (6, True)                   # пропустил вторник — заморозка
    assert L.next_streak("2026-11-02", 5, wed, L.week_key(mon)) == (1, False)       # заморозка уже потрачена
    assert L.next_streak("2026-11-02", 5, thu, None) == (1, False)                  # два дня пропуска — сгорела
    assert L.current_streak("2026-11-02", 5, tue, None) == 5 and L.current_streak("2026-11-02", 5, thu, None) == 0
    assert L.current_streak("2026-11-02", 5, wed, L.week_key(wed)) == 0

    S = 9801
    await say(S, "/start"); await say(S, "Маша"); out = await press(S, "grade_5")
    assert ("🎮 Игры", "games") in btns(out) and ("🗼 Мой маяк", "lh") in btns(await press(S, "games"))
    h = UserHandler(api, db, redis)
    assert await h._bump_streak(S, {"streak_days": 4, "last_study_date": "2026-10-30"}, mon) == 5
    assert redis.d[f"lh_new_{S}"] == "флаг на крыше" and redis.d[f"lh_best_{S}"] == "5"
    assert "на маяке появилось: флаг на крыше" in await h._lighthouse_note(S) and await h._lighthouse_note(S) == ""
    redis.d.pop(f"lh_new_{S}")
    assert await h._bump_streak(S, {"streak_days": 5, "last_study_date": "2026-11-02"}, wed) == 6
    assert f"lh_new_{S}" not in redis.d and "Заморозка спасла" in await h._lighthouse_note(S)
    assert await h._bump_streak(S, {"streak_days": 6, "last_study_date": "2026-11-04"}, date(2026, 11, 6)) == 1
    assert redis.d[f"lh_best_{S}"] == "6"
    # первое решённое задание — строка «фундамент» в сообщении с наградой
    redis.d.pop(f"lh_best_{S}"); db.rows[S].streak_days = 0; db.rows[S].last_study_date = None
    note = await h._count_solved(S, 1); assert "на маяке появилось: фундамент" in note and "всего" in note, note
    assert db.rows[S].streak_days == 1
    out = await press(S, "lh"); assert "🗼 Мой маяк" in last(out) and "рекорд" in last(out) or "построим маяк" in last(out)
    settings.IMAGE_CARDS = True
    try:
        out = await press(S, "lh"); assert out[-1]["attachments"] and "Мой маяк" in last(out)
    finally:
        settings.IMAGE_CARDS = False
    out = await say(S, "/mayak"); assert "Мой маяк" in last(out)
    await press(S, "delete_me_yes"); assert f"lh_best_{S}" not in redis.d
    assert not WIDE, sorted({tuple(r) for r in WIDE})
    print("   Мой маяк: серия, заморозка раз в неделю, этапы стройки, картинка, кнопка в меню: OK")


async def test_chest():
    """«🎁 Сундук дня»: открывается после 3 заданий, раз в день; наклейки, повторки, гарантия редкой, альбом."""
    import random as _r
    from app.services import chest as C
    assert len(C.STICKERS) == 30 and len(set(C.STICKERS)) == 30 and len(C.FACTS) >= 30
    album = C.new_album(); rng = _r.Random(1)
    first = C.roll(album, rng); assert first["kind"] == "sticker" and not first["duplicate"]      # первый — наклейка
    kinds = {C.roll(album, rng)["kind"] for _ in range(300)}
    assert kinds == {"sticker", "fact", "title", "bonus"}, kinds
    assert C.collected(album) == 30 and len(album["titles"]) == len(C.TITLES)                    # за 300 — всё собрано
    dup = [C.roll(album, rng) for _ in range(50)]
    assert all(p["points"] == C.POINTS_DUPLICATE for p in dup if p["kind"] == "sticker")         # повторка → очки
    a2 = C.new_album(); a2["opened"] = 5; a2["no_rare"] = 7
    class Hi:                                                                                 # «невезучий» бросок
        def random(self): return 0.99
        def choice(self, x): return x[0]
    p = C.roll(a2, Hi()); assert p["kind"] == "sticker" and p["rarity"] == C.RARE                # гарантия редкой
    assert "❔" in "\n".join(C.album_lines(C.new_album())) and "0 из 30" in C.album_lines(C.new_album())[0]
    assert C.album_lines(album)[0] == "📒 Альбом наклеек: 30 из 30"

    S = 9811
    await say(S, "/start"); await say(S, "Петя"); out = await press(S, "grade_3")
    assert ("🎁 Открыть сундук", "chest") not in btns(out)
    out = await press(S, "chest"); assert "Реши ещё 3 задания" in last(out)
    h = UserHandler(api, db, redis)
    assert "Сундук дня" not in await h._count_solved(S, 1) + await h._count_solved(S, 1)
    note = await h._count_solved(S, 1); assert "🎁 Сундук дня готов" in note
    assert "Сундук дня готов" not in await h._count_solved(S, 1)                              # говорим один раз
    out = await press(S, "subjects_3"); assert btns(out)[1] == ("🎁 Открыть сундук", "chest")
    pts = json.loads(db.rows[S].rewards or "{}").get("points", 0)
    out = await press(S, "chest"); assert "🎁" in last(out) and "Следующий сундук — завтра" in last(out)
    assert "1 из 30" in last(out) and ("📒 Альбом", "album") in btns(out)                     # первый — наклейка
    out = await press(S, "chest"); assert "уже открыт" in last(out)
    out = await press(S, "subjects_3"); assert ("🎁 Открыть сундук", "chest") not in btns(out)
    out = await press(S, "album"); assert "📒 Альбом наклеек: 1 из 30" in last(out) and "новый завтра" in last(out)
    # звание видно в меню
    alb = json.loads(redis.d[f"chest_{S}"]); alb["title"] = "Звездочёт"; redis.d[f"chest_{S}"] = json.dumps(alb)
    out = await press(S, "subjects_3"); assert "🏅 Звездочёт" in out[-1]["text"]
    settings.IMAGE_CARDS = True
    try:
        redis.d.pop(f"chest_day_{S}")
        out = await press(S, "chest"); assert out[-1]["attachments"]
    finally:
        settings.IMAGE_CARDS = False
    out = await say(S, "/album"); assert "Альбом наклеек" in last(out)
    await press(S, "delete_me_yes"); assert f"chest_{S}" not in redis.d and f"chest_day_{S}" not in redis.d
    assert not WIDE, sorted({tuple(r) for r in WIDE})
    print("   Сундук дня: после 3 заданий, раз в день, наклейки/факты/звания/очки, альбом, гарантия редкой: OK")


async def test_lightning():
    """«⚡ Молния»: 60 секунд, 3 жизни, вопросы без ИИ, рекорд, старые кнопки, картинка."""
    import random as _r
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import app.services.progress as prog
    from app.services import lightning as Z
    rng = _r.Random(3)
    for g in range(1, 12):
        for mode in ("math", "eng"):
            for _ in range(300):
                q = Z.question(mode, g, rng); n = 3 if g <= 4 else 4
                assert len(q["options"]) == n == len(set(q["options"])) and 0 <= q["answer"] < n, (g, q)
    assert Z.question("math", 3, _r.Random(1))["text"].endswith("= ?")
    assert Z.points_for(14) == 2 and Z.rank(30).startswith("Повелитель")
    real_now = prog.now
    t0 = _dt(2026, 11, 2, 10, 0, tzinfo=_tz.utc); clock = [t0]
    prog.now = lambda: clock[0]
    try:
        S = 9821
        await say(S, "/start"); await say(S, "Даня"); await press(S, "grade_1"); out = await press(S, "subjects_1")
        assert ("⚡ Молния — на скорость", "zap") in btns(await press(S, "games"))
        out = await press(S, "zap"); assert [p for _, p in btns(out)][:1] == ["zap_go_math"]           # 1 класс — только счёт
        out = await press(S, "zap_go_math"); t = last(out)
        assert t.startswith("🏁 Поехали!") and "⏱ 60 с" in t and "❤️❤️❤️" in t and calls["judge"] == calls["judge"]
        def answer(out, right=True):
            st = json.loads(redis.d[f"zap_{S}"]); q = st["q"]
            i = q["answer"] if right else (q["answer"] + 1) % len(q["options"])
            return f"zap_a_{st['game']}_{st['n']}_{i}"
        for _ in range(5):
            clock[0] += _td(seconds=5); out = await press(S, answer(out))
        assert last(out).startswith("✅ Верно!") and "⚡ 5" in last(out) and "⏱ 35 с" in last(out)
        stale = answer(out)
        out = await press(S, answer(out, False)); assert last(out).startswith("❌ Было:") and "❤️❤️🖤" in last(out)
        out = await press(S, stale); assert "уже закончилась" in last(out)                         # старая кнопка
        out = await press(S, answer(out, False)); out = await press(S, answer(out, False))
        assert "Жизни закончились" in last(out) and "Результат: 5 верных" in last(out) and "Новый рекорд" in last(out)
        assert json.loads(redis.d[f"zap_best_{S}"]) == {"math": 5} and f"zap_{S}" not in redis.d
        assert json.loads(db.rows[S].rewards)["points"] >= 1 and ("⚡ Ещё раз", "zap_go_math") in btns(out)
        # время вышло: ответ после 60-й секунды не засчитывается
        out = await press(S, "zap_go_math"); clock[0] += _td(seconds=61)
        out = await press(S, answer(out)); assert "Время вышло" in last(out) and "Результат: 0" in last(out)
        assert "Рекорд: 5" in last(out)
        # стоп и картинка
        settings.IMAGE_CARDS = True
        try:
            out = await press(S, "zap_go_math"); out = await press(S, "zap_stop")
            assert out[-1]["attachments"] and "Игра остановлена" in last(out)
        finally:
            settings.IMAGE_CARDS = False
        out = await press(S, "zap"); assert "🔢 Счёт: 5" in last(out)
        # 5 класс: слова тоже есть, 4 варианта
        E = 9822
        await say(E, "/start"); await say(E, "Ира"); await press(E, "grade_5")
        out = await say(E, "/zap"); assert [p for _, p in btns(out)][:2] == ["zap_go_math", "zap_go_eng"]
        out = await press(E, "zap_go_eng"); assert len(json.loads(redis.d[f"zap_{E}"])["q"]["options"]) == 4
        assert "«" in last(out)
        await press(S, "delete_me_yes"); assert f"zap_best_{S}" not in redis.d
    finally:
        prog.now = real_now
    assert not WIDE, sorted({tuple(r) for r in WIDE})
    print("   Молния: 60 секунд, 3 жизни, счёт и слова без ИИ, рекорд, старые кнопки, картинка: OK")


async def test_flashcards():
    """«🃏 Карточки»: набор по теме от ИИ один раз на класс, интервальное повторение, «Не помню» — ещё раз."""
    from datetime import date as _d
    from app.services import flashcards as F
    from app.services.flashcards import parse_flashcards
    assert parse_flashcards("КАРТОЧКА: Столица России? || Москва\n2. КАРТОЧКА 2: 2+2? || 4\nмусор\nКАРТОЧКА: столица россии? || дубль") == \
        [{"front": "Столица России?", "back": "Москва"}, {"front": "2+2?", "back": "4"}]
    st = {}; t = _d(2026, 11, 2)
    assert F.add_deck(st, [{"front": "A", "back": "1"}, {"front": "B", "back": "2"}], "История", "Тема", t) == 2
    assert F.add_deck(st, [{"front": "a", "back": "1"}], "История", "Тема", t) == 0                    # без дублей
    a, b = F.card_id("A"), F.card_id("B")
    F.grade_card(st, a, True, t); assert st["cards"][a]["due"] == "2026-11-03" and st["cards"][a]["box"] == 1
    F.grade_card(st, a, True, _d(2026, 11, 3)); assert st["cards"][a]["due"] == "2026-11-06"
    F.grade_card(st, b, False, t); assert F.due_cards(st, t) == [b] and F.next_due(st, t) == "2026-11-06"

    S = 9831
    await say(S, "/start"); await say(S, "Лиза"); await press(S, "grade_8")
    out = await press(S, "games"); assert ("🃏 Карточки", "fc") in btns(out)
    out = await press(S, "fc"); assert "Карточки для запоминания" in last(out) and ("➕ Новый набор", "fc_new") in btns(out)
    out = await press(S, "fc_new"); assert ("История", "fc_s_hist") in btns(out)
    out = await press(S, "fc_s_hist"); assert "напиши" in last(out)
    n = len(calls.get("fc", [])); out = await say(S, "Петр I")                                  # своя тема сообщением
    assert "Набор «Петр I»: 5 новых" in last(out) and ("🔁 Повторить (5)", "fc_go") in btns(out) and len(calls["fc"]) == n + 1
    S2 = 9832
    await say(S2, "/start"); await say(S2, "Олег"); await press(S2, "grade_8")
    await press(S2, "fc_s_hist"); out = await say(S2, "петр i"); assert len(calls["fc"]) == n + 1       # набор из кеша
    out = await press(S, "fc_go"); assert "1 из 5" in last(out) and "❓" in last(out)
    out = await press(S, "fc_show"); assert "💡 ответ" in last(out) and btns(out) == [("✅ Помню", "fc_yes"), ("❌ Не помню", "fc_no")]
    out = await press(S, "fc_no"); assert "2 из 6" in last(out)                                  # забытая — в конец
    for _ in range(5):
        await press(S, "fc_show"); out = await press(S, "fc_yes")
    assert "Тренировка готова" in last(out) and "Помню: 5, повторим ещё: 1" in last(out) and "Следующее повторение" in last(out)
    assert db.rows[S].streak_days == 1                                                         # тренировка = занятие
    out = await press(S, "fc_yes"); assert "уже закончилась" in last(out)
    out = await press(S, "fc"); assert "На сегодня всё повторено" in last(out)
    await press(S, "delete_me_yes"); assert f"fc_{S}" not in redis.d
    assert not WIDE, sorted({tuple(r) for r in WIDE})
    print("   Карточки: набор по теме (ИИ один раз на класс), интервалы 1-3-7-14-30, «Не помню» — ещё раз: OK")


async def test_exam_variant():
    """Пробный вариант ОГЭ/ЕГЭ: 10 заданий в формате экзамена, время, прогноз, «было → стало», что подтянуть."""
    from app.services import exam_forecast as X
    assert [X.oge_mark(s) for s in (0.2, 0.4, 0.7, 0.9)] == [2, 3, 4, 5]
    assert X.ege_score(0) == 0 and X.ege_score(1) == 100 and 55 < X.ege_score(0.5) < 70
    assert X.forecast("ege", 3, 10)["text"].startswith("≈") and X.forecast("oge", 9, 10)["value"] == 5
    assert X.duration_text(75) == "1 мин 15 с" and X.duration_text(9) == "9 с"
    import app.services.progress as prog
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    real_now = prog.now; clock = [_dt(2026, 11, 2, 10, 0, tzinfo=_tz.utc)]; prog.now = lambda: clock[0]
    try:
        S = 9841
        await say(S, "/start"); await say(S, "Артём"); await press(S, "grade_9")
        out = await press(S, "exam_oge_9"); assert btns(out)[0] == ("🧪 Пробный вариант", "exvarl_oge_9")
        out = await press(S, "exvarl_oge_9"); assert ("Математика", "exvar_oge_9_math") in btns(out)
        level_before = await UserHandler(api, db, redis)._subject_level(S, "math")
        out = await press(S, "exvar_oge_9_math")
        assert calls["scope"][-1] == "exam_oge" and calls["tests"][-1][2] == 10 and calls["tests"][-1][3] == 4
        assert any("🧪 Пробный вариант ОГЭ" in m["text"] for m in out)
        def pick(out, label): return next(p for t, p in btns(out) if p.startswith("sta_") and t.split(" ", 1)[1] == label)
        for i in range(10):
            clock[0] += _td(seconds=30); out = await press(S, pick(out, "B2" if i < 7 else "A1"))
        t = last(out)
        assert "Пробный вариант готов" in t and "7 из 10" in t and "⏱ Время: 5 мин 00 с" in t, t
        assert "Прогноз ОГЭ: оценка «4»" in t and "🎯 Что подтянуть: «Тема 8», «Тема 9», «Тема 10»" in t
        assert ("🧪 Ещё вариант", "exvar_oge_9_math") in btns(out)
        assert await UserHandler(api, db, redis)._subject_level(S, "math") == level_before          # уровень не трогаем
        out = await press(S, "exvar_oge_9_math")
        for i in range(10):
            out = await press(S, pick(out, "B2"))
        assert "📈 Прошлый вариант: 70% → сейчас: 100%" in last(out) and "оценка «5»" in last(out)
        out = await press(S, "exvarl_oge_9"); assert "Математика: последний — 100%" in last(out)
        E = 9842                                                                                   # 11 класс — ЕГЭ в баллах
        await say(E, "/start"); await say(E, "Вика"); await press(E, "grade_11")
        out = await press(E, "exvar_ege_11_rus")
        for i in range(10):
            out = await press(E, pick(out, "B2" if i < 5 else "A1"))
        assert "Прогноз ЕГЭ: ≈ 62 баллов из 100" in last(out), last(out)
        out = await press(E, "exam_vpr_7"); assert ("🧪 Пробный вариант", "exvarl_vpr_7") not in btns(out)   # для ВПР нет
        await press(S, "delete_me_yes"); assert f"exvar_{S}" not in redis.d
    finally:
        prog.now = real_now
    assert not WIDE, sorted({tuple(r) for r in WIDE})
    print("   Пробный вариант ОГЭ/ЕГЭ: 10 заданий на время, прогноз оценки/балла, было → стало, что подтянуть: OK")


def test_redis_transfer():
    """Переезд на сервер: данные Redis (строки и их срок жизни) выгружаются в файл и загружаются обратно."""
    import types, tempfile
    class R:
        def __init__(self): self.d = {"user_state:tclass_A": ("Класс", None), "user_state:x": ("1", 500), "lst": (["z"], None)}
        async def scan_iter(self, count=0):
            for k in list(self.d): yield k
        async def type(self, k): return "list" if isinstance(self.d[k][0], list) else "string"
        async def get(self, k): return self.d[k][0]
        async def ttl(self, k): return self.d[k][1] or -1
        async def set(self, k, v, ex=None): self.d[k] = (v, ex)
        async def aclose(self): pass
    r = R()
    pkg = types.ModuleType("redis"); aio = types.ModuleType("redis.asyncio"); aio.from_url = lambda *a, **k: r; pkg.asyncio = aio
    saved = {k: sys.modules.get(k) for k in ("redis", "redis.asyncio")}
    sys.modules["redis"], sys.modules["redis.asyncio"] = pkg, aio
    try:
        sys.modules.pop("app.tools.redis_transfer", None)
        from app.tools import redis_transfer as T
        path = os.path.join(tempfile.mkdtemp(), "r.json")
        asyncio.run(T.export(path))
        r.d = {}
        asyncio.run(T.load(path))
        assert r.d == {"user_state:tclass_A": ("Класс", None), "user_state:x": ("1", 500)}, r.d
    finally:
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v
    print("   Переезд на сервер: данные Redis выгружаются и загружаются с тем же сроком жизни: OK")


async def test_miniapp_games():
    """Мини-приложение: Мой маяк и Сундук в данных, Молния (счёт на сервере), сундук, альбом, карточки, картинка маяка."""
    from contextlib import asynccontextmanager
    import app.services.progress as prog
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from app.webapp.api import ApiError, MiniAppAPI
    @asynccontextmanager
    async def session_factory():
        yield db
    A = MiniAppAPI(lambda session: UserHandler(api, session, redis), session_factory, "mayachok_bot")
    G = 9851
    await say(G, "/start"); await say(G, "Гриша"); await press(G, "grade_6")
    st = await A.state(G); g = st["games"]
    assert g["lighthouse"]["built"] == 0 and g["lighthouse"]["next"]["title"] == "фундамент"
    assert g["chest"] == {"ready": False, "opened": False, "left": 3, "need": 3, "collected": 0, "total": 30, "title": None}
    assert [m["id"] for m in g["zap"]["modes"]] == ["math", "eng"] and g["cards"] == {"available": True, "due": 0}
    # Молния: вопросы пачкой, счёт считает сервер (3 ошибки — конец), рекорд
    real_now = prog.now; clock = [_dt(2026, 11, 2, 10, 0, tzinfo=_tz.utc)]; prog.now = lambda: clock[0]
    try:
        z = await A.zap_start(G, "math")
        assert len(z["questions"]) == 40 and z["seconds"] == 60 and z["lives"] == 3
        right = [q["answer"] for q in z["questions"]]
        bad = lambda i: (right[i] + 1) % len(z["questions"][i]["options"])
        answers = right[:5] + [bad(5)] + right[6:8] + [bad(8), bad(9)] + right[10:20]   # 3-я ошибка — на 10-м
        clock[0] += _td(seconds=59)
        r = await A.zap_finish(G, z["game"], answers)
        assert r["score"] == 7 and r["wrong"] == 3 and r["new_record"] and r["best"] == 7 and r["reward"]
        try: await A.zap_finish(G, z["game"], answers); assert False
        except ApiError as e: assert e.code == "expired"                                    # второй раз нельзя
        z = await A.zap_start(G, "math"); clock[0] += _td(seconds=120)
        r = await A.zap_finish(G, z["game"], [q["answer"] for q in z["questions"]])
        assert r["score"] == 0 and r["best"] == 7                                           # слишком поздно
        try: await A.zap_start(G, "chess"); assert False
        except ApiError as e: assert e.status == 400
    finally:
        prog.now = real_now
    # Сундук: закрыт → 3 задания → открыт один раз
    try: await A.chest_open(G); assert False
    except ApiError as e: assert e.code == "locked"
    h = UserHandler(api, db, redis)
    for _ in range(3): await h._count_solved(G, 1)
    assert (await A.state(G))["games"]["chest"]["ready"]
    p = await A.chest_open(G); assert p["kind"] == "sticker" and p["collected"] == 1 and p["emoji"]
    try: await A.chest_open(G); assert False
    except ApiError as e: assert e.code == "opened"
    al = await A.album(G); assert len(al["stickers"]) == 30 and sum(1 for s in al["stickers"] if s["count"]) == 1
    st = await A.state(G); assert st["games"]["lighthouse"]["built"] == 1 and st["games"]["lighthouse"]["today"]
    # Карточки: набор, повторение, 5 карточек за день = занятие (один раз)
    tp = await A.cards_topics(G, "hist"); assert isinstance(tp["topics"], list)
    n = await A.cards_new(G, "hist", "Древний Рим"); assert n["added"] == 5
    c = await A.cards(G); assert c["due_total"] == 5 and c["due"][0]["front"] and "Древний Рим" in c["decks"]
    before = db.rows[G].streak_days
    for i, card in enumerate(c["due"]):
        r = await A.cards_grade(G, card["id"], i != 0)
        assert bool(r["reward"]) == (i == 4)
    assert (await A.cards(G))["due_total"] == 1 and db.rows[G].streak_days == before
    for bad_args in (("nope", True), (c["due"][1]["id"], "yes")):
        try: await A.cards_grade(G, *bad_args); assert False
        except ApiError as e: assert e.status == 400
    try: await A.cards_new(G, "chem", "Атомы"); assert False
    except ApiError as e: assert e.status == 400
    # картинка маяка для приложения
    from app.services import cards as _cards, lighthouse as _L
    if _cards.available():
        assert _cards.lighthouse_scene(5, _L.MILESTONES)[:2] == b"\xff\xd8"                 # JPEG
    await press(G, "delete_me_yes"); assert f"fcapp_{G}" not in redis.d
    print("   Мини-приложение: маяк, сундук и альбом, Молния со счётом на сервере, карточки: OK")


async def test_miniapp_account():
    """Мини-приложение целиком: знакомство (ученик/родитель/учитель), согласие, настройки, кабинет учителя."""
    from contextlib import asynccontextmanager
    from app.webapp.api import ApiError, MiniAppAPI
    @asynccontextmanager
    async def session_factory():
        yield db
    A = MiniAppAPI(lambda session: UserHandler(api, session, redis), session_factory, "mayachok_bot")
    async def fails(coro, status=400):
        try: await coro; assert False, "ожидалась ошибка"
        except ApiError as e: assert e.status == status, (e.status, e.message); return e
    # 1) Ученик знакомится прямо в приложении: согласие, имя, класс, часовой пояс телефона
    U = 9861
    assert (await A.state(U))["role"] == "new"
    assert "согласие" in (await A.onboarding_info())["consent_text"]
    settings.CONSENT_REQUIRED = True
    try:
        e = await fails(A.onboard_student(U, "Оля", 7, 300, False)); assert e.code == "consent"
        await fails(A.onboard_student(U, "", 7, 300, True)); await fails(A.onboard_student(U, "Оля", 13, 300, True))
        n = len(api.sent)
        assert (await A.onboard_student(U, "  Оля  ", 7, 300, True))["ok"] and len(api.sent) == n   # в чат ничего не пишем
        assert await UserHandler(api, db, redis).has_consent(U)
    finally:
        settings.CONSENT_REQUIRED = False
    st = await A.state(U); assert st["role"] == "student" and st["name"] == "Оля" and st["grade"] == 7
    assert db.rows[U].tz_offset == 300
    # 2) Настройки: имя, класс, увлечения
    cfg = await A.settings(U); assert cfg["name"] == "Оля" and cfg["grade"] == 7 and cfg["interests"] == [] and cfg["parents"] == []
    cfg = await A.save_settings(U, {"name": "Ольга", "grade": 8, "interests": ["foot", "space"]})
    assert cfg["name"] == "Ольга" and cfg["grade"] == 8 and cfg["interests"] == ["foot", "space"]
    await fails(A.save_settings(U, {"interests": ["chess"]})); await fails(A.save_settings(U, {"grade": 0}))
    # 3) Родитель подключается кодом в приложении, ребёнок получает уведомление в чат
    code = (await A.parent_code(U))["code"]; assert len(code) == 6
    P = 9862
    await fails(A.link_parent(P, "000000"))
    n = len(api.sent)
    r = await A.link_parent(P, code); assert r["children"] == [U]
    assert any("подключился родитель" in m["text"] for m in api.sent[n:])
    assert (await A.state(P))["role"] == "parent" and (await A.settings(U))["parents"][0]["id"] == P
    cfg = await A.unlink_parent(U, P); assert cfg["parents"] == [] and (await A.state(P))["role"] == "new"
    # 4) Учитель: запрос → кабинет сразу, профиль, класс, ученик вступает, задание, результаты, напоминание
    T = 9863
    r = await A.teacher_request(T); assert r["teacher"] and (await A.state(T))["role"] == "teacher"
    home = await A.teacher_home(T); assert home["profile"]["kind"] is None and home["classes"] == []
    await fails(A.teacher_profile(T, "subject", [], "Анна Петровна"))
    home = await A.teacher_profile(T, "subject", ["math", "phys"], "Анна Петровна")
    assert home["profile"]["name"] == "Анна Петровна" and 8 in home["grades"] and 3 not in home["grades"]
    c = (await A.teacher_class_new(T, 8, "8Б"))["code"]
    assert (await A.teacher_home(T))["classes"][0]["code"] == c
    await fails(A.join_class(U, "ZZZZZZ"))
    cfg = await A.join_class(U, c.lower()); assert cfg["tclass"]["name"] == "8Б" and "Анна Петровна" in cfg["tclass"]["teachers"]
    cls = await A.teacher_class(T, c)
    assert [s["name"] for s in cls["students"]] == ["Ольга"] and [s["id"] for s in cls["subjects"]] == ["math", "phys"]
    await fails(A.teacher_assign(T, c, "rus", "Причастия"))
    n = len(api.sent)
    await A.teacher_assign(T, c, "math", "Квадратные уравнения")
    assert any("Задание от учителя — Анна Петровна" in m["text"] for m in api.sent[n:])
    a = (await A.teacher_class(T, c))["assignments"][0]; assert a["topic"] == "Квадратные уравнения" and a["done"] == 0
    det = await A.teacher_assignment(T, c, a["id"]); assert det["students"][0]["done"] is False
    assert "Напомнил" in (await A.teacher_remind(T, c, a["id"]))["note"]
    assert "Ольга" in (await A.teacher_student(T, c, U))["report"]
    await fails(A.teacher_student(T, c, 1), 404); await fails(A.teacher_class(9999, c), 404)
    await fails(A.teacher_home(U), 403)
    assert await A.teacher_photo(T, c, a["id"], U) is None
    # другой учитель подключается к тому же классу по коду
    T2 = 9864
    await A.teacher_request(T2); await A.teacher_profile(T2, "subject", ["phys"], "Иван Ильич")
    assert (await A.teacher_class_link(T2, c))["code"] == c
    await fails(A.teacher_class_link(T2, "NOPE00"))
    home2 = await A.teacher_class_remove(T2, c)
    assert home2["classes"] == [] and (await A.teacher_class(T, c))["code"] == c          # класс остался у владельца
    # 5) Ученик выходит из класса и удаляет данные
    cfg = await A.leave_class(U); assert cfg["tclass"] is None
    assert (await A.delete_me(U))["ok"] and (await A.state(U))["role"] == "new"
    await A.teacher_class_remove(T, c); assert (await A.teacher_home(T))["classes"] == []
    assert "Политика" in (await A.privacy())["text"]
    print("   Мини-приложение целиком: знакомство, согласие, настройки, родитель по коду, кабинет учителя: OK")


asyncio.run(main())
asyncio.run(test_visual())
asyncio.run(test_miniapp())
asyncio.run(test_miniapp_games())
asyncio.run(test_miniapp_account())
asyncio.run(test_savings())
asyncio.run(test_consent())
asyncio.run(test_teacher())
asyncio.run(test_lighthouse())
asyncio.run(test_chest())
asyncio.run(test_lightning())
asyncio.run(test_flashcards())
asyncio.run(test_exam_variant())
test_redis_transfer()
test_normalize_voice()
test_audio_ffmpeg()
test_progress_rules()
test_ai_task_parsing()
test_daily_rules()
test_rewards_rules()
test_diagnostic_files()
test_speakable()
test_voice()
test_timezones()
test_schedule_rules()
test_programs()
test_school_programs()
test_documents()
test_download_reads_whole_file()
test_task_cache()
test_html_messages()
shutil.rmtree(tmp)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
