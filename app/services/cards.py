"""Картинки-карточки для чата: прогресс, результат теста, грамота, недельный отчёт родителю.

Рисуются библиотекой Pillow (PNG, ширина 1080). Шрифт — DejaVu Sans (в Docker-образе ставится пакетом
fonts-dejavu-core). Эмодзи в картинках не используем: обычные шрифты их не рисуют — вместо них фигуры.
Здесь только рисование: никаких MAX, базы и Redis.
"""
import io
import math
import os
import re
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:   # Pillow не установлен — бот работает без картинок
    Image = ImageDraw = ImageFont = None

from app.services.mascot import mascot

W = 1080
BG = (244, 246, 251)
CARD = (255, 255, 255)
TEXT = (31, 41, 55)
MUTED = (107, 114, 128)
LINE = (229, 231, 235)
INDIGO = (79, 70, 229)
VIOLET = (124, 58, 237)
TEAL = (20, 184, 166)
GREEN = (34, 197, 94)
ORANGE = (245, 158, 11)
RED = (239, 68, 68)
GOLD = (234, 179, 8)
BRAND = "Маячок · репетитор"

_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]


def available() -> bool:
    return Image is not None and _font_path(False) is not None


def _font_path(bold: bool) -> Optional[str]:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for folder in _FONT_DIRS:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return path
    return None


_fonts: Dict[Tuple[int, bool], "ImageFont.FreeTypeFont"] = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _fonts:
        path = _font_path(bold) or _font_path(False)
        _fonts[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    return _fonts[key]


def _png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _gradient(size: Tuple[int, int], left: Tuple[int, int, int], right: Tuple[int, int, int]):
    w, h = size
    grad = Image.new("RGB", (w, 1))
    for x in range(w):
        t = x / max(1, w - 1)
        grad.putpixel((x, 0), tuple(int(left[i] + (right[i] - left[i]) * t) for i in range(3)))
    return grad.resize((w, h))


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D]")


def plain(text: str) -> str:
    """Без эмодзи: шрифт DejaVu их не рисует (были бы квадратики)."""
    return re.sub(r"\s+([»)!.,])", r"\1", " ".join(_EMOJI.sub("", str(text)).split()))


def _fit(draw, text: str, size: int, max_width: int, bold: bool = False):
    """Шрифт поменьше, если текст не влезает в ширину; в крайнем случае — обрезка с «…»."""
    text = plain(text)
    while size > 18 and draw.textlength(text, font=font(size, bold)) > max_width:
        size -= 2
    f = font(size, bold)
    while draw.textlength(text, font=f) > max_width and len(text) > 2:
        text = text[:-2].rstrip() + "…"
    return text, f


def _put_mascot(image, x: int, y: int, height: int, mood: str = "happy") -> None:
    m = mascot(height, mood)
    image.paste(m, (x, y), m)


