import asyncio
import base64
import random
import aiohttp
import logging
import re
import time

from app.services.flashcards import parse_flashcards
from app.core.config import settings
from app.services.task_utils import PRAISE, needs_picture, normalize_answer, parse_options
from app.services import voice as voice_tools

logger = logging.getLogger(__name__)

from datetime import date

# Ориентиры по программе математики (ФОП) — чтобы задания были именно уровня класса
GRADE_LEVEL = {
    1: "числа в пределах 20, сложение и вычитание, сравнение чисел, задачи в одно действие; без умножения, дробей и уравнений.",
    2: "числа до 100, сложение и вычитание с переходом через десяток, смысл умножения и деления, таблица умножения на 2 и 3, задачи в 1–2 действия.",
    3: "числа до 1000, вся таблица умножения и деления, внетабличное умножение и деление, деление с остатком, задачи в 2–3 действия, периметр и площадь прямоугольника.",
    4: "многозначные числа, письменные вычисления, умножение и деление на двузначное число, задачи на движение и куплю-продажу в 2–4 действия, доли; уровень ВПР 4 класса.",
    5: "натуральные числа, обыкновенные и десятичные дроби, простые уравнения, площадь и объём, текстовые задачи в несколько действий.",
    6: "делимость, НОД и НОК, все действия с дробями, отношения и пропорции, проценты, положительные и отрицательные числа, координатная плоскость.",
    7: "алгебра: линейные уравнения, степени, многочлены, формулы сокращённого умножения, линейная функция; геометрия: треугольники, признаки равенства, параллельные прямые.",
    8: "алгебра: квадратные корни, квадратные уравнения, алгебраические дроби, неравенства; геометрия: четырёхугольники, площади, теорема Пифагора, подобие.",
    9: "уровень ОГЭ: квадратичная функция, системы уравнений и неравенств, прогрессии, вероятность; геометрия: окружность, тригонометрия в треугольнике, векторы.",
    10: "тригонометрия, начала производной, стереометрия: параллельность и перпендикулярность в пространстве, многогранники.",
    11: "уровень ЕГЭ: производная и первообразная, показательные и логарифмические уравнения и неравенства, стереометрия, вероятность.",
}


# Ориентиры по программе (ФОП) для остальных предметов начальной школы — чтобы задание было по своему предмету
SUBJECT_HINTS = {
    "окружающий мир": {
        1: "природа и рукотворный мир, времена года, растения (части растения) и животные (звери, птицы, рыбы, насекомые), "
           "правила безопасности дома и на улице, режим дня, семья, Родина.",
        2: "живая и неживая природа, погода, деревья, кустарники и травы, группы животных, здоровье человека, "
           "безопасность, город и село, стороны горизонта, планета Земля.",
        3: "тела и вещества, воздух и вода, круговорот воды, почва, растения, грибы, цепи питания, "
           "организм человека и здоровье, страны и народы.",
        4: "природные зоны России, Солнечная система, моря, реки и горы России, история нашей страны, "
           "права и обязанности граждан, родной край.",
    },
    "русский язык": {
        1: "звуки и буквы, гласные и согласные, твёрдые и мягкие согласные, слоги, ударение, жи-ши, ча-ща, чу-щу, "
           "заглавная буква в именах и кличках, перенос слов.",
        2: "мягкий знак, парные согласные, безударные гласные в корне, части речи (общее понятие), предложение и текст.",
        3: "состав слова (корень, приставка, суффикс, окончание), части речи, род и число существительных, "
           "правописание приставок, непроизносимые согласные.",
        4: "склонение и падежи существительных, спряжение глаголов, -тся и -ться, однородные члены, местоимения.",
    },
    "литературное чтение": {
        1: "сказки, загадки, пословицы, считалки, стихи о природе, понимание короткого текста.",
        2: "русские народные сказки, произведения Пушкина, Толстого, Носова, Чуковского, Михалкова; "
           "жанры: сказка, рассказ, стихотворение, басня.",
        3: "былины, басни Крылова, произведения Пушкина, Лермонтова, Толстого, Паустовского; тема и главная мысль текста.",
        4: "былины и летописи, произведения Ершова, Пушкина, Чехова, Бажова, Одоевского; герои и их поступки.",
    },
    "информатика": {
        5: "компьютер и его устройства, файлы и папки, единицы информации (бит, байт), алгоритмы и исполнители "
           "(команды, линейные программы), кодирование информации (простые шифры и коды), логические задачи.",
        6: "алгоритмы с ветвлениями и циклами для исполнителей, информационные модели (описанные словами схемы и графы), "
           "кодирование и декодирование, двоичные числа (начала), логические задачи и высказывания.",
        7: "информация и её измерение (бит, байт, Кбайт, Мбайт), информационный объём текста, кодирование текста и "
           "изображений, скорость передачи данных, устройство компьютера, файловая система, поиск информации; "
           "уровень ВПР по информатике.",
    },
    "английский язык": {
        2: "английский алфавит, приветствия, цвета, числа до 10, животные, семья, I am, I have got.",
        3: "Present Simple, have got, can, предлоги места, еда, дом, части тела.",
        4: "Present Continuous, Past Simple правильных глаголов, времена года и погода, распорядок дня, "
           "степени сравнения прилагательных.",
    },
}
MATH_SUBJECTS = ("математика", "алгебра", "геометрия")


def program_hint(subject: str, grade: int) -> str:
    """Ориентир по программе класса для предмета (или пустая строка)."""
    name = subject.strip().lower()
    if name in MATH_SUBJECTS:
        return GRADE_LEVEL.get(grade, "")
    return SUBJECT_HINTS.get(name, {}).get(grade, "")


# Пример на вычисление вида «12 + 5» — признак того, что вместо нужного предмета получилась математика
# («2-3 слова», «в 7:30», «1/2 яблока» вычислением не считаем: у минуса, двоеточия и дроби нужны пробелы)
_ARITHMETIC = re.compile(r"\d+\s*[+×*÷]\s*\d+|\d+\s+[-−:/]\s+\d+")


def looks_like_wrong_subject(task_text: str, subject: str) -> bool:
    """True, если по нематематическому предмету ИИ прислал задачу на вычисления."""
    if subject.strip().lower() in MATH_SUBJECTS or "физик" in subject.lower() or "хими" in subject.lower() \
            or "информатик" in subject.lower():
        return False
    return bool(_ARITHMETIC.search(task_text))


# Сколько английских слов допустимо в задании по английскому: в младших классах условие — по-русски
ENGLISH_WORD_LIMIT = {2: 6, 3: 8, 4: 12, 5: 20, 6: 30}
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def english_rule(subject: str, grade: int) -> str:
    """Правило о языке задания по английскому для младших классов."""
    if "английск" not in subject.lower():
        return ""
    if grade <= 4:
        return (
            "Ученик только начинает учить английский и знает мало слов. Условие задания пиши ПО-РУССКИ. "
            "По-английски — только те слова или очень короткие фразы, которые проверяются в задании "
            f"(всего не больше {ENGLISH_WORD_LIMIT.get(grade, 6)} английских слов), и только простые слова из программы класса. "
            "Хорошие примеры: «Как по-английски „кошка“?», «Что значит слово „dog“?», «Вставь пропущенное слово: I ... a cat»."
        )
    if grade <= 6:
        return ("Условие задания пиши по-русски. Английские предложения в задании — короткие и простые, "
                "только из слов, которые проходят в этом классе.")
    return ""


def too_much_english(task_text: str, subject: str, grade: int) -> bool:
    """True, если в задании для младших классов слишком много английского текста."""
    limit = ENGLISH_WORD_LIMIT.get(grade)
    if limit is None or "английск" not in subject.lower():
        return False
    return len(_LATIN_WORD.findall(task_text)) > limit


