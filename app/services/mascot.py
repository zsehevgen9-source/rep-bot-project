"""Маячок — талисман бота: добрый маяк в красно-белую полоску с лицом и светящимся фонарём.

Рисуется кодом (Pillow), без файлов-картинок: mascot(высота, настроение) → картинка RGBA с прозрачным фоном.
Настроения: "happy" — улыбается, "cheer" — радуется (поднятые руки, звёздочки), "wink" — подмигивает.
Рисуем в 4 раза крупнее и уменьшаем — так края получаются гладкими.
"""
import math
from functools import lru_cache

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    Image = ImageDraw = ImageFilter = None

RED = (230, 57, 70)
DARK_RED = (168, 32, 48)
WHITE = (255, 255, 255)
CREAM = (255, 248, 235)
LAMP = (255, 214, 10)
LAMP_LIGHT = (255, 240, 150)
INK = (40, 30, 50)
CHEEK = (255, 150, 160)
ROCK = (96, 112, 140)
ROCK_LIGHT = (130, 146, 175)
WAVE = (90, 170, 230)
GOLD = (250, 204, 21)

BASE_W, BASE_H = 300, 400
SS = 4   # во сколько раз рисуем крупнее


def _p(*xy):
    return [v * SS for v in xy]


def _star(draw, cx, cy, r, fill):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(((cx + rr * math.cos(a)) * SS, (cy + rr * math.sin(a)) * SS))
    draw.polygon(pts, fill=fill)


@lru_cache(maxsize=32)
def _render(height: int, mood: str):
    w, h = BASE_W * SS, BASE_H * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # Свет фонаря: мягкое сияние и два луча
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse(_p(95, 40, 205, 150), fill=(255, 230, 120, 150))
    gd.polygon(_p(150, 88, 40, 62, 40, 114), fill=(255, 236, 140, 110))
    gd.polygon(_p(150, 88, 260, 62, 260, 114), fill=(255, 236, 140, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(8 * SS))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)

    # Камень и волны внизу
    d.ellipse(_p(55, 335, 245, 385), fill=ROCK)
    d.ellipse(_p(80, 338, 200, 360), fill=ROCK_LIGHT)
    for x in (40, 95, 150, 205):
        d.arc(_p(x, 368, x + 55, 395), start=200, end=340, fill=WAVE, width=5 * SS)

    # Руки (за башней) — при радости подняты вверх
    if mood == "cheer":
        d.line(_p(100, 235, 62, 190), fill=DARK_RED, width=13 * SS)
        d.line(_p(200, 235, 238, 190), fill=DARK_RED, width=13 * SS)
        d.ellipse(_p(50, 176, 74, 200), fill=CREAM)
        d.ellipse(_p(226, 176, 250, 200), fill=CREAM)
    else:
        d.line(_p(100, 240, 70, 285), fill=DARK_RED, width=13 * SS)
        d.line(_p(200, 240, 230, 285), fill=DARK_RED, width=13 * SS)
        d.ellipse(_p(58, 275, 82, 299), fill=CREAM)
        d.ellipse(_p(218, 275, 242, 299), fill=CREAM)

    # Башня: трапеция в полоску
    tower = _p(112, 132, 188, 132, 214, 348, 86, 348)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(tower, fill=255)
    stripes = Image.new("RGBA", (w, h), WHITE + (255,))
    sd = ImageDraw.Draw(stripes)
    for top, bottom in ((132, 158), (238, 272), (308, 348)):
        sd.rectangle(_p(0, top, BASE_W, bottom), fill=RED + (255,))
    # Тень справа — башня объёмнее
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shade).polygon(_p(170, 132, 188, 132, 214, 348, 184, 348), fill=(40, 20, 60, 38))
    img.paste(stripes, (0, 0), mask)
    img.alpha_composite(Image.composite(shade, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask))
    d = ImageDraw.Draw(img)
    d.polygon(tower, outline=DARK_RED, width=3 * SS)

    # Дверь
    d.rounded_rectangle(_p(137, 314, 163, 348), radius=12 * SS, fill=(120, 70, 50))
    d.ellipse(_p(155, 330, 159, 334), fill=GOLD)

    # Балкон
    d.rounded_rectangle(_p(98, 120, 202, 136), radius=5 * SS, fill=DARK_RED)
    for x in range(106, 200, 12):
        d.line(_p(x, 108, x, 120), fill=DARK_RED, width=3 * SS)
    d.line(_p(100, 108, 200, 108), fill=DARK_RED, width=4 * SS)

    # Фонарь
    d.rounded_rectangle(_p(120, 72, 180, 120), radius=8 * SS, fill=LAMP, outline=DARK_RED, width=3 * SS)
    d.ellipse(_p(136, 82, 164, 110), fill=LAMP_LIGHT)
    for x in (140, 160):
        d.line(_p(x, 74, x, 118), fill=(230, 170, 0), width=2 * SS)

    # Крыша-шапочка
    d.polygon(_p(110, 74, 190, 74, 150, 30), fill=RED, outline=DARK_RED)
    d.polygon(_p(150, 30, 190, 74, 165, 74), fill=DARK_RED)
    d.ellipse(_p(143, 18, 157, 32), fill=GOLD, outline=DARK_RED, width=2 * SS)

    # Лицо на белой полосе
    eye_y = 186
    for ex in (130, 170):
        if mood == "wink" and ex == 170:
            d.arc(_p(ex - 9, eye_y - 6, ex + 9, eye_y + 10), start=200, end=340, fill=INK, width=4 * SS)
            continue
        if mood == "cheer":
            d.arc(_p(ex - 9, eye_y - 4, ex + 9, eye_y + 12), start=200, end=340, fill=INK, width=4 * SS)
            continue
        d.ellipse(_p(ex - 8, eye_y - 10, ex + 8, eye_y + 10), fill=INK)
        d.ellipse(_p(ex - 4, eye_y - 7, ex + 1, eye_y - 2), fill=WHITE)
    d.ellipse(_p(112, 202, 126, 212), fill=CHEEK)
    d.ellipse(_p(174, 202, 188, 212), fill=CHEEK)
    if mood == "cheer":
        d.chord(_p(136, 200, 164, 226), start=0, end=180, fill=INK)
        d.chord(_p(143, 212, 157, 226), start=0, end=180, fill=(240, 100, 110))
    else:
        d.arc(_p(137, 198, 163, 222), start=20, end=160, fill=INK, width=4 * SS)

    if mood == "cheer":
        for cx, cy, r in ((40, 120, 12), (262, 112, 10), (30, 250, 8), (272, 250, 9)):
            _star(d, cx, cy, r, GOLD)

    img = img.resize((BASE_W * height // BASE_H, height), Image.LANCZOS)
    return img


def mascot(height: int = 400, mood: str = "happy"):
    """Маячок высотой height пикселей (RGBA). mood: happy / cheer / wink."""
    return _render(int(height), mood if mood in ("happy", "cheer", "wink") else "happy").copy()


def avatar(size: int = 512):
    """Аватарка для профиля бота: Маячок в круге на фиолетовом фоне."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), (int(99 + (79 - 99) * t), int(102 + (70 - 102) * t), int(241 + (229 - 241) * t)))
    bg.paste(grad.resize((size, size)), (0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.paste(bg, (0, 0), mask)
    m = mascot(int(size * 0.86), "happy")
    img.alpha_composite(m, ((size - m.width) // 2, size - m.height - int(size * 0.02)))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out