def _header(image, draw, title: str, subtitle: str, height: int = 230, mood: str = "happy"):
    image.paste(_gradient((W, height), INDIGO, VIOLET), (0, 0))
    # «Луч маяка» — мягкие полупрозрачные круги в углу
    overlay = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for r, alpha in ((260, 28), (180, 34), (100, 40)):
        od.ellipse((W - 120 - r, -80 - r // 3, W - 120 + r, -80 + r + r // 2), fill=(255, 255, 255, alpha))
    image.paste(overlay, (0, 0), overlay)
    _put_mascot(image, W - 215, 8, height - 14, mood)
    draw.text((60, 36), BRAND.upper(), font=font(24, True), fill=(224, 231, 255))
    t, f = _fit(draw, title, 58, W - 300, True)
    draw.text((60, 76), t, font=f, fill=(255, 255, 255))
    s, f = _fit(draw, subtitle, 30, W - 300)
    draw.text((60, 158), s, font=f, fill=(224, 231, 255))


def _bar(draw, x: int, y: int, w: int, h: int, share: float, color, back=LINE):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=back)
    if share > 0:
        draw.rounded_rectangle((x, y, x + max(h, int(w * min(share, 1.0))), y + h), radius=h // 2, fill=color)


def _star(draw, cx: float, cy: float, r: float, fill):
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = r if i % 2 == 0 else r * 0.45
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _flame(draw, cx: int, cy: int, s: int):
    draw.ellipse((cx - s, cy - s // 2, cx + s, cy + s * 3 // 2), fill=ORANGE)
    draw.polygon([(cx - s, cy + s // 3), (cx, cy - s * 2), (cx + s, cy + s // 3)], fill=ORANGE)
    draw.ellipse((cx - s // 2, cy + s // 6, cx + s // 2, cy + s * 7 // 6), fill=(253, 224, 71))


def _color_for(share: float):
    return GREEN if share >= 0.75 else (TEAL if share >= 0.5 else (ORANGE if share >= 0.25 else RED))


# ---------- Карточка «Мой прогресс» ----------

def progress_card(name: str, grade: int, level_text: str, points_text: str, streak: int,
                  week_done: int, week_goal: int, subjects: Sequence[Tuple[str, int, int, int]]) -> bytes:
    """subjects: (предмет, знает/закреплено, изучаем, подтянуть)."""
    rows = list(subjects)[:12]
    height = 230 + 190 + 60 + max(1, len(rows)) * 86 + 120
    image = Image.new("RGB", (W, height), BG)
    draw = ImageDraw.Draw(image)
    _header(image, draw, name, f"{grade} класс · {level_text}")

    # Плитки: очки, серия, цель недели
    y = 260
    tiles = [("Очки", points_text, INDIGO), ("Серия", f"{streak} дн.", ORANGE), ("Неделя", f"{week_done}/{week_goal}", TEAL)]
    tw = (W - 120 - 2 * 24) // 3
    for i, (label, value, color) in enumerate(tiles):
        x = 60 + i * (tw + 24)
        draw.rounded_rectangle((x, y, x + tw, y + 150), radius=28, fill=CARD)
        draw.text((x + 28, y + 24), label, font=font(26), fill=MUTED)
        v, f = _fit(draw, value, 50, tw - 90, True)
        draw.text((x + 28, y + 64), v, font=f, fill=color)
        if label == "Серия":
            _flame(draw, x + tw - 48, y + 92, 18)
        elif label == "Очки":
            _star(draw, x + tw - 48, y + 96, 24, GOLD)
        else:
            _bar(draw, x + 28, y + 128, tw - 56, 10, week_done / max(1, week_goal), TEAL)

    # Предметы: доля тем, которые знает или закрепил
    y += 190
    draw.text((60, y), "Сколько тем уже знает", font=font(34, True), fill=TEXT)
    y += 60
    for subject, good, learning, weak in rows:
        total = good + learning + weak
        share = good / total if total else 0
        draw.rounded_rectangle((60, y, W - 60, y + 72), radius=22, fill=CARD)
        s, f = _fit(draw, subject, 30, 300, True)
        draw.text((88, y + 20), s, font=f, fill=TEXT)
        _bar(draw, 410, y + 28, 420, 18, share, _color_for(share))
        draw.text((850, y + 20), f"{round(share * 100)}%", font=font(30, True), fill=_color_for(share))
        if weak:
            draw.ellipse((W - 112, y + 22, W - 84, y + 50), fill=RED)
            draw.text((W - 98, y + 36), str(weak), font=font(20, True), fill=(255, 255, 255), anchor="mm")
        y += 86
    if not rows:
        draw.text((60, y + 10), "Пока нет тем — начни с входного теста в любом предмете!", font=font(28), fill=MUTED)
        y += 86
    y += 20
    legend = [(GREEN, "отлично"), (TEAL, "хорошо"), (ORANGE, "изучаем"), (RED, "цифра — темы подтянуть")]
    x = 60
    for color, label in legend:
        draw.ellipse((x, y + 6, x + 22, y + 28), fill=color)
        draw.text((x + 32, y + 2), label, font=font(24), fill=MUTED)
        x += 60 + int(draw.textlength(label, font=font(24)))
    return _png(image)


# ---------- Результат теста ----------

def result_card(title: str, subtitle: str, right: int, total: int, answers: Sequence[bool],
                mark: Optional[int] = None) -> bytes:
    height = 230 + 560
    image = Image.new("RGB", (W, height), BG)
    draw = ImageDraw.Draw(image)
    share0 = right / total if total else 0
    _header(image, draw, title, subtitle, mood="cheer" if share0 >= 0.8 else ("wink" if share0 >= 0.5 else "happy"))
    share = right / total if total else 0
    color = _color_for(share)
    cx, cy, r = W // 2, 230 + 230, 160
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LINE, width=34)
    if share > 0:
        draw.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=-90 + 360 * share, fill=color, width=34)
    draw.text((cx, cy - 18), f"{right} из {total}", font=font(64, True), fill=TEXT, anchor="mm")
    draw.text((cx, cy + 46), f"{round(share * 100)}%", font=font(36), fill=MUTED, anchor="mm")
    if mark:
        mx, my = W - 170, 230 + 120
        draw.ellipse((mx - 70, my - 70, mx + 70, my + 70), fill=_color_for((mark - 2) / 3))
        draw.text((mx, my - 6), str(mark), font=font(84, True), fill=(255, 255, 255), anchor="mm")
        draw.text((mx, my + 92), "оценка", font=font(26), fill=MUTED, anchor="mm")
    # Кружки по вопросам: зелёный — верно, красный — ошибка
    n = len(answers)
    step = min(90, (W - 160) // max(1, n))
    x0 = cx - step * (n - 1) / 2
    for i, ok in enumerate(answers):
        x = x0 + i * step
        draw.ellipse((x - 26, height - 110, x + 26, height - 58), fill=GREEN if ok else RED)
        draw.text((x, height - 84), str(i + 1), font=font(24, True), fill=(255, 255, 255), anchor="mm")
    return _png(image)


# ---------- Грамота ----------

def certificate_card(name: str, title: str, subtitle: str, when: Optional[date] = None) -> bytes:
    w, h = W, 760
    image = Image.new("RGB", (w, h), (255, 251, 235))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, w - 24, h - 24), radius=36, outline=GOLD, width=10)
    draw.rounded_rectangle((52, 52, w - 52, h - 52), radius=26, outline=(253, 230, 138), width=4)
    for x, y, r in ((120, 120, 30), (w - 120, 120, 30), (120, h - 120, 30)):
        _star(draw, x, y, r, GOLD)
    _put_mascot(image, w - 270, h - 300, 250, "cheer")
    draw.text((w // 2, 150), "ГРАМОТА", font=font(84, True), fill=(180, 83, 9), anchor="mm")
    draw.text((w // 2, 230), "награждается", font=font(30), fill=MUTED, anchor="mm")
    n, f = _fit(draw, name, 72, w - 260, True)
    draw.text((w // 2, 320), n, font=f, fill=INDIGO, anchor="mm")
    t, f = _fit(draw, title, 46, w - 220, True)
    draw.text((w // 2, 430), t, font=f, fill=TEXT, anchor="mm")
    s, f = _fit(draw, subtitle, 30, w - 220)
    draw.text((w // 2, 500), s, font=f, fill=MUTED, anchor="mm")
    for i in range(5):
        _star(draw, w // 2 - 160 + i * 80, 590, 26, GOLD)
    stamp = (when or date.today()).strftime("%d.%m.%Y")
    draw.text((w // 2, h - 100), f"{BRAND} · {stamp}", font=font(26), fill=MUTED, anchor="mm")
    return _png(image)


# ---------- Знакомство: «Привет! Я Маячок» ----------

def welcome_card() -> bytes:
    h = 640
    image = Image.new("RGB", (W, h), BG)
    image.paste(_gradient((W, h), INDIGO, VIOLET), (0, 0))
    draw = ImageDraw.Draw(image)
    overlay = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for x, y, r in ((80, 560, 140), (980, 80, 120), (620, 640, 90)):
        od.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 24))
    image.paste(overlay, (0, 0), overlay)
    _put_mascot(image, 40, 70, 520, "cheer")
    x = 470
    draw.text((x, 110), "Привет!", font=font(44), fill=(224, 231, 255))
    draw.text((x, 162), "Я Маячок", font=font(78, True), fill=(255, 255, 255))
    draw.text((x, 262), "твой репетитор по школьным", font=font(30), fill=(224, 231, 255))
    draw.text((x, 300), "предметам с 1 по 11 класс", font=font(30), fill=(224, 231, 255))
    items = ["Объясняю темы по твоему учебнику", "Даю задания, тесты и ставлю оценки",
             "Помогаю с домашкой по фото", "Готовлю к ВПР, ОГЭ и ЕГЭ"]
    y = 370
    for item in items:
        _star(draw, x + 14, y + 18, 13, (253, 224, 71))
        t, f = _fit(draw, item, 28, W - x - 80)
        draw.text((x + 40, y + 2), t, font=f, fill=(255, 255, 255))
        y += 56
    return _png(image)


# ---------- Недельный отчёт родителю ----------

def parent_card(name: str, grade: Optional[int], period: str, days: int, solved: int, accuracy: Optional[int],
                marks: Sequence[Tuple[str, int]], weak: Sequence[str]) -> bytes:
    marks = list(marks)[:6]
    weak = list(weak)[:4]
    height = 230 + 190 + 70 + max(1, len(marks)) * 70 + (70 + len(weak) * 56 if weak else 0) + 60
    image = Image.new("RGB", (W, height), BG)
    draw = ImageDraw.Draw(image)
    _header(image, draw, f"{name}" + (f", {grade} класс" if grade else ""), f"Неделя {period}")
    y = 260
    tiles = [("Дней занятий", f"{days} из 7", INDIGO), ("Решено", str(solved), TEAL),
             ("С первой попытки", f"{accuracy}%" if accuracy is not None else "—", GREEN)]
    tw = (W - 120 - 2 * 24) // 3
    for i, (label, value, color) in enumerate(tiles):
        x = 60 + i * (tw + 24)
        draw.rounded_rectangle((x, y, x + tw, y + 150), radius=28, fill=CARD)
        lbl, f = _fit(draw, label, 24, tw - 50)
        draw.text((x + 26, y + 24), lbl, font=f, fill=MUTED)
        draw.text((x + 26, y + 64), value, font=font(50, True), fill=color)
    y += 190
    draw.text((60, y), "Оценки за уроки", font=font(34, True), fill=TEXT)
    y += 70
    for lesson, mark in marks:
        draw.rounded_rectangle((60, y, W - 60, y + 58), radius=18, fill=CARD)
        t, f = _fit(draw, lesson, 28, W - 260)
        draw.text((88, y + 14), t, font=f, fill=TEXT)
        draw.ellipse((W - 128, y + 7, W - 84, y + 51), fill=_color_for((mark - 2) / 3))
        draw.text((W - 106, y + 29), str(mark), font=font(28, True), fill=(255, 255, 255), anchor="mm")
        y += 70
    if not marks:
        draw.text((60, y), "На этой неделе оценок за уроки не было", font=font(28), fill=MUTED)
        y += 70
    if weak:
        draw.text((60, y + 10), "Стоит подтянуть", font=font(34, True), fill=TEXT)
        y += 70
        for topic in weak:
            draw.ellipse((66, y + 12, 86, y + 32), fill=RED)
            t, f = _fit(draw, topic, 28, W - 180)
            draw.text((104, y + 6), t, font=f, fill=TEXT)
            y += 56
    return _png(image)


# ---------- «Мой маяк»: серия дней и маяк, который строится ----------

def _sky(w: int, h: int, top, bottom):
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return grad.resize((w, h))


def _gull(draw, x: int, y: int, s: int, color):
    draw.arc((x - s, y - s // 2, x, y + s // 2), start=200, end=340, fill=color, width=5)
    draw.arc((x, y - s // 2, x + s, y + s // 2), start=200, end=340, fill=color, width=5)


def _firework(draw, cx: int, cy: int, r: int, color):
    for i in range(12):
        a = i * math.pi / 6
        x1, y1 = cx + r * 0.35 * math.cos(a), cy + r * 0.35 * math.sin(a)
        x2, y2 = cx + r * math.cos(a), cy + r * math.sin(a)
        draw.line((x1, y1, x2, y2), fill=color, width=6)
        draw.ellipse((x2 - 7, y2 - 7, x2 + 7, y2 + 7), fill=color)


def lighthouse_card(name: str, streak: int, best: int, freeze: bool,
                    milestones: Sequence[Tuple[int, str]]) -> bytes:
    """Маяк достраивается с каждым днём серии: фундамент → стены → окна → флаг → огонь → чайки → лодка → звёзды → салют."""
    built = sum(1 for days, _ in milestones if streak >= days)
    night = built >= 8
    sw, sh = W, 760
    image = Image.new("RGB", (W, sh + 420), BG)
    scene = _sky(sw, sh, (30, 27, 75) if night else (125, 211, 252), (67, 56, 202) if night else (224, 242, 254))
    image.paste(scene, (0, 0))
    draw = ImageDraw.Draw(image)
    glow = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)

    # Небо: солнце днём, луна и звёзды ночью
    if night:
        for x, y, r in ((90, 90, 7), (230, 170, 5), (380, 60, 6), (700, 120, 5), (860, 70, 8), (990, 190, 5),
                        (160, 300, 5), (930, 330, 6), (520, 40, 5), (300, 250, 4)):
            _star(draw, x, y, r * 2, (254, 249, 195))
        draw.ellipse((830, 80, 930, 180), fill=(254, 249, 195))
        draw.ellipse((860, 66, 960, 166), fill=(43, 38, 110))
    else:
        draw.ellipse((860, 70, 980, 190), fill=(253, 224, 71))
        for x, y in ((120, 120), (300, 80)):
            draw.ellipse((x, y, x + 150, y + 54), fill=(255, 255, 255))
            draw.ellipse((x + 40, y - 26, x + 120, y + 40), fill=(255, 255, 255))

    # Море и волны
    sea_y = sh - 190
    draw.rectangle((0, sea_y, sw, sh), fill=(30, 64, 175) if night else (14, 116, 214))
    for row in range(4):
        y = sea_y + 30 + row * 40
        for x in range(-40 + (row % 2) * 60, sw, 120):
            draw.arc((x, y - 14, x + 60, y + 14), start=200, end=340, fill=(147, 197, 253), width=4)

    # Скалы
    cx = 470
    rock = (100, 116, 139)
    draw.polygon([(cx - 260, sh - 120), (cx - 190, sea_y - 40), (cx - 60, sea_y - 70), (cx + 90, sea_y - 60),
                  (cx + 230, sea_y - 20), (cx + 300, sh - 120)], fill=rock)
    draw.polygon([(cx - 190, sea_y - 40), (cx - 60, sea_y - 70), (cx - 20, sea_y - 20), (cx - 150, sea_y)],
                 fill=(148, 163, 184))

    base_y = sea_y - 64
    top_y = 300
    bw, tw = 110, 66   # половина ширины башни внизу и вверху

    def half(y):   # половина ширины башни на высоте y
        t = (base_y - y) / (base_y - top_y)
        return bw + (tw - bw) * t

    def tower_part(y1, y2, color):
        draw.polygon([(cx - half(y1), y1), (cx + half(y1), y1), (cx + half(y2), y2), (cx - half(y2), y2)], fill=color)

    if built == 0:
        # Только место под стройку: пунктирный контур будущего маяка
        for y in range(top_y, base_y, 28):
            draw.line((cx - half(y), y, cx - half(y + 14), y + 14), fill=(255, 255, 255), width=4)
            draw.line((cx + half(y), y, cx + half(y + 14), y + 14), fill=(255, 255, 255), width=4)
    else:
        height_share = {1: 0.22, 2: 0.75}.get(built, 1.0)
        top = int(base_y - (base_y - top_y) * height_share)
        stripes = 6
        step = (base_y - top_y) / stripes
        for i in range(stripes):
            y1, y2 = base_y - i * step, base_y - (i + 1) * step
            if y1 <= top:
                break
            tower_part(y1, max(y2, top), (220, 38, 38) if i % 2 == 0 else (255, 255, 255))
        draw.rectangle((cx - bw - 20, base_y - 6, cx + bw + 20, base_y + 18), fill=(71, 85, 105))
        if built < 3:   # стройка: леса вокруг башни
            for x in (cx - bw - 30, cx + bw + 30):
                draw.line((x, base_y, x, top - 30), fill=(180, 120, 60), width=8)
            for y in range(base_y - 30, top - 30, -70):
                draw.line((cx - bw - 30, y, cx + bw + 30, y), fill=(180, 120, 60), width=6)
        if built >= 3:
            # Дверь и окна
            draw.rounded_rectangle((cx - 26, base_y - 84, cx + 26, base_y - 4), radius=24, fill=(120, 53, 15))
            for wy in (base_y - 160,):
                draw.rounded_rectangle((cx - 16, wy, cx + 16, wy + 44), radius=14, fill=(30, 58, 138))
            # Площадка, фонарь и купол
            lamp_y = top_y - 90
            draw.rectangle((cx - tw - 26, top_y - 14, cx + tw + 26, top_y + 6), fill=(51, 65, 85))
            lit = built >= 5   # огонь — с 7 дней (этап 5)
            draw.rectangle((cx - 50, lamp_y, cx + 50, top_y - 14), fill=(254, 240, 138) if lit else (186, 230, 253))
            for x in (cx - 50, cx - 17, cx + 17, cx + 50):
                draw.line((x, lamp_y, x, top_y - 14), fill=(51, 65, 85), width=6)
            draw.pieslice((cx - 66, lamp_y - 60, cx + 66, lamp_y + 60), start=180, end=360, fill=(220, 38, 38))
            draw.ellipse((cx - 10, lamp_y - 76, cx + 10, lamp_y - 56), fill=(51, 65, 85))
            if lit:
                ly = (lamp_y + top_y - 14) // 2
                for r, alpha in ((130, 35), (85, 55), (50, 90)):
                    gd.ellipse((cx - r, ly - r, cx + r, ly + r), fill=(253, 224, 71, alpha))
                gd.polygon([(cx + 40, ly - 20), (sw, ly - 130), (sw, ly + 110), (cx + 40, ly + 20)],
                           fill=(254, 240, 138, 90))
                gd.polygon([(cx - 40, ly - 20), (0, ly - 110), (0, ly + 90), (cx - 40, ly + 20)],
                           fill=(254, 240, 138, 70))
            if built >= 4:   # флаг на шпиле
                draw.line((cx, lamp_y - 70, cx, lamp_y - 170), fill=(51, 65, 85), width=6)
                draw.polygon([(cx + 3, lamp_y - 170), (cx + 80, lamp_y - 146), (cx + 3, lamp_y - 122)], fill=INDIGO)
    image.paste(glow, (0, 0), glow)
    draw = ImageDraw.Draw(image)

    if built >= 6:   # чайки
        for x, y, s in ((700, 240, 44), (790, 300, 34), (160, 380, 38)):
            _gull(draw, x, y, s, (255, 255, 255) if night else (51, 65, 85))
    if built >= 7:   # лодка
        bx, by = 840, sea_y + 70
        draw.polygon([(bx - 90, by), (bx + 90, by), (bx + 60, by + 46), (bx - 60, by + 46)], fill=(180, 83, 9))
        draw.line((bx, by, bx, by - 150), fill=(120, 53, 15), width=6)
        draw.polygon([(bx + 4, by - 150), (bx + 4, by - 10), (bx + 90, by - 10)], fill=(255, 255, 255))
    if built >= 9:   # салют
        for x, y, r, color in ((180, 160, 80, (244, 114, 182)), (760, 130, 70, (253, 224, 71)),
                               (960, 330, 60, (74, 222, 128)), (330, 330, 55, (96, 165, 250))):
            _firework(draw, x, y, r, color)

    # Маячок на берегу
    _put_mascot(image, 760 if built < 7 else 640, sea_y - 250, 250, "cheer" if streak else "happy")
    draw.text((40, 30), BRAND.upper(), font=font(24, True), fill=(255, 255, 255) if night else (30, 58, 138))

    # Панель снизу: серия, рекорд, заморозка, следующий этап
    y = sh + 30
    draw.rounded_rectangle((40, y, W - 40, y + 360), radius=32, fill=CARD)
    _flame(draw, 104, y + 74, 26)
    title = f"Серия: {streak} {_days(streak)} подряд" if streak else "Серия: начнём сегодня!"
    t, f = _fit(draw, title, 50, W - 260, True)
    draw.text((150, y + 40), t, font=f, fill=TEXT)
    nm, f = _fit(draw, name, 28, W - 200)
    draw.text((150, y + 106), nm, font=f, fill=MUTED)
    nxt = next((m for m in milestones if streak < m[0]), None)
    if nxt:
        left = nxt[0] - streak
        draw.text((80, y + 166), plain(f"До этапа «{nxt[1]}»: ещё {left} {_days(left)}"), font=font(30), fill=TEXT)
        _bar(draw, 80, y + 214, W - 160, 22, streak / nxt[0], ORANGE)
    else:
        draw.text((80, y + 166), "Маяк построен полностью! Ты — легенда.", font=font(30, True), fill=GREEN)
        _bar(draw, 80, y + 214, W - 160, 22, 1.0, GREEN)
    chips = [(f"Рекорд: {best} {_days(best)}", INDIGO),
             ("Заморозка: есть" if freeze else "Заморозка: на след. неделе", TEAL if freeze else MUTED)]
    x = 80
    for label, color in chips:
        tw_ = int(draw.textlength(label, font=font(26, True)))
        draw.rounded_rectangle((x, y + 266, x + tw_ + 44, y + 320), radius=27, fill=BG)
        draw.text((x + 22, y + 278), label, font=font(26, True), fill=color)
        x += tw_ + 64
    return _png(image)


def _days(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "дней"
    return {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(n % 10, "дней")


# ---------- «Сундук дня» ----------

_EMOJI_FONTS = ["/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/usr/share/fonts/noto/NotoColorEmoji.ttf",
                "/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf"]


def _emoji_image(symbol: str, size: int):
    """Цветной эмодзи картинкой (шрифт Noto Color Emoji рисуется только размером 109, потом масштабируем).
    None — шрифта нет (тогда рисуем звезду)."""
    path = next((p for p in _EMOJI_FONTS if os.path.exists(p)), None)
    if not path:
        return None
    try:
        f = ImageFont.truetype(path, 109)
        tile = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((80, 80), symbol, font=f, embedded_color=True, anchor="mm")
        box = tile.getbbox()
        if not box:
            return None
        tile = tile.crop(box)
        k = size / max(tile.size)
        return tile.resize((max(1, int(tile.width * k)), max(1, int(tile.height * k))), Image.LANCZOS)
    except Exception:
        return None


def _wrap(draw, text: str, f, width: int) -> List[str]:
    lines, cur = [], ""
    for word in plain(text).split():
        test = f"{cur} {word}".strip()
        if draw.textlength(test, font=f) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


CHEST_STYLE = {   # редкость: (цвет фона слева, справа, подпись)
    "common": ((20, 184, 166), (14, 116, 144), "ОБЫЧНАЯ НАКЛЕЙКА"),
    "rare": ((124, 58, 237), (79, 70, 229), "РЕДКАЯ НАКЛЕЙКА"),
    "legendary": ((234, 179, 8), (234, 88, 12), "ЛЕГЕНДАРНАЯ НАКЛЕЙКА"),
    "fact": ((79, 70, 229), (37, 99, 235), "ИНТЕРЕСНЫЙ ФАКТ"),
    "title": ((245, 158, 11), (220, 38, 38), "НОВОЕ ЗВАНИЕ"),
    "bonus": ((34, 197, 94), (21, 128, 61), "БОНУСНЫЕ ОЧКИ"),
}


def chest_card(symbol: str, name: str, rarity: str, footer: str = "") -> bytes:
    left, right, label = CHEST_STYLE.get(rarity, CHEST_STYLE["common"])
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    is_fact = rarity == "fact"
    body = _wrap(probe, name, font(40 if is_fact else 64, True), W - 160)[:6]
    h = 760 + (len(body) - 1) * (56 if is_fact else 80)
    image = _gradient((W, h), left, right)
    draw = ImageDraw.Draw(image)
    # Лучи от награды
    rays = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    cx, cy = W // 2, 290
    for i in range(16):
        a1, a2 = i * math.pi / 8, i * math.pi / 8 + math.pi / 16
        rd.polygon([(cx, cy), (cx + 1400 * math.cos(a1), cy + 1400 * math.sin(a1)),
                    (cx + 1400 * math.cos(a2), cy + 1400 * math.sin(a2))], fill=(255, 255, 255, 30))
    for r, alpha in ((210, 40), (160, 60)):
        rd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, alpha))
    image.paste(rays, (0, 0), rays)
    draw = ImageDraw.Draw(image)
    for x, y, r in ((140, 140, 22), (930, 120, 18), (880, 430, 14), (190, 420, 16), (520, 70, 12)):
        _star(draw, x, y, r, (255, 255, 255))
    pic = _emoji_image(symbol, 230)
    if pic is not None:
        image.paste(pic, (cx - pic.width // 2, cy - pic.height // 2), pic)
    else:
        _star(draw, cx, cy, 120, (253, 224, 71))
    draw.text((40, 30), "СУНДУК ДНЯ · " + BRAND.upper(), font=font(24, True), fill=(255, 255, 255))
    lw = int(draw.textlength(label, font=font(28, True)))
    draw.rounded_rectangle((cx - lw // 2 - 28, 470, cx + lw // 2 + 28, 524), radius=27, fill=(255, 255, 255))
    draw.text((cx, 497), label, font=font(28, True), fill=right, anchor="mm")
    y = 560
    f = font(40 if is_fact else 64, True)
    for line in body:
        draw.text((cx, y), line, font=f, fill=(255, 255, 255), anchor="ma")
        y += 56 if is_fact else 80
    if footer:
        t, ff = _fit(draw, footer, 28, W - 120)
        draw.text((cx, h - 50), t, font=ff, fill=(255, 255, 255), anchor="mm")
    return _png(image)


# ---------- Игра «Молния» ----------

def _bolt(draw, cx: int, cy: int, s: float, fill):
    pts = [(0.15, -1.0), (-0.55, 0.12), (-0.05, 0.12), (-0.25, 1.0), (0.6, -0.25), (0.08, -0.25), (0.35, -1.0)]
    draw.polygon([(cx + x * s, cy + y * s) for x, y in pts], fill=fill)


def lightning_card(name: str, mode_title: str, score: int, best: int, new_record: bool, rank: str,
                   right: int, wrong: int) -> bytes:
    h = 900
    image = _sky(W, h, (30, 27, 75), (76, 29, 149))
    draw = ImageDraw.Draw(image)
    glow = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r, alpha in ((300, 30), (220, 45), (150, 60)):
        gd.ellipse((300 - r, 330 - r, 300 + r, 330 + r), fill=(250, 204, 21, alpha))
    image.paste(glow, (0, 0), glow)
    draw = ImageDraw.Draw(image)
    for x, y, r in ((80, 90, 10), (980, 70, 14), (900, 560, 9), (60, 600, 12), (620, 60, 8), (1000, 380, 8)):
        _star(draw, x, y, r * 2, (254, 249, 195))
    _bolt(draw, 300, 330, 230, (250, 204, 21))
    _bolt(draw, 300, 330, 160, (254, 240, 138))
    draw.text((40, 30), "МОЛНИЯ · " + BRAND.upper(), font=font(24, True), fill=(224, 231, 255))
    draw.text((760, 170), "верных за 60 секунд", font=font(28), fill=(224, 231, 255), anchor="mm")
    draw.text((760, 330), str(score), font=font(220, True), fill=(255, 255, 255), anchor="mm")
    m, f = _fit(draw, mode_title, 32, 420, True)
    draw.text((760, 480), m, font=f, fill=(250, 204, 21), anchor="mm")
    if new_record:
        lw = int(draw.textlength("НОВЫЙ РЕКОРД!", font=font(34, True)))
        draw.rounded_rectangle((760 - lw // 2 - 30, 530, 760 + lw // 2 + 30, 590), radius=30, fill=(250, 204, 21))
        draw.text((760, 560), "НОВЫЙ РЕКОРД!", font=font(34, True), fill=(76, 29, 149), anchor="mm")
    y = 650
    draw.rounded_rectangle((40, y, W - 40, h - 40), radius=32, fill=CARD)
    n, f = _fit(draw, name, 30, W - 160)
    draw.text((80, y + 30), n, font=f, fill=MUTED)
    rk, f = _fit(draw, rank, 40, W - 160, True)
    draw.text((80, y + 76), rk, font=f, fill=TEXT)
    chips = [(f"Рекорд: {best}", INDIGO), (f"Верно: {right}", GREEN), (f"Ошибок: {wrong}", RED)]
    x = 80
    for label, color in chips:
        tw_ = int(draw.textlength(label, font=font(26, True)))
        draw.rounded_rectangle((x, y + 146, x + tw_ + 44, y + 198), radius=26, fill=BG)
        draw.text((x + 22, y + 157), label, font=font(26, True), fill=color)
        x += tw_ + 64
    return _png(image)