def school_term_hint(today: date | None = None) -> str:
    """Какие темы сейчас проходят: подсказка для ИИ по месяцу учебного года."""
    month = (today or date.today()).month
    if month in (9, 10):
        return "сейчас первая четверть — возьми тему, которую обычно проходят в сентябре–октябре этого класса."
    if month in (11, 12):
        return "сейчас вторая четверть — возьми тему, которую обычно проходят в ноябре–декабре этого класса."
    if month in (1, 2, 3):
        return "сейчас третья четверть — возьми тему, которую обычно проходят в январе–марте этого класса."
    if month in (4, 5):
        return "сейчас четвёртая четверть — возьми тему конца учебного года этого класса."
    return "сейчас каникулы — возьми важную тему для повторения за этот класс."


def textbook_rule(textbook: str | None) -> str:
    """Подсказка ИИ: ученик учится по такому-то учебнику — термины, обозначения и подача как в нём."""
    if not textbook:
        return ""
    return (f"Ученик учится по учебнику: {textbook}. Используй термины, обозначения и способы записи, принятые в этом "
            "учебнике (если знаешь их), но не копируй задания из учебника — составляй свои.\n")


def today_hint(today: date | None = None) -> str:
    """«сегодня 31 октября, идёт первая четверть» — чтобы ИИ понимал, какие темы уже прошли."""
    today = today or date.today()
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
    quarter = {9: "первая", 10: "первая", 11: "вторая", 12: "вторая", 1: "третья", 2: "третья", 3: "третья",
               4: "четвёртая", 5: "четвёртая"}.get(today.month)
    part = f"идёт {quarter} четверть" if quarter else "сейчас летние каникулы, учебный год пройден целиком"
    return f"сегодня {today.day} {months[today.month - 1]}, {part}"


def _age_style(grade: int | None) -> str:
    if grade is None:
        return "Возраст ученика неизвестен: пиши просто и понятно."
    if grade <= 4:
        return ("Это младший школьник: короткие предложения, простые слова, примеры из жизни "
                "(конфеты, игрушки, прогулка, животные), никаких сложных терминов без объяснения. "
                "Режим «Играйка»: ты добрый ведущий игры — подбадривай, радуйся успехам, "
                "иногда упоминай звёзды ⭐, которые ученик зарабатывает за задания.")
    if grade <= 8:
        return ("Это ученик средней школы: объясняй понятно, с примерами, вводи термины и сразу их поясняй. "
                "Режим «Тренер»: ты как спортивный тренер — помогаешь ставить цели, отмечаешь рост, "
                "поддерживаешь регулярность занятий.")
    if grade == 9:
        return ("Это девятиклассник: говори как со взрослым, используй точные термины, связывай темы с ОГЭ. "
                "Режим «Тренер»: помогай ставить цели и держать ритм подготовки.")
    return ("Это старшеклассник: говори как со взрослым, используй точные термины, "
            "связывай темы с заданиями ЕГЭ, экономь его время. "
            "Режим «Стратег»: помогай планировать подготовку, говори по делу, без игровых элементов.")


def tutor_persona(context: dict | None = None) -> str:
    """Системный промпт репетитора: характер, стиль, правила безопасности и то, что известно об ученике."""
    ctx = context or {}
    grade = ctx.get("grade")
    known = []
    if ctx.get("name"):
        known.append(f"ученика зовут {ctx['name']}")
    if grade:
        known.append(f"он учится в {grade} классе")
    if ctx.get("subject"):
        known.append(f"сейчас занимается предметом «{ctx['subject']}»")
    known_part = ("Что известно: " + ", ".join(known) + ".") if known else "Об ученике пока ничего не известно."

    return (
        "Ты — Маячок, умный и внимательный ИИ-репетитор в мессенджере MAX: добрый мальчик-маяк, который "
        "освещает ученику дорогу в учёбе. Ты сильный школьный учитель и при этом друг. О себе всегда говори "
        "в мужском роде («я понял», «я рад», «я запомнил», «я проверил»). Своё имя называй, только если "
        "спросят, кто ты, или если это уместно в приветствии. Иногда, к месту и не чаще раза за ответ, можешь "
        "использовать «маячные» образы («посвечу тебе подсказкой», «держим курс»), но без перебора. Твоя цель — чтобы ученик понял и научился сам, а не просто получил ответ.\n"
        f"{known_part}\n"
        f"{_age_style(grade)}\n"
        "Как ты общаешься:\n"
        "- Живо, по-человечески и с уважением. Без канцелярита, без сюсюканья и без нарочитого сленга.\n"
        "- Коротко: обычно 2–6 предложений. Если нужно длинное объяснение — по шагам, каждый шаг с новой строки.\n"
        "- Объясняешь так: сначала суть одной фразой, потом пример, потом короткий вопрос, чтобы проверить понимание.\n"
        "- Готовые ответы и решения за ученика не выдаёшь никогда: ведёшь наводящими вопросами и подсказками, "
        "разбивая задачу на маленькие шаги, пока он не дойдёт до ответа сам.\n"
        "- Хвалишь за конкретное, а не дежурными словами. Ошибки разбираешь спокойно, без упрёков.\n"
        "- Отвечаешь только то, в чём уверен. Если не уверен в факте — честно говоришь об этом.\n"
        "- Если вопрос не про учёбу — коротко и дружелюбно ответь и мягко предложи вернуться к занятиям.\n"
        "- Не обсуждаешь взрослые и опасные темы. Если ребёнок пишет о чём-то тревожном (его обижают, он в опасности, "
        "ему очень плохо) — отнесись серьёзно, посоветуй рассказать взрослому, которому он доверяет, "
        "а если есть угроза жизни и здоровью — позвонить 112.\n"
        "Формат: обычный текст для мессенджера, можно 1–2 уместных эмодзи. Без LaTeX и без символов '$', '*', '#'. "
        "Формулы пиши как в тетради: x^2, 3/4, √16."
    )



def _spoiler_patterns(reference: str) -> list:
    """Как может выглядеть правильный ответ в тексте: число целиком или основа значимых слов."""
    ref = reference.lower().replace("ё", "е").strip()
    patterns = []
    for number in re.findall(r"\d+(?:[.,]\d+)?", ref):
        variants = {number, number.replace(",", "."), number.replace(".", ",")}
        patterns += [rf"(?<![\d.,]){re.escape(v)}(?![\d]|[.,]\d)" for v in variants]
    for word in re.findall(r"[a-zа-я]+", ref):
        if len(word) >= 4:
            stem = word[: max(4, len(word) - 2)]  # грубая основа: «петра» и «петр» совпадут
            patterns.append(rf"(?<![a-zа-я]){re.escape(stem)}")
    return patterns


def strip_spoilers(text: str, reference: str | None) -> str:
    """Убирает из подсказки предложения, в которых проскочил правильный ответ."""
    if not reference:
        return text
    patterns = _spoiler_patterns(reference)
    if not patterns:
        return text
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [s for s in sentences if not any(re.search(p, s.lower().replace("ё", "е")) for p in patterns)]
    if len(kept) == len(sentences):
        return text
    result = " ".join(s.strip() for s in kept if s.strip())
    return result or "Пока не совсем так 🙂 Перечитай условие внимательно и попробуй ещё раз — у тебя получится!"


def parse_task_reply(raw: str) -> dict:
    """Разбирает ответ ИИ вида «ЗАДАНИЕ: … ВАРИАНТЫ: … ОТВЕТ: … ТЕМА: …» в словарь."""
    labels = ("ЗАДАНИЕ", "ВАРИАНТЫ", "ОТВЕТ", "ТЕМА", "ПРЕДМЕТ")
    pattern = r"(?m)^\s*(" + "|".join(labels) + r")\s*:\s*"
    pieces = re.split(pattern, raw)
    result = {}
    for i in range(1, len(pieces) - 1, 2):
        result[pieces[i]] = pieces[i + 1].strip()
    return result


