"""Игра «⚡ Молния»: 60 секунд, вопросы с вариантами, 3 жизни. Счёт — сколько верных ответов успел дать.

Вопросы бот придумывает сам по правилам (нейросеть не нужна, ответы всегда верные):
  • «🔢 Счёт» — устный счёт по классу: от «7 + 5» в 1 классе до степеней, корней и процентов в старших;
  • «🇬🇧 Слова» — английские слова (со 2 класса): перевод в обе стороны.
Здесь только правила — без MAX и Redis.
"""
import random
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

GAME_SECONDS = 60
LIVES = 3
MODES = {"math": "🔢 Счёт", "eng": "🇬🇧 Слова"}

# ---------- устный счёт ----------


def _fmt(x) -> str:
    if isinstance(x, Fraction):
        x = float(x)
    if isinstance(x, float):
        if x.is_integer():
            return str(int(x)).replace("-", "−")
        return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",").replace("-", "−")
    return str(x).replace("-", "−")


def _distractors(answer, rng: random.Random, count: int) -> List[Any]:
    """Похожие неверные ответы: ±1, ±2, ±10, переставленные цифры."""
    pool = set()
    if isinstance(answer, int):
        for d in (1, -1, 2, -2, 10, -10, 3, -3, 5, -5):
            pool.add(answer + d)
        s = str(abs(answer))
        if len(s) == 2 and s[0] != s[1]:
            pool.add(int(s[::-1]) * (1 if answer >= 0 else -1))
        if answer:
            pool.add(-answer)
        if answer >= 0:
            pool = {p for p in pool if p >= 0} or pool
    else:
        for d in (0.1, -0.1, 0.5, -0.5, 1, -1, 0.01, 0.2):
            pool.add(round(answer + d, 2))
    pool.discard(answer)
    pool = list(pool)
    rng.shuffle(pool)
    return pool[:count]


def _math_question(grade: int, rng: random.Random) -> Tuple[str, Any]:
    r = rng.randint
    g = max(1, min(11, grade))
    kinds = {
        1: ["add10", "sub10", "add20"],
        2: ["add100", "sub100", "mul5"],
        3: ["mul", "div", "add1000"],
        4: ["mul2x1", "div", "add1000", "mul"],
        5: ["mul2x1", "dec_add", "sq", "div", "mul10"],
        6: ["neg", "frac_of", "pct", "dec_add", "sq"],
        7: ["pow", "neg", "lin", "pct", "sq"],
        8: ["root", "pow", "lin", "pct", "neg"],
        9: ["root", "pow", "lin", "pct", "trig"],
    }.get(g, ["root", "pow", "lin", "pct", "trig", "log"])
    kind = rng.choice(kinds)
    if kind == "add10":
        a = r(1, 9); b = r(1, 10 - a) if a < 10 else 0; return f"{a} + {b}", a + b
    if kind == "sub10":
        a = r(2, 10); b = r(1, a); return f"{a} − {b}", a - b
    if kind == "add20":
        a = r(5, 15); b = r(2, 20 - a) if a < 18 else 2; return f"{a} + {b}", a + b
    if kind == "add100":
        a = r(11, 79); b = r(5, 99 - a); return f"{a} + {b}", a + b
    if kind == "sub100":
        a = r(30, 99); b = r(5, a - 5); return f"{a} − {b}", a - b
    if kind == "mul5":
        a = r(2, 5); b = r(2, 9); return f"{a} × {b}", a * b
    if kind == "mul":
        a = r(2, 9); b = r(2, 9); return f"{a} × {b}", a * b
    if kind == "div":
        b = r(2, 9); c = r(2, 9); return f"{b * c} : {b}", c
    if kind == "add1000":
        a = r(10, 60) * 10; b = r(5, 30) * 10
        return (f"{a} + {b}", a + b) if rng.random() < 0.5 else (f"{a + b} − {b}", a)
    if kind == "mul2x1":
        a = r(11, 49); b = r(2, 6); return f"{a} × {b}", a * b
    if kind == "mul10":
        a = r(3, 99); m = rng.choice([10, 100]); return f"{a} × {m}", a * m
    if kind == "dec_add":
        a = r(1, 49) / 10; b = r(1, 49) / 10
        return f"{_fmt(a)} + {_fmt(b)}", round(a + b, 2)
    if kind == "sq":
        a = r(2, 15); return f"{a}²", a * a
    if kind == "neg":
        a = r(-12, 12); b = r(-12, 12)
        op = rng.choice(["+", "−", "×"])
        res = a + b if op == "+" else (a - b if op == "−" else a * b)
        return f"{_fmt(a)} {op} ({_fmt(b)})" if b < 0 else f"{_fmt(a)} {op} {b}", res
    if kind == "frac_of":
        den = rng.choice([2, 3, 4, 5]); num = r(1, den - 1); whole = den * r(2, 10)
        return f"{num}/{den} от {whole}", whole * num // den
    if kind == "pct":
        p = rng.choice([10, 20, 25, 50, 5, 30]); whole = rng.choice([20, 40, 60, 80, 120, 200, 300, 400])
        return f"{p}% от {whole}", whole * p // 100
    if kind == "pow":
        base = rng.choice([2, 2, 3, 5, -2, -3, 10]); e = r(3, 6) if abs(base) == 2 else r(2, 4 if abs(base) == 3 else 3)
        sup = "⁰¹²³⁴⁵⁶⁷⁸⁹"[e]
        return (f"({_fmt(base)}){sup}" if base < 0 else f"{base}{sup}"), base ** e
    if kind == "lin":
        a = r(2, 9); x = r(-9, 12); b = r(-15, 15)
        tail = f" + {b}" if b > 0 else (f" − {-b}" if b < 0 else "")
        return f"{a}x{tail} = {_fmt(a * x + b)}. x = ?", x
    if kind == "root":
        a = r(2, 20); return f"√{a * a}", a
    if kind == "trig":
        q, ans = rng.choice([("sin 30°", 0.5), ("cos 60°", 0.5), ("tg 45°", 1), ("sin 90°", 1), ("cos 0°", 1),
                             ("sin 0°", 0), ("cos 90°", 0), ("ctg 45°", 1)])
        return q, ans
    if kind == "log":
        base = rng.choice([2, 3, 10]); e = r(1, 5 if base == 2 else 3)
        return f"log{'₂' if base == 2 else ('₃' if base == 3 else '₁₀')} {base ** e}", e
    return "2 + 2", 4


