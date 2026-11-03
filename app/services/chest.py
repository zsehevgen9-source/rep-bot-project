"""«🎁 Сундук дня»: случайная награда после занятия — наклейка в альбом, звание, интересный факт или бонусные очки.

Сундук открывается раз в день, когда ученик решил сегодня CHEST_NEED заданий (верные ответы теста тоже считаются).
Здесь только правила и содержимое — без MAX и Redis. Нейросеть не нужна: всё заранее написано.
"""
import random
from typing import Any, Dict, List, Optional

CHEST_NEED = 3

COMMON, RARE, LEGENDARY = "common", "rare", "legendary"
RARITY_NAMES = {COMMON: "обычная", RARE: "редкая", LEGENDARY: "легендарная"}

# id: (эмодзи, название, редкость)
STICKERS: Dict[str, tuple] = {
    "cat": ("🐱", "Котик-отличник", COMMON), "dog": ("🐶", "Пёс-почемучка", COMMON),
    "fox": ("🦊", "Лис-хитрец", COMMON), "bear": ("🐻", "Мишка-соня", COMMON),
    "panda": ("🐼", "Панда-математик", COMMON), "koala": ("🐨", "Коала-мечтатель", COMMON),
    "frog": ("🐸", "Лягушка-путешественница", COMMON), "monkey": ("🐵", "Обезьянка-шалунья", COMMON),
    "penguin": ("🐧", "Пингвин-капитан", COMMON), "turtle": ("🐢", "Черепаха-мудрец", COMMON),
    "octopus": ("🐙", "Осьминог-многорук", COMMON), "owl": ("🦉", "Сова-библиотекарь", COMMON),
    "bee": ("🐝", "Пчела-трудяга", COMMON), "butterfly": ("🦋", "Бабочка-художница", COMMON),
    "ladybug": ("🐞", "Божья коровка", COMMON), "dolphin": ("🐬", "Дельфин-умник", COMMON),
    "unicorn": ("🦄", "Единорог", RARE), "dragon": ("🐉", "Дракон знаний", RARE),
    "dino": ("🦕", "Диплодок-долгожитель", RARE), "trex": ("🦖", "Ти-рекс", RARE),
    "whale": ("🐳", "Кит-кругосветка", RARE), "peacock": ("🦚", "Павлин-модник", RARE),
    "flamingo": ("🦩", "Фламинго-балерина", RARE), "parrot": ("🦜", "Попугай-полиглот", RARE),
    "lion": ("🦁", "Лев-вожак", RARE), "tiger": ("🐯", "Тигр-чемпион", RARE),
    "rocket": ("🚀", "Ракета «Пятёрка»", LEGENDARY), "crown": ("👑", "Корона знаний", LEGENDARY),
    "diamond": ("💎", "Алмазный ум", LEGENDARY), "star": ("🌟", "Звезда Маячка", LEGENDARY),
}

TITLES = ["Хранитель огня", "Капитан знаний", "Покоритель задач", "Звездочёт", "Морской волк",
          "Гроза контрольных", "Книжный пират", "Смотритель маяка"]

FACTS = [
    "Осьминог может пробовать еду на вкус своими щупальцами — на них есть вкусовые рецепторы.",
    "Мёд не портится: в древнеегипетских гробницах нашли мёд, которому около 3000 лет, и он был съедобен.",
    "Слово «спутник» вошло во многие языки мира после запуска первого советского спутника в 1957 году.",
    "Свет от Солнца летит до Земли около 8 минут. Мы видим Солнце таким, каким оно было 8 минут назад.",
    "У улитки может быть больше 10 000 зубов — они расположены на «языке»-тёрке.",
    "Байкал — самое глубокое озеро на планете: больше 1600 метров. В нём около пятой части всей озёрной пресной воды Земли.",
    "Число ноль придумали в Индии. Без него не было бы ни компьютеров, ни калькуляторов.",
    "Сердце синего кита весит почти 200 килограммов — как три взрослых человека.",
    "По одной из версий, слово «вокзал» пришло из Англии: так назывался парк Воксхолл с музыкальным залом.",
    "Пчёлы танцуют, чтобы рассказать другим пчёлам, где нашли цветы.",
    "На Венере день длиннее года: она поворачивается вокруг себя медленнее, чем облетает Солнце.",
    "«Сказку о рыбаке и рыбке» Пушкин написал в Болдине осенью 1833 года — в свою вторую «Болдинскую осень».",
    "Молния горячее поверхности Солнца — примерно в пять раз.",
    "Бамбук может вырасти почти на метр за сутки — это самое быстрорастущее растение.",
    "В русском языке есть слова без корня — например, глагол «вынуть»: «вы-» — приставка, «-ну-» — суффикс.",
    "Колибри — единственная птица, которая умеет летать задом наперёд.",
    "Эйфелеву башню хотели разобрать через 20 лет после постройки, но спасла её… радиоантенна.",
    "Если сложить все числа от 1 до 100, получится 5050. Маленький Гаусс посчитал это в уме за минуту.",
    "Жираф может почистить себе уши языком: длина языка — около полуметра.",
    "Самое длинное слово в словаре русского языка — «превысокомногорассмотрительствующий» (35 букв).",
    "Морская звезда может отрастить себе новый луч, а иногда из луча вырастает целая новая звезда.",
    "Земля вращается со скоростью около 1670 км/ч на экваторе, но мы этого совсем не чувствуем.",
    "Первую в мире женщину-космонавта звали Валентина Терешкова. Она полетела в космос в 1963 году.",
    "Коровы заводят себе лучших друзей и скучают, если их разлучают.",
    "Знак «=» придумал англичанин Роберт Рекорд в 1557 году: «нет ничего более равного, чем две параллельные линии».",
    "Шахматы появились в Индии примерно 1500 лет назад и назывались «чатуранга».",
    "Вода — одно из немногих веществ, которое в природе встречается сразу в трёх видах: лёд, жидкость и пар.",
    "В Антарктиде есть «долины без снега» — там не было дождя и снега уже миллионы лет.",
    "Глаз страуса больше его мозга.",
    "Слово «робот» появилось в пьесе чешского писателя Карела Чапека (придумал его брат Йозеф). Robota — «тяжёлый труд».",
]