from app.services.levels import DEFAULT_LEVEL, LEVEL_NAMES, LEVEL_RULES  # noqa: E402,F401

_OPTION_MARK = re.compile(r"^\s*(?:[а-гa-d]|\d)\s*[).]\s+", re.IGNORECASE)


def _strip_mark(option: str) -> str:
    """«Б) 120 км» -> «120 км»."""
    return _OPTION_MARK.sub("", option).strip()


def resolve_option(answer: str | None, options: list) -> str | None:
    """Какой из вариантов имел в виду ИИ: точный текст, текст без «Б)», буква или номер варианта."""
    if not answer or not options:
        return None
    by_norm = {normalize_answer(o): o for o in options}
    for candidate in (answer, _strip_mark(answer)):
        if normalize_answer(candidate) in by_norm:
            return by_norm[normalize_answer(candidate)]
    mark = answer.strip().lower().rstrip(").")
    letters = "абвг" if re.fullmatch(r"[абвг]", mark) else "abcd"
    if re.fullmatch(r"[абвгabcd]", mark):
        index = letters.index(mark)
        return options[index] if index < len(options) else None
    if mark.isdigit() and 1 <= int(mark) <= len(options) and mark not in by_norm:
        return options[int(mark) - 1]
    return None


def parse_test_reply(raw: str) -> list:
    """Разбирает тест вида «ВОПРОС 1: … ВАРИАНТЫ: … ОТВЕТ: … ТЕМА: …» (повторяется) в список вопросов."""
    blocks = re.split(r"(?im)^\s*ВОПРОС\s*№?\s*\d+\s*[:.)]\s*", raw)
    questions = []
    for block in blocks[1:]:
        parts = parse_task_reply("ЗАДАНИЕ: " + block)
        text = (parts.get("ЗАДАНИЕ") or "").strip()
        raw_options = parts.get("ВАРИАНТЫ") or ""
        if "|" not in raw_options and "\n" in raw_options:
            raw_options = "|".join(raw_options.splitlines())
        options = [_strip_mark(o) for o in parse_options(raw_options)]
        answer = resolve_option(parts.get("ОТВЕТ"), options)
        questions.append({"q": text, "options": options, "answer": answer, "topic": (parts.get("ТЕМА") or "").strip()})
    return questions


def question_problem(question: dict, subject: str, grade: int, n_options: int) -> str | None:
    """Почему вопрос теста не годится (или None, если годится)."""
    text, options = question.get("q") or "", question.get("options") or []
    if len(text) < 10:
        return "пустой вопрос"
    if not (max(2, n_options - 1) <= len(options) <= n_options + 1):
        return "неверное число вариантов"
    if len({normalize_answer(o) for o in options}) != len(options) or any(len(o) > 90 for o in options):
        return "варианты повторяются или слишком длинные"
    if not question.get("answer"):
        return "верного ответа нет среди вариантов"
    if needs_picture(text):
        return "ссылается на рисунок"
    if looks_like_wrong_subject(text, subject):
        return "не по предмету"
    if too_much_english(text, subject, grade):
        return "слишком много английского"
    return None




