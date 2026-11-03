import asyncio
import inspect
import logging
import random

logger = logging.getLogger(__name__)

try:
    from sdamgia import SdamGIA
except Exception as e:  # библиотека не установилась
    logger.warning("sdamgia недоступна: %s", e)
    SdamGIA = None

# Текст, который пишет пользователь -> id предмета (те же id, что в SUBJECTS в handlers/user.py)
SUBJECT_ALIASES = {
    "окружающий мир": "world", "окружающий": "world",
    "литературное чтение": "read", "чтение": "read",
    "математика": "math", "алгебра": "math", "геометрия": "math",
    "русский": "rus", "русский язык": "rus",
    "физика": "phys", "химия": "chem", "биология": "bio",
    "история": "hist", "обществознание": "soc",
    "информатика": "inf", "английский": "eng",
    "литература": "lit", "география": "geo",
    "обзр": "obzr", "обж": "obzr", "основы безопасности": "obzr",
}

# id предмета -> код предмета на sdamgia
SDAMGIA_CODES = {
    "math": "math", "rus": "rus", "phys": "phys", "chem": "chem", "bio": "bio", "hist": "hist",
    "soc": "soc", "inf": "inf", "eng": "en", "geo": "geo", "lit": "lit",
}

# Номера заданий для sdamgia подобраны вручную и покрывают лишь малую часть банка
SDAMGIA_PROBLEM_IDS = {
    "math": ["1001", "1002", "1003", "1005"],
    "rus": ["2001", "2002", "2003"],
    "phys": ["3001", "3002"],
    "chem": ["4001", "4002"],
}


def detect_subject(subject_text: str) -> str | None:
    """Определяет id предмета по тексту пользователя (или None, если предмет не известен)."""
    subject_text = subject_text.lower().strip()
    for key, subject_id in SUBJECT_ALIASES.items():
        if key in subject_text:
            return subject_id
    return None


def get_sdamgia_task(subject_id: str) -> dict | None:
    """Задание с сайта sdamgia (используется только для ЕГЭ). Синхронная функция: вызывать через asyncio.to_thread."""
    code = SDAMGIA_CODES.get(subject_id)
    if SdamGIA is None or code is None:
        return None

    try:
        problem_id = random.choice(SDAMGIA_PROBLEM_IDS.get(subject_id, ["1001"]))
        result = SdamGIA().get_problem_by_id(code, problem_id)
        if inspect.isawaitable(result):  # у новых версий библиотеки клиент асинхронный
            result = asyncio.run(result)

        if not result or "condition" not in result:
            return None
        condition = result["condition"]["text"]
        answer = str(result.get("answer", "")).strip()
        if not condition or not answer:
            return None
        return {"text": condition, "answer": answer, "source": "сайт «Решу ЕГЭ» (sdamgia.ru)"}
    except Exception as e:
        logger.error("Ошибка при получении задания с sdamgia: %s", e)
        return None