POINTS_FACT = 5
POINTS_DUPLICATE = 5
POINTS_BONUS = 15


def new_album() -> Dict[str, Any]:
    return {"stickers": {}, "titles": [], "opened": 0, "title": None, "facts": []}


def _pick_sticker(rng: random.Random, rarity: str) -> str:
    return rng.choice([sid for sid, s in STICKERS.items() if s[2] == rarity])


def roll(album: Dict[str, Any], rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """Что лежит в сундуке. Меняет album (наклейка/звание/факт добавляются) и возвращает награду:
    {"kind": "sticker"|"title"|"fact"|"bonus", "emoji", "name", "rarity", "points", "duplicate"}."""
    rng = rng or random.Random()
    album.setdefault("stickers", {}); album.setdefault("titles", []); album.setdefault("facts", [])
    album["opened"] = album.get("opened", 0) + 1
    r = rng.random()
    if album["opened"] == 1:       # первый сундук — всегда наклейка, чтобы начать альбом
        r = 0.5
    # Гарантия: если 7 сундуков подряд без редкой — следующая наклейка редкая
    if album.get("no_rare", 0) >= 7 and r >= 0.62:
        r = 0.1
    if r < 0.62:
        rarity = LEGENDARY if r < 0.04 else (RARE if r < 0.20 else COMMON)
        sid = _pick_sticker(rng, rarity)
        emoji, name, rarity = STICKERS[sid]
        duplicate = sid in album["stickers"]
        album["stickers"][sid] = album["stickers"].get(sid, 0) + 1
        album["no_rare"] = 0 if rarity != COMMON else album.get("no_rare", 0) + 1
        return {"kind": "sticker", "id": sid, "emoji": emoji, "name": name, "rarity": rarity,
                "points": POINTS_DUPLICATE if duplicate else 0, "duplicate": duplicate}
    album["no_rare"] = album.get("no_rare", 0) + 1
    free_titles = [t for t in TITLES if t not in album["titles"]]
    if r < 0.82 or (r < 0.92 and not free_titles):
        unseen = [i for i in range(len(FACTS)) if i not in album["facts"]] or list(range(len(FACTS)))
        i = rng.choice(unseen)
        album["facts"] = (album["facts"] + [i])[-len(FACTS) + 1:]
        return {"kind": "fact", "emoji": "💡", "name": FACTS[i], "rarity": "fact", "points": POINTS_FACT}
    if r < 0.92:
        title = rng.choice(free_titles)
        album["titles"].append(title)
        album["title"] = title
        return {"kind": "title", "emoji": "🏅", "name": title, "rarity": "title", "points": 0}
    return {"kind": "bonus", "emoji": "💰", "name": "Горсть бонусных очков", "rarity": "bonus",
            "points": POINTS_BONUS}


def collected(album: Dict[str, Any]) -> int:
    return len(album.get("stickers") or {})


def album_lines(album: Dict[str, Any]) -> List[str]:
    """Альбом текстом: наклейки по редкости (❔ — ещё не найдена), звания."""
    have = album.get("stickers") or {}
    lines = [f"📒 Альбом наклеек: {collected(album)} из {len(STICKERS)}"]
    for rarity, head in ((COMMON, "Обычные"), (RARE, "Редкие ✨"), (LEGENDARY, "Легендарные 🌟")):
        ids = [sid for sid, s in STICKERS.items() if s[2] == rarity]
        got = sum(1 for sid in ids if sid in have)
        row = " ".join(STICKERS[sid][0] if sid in have else "❔" for sid in ids)
        lines.append(f"\n{head} · {got}/{len(ids)}\n{row}")
    if album.get("titles"):
        lines.append("\n🏅 Звания: " + ", ".join(f"«{t}»" for t in album["titles"]))
        if album.get("title"):
            lines.append(f"Сейчас ты — «{album['title']}»")
    return lines