# ---------- английские слова ----------

WORDS_EASY = [
    ("cat", "кошка"), ("dog", "собака"), ("red", "красный"), ("blue", "синий"), ("green", "зелёный"),
    ("apple", "яблоко"), ("house", "дом"), ("sun", "солнце"), ("book", "книга"), ("pen", "ручка"),
    ("school", "школа"), ("milk", "молоко"), ("bird", "птица"), ("fish", "рыба"), ("big", "большой"),
    ("small", "маленький"), ("one", "один"), ("three", "три"), ("mother", "мама"), ("father", "папа"),
    ("sister", "сестра"), ("brother", "брат"), ("friend", "друг"), ("ball", "мяч"), ("tree", "дерево"),
    ("water", "вода"), ("happy", "весёлый"), ("cold", "холодный"), ("hot", "горячий"), ("run", "бегать"),
    ("jump", "прыгать"), ("swim", "плавать"), ("bear", "медведь"), ("frog", "лягушка"), ("snow", "снег"),
    ("winter", "зима"), ("summer", "лето"), ("table", "стол"), ("window", "окно"), ("door", "дверь"),
]
WORDS_HARD = [
    ("knowledge", "знание"), ("weather", "погода"), ("journey", "путешествие"), ("library", "библиотека"),
    ("answer", "ответ"), ("question", "вопрос"), ("country", "страна"), ("beautiful", "красивый"),
    ("difficult", "трудный"), ("important", "важный"), ("remember", "помнить"), ("understand", "понимать"),
    ("believe", "верить"), ("explain", "объяснять"), ("between", "между"), ("always", "всегда"),
    ("never", "никогда"), ("tomorrow", "завтра"), ("yesterday", "вчера"), ("enough", "достаточно"),
    ("health", "здоровье"), ("environment", "окружающая среда"), ("decision", "решение"), ("science", "наука"),
    ("experience", "опыт"), ("opportunity", "возможность"), ("advice", "совет"), ("achieve", "достигать"),
    ("improve", "улучшать"), ("compare", "сравнивать"), ("government", "правительство"), ("century", "век"),
    ("pollution", "загрязнение"), ("success", "успех"), ("community", "сообщество"), ("career", "карьера"),
    ("challenge", "вызов, испытание"), ("increase", "увеличивать"), ("reliable", "надёжный"), ("whole", "целый"),
]


def _word_question(grade: int, rng: random.Random, n_options: int) -> Tuple[str, str, List[str]]:
    words = WORDS_EASY if grade <= 5 else (WORDS_EASY + WORDS_HARD if grade <= 7 else WORDS_HARD)
    pick = rng.sample(words, n_options)
    eng, rus = pick[0]
    if rng.random() < 0.5:
        return f"«{eng}» — это…", rus, [w[1] for w in pick]
    return f"«{rus}» по-английски…", eng, [w[0] for w in pick]


# ---------- вопрос целиком ----------

def question(mode: str, grade: int, rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """{"text": "6 × 7", "options": ["42", "48", "36"], "answer": 0} — answer = номер верного варианта."""
    rng = rng or random.Random()
    n_options = 3 if grade <= 4 else 4
    if mode == "eng":
        text, right, options = _word_question(grade, rng, n_options)
    else:
        text, value = _math_question(grade, rng)
        right = _fmt(value)
        options = [right] + [_fmt(d) for d in _distractors(value, rng, n_options + 2)]
        options = list(dict.fromkeys(options))[:n_options]
        if not text.endswith("?"):
            text = f"{text} = ?"
    rng.shuffle(options)
    return {"text": text, "options": options, "answer": options.index(right)}


def points_for(score: int) -> int:
    """Очки за игру: 1 очко за каждые 5 верных ответов."""
    return score // 5


def rank(score: int) -> str:
    if score >= 25:
        return "Повелитель молний ⚡⚡⚡"
    if score >= 15:
        return "Гроза задачника ⚡⚡"
    if score >= 8:
        return "Быстрый разум ⚡"
    return "Разминка"