def spread(items: list, count: int) -> list:
    """До count элементов, равномерно по всему списку (последний — всегда): входной тест охватывает весь пройденный
    материал, а не только первые темы года."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


class AIClient:
    def __init__(self):
        self.api_key = settings.YANDEX_API_KEY
        self.folder_id = settings.YANDEX_FOLDER_ID
        self.api_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        self.model = settings.YANDEX_GPT_MODEL.strip()
        # Полный URI можно указать прямо в .env, иначе собираем gpt://<folder_id>/<модель>
        self.model_uri = self._uri(self.model)
        fallback = (getattr(settings, "YANDEX_GPT_FALLBACK_MODEL", "") or "").strip()
        self.fallback_uri = self._uri(fallback) if fallback else None
        self._tts_primary_broken_until = 0.0   # до какого момента основной голос считаем недоступным
        # Лёгкая (дешёвая) модель для простых действий: проверить ответ, разобрать расписание или фото домашки
        light = (getattr(settings, "YANDEX_GPT_LIGHT_MODEL", "") or "").strip()
        self.light_uri = self._uri(light) if light else None
        self.usage: dict = {}   # расход за сегодня: запросы к моделям, единицы озвучки, распознавания (для /stats)

    def count_usage(self, key: str, n: int = 1) -> None:
        today = date.today().isoformat()
        if self.usage.get("date") != today:
            self.usage = {"date": today}
        self.usage[key] = self.usage.get(key, 0) + n

    def usage_report(self) -> list:
        """Строки для /stats: на что сегодня ушли деньги в Yandex Cloud."""
        u = self.usage if self.usage.get("date") == date.today().isoformat() else {}
        gpt = {k[4:]: v for k, v in u.items() if k.startswith("gpt:")}
        lines = ["💰 Расход Yandex Cloud сегодня (с запуска бота):"]
        lines.append("• YandexGPT: " + (", ".join(f"{m} — {n}" for m, n in sorted(gpt.items())) if gpt else "0") + " запросов")
        lines.append(f"• Озвучка: {u.get('tts_units', 0)} платных единиц, из кеша бесплатно: {u.get('tts_cached', 0)}")
        lines.append(f"• Распознавание фото: {u.get('ocr', 0)}")
        return lines

    def _uri(self, model: str) -> str:
        return model if "://" in model else f"gpt://{self.folder_id}/{model}"

    def _get_persona(self, mentor: str) -> str:
        mentor = str(mentor).strip()
        if mentor == "owl":
            return "Ты — Совенок Умник. Веселый, заводной друг. Общайся на равных, используй эмодзи, шути."
        elif mentor == "wizard":
            return "Ты — Магистр Знаний. Мудрый, спокойный наставник. Говори немного загадочно, но очень понятно."
        elif mentor == "teacher":
            return "Ты — Мария Ивановна. Строгая, но очень добрая учительница. Всегда поддержишь ученика."
        else:
            return "Ты - ИИ-наставник. Общайся дружелюбно."

    async def _request(
        self, system_prompt: str, user_prompt: str = "", history: list = None, temperature: float = 0.6,
        max_tokens: int = 2000, timeout: int = 40, light: bool = False,
    ) -> str:
        """light=True — простое действие: сначала дешёвая модель (YANDEX_GPT_LIGHT_MODEL), при сбое — основная."""
        headers = {"Authorization": f"Api-Key {self.api_key}", "Content-Type": "application/json"}
        messages = [{"role": "system", "text": system_prompt}]
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "text": msg["content"]})
        else:
            messages.append({"role": "user", "text": user_prompt})

        uris = [self.light_uri] if light and self.light_uri else []
        uris.append(self.model_uri)
        if self.fallback_uri and self.fallback_uri != self.model_uri:
            uris.append(self.fallback_uri)   # если основная модель недоступна — запасная
        for uri in uris:
            payload = {
                "modelUri": uri,
                "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": max_tokens},
                "messages": messages,
            }
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                    async with session.post(self.api_url, headers=headers, json=payload) as response:
                        if response.status == 200:
                            self.count_usage("gpt:" + uri.split("//", 1)[-1].split("/", 1)[-1])
                            data = await response.json()
                            text = data["result"]["alternatives"][0]["message"]["text"]
                            return self._clean_text(text)
                        error = await response.text()
                        logger.error("YandexGPT (%s) ошибка %s: %s", uri.rsplit("/", 1)[-1], response.status, error[:300])
                        if uri == self.light_uri and response.status in (400, 403, 404):
                            logger.warning("Лёгкая модель %s недоступна — простые действия идут на основной", uri)
                            self.light_uri = None
                        elif response.status in (400, 403, 404) and uri == self.model_uri and uri != uris[-1] \
                                and ("model" in error.lower() or response.status == 404):
                            logger.warning("Модель %s недоступна — дальше работаю на запасной %s", uri, uris[-1])
                            self.model_uri = uris[-1]
                        if response.status == 429:
                            await asyncio.sleep(1)
            except Exception as e:
                logger.error("Сбой YandexGPT (%s): %s", uri.rsplit("/", 1)[-1], e)
        return "Ошибка при обращении к ИИ."

    @staticmethod
    def _failed(raw: str) -> bool:
        return raw.startswith(("Ошибка при обращении к ИИ", "Ошибка сети"))

    def _clean_text(self, text: str) -> str:
        text = text.replace("$", "").replace("*", "")
        text = text.replace("\\[", "").replace("\\]", "").replace("\\(", "").replace("\\)", "")
        text = text.replace("\\\\", "\n")
        text = re.sub(r'\\begin\{.*?\}', '', text)
        text = re.sub(r'\\end\{.*?\}', '', text)
        text = text.replace("\\", "")
        return text.strip()

    async def generate_task(
        self, grade: int, subject: str, topic: str | None = None, avoid: str | None = None,
        with_options: bool = False, level: int = DEFAULT_LEVEL, textbook: str | None = None,
    ) -> tuple[str, str | None, dict]:
        """Придумывает задание. Возвращает (текст, эталонный ответ или None, {"topic": …, "options": […]}).

        Каждое задание бот решает ещё раз «с чистого листа»: если ответы не сходятся, задание выбрасывается —
        так ученик не получит задачу с неверным эталоном.
        """
        task_type = "школьное задание"
        base_subject = subject
        level = level if level in LEVEL_RULES else DEFAULT_LEVEL

        for prefix, desc in (
            ("ОГЭ ", "задание в формате ОГЭ (9 класс)"),
            ("ЕГЭ ", "задание в формате ЕГЭ (11 класс)"),
            ("ВПР ", "задание в формате Всероссийской проверочной работы (ВПР)"),
        ):
            if subject.startswith(prefix):
                task_type = desc
                base_subject = subject[len(prefix):]
                break

        # «Тест» — особая команда: тему выбирает ИИ по времени учебного года
        if topic and topic.strip().lower() != "тест":
            topic_part = f"Тема: «{topic.strip()}»."
        else:
            topic_part = f"Тему выбери сам: {school_term_hint()}"

        avoid_part = f"Не повторяй это задание и не делай похожее по числам и сюжету: «{avoid[:300]}».\n" if avoid else ""
        hint = program_hint(base_subject, grade)
        language_rule = english_rule(base_subject, grade)

        system_prompt = (
            f"Ты сильный учитель-методист и составитель заданий ВПР, ОГЭ и ЕГЭ. Составь ОДНО {task_type} "
            f"по предмету «{base_subject}» для ученика {grade} класса. {topic_part}\n"
            f"Задание должно строго соответствовать программе {grade} класса (ФОП/ФГОС): не бери темы старших классов "
            "и не упрощай до уровня младших.\n"
            + (f"Ориентир по программе {grade} класса по этому предмету: {hint}\n" if hint else "")
            + f"Задание должно быть именно по предмету «{base_subject}». Не составляй задачу по другому предмету "
            "(например, по математике), даже если в задании встречаются числа.\n"
            + (language_rule + "\n" if language_rule else "")
            + f"Уровень сложности: {LEVEL_RULES[level]}\n"
            + textbook_rule(textbook)
            + "Задание проверяет понимание, а не память: ученик должен рассуждать, сравнивать, применять правило. "
            "Никаких вопросов «назови определение» и заданий, ответ на которые виден из условия. "
            "Сюжет живой и разнообразный (не «Маша купила яблоки»), числа — не круглые, если это не мешает. "
            "В условии нет подсказок, ответ однозначный и короткий (число, слово, вариант ответа или короткое выражение).\n"
            "Задание должно быть полностью ТЕКСТОВЫМ и решаемым в переписке: не ссылайся на рисунки, фотографии, "
            "схемы, графики, карты, диаграммы и таблицы, которых нет в тексте, и не проси ученика что-то нарисовать "
            "или начертить. Если нужен объект (растение, прибор, фигура, карта) — опиши его признаки словами прямо в условии.\n"
            f"{avoid_part}"
            "Ответь СТРОГО в таком формате, без лишних слов:\n"
            "ЗАДАНИЕ: <полный текст задания>\n"
            + ("ВАРИАНТЫ: <3 коротких варианта ответа через « | »: один верный, остальные — типичные ошибки учеников, "
               "например результат неверного шага решения>\n" if with_options else "")
            + "ОТВЕТ: <верный ответ" + (", точно как один из вариантов" if with_options else "") + ">\n"
            "ТЕМА: <тема задания из школьной программы, 2–5 слов>\n"
            "Перед ответом реши задание сам от начала до конца и убедись, что ответ верный и единственный. "
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать LaTeX, символы '$' и '*'."
        )
        prompt = system_prompt
        for attempt in range(3):
            raw = await self._request(prompt, user_prompt=f"Класс: {grade}", temperature=0.6)
            if self._failed(raw):
                return raw, None, {}

            parts = parse_task_reply(raw)
            task_text = parts.get("ЗАДАНИЕ") or raw.replace("ЗАДАНИЕ:", "").strip()
            answer = parts.get("ОТВЕТ") or None
            meta = {"topic": parts.get("ТЕМА") or topic or None, "options": []}
            options = parse_options(parts.get("ВАРИАНТЫ"))
            if with_options and answer and 2 <= len(options) <= 4 and \
                    normalize_answer(answer) in [normalize_answer(o) for o in options] and \
                    len({normalize_answer(o) for o in options}) == len(options):
                meta["options"] = options

            if looks_like_wrong_subject(task_text, base_subject):
                logger.info("Задание не по предмету «%s», прошу другое: %s", base_subject, task_text[:120])
                prompt = system_prompt + (
                    f"\nВНИМАНИЕ: предыдущий вариант был задачей на вычисления, а нужен предмет «{base_subject}». "
                    "Составь задание по этому предмету без арифметики."
                )
                continue
            if too_much_english(task_text, base_subject, grade):
                logger.info("Слишком много английского для %s класса, прошу другое: %s", grade, task_text[:120])
                prompt = system_prompt + (
                    "\nВНИМАНИЕ: предыдущий вариант был почти целиком на английском. Ученик его не поймёт: "
                    "напиши условие по-русски, по-английски оставь только проверяемые слова."
                )
                continue
            if needs_picture(task_text):
                logger.info("Задание ссылается на рисунок, прошу другое (попытка %d): %s", attempt + 1, task_text[:120])
                prompt = system_prompt + (
                    "\nВНИМАНИЕ: предыдущий вариант ссылался на рисунок или просил что-то нарисовать. "
                    "Так нельзя: составь другое задание, где все данные даны словами."
                )
                continue
            if answer and await self.verify_task(task_text, answer, meta["options"]) is False:
                logger.info("Ответ к заданию не подтвердился при повторном решении, прошу другое: %s", task_text[:120])
                prompt = system_prompt + (
                    "\nВНИМАНИЕ: в предыдущем варианте ответ оказался неверным или неоднозначным. "
                    "Составь другое задание и особенно тщательно проверь ответ."
                )
                continue
            return task_text, answer, meta
        return "", None, {}

    async def solve(self, task: str, options: list | None = None) -> str | None:
        """Решает задание «с чистого листа» и возвращает только итоговый ответ (или None)."""
        options_part = ("\nВарианты ответа: " + " | ".join(options) + ". В строке ИТОГ напиши текст верного варианта."
                        if options else "")
        raw = await self._request(
            "Ты очень сильный и внимательный учитель. Реши задание самостоятельно. Рассуждай коротко по шагам "
            "(не больше 10 строк), проверь себя. Последней строкой напиши: ИТОГ: <ответ>",
            user_prompt=task + options_part, temperature=0, max_tokens=1500,
        )
        if self._failed(raw):
            return None
        found = re.findall(r"ИТОГ\s*:\s*(.+)", raw)
        return found[-1].strip().rstrip(".") if found else None

    async def verify_task(self, task: str, answer: str, options: list | None = None) -> bool | None:
        """Совпадает ли эталон с независимым решением: True / False / None (проверить не получилось)."""
        solved = await self.solve(task, options)
        if not solved:
            return None
        if normalize_answer(solved) == normalize_answer(answer):
            return True
        if options:
            chosen = resolve_option(solved, options)
            if chosen is not None:
                return normalize_answer(chosen) == normalize_answer(answer)
        return await self.judge_answer(task, solved, reference=answer)

    async def generate_test(
        self, grade: int, subject: str, count: int, n_options: int, level: int = DEFAULT_LEVEL,
        focus_topics: list | None = None, avoid: list | None = None, scope: str = "default",
        textbook: str | None = None,
    ) -> list:
        """Тест из count вопросов с вариантами ответа — каждый раз новый. Возвращает разобранные годные вопросы.

        scope: "default" — темы программы и повторение; "passed" — входной тест по темам, которые уже прошли
        с начала учебного года; "topic" — все вопросы по одной теме (задания урока на оценку).
        """
        level = level if level in LEVEL_RULES else DEFAULT_LEVEL
        hint = program_hint(subject, grade)
        language_rule = english_rule(subject, grade)
        if scope.startswith("exam_"):
            exam_name = {"oge": "ОГЭ", "ege": "ЕГЭ", "vpr": "ВПР"}.get(scope[5:], "экзамена")
            topics_part = (f"Это ПРОБНЫЙ ВАРИАНТ {exam_name} по предмету «{subject}»: вопросы в формате и по темам заданий "
                           f"первой части настоящего {exam_name} (кодификатор ФИПИ), по одному на разные номера заданий, "
                           f"от первых номеров к последним. Покрой не меньше {min(count, 8)} разных тем экзамена.")
        elif scope == "topic" and focus_topics:
            topics_part = (f"ВСЕ вопросы — строго по теме «{focus_topics[0]}» (разные стороны темы, от простого к сложному). "
                           "Это проверка после урока по этой теме.")
        elif scope == "passed" and focus_topics:
            topics_part = (f"Это входной тест по темам, которые ученик {grade} класса УЖЕ прошёл по программе "
                           f"({today_hint()}): " + "; ".join(f"«{t}»" for t in spread(focus_topics, 12))
                           + f". Бери вопросы ТОЛЬКО из этих тем (не меньше {min(count, 4)} разных, больше внимания — "
                           "последним), темы, которые будут позже, не бери.")
        elif scope == "passed":
            topics_part = (f"Это входной тест: проверь темы, которые ученик {grade} класса УЖЕ прошёл к сегодняшнему дню "
                           f"({today_hint()}): темы с начала учебного года до текущего месяца плюс 1–2 ключевые темы "
                           f"прошлого класса. Не бери темы, которые по программе будут позже. Возьми не меньше "
                           f"{min(count, 4)} разных тем.")
        elif focus_topics:
            topics_part = ("Обязательно включи вопросы на темы, которые ученику пора повторить: "
                           + "; ".join(f"«{t}»" for t in focus_topics[:4])
                           + f". Остальные вопросы — по другим важным темам программы {grade} класса.")
        else:
            topics_part = (f"Возьми не меньше {min(count, 4)} разных тем программы {grade} класса: {school_term_hint()} "
                           "Добавь 1–2 вопроса на ключевые темы прошлых четвертей.")
        avoid_part = ("Не повторяй эти вопросы и не делай похожие: " + " / ".join(a[:120] for a in avoid[:10]) + "\n"
                      if avoid else "")
        system_prompt = (
            "Ты сильный учитель-методист и составитель КИМ ВПР, ОГЭ и ЕГЭ. Составь проверочный тест "
            f"по предмету «{subject}» для ученика {grade} класса: ровно {count} вопросов с выбором ответа.\n"
            f"Тест строго по программе {grade} класса (ФОП/ФГОС) и строго по предмету «{subject}».\n"
            + (f"Ориентир по программе: {hint}\n" if hint else "")
            + (language_rule + "\n" if language_rule else "")
            + f"{topics_part}\n"
            + textbook_rule(textbook)
            + f"Уровень сложности теста: {LEVEL_RULES[level]}\n"
            "Лесенка: первые вопросы чуть проще, последние — самые трудные. Не меньше половины вопросов — на рассуждение "
            "в несколько шагов, а не на простое воспроизведение факта. Нельзя, чтобы ответ угадывался по формулировке "
            "или по длине варианта.\n"
            f"У каждого вопроса ровно {n_options} варианта ответа: один верный, остальные — правдоподобные типичные "
            "ошибки учеников (например, результат неверного шага решения). Варианты короткие (до 60 символов), "
            "разные между собой, без «все ответы верны» и «нет верного ответа». Верный вариант ставь на разные места.\n"
            "Вопросы только ТЕКСТОВЫЕ: никаких рисунков, схем, карт, графиков и таблиц, которых нет в тексте.\n"
            f"{avoid_part}"
            "Перед тем как написать ответ, реши каждый вопрос сам и убедись, что верный вариант ровно один.\n"
            "Формат — строго так для каждого вопроса, без пояснений и решений:\n"
            "ВОПРОС 1: <текст вопроса>\n"
            "ВАРИАНТЫ: <вариант> | <вариант> | …\n"
            "ОТВЕТ: <верный вариант, слово в слово как в строке ВАРИАНТЫ>\n"
            "ТЕМА: <тема, 2–5 слов>\n\n"
            f"…и так до ВОПРОС {count}. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать LaTeX, символы '$' и '*'."
        )
        raw = await self._request(system_prompt, user_prompt=f"Класс: {grade}. Предмет: {subject}.",
                                  temperature=0.7, max_tokens=5000, timeout=90)
        if self._failed(raw):
            return []
        good = []
        for question in parse_test_reply(raw):
            problem = question_problem(question, subject, grade, n_options)
            if problem:
                logger.info("Вопрос теста отброшен (%s): %s", problem, (question.get("q") or "")[:100])
                continue
            good.append(question)
        return good

    async def verify_test(self, questions: list) -> list:
        """Решает вопросы теста заново и оставляет только те, где ответ совпал с эталоном."""
        if not questions:
            return []
        listing = "\n\n".join(
            f"ВОПРОС {i}: {q['q']}\nВАРИАНТЫ: " + " | ".join(q["options"]) for i, q in enumerate(questions, 1)
        )
        raw = await self._request(
            "Ты очень сильный и внимательный учитель. Реши каждый вопрос теста самостоятельно. Для каждого вопроса: "
            "1–3 строки короткого решения, затем отдельная строка «ОТВЕТ <номер вопроса>: <точный текст верного варианта>».",
            user_prompt=listing, temperature=0, max_tokens=5000, timeout=90,
        )
        if self._failed(raw):
            return questions   # проверить не удалось — оставляем как есть (каждый вопрос уже прошёл фильтры)
        solved = {int(n): a.strip().rstrip(".") for n, a in re.findall(r"(?im)^\s*ОТВЕТ\s*(\d+)\s*[:.)]\s*(.+)$", raw)}
        kept = []
        for i, q in enumerate(questions, 1):
            if i in solved:
                chosen = resolve_option(solved[i], q["options"])
                if chosen is not None and normalize_answer(chosen) != normalize_answer(q["answer"]):
                    logger.info("Вопрос теста отброшен: ответ не подтвердился (%s ≠ %s): %s", chosen, q["answer"], q["q"][:100])
                    continue
            kept.append(q)
        return kept

    async def build_test(
        self, grade: int, subject: str, count: int, n_options: int, level: int = DEFAULT_LEVEL,
        focus_topics: list | None = None, scope: str = "default", textbook: str | None = None,
        on_progress=None,
    ) -> list:
        """Готовый проверенный тест: генерация → независимая проверка → при нехватке догенерация.
        on_progress(stage, done, total) — сообщает, что вопросы составлены («generated») и проверены («verified»)."""
        questions: list = []
        for round_ in range(2):
            need = count - len(questions)
            if need <= 0:
                break
            fresh = await self.generate_test(
                grade, subject, need + 1 if round_ == 0 else need, n_options, level,
                focus_topics=focus_topics if (round_ == 0 or scope in ("topic", "passed")) else None,
                avoid=[q["q"] for q in questions] or None, scope=scope, textbook=textbook,
            )
            if on_progress and round_ == 0:
                await on_progress("generated", len(fresh), count)
            questions += await self.verify_test(fresh)
        if on_progress:
            await on_progress("verified", min(len(questions), count), count)
        return questions[:count]

    async def judge_answer(self, task: str, user_answer: str, reference: str | None = None) -> bool | None:
        """Только вердикт: True — верно, False — неверно, None — ученик не ответил (спросил, «не знаю»)."""
        reference_part = f"Эталонный ответ: «{reference}».\n" if reference else "Сначала реши задание сам.\n"
        prompt = (
            f"Задание: «{task}»\n{reference_part}Ответ ученика: «{user_answer}»\n\n"
            "Запись в другой форме (2,5 и 2.5, 1/2 и 0,5, синоним, другой падеж) считается верной. "
            "Если ученик не дал ответ, а задал вопрос или написал «не знаю»/«не понял» — это НЕТ ОТВЕТА.\n"
            "Ответь ОДНИМ вариантом без пояснений: ВЕРНО, НЕВЕРНО или НЕТ ОТВЕТА."
        )
        raw = await self._request("Ты строгий и точный проверяющий школьных заданий.", user_prompt=prompt, temperature=0,
                                  max_tokens=20, light=True)
        verdict = raw.strip().upper()
        if verdict.startswith("НЕТ ОТВЕТА") or "НЕТ ОТВЕТА" in verdict:
            return None
        if verdict.startswith("НЕВЕРНО") or "НЕВЕРНО" in verdict:
            return False
        if verdict.startswith("ВЕРНО"):
            return True
        return None

    async def check_answer(
        self,
        task: str,
        user_answer: str,
        reference: str | None = None,
        context: dict | None = None,
        attempt: int = 1,
        known: bool | None = None,
    ) -> tuple[bool | None, str]:
        """Проверка ответа. Возвращает (верно ли, текст для ученика).

        Правильный ответ бот не называет никогда — ни в обычной учёбе, ни в ВПР/ОГЭ/ЕГЭ: только подсказки,
        с каждой попыткой всё подробнее. Эталон в запрос на подсказку не передаётся, поэтому ИИ не может его выдать.
        """
        # known — вердикт уже ясен (ответ кнопкой с вариантом): проверять у ИИ не нужно
        correct = known if known is not None else await self.judge_answer(task, user_answer, reference)
        persona = tutor_persona(context)
        base = f"Задание: «{task}»\nОтвет ученика: «{user_answer}»\n"

        if correct:
            # Похвала — готовой фразой: отдельный запрос к ИИ ради «молодец» не стоит денег
            return True, random.choice(PRAISE)

        if correct is None:
            instruction = (
                "Ученик не ответил: задал вопрос или написал, что не знает/не понял. Объясни нужную идею с основ "
                "на простом примере (не на этом задании) и предложи попробовать ещё раз."
            )
        elif attempt <= 1:
            instruction = (
                "Ученик ответил НЕВЕРНО. Скажи, что пока не так, и дай подсказку: какой факт или правило вспомнить, "
                "в чём направление ошибки. Задай один наводящий вопрос."
            )
        elif attempt == 2:
            instruction = (
                "Ученик ответил НЕВЕРНО второй раз. Дай более сильную подсказку: сузь круг вариантов "
                "или напомни ключевой факт, по которому можно найти ответ. Задай наводящий вопрос."
            )
        else:
            instruction = (
                f"Ученик ответил НЕВЕРНО уже {attempt}-й раз. Не сдавайся и не раскрывай ответ. Разбей решение на маленькие "
                "шаги и проведи ученика только по ОДНОМУ ближайшему шагу: задай простой вопрос, на который он точно "
                "сможет ответить, и скажи, что из этого следует дальше. Подбодри: он уже близко."
            )
        no_spoiler = (
            "\nСтрого запрещено называть правильный ответ или выдавать его косвенно: ни словами, ни числом, "
            "ни первой буквой, ни фразами вроде «правильный ответ — …» или «это …». Ответ ученик должен назвать сам."
        )
        text = await self._request(persona + "\n" + instruction + no_spoiler, user_prompt=base, temperature=0.5)
        return correct, strip_spoilers(text, reference)

    async def explain_topic(
        self, grade: int, subject: str, topic: str, context: dict | None = None,
        task: str | None = None, reference: str | None = None, detailed: bool = False, textbook: str | None = None,
    ) -> str:
        """Объяснение темы перед заданием (или во время задания) — без ответа на само задание.
        detailed — подробный урок: с несколькими примерами и разбором по шагам."""
        task_part = (
            f"Ученик сейчас решает задание: «{task[:600]}». НЕ решай его и не называй ответ: объясни тему "
            "на ДРУГОМ примере, чтобы он смог решить задание сам.\n" if task else ""
        )
        system_prompt = tutor_persona(context) + (
            f"\nОбъясни ученику {grade} класса тему «{topic}» по предмету «{subject}» — коротко и понятно, "
            "как лучший репетитор. Строго по программе этого класса.\n"
            "Структура (обычный текст, каждый пункт с новой строки, всего не больше 8–10 строк):\n"
            "1) Суть темы в 1–2 предложениях простыми словами.\n"
            "2) Главное правило или способ решения — по шагам, если шагов несколько.\n"
            "3) Один разобранный пример (короткий, с ответом — это пример, а не задание ученика).\n"
            "4) Типичная ошибка, которую часто делают, — одной фразой.\n"
            "5) Закончи ободряющей фразой, что теперь можно попробовать самому.\n"
            + task_part
        )
        if detailed:
            system_prompt = tutor_persona(context) + (
                f"\nПроведи урок для ученика {grade} класса по теме «{topic}» (предмет «{subject}»), как лучший репетитор: "
                "подробно, понятно и интересно. Строго по программе этого класса, термины — как в школьных учебниках.\n"
                "Структура (обычный текст, каждый пункт с новой строки, всего 15–25 строк):\n"
                "1) Зачем нужна тема — пример из жизни в 1–2 предложениях.\n"
                "2) Главное: определение или правило простыми словами.\n"
                "3) Как решать — по шагам.\n"
                "4) Пример 1 (попроще) — разбери по шагам с ответом.\n"
                "5) Пример 2 (посложнее) — разбери по шагам с ответом.\n"
                "6) Типичные ошибки — 1–2 пункта.\n"
                "7) Короткая памятка в одну строку и ободряющая фраза: дальше будет пробный пример.\n"
            )
        system_prompt += "\n" + textbook_rule(textbook)
        text = await self._request(system_prompt, user_prompt=f"Тема: {topic}", temperature=0.4,
                                   max_tokens=3000 if detailed else 2000)
        return strip_spoilers(text, reference) if task else text

    async def explain_via(self, grade: int, subject: str, topic: str, interest: str, world: str) -> str:
        """«Объясни через…»: та же тема, но через мир, который нравится ученику (футбол, космос, Майнкрафт…).
        Без настоящих персонажей и названий произведений; точность важнее красоты."""
        system_prompt = tutor_persona({"grade": grade, "subject": subject}) + (
            f"\nОбъясни ученику {grade} класса тему «{topic}» (предмет «{subject}») через мир «{interest}»: {world}.\n"
            "Всё объяснение построй на этом мире: сравнения, герои-примеры и задачка — оттуда, чтобы было интересно "
            "и запоминалось. Но содержание темы должно быть ТОЧНЫМ и по школьной программе этого класса: "
            "аналогия помогает понять, а не заменяет правило — правило/определение назови прямо.\n"
            "Не используй настоящих персонажей, названия конкретных фильмов, аниме, игр, команд и брендов "
            "(кроме общего названия мира). Придумай своих героев.\n"
            "Структура (обычный текст, каждый пункт с новой строки, всего 8–12 строк):\n"
            "1) Короткое вступление-история в этом мире (1–2 предложения).\n"
            "2) Суть темы через аналогию.\n"
            "3) Правило или определение простыми словами — точно, как в учебнике.\n"
            "4) Пример из этого мира, разобранный по шагам, с ответом.\n"
            "5) Ободряющая фраза в стиле этого мира.\n"
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать LaTeX, символы '$' и '*'."
        )
        return await self._request(system_prompt, user_prompt=f"Тема: {topic}", temperature=0.7, max_tokens=1800)

    async def parent_help(self, grade: int, subject: str, topic: str) -> str:
        """«Как помочь дома»: совет родителю (не учителю!), как за 10 минут разобрать тему с ребёнком."""
        system_prompt = (
            "Ты опытный учитель и пишешь родителю школьника — человеку без педагогического образования. "
            f"Ребёнок учится в {grade} классе, ему трудно даётся тема «{topic}» (предмет «{subject}»).\n"
            "Подскажи родителю простыми словами, как за 10–15 минут разобрать эту тему дома. Строго по школьной программе "
            "этого класса, только верные факты.\n"
            "Структура (обычный текст, 8–12 коротких строк, каждый пункт с новой строки):\n"
            "1) Суть темы для взрослого — в 1–2 предложениях.\n"
            "2) Пример из жизни, через который удобно объяснить (кухня, магазин, прогулка, игра).\n"
            "3) 2–3 вопроса, которые можно задать ребёнку, и какие ответы правильные.\n"
            "4) Частая ошибка детей в этой теме и как её заметить.\n"
            "5) Совет по поддержке: как хвалить и не давить.\n"
            "Не решай за ребёнка домашнее задание. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать LaTeX, символы '$' и '*'."
        )
        return await self._request(system_prompt, user_prompt=f"Тема: {topic}", temperature=0.4, max_tokens=1500)

    async def flashcards(self, grade: int, subject: str, topic: str, count: int = 8) -> list:
        """Карточки для запоминания по теме: [{"front": вопрос, "back": ответ}] — термины, даты, формулы, правила."""
        system_prompt = (
            "Ты опытный учитель-методист. Составь карточки для запоминания (как флеш-карточки) "
            f"для ученика {grade} класса по предмету «{subject}», тема «{topic}». Строго по школьной программе.\n"
            f"Ровно {count} карточек: то, что нужно знать наизусть — термины и определения, даты и события, "
            "формулы, правила, ключевые факты, слова (для языков).\n"
            "Лицевая сторона — короткий вопрос (до 80 символов), обратная — короткий точный ответ (до 120 символов).\n"
            "Только факты, в которых ты уверен на 100%. Без вопросов «почему» на рассуждение.\n"
            "Формат строго, каждая карточка с новой строки:\n"
            "КАРТОЧКА: <вопрос> || <ответ>\n"
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать LaTeX, символы '$' и '*'."
        )
        raw = await self._request(system_prompt, user_prompt=f"Тема: {topic}", temperature=0.3, max_tokens=2000)
        if self._failed(raw):
            return []
        return parse_flashcards(raw)[:count]

    async def diagnostic_summary(self, name: str, grade: int, results: list) -> str:
        """Итоги диагностики: сильные стороны и что подтянуть. results — список словарей с полями
        subject, topic, level, status ("верно"/"неверно"/"не знаю")."""
        lines = "\n".join(f"- {r['subject']}, тема «{r['topic']}», уровень {r['level']}: {r['status']}" for r in results)
        system_prompt = tutor_persona({"name": name, "grade": grade}) + (
            "\nУченик только что прошёл короткую диагностику. Напиши ему итог: 4–6 предложений.\n"
            "1) Тёплое начало по имени. 2) Что получается хорошо — назови предметы или темы. "
            "3) Что стоит подтянуть — назови конкретные темы, но осторожно: вопросов было мало, поэтому говори "
            "«похоже, стоит повторить…», а не «у тебя пробел». 4) Скажи, что ты запомнил это и будешь чаще "
            "давать задания на эти темы. 5) Подбодри.\n"
            "Не называй правильные ответы на вопросы. Не перечисляй все вопросы подряд."
        )
        return await self._request(system_prompt, user_prompt=f"Результаты:\n{lines}", temperature=0.6)

    async def chat_with_mentor(self, history: list, context: dict | None = None) -> str:
        return await self._request(tutor_persona(context), history=history, temperature=0.7)

    async def recognize_speech(self, audio_bytes: bytes) -> str:
        """STT: Распознавание голоса в текст"""
        url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        headers = {"Authorization": f"Api-Key {self.api_key}"}
        params = {"folderId": self.folder_id, "lang": "ru-RU", "format": "oggopus"}
        
        # Таймаут 15 секунд
        timeout = aiohttp.ClientTimeout(total=15)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, params=params, data=audio_bytes) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("result", "")
                    else:
                        logger.error(f"STT error: {await response.text()}")
                        return ""
        except Exception as e:
            logger.error(f"Ошибка STT (или таймаут): {e}")
            return ""

    async def recognize_image_text(self, data: bytes, mime: str) -> str:
        """Текст с фото, скриншота или одностраничного PDF через Yandex Vision OCR.

        Таблицы (как в расписании) собираются по строкам: ячейки через « | ». Пустая строка — не получилось
        (нет роли ai.vision.user у сервисного аккаунта, плохое фото и т. п. — подробности в логе).
        """
        content = base64.b64encode(data).decode()
        short = {"image/jpeg": "JPEG", "image/png": "PNG", "application/pdf": "PDF"}.get(mime, mime)
        headers = {"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id}
        attempts = [(url, m) for url in ("https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText",
                                         "https://ai.api.cloud.yandex.net/ocr/v1/recognizeText")
                    for m in dict.fromkeys([short, mime])]
        for url, mime_value in attempts:
            payload = {"mimeType": mime_value, "languageCodes": ["ru", "en"], "model": "page", "content": content}
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                    async with session.post(url, headers=headers, json=payload) as response:
                        if response.status == 200:
                            self.count_usage("ocr")
                        if response.status != 200:
                            logger.error("OCR ошибка %s: %s", response.status, (await response.text())[:300])
                            if response.status in (401, 403):
                                logger.error("Нет доступа к Vision OCR: выдайте сервисному аккаунту роль ai.vision.user")
                                return ""
                            continue
                        data_json = await response.json()
            except Exception as e:
                logger.error("Сбой OCR (%s): %s", url, e)
                continue
            annotation = (data_json.get("result") or data_json).get("textAnnotation") or {}
            lines = []
            for table in annotation.get("tables") or []:
                rows: dict = {}
                for cell in table.get("cells") or []:
                    rows.setdefault(int(cell.get("rowIndex") or 0), {})[int(cell.get("columnIndex") or 0)] = \
                        (cell.get("text") or "").strip()
                for r in sorted(rows):
                    lines.append(" | ".join(rows[r][c] for c in sorted(rows[r])))
            text = annotation.get("fullText") or ""
            return ("Таблица:\n" + "\n".join(lines) + "\n\nВесь текст:\n" + text) if lines else text
        return ""

    async def homework_tasks(self, raw: str, grade: int, subject_titles: list) -> list:
        """Задания из распознанного фото домашки: [{"text", "subject", "topic"}] (до 3 заданий)."""
        system_prompt = (
            f"Ученик {grade} класса прислал фото домашнего задания, текст распознан автоматически (могут быть опечатки "
            "распознавания и лишние куски страницы). Выдели из него задания, которые нужно решить, — не больше трёх.\n"
            "Восстанови условие каждого задания полностью и грамотно, исправь очевидные ошибки распознавания, "
            "формулы запиши как в тетради (x^2, 3/4, √16). НЕ решай задания и не пиши ответы.\n"
            "Предмет выбери из списка: " + ", ".join(subject_titles) + ".\n"
            "Ответь строго в формате, для каждого задания:\n"
            "ЗАДАНИЕ 1: <условие>\nПРЕДМЕТ: <предмет из списка>\nТЕМА: <тема, 2–5 слов>\n\n"
            "Если заданий в тексте нет — ответь одним словом: НЕТ."
        )
        reply = await self._request(system_prompt, user_prompt=raw[:6000], temperature=0, max_tokens=2000, light=True)
        if self._failed(reply) or reply.strip().upper().startswith("НЕТ"):
            return []
        tasks = []
        for block in re.split(r"(?im)^\s*ЗАДАНИЕ\s*№?\s*\d+\s*[:.)]\s*", reply)[1:]:
            parts = parse_task_reply("ЗАДАНИЕ: " + block)
            text = (parts.get("ЗАДАНИЕ") or "").strip()
            if len(text) >= 5:
                tasks.append({"text": text, "subject": (parts.get("ПРЕДМЕТ") or "").strip(),
                              "topic": (parts.get("ТЕМА") or "").strip()})
        return tasks[:3]

    async def schedule_lines(self, raw: str, subject_titles: list) -> str:
        """Расписание из распознанного текста (таблица, столбики, сокращения) → строки «Пн: предмет, предмет»."""
        system_prompt = (
            "Ты помогаешь разобрать школьное расписание уроков, распознанное с фото или из файла. "
            "Текст может быть таблицей (дни — столбцами или строками), со временем уроков, номерами, кабинетами и "
            "сокращениями. Определи, какие уроки в какой день идут, по порядку.\n"
            "Ответь ТОЛЬКО строками такого вида, по одной на день, без пояснений:\n"
            "Пн: предмет, предмет, предмет\nВт: …\n"
            "Дни: Пн, Вт, Ср, Чт, Пт, Сб. Названия предметов пиши полностью, выбирая из списка: "
            + ", ".join(subject_titles)
            + ". Уроки, которых нет в списке (физкультура, музыка, ИЗО, труд, классный час и т. п.), тоже пиши как есть. "
            "Если расписания в тексте нет — ответь одним словом: НЕТ."
        )
        raw_reply = await self._request(system_prompt, user_prompt=raw[:8000], temperature=0, max_tokens=1500, light=True)
        return "" if self._failed(raw_reply) or raw_reply.strip().upper().startswith("НЕТ") else raw_reply

    def _voice_chain(self, voice: dict | None = None) -> list:
        """Каким голосом пробовать озвучить: выбранный (/voice или .env), потом запасной."""
        if voice and voice.get("voice") and voice["voice"] not in voice_tools.FEMALE_VOICES:
            primary = (voice.get("model") or "", voice["voice"], voice.get("role") or "")
        else:
            name = (getattr(settings, "TTS_VOICE", "") or "").strip()
            primary = ("", name, (getattr(settings, "TTS_ROLE", "") or "").strip())
            if not name or name in voice_tools.FEMALE_VOICES:   # Маячок — мальчик: старый женский голос не берём
                primary = voice_tools.MASCOT_VOICE
        if not primary[0] and primary[1] in voice_tools.LIVETTS_VOICES:
            primary = ("livetts",) + primary[1:]
        broken = not voice and time.monotonic() < self._tts_primary_broken_until
        chain = [] if broken else [primary]
        if voice_tools.FALLBACK_VOICE not in chain:
            chain.append(voice_tools.FALLBACK_VOICE)
        return chain

    async def _tts_request(self, text: str, model: str, voice: str, role: str, raw_pcm: bool) -> tuple[bytes, int]:
        """Один запрос к SpeechKit API v3. Возвращает (звук, HTTP-статус); статус 0 — сбой сети."""
        hints = [{"voice": voice}]
        if role:
            hints.append({"role": role})
        speed = float(getattr(settings, "TTS_SPEED", 1.0) or 1.0)
        if model != "livetts" and speed != 1.0:
            hints.append({"speed": speed})      # LiveTTS скорость не поддерживает
        payload = {
            "text": text,
            "hints": hints,
            "outputAudioSpec": (
                {"rawAudio": {"audioEncoding": "LINEAR16_PCM", "sampleRateHertz": voice_tools.SAMPLE_RATE}}
                if raw_pcm else {"containerAudio": {"containerAudioType": "OGG_OPUS"}}
            ),
        }
        if model:
            payload["model"] = model
        if len(text) > voice_tools.TTS_CHUNK:
            payload["unsafeMode"] = True        # длинный текст SpeechKit сам разобьёт на фразы
        headers = {"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id}
        url = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    body = await response.read()
                    if response.status != 200:
                        logger.error("TTS (%s/%s) ошибка %s: %s", model or "general", voice, response.status,
                                     body[:300].decode(errors="replace"))
                        return b"", response.status
                    self.count_usage("tts_units", max(1, -(-len(text) // 250)))   # 1 единица = до 250 символов
                    return voice_tools.parse_tts_stream(body), 200
        except Exception as e:
            logger.error("Ошибка TTS (%s): %s", voice, e)
            return b"", 0

    async def synthesize_speech(self, text: str, voice: dict | None = None) -> bytes:
        """TTS: всё сообщение — одним голосовым OGG/Opus.

        Текст режется по предложениям (SpeechKit озвучивает до ~250 символов за раз), куски озвучиваются
        одновременно, склеиваются с короткими паузами и собираются в одно голосовое через ffmpeg.
        """
        chunks = voice_tools.split_for_tts(text)
        if not chunks:
            return b""
        primary_rejected = False
        for model, name, role in self._voice_chain(voice):
            if len(chunks) == 1 or not voice_tools.has_ffmpeg():
                audio, status = await self._tts_request(" ".join(chunks), model, name, role, raw_pcm=False)
            else:
                results = await asyncio.gather(*(self._tts_request(c, model, name, role, raw_pcm=True) for c in chunks))
                status = next((st for _, st in results if st != 200), 200)
                audio = b""
                if status == 200 and all(a for a, _ in results):
                    pause = voice_tools.silence()
                    audio = await voice_tools.pcm_to_ogg(pause.join(a for a, _ in results)) or b""
            is_fallback = (model, name, role) == voice_tools.FALLBACK_VOICE
            if audio:
                if is_fallback and primary_rejected:
                    # Запасной голос этот же текст озвучил — значит, дело в основном голосе: час не трогаем его
                    logger.warning("Основной голос недоступен — следующий час говорю запасным (%s)", name)
                    self._tts_primary_broken_until = time.monotonic() + 3600
                return audio
            if 400 <= status < 500 and not is_fallback and not voice:
                primary_rejected = True
        return b""


# Экземпляр клиента для использования в других модулях
ai_client = AIClient()
