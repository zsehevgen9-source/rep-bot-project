"""«🎁 Сундук дня»: раз в день после CHEST_NEED решённых заданий — случайная награда и альбом наклеек.

Redis: chest_<uid> — альбом (наклейки, звания, открытые факты), chest_day_<uid> — дата последнего сундука.
Нейросеть не нужна: содержимое сундуков написано заранее (services/chest.py), картинку рисует Pillow.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards
from app.services import chest as chest_rules


class ChestMixin:

    async def _chest_album(self, uid: int) -> Dict[str, Any]:
        album = load_json(await self.redis.get_state(f"chest_{uid}"), None)
        return album if isinstance(album, dict) else chest_rules.new_album()

    async def _chest_status(self, uid: int) -> Dict[str, Any]:
        """{"ready": можно открыть, "opened": уже открыт сегодня, "left": сколько заданий ещё решить}."""
        profile = await self._load_profile(uid)
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date().isoformat()
        solved = load_json(profile.get("activity"), {}).get(today, {}).get("solved", 0)
        opened = await self.redis.get_state(f"chest_day_{uid}") == today
        left = max(0, chest_rules.CHEST_NEED - solved)
        return {"ready": not opened and left == 0, "opened": opened, "left": left, "today": today}

    async def _chest_note(self, uid: int) -> str:
        """Строка к награде за задание: сундук только что стал доступен (один раз за день)."""
        st = await self._chest_status(uid)
        if not st["ready"] or await self.redis.get_state(f"chest_told_{uid}") == st["today"]:
            return ""
        await self.redis.set_state(f"chest_told_{uid}", st["today"], ex=2 * DAY)
        return "\n🎁 Сундук дня готов! Открой его кнопкой «🎁 Сундук» в меню."

    def _chest_rows(self) -> List[List[Dict[str, str]]]:
        return [[_btn("🎁 Открыть сундук", "chest")]]

    async def open_chest(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        back = [_back(f"subjects_{grade}" if grade else "back_to_menu")]
        st = await self._chest_status(uid)
        if st["opened"]:
            await self._quiet(message, "🎁 Сундук дня уже открыт. Новый будет завтра — после "
                                       f"{chest_rules.CHEST_NEED} решённых заданий!",
                              keyboard=_kb([[_btn("📒 Альбом", "album")], back]))
            return
        if not st["ready"]:
            await self._quiet(message, f"🔒 Сундук дня закрыт. Реши ещё {st['left']} "
                                       f"{_tasks_word(st['left'])} — и он откроется!",
                              keyboard=_kb([[_btn("📅 Позаниматься", "today")], back]))
            return
        prize = await self._chest_open(uid, grade, st["today"])
        text, footer = prize["text"], prize["footer"]
        rows = [[_btn("📒 Альбом", "album")], back]
        sent = await self._send_card(message, cards.chest_card, prize["emoji"], prize["name"], prize["rarity"],
                                     footer, text=text, keyboard=_kb(rows))
        if not sent:
            await self._quiet(message, text, keyboard=_kb(rows))

    async def _chest_open(self, uid: int, grade: Optional[int], today: str) -> Dict[str, Any]:
        """Открыть сундук (проверки «можно ли» — у вызывающего). Общая часть для чата и мини-приложения:
        бросок, альбом, очки, текст. Возвращает награду с полями text, footer, points_text, collected, total."""
        album = await self._chest_album(uid)
        prize = chest_rules.roll(album)
        await self.redis.set_state(f"chest_{uid}", json.dumps(album, ensure_ascii=False))
        await self.redis.set_state(f"chest_day_{uid}", today, ex=2 * DAY)

        mode = rewards_rules.mode_for_grade(grade)
        points_text = ""
        if prize["points"]:
            profile = await self._load_profile(uid)
            rewards = load_json(profile.get("rewards"), {})
            rewards["points"] = rewards.get("points", 0) + prize["points"]
            await self._save_profile(uid, rewards=json.dumps(rewards, ensure_ascii=False))
            points_text = rewards_rules.gained_label(prize["points"], mode) or f"+{prize['points']} очков"

        total = len(chest_rules.STICKERS)
        if prize["kind"] == "sticker":
            rarity = chest_rules.RARITY_NAMES[prize["rarity"]]
            head = {"legendary": "🌟 ЛЕГЕНДАРНАЯ наклейка!!!", "rare": "✨ Редкая наклейка!"}.get(
                prize["rarity"], "Новая наклейка!")
            if prize["duplicate"]:
                text = (f"🎁 В сундуке — {prize['emoji']} «{prize['name']}» ({rarity}).\n"
                        f"Такая уже есть в альбоме — повторка превращается в очки: {points_text}")
            else:
                text = f"🎁 {head}\n{prize['emoji']} «{prize['name']}» — в твоём альбоме."
            footer = f"Альбом: {chest_rules.collected(album)} из {total}"
            text += f"\n📒 Собрано: {chest_rules.collected(album)} из {total}"
        elif prize["kind"] == "fact":
            text = f"🎁 В сундуке — интересный факт!\n\n💡 {prize['name']}"
            footer = points_text
            if points_text:
                text += f"\n\n{points_text}"
        elif prize["kind"] == "title":
            text = f"🎁 Новое звание: 🏅 «{prize['name']}»!\nТеперь в меню будет видно, кто ты 😎"
            footer = "Все звания — в альбоме"
        else:
            text = f"🎁 Горсть бонусных очков: {points_text}!"
            footer = points_text
        text += "\n\nСледующий сундук — завтра."
        return {**prize, "text": text, "footer": footer, "points_text": points_text,
                "collected": chest_rules.collected(album), "total": total}

    async def show_album(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        album = await self._chest_album(uid)
        lines = chest_rules.album_lines(album)
        st = await self._chest_status(uid)
        rows = []
        if st["ready"]:
            rows.append([_btn("🎁 Открыть сундук", "chest")])
            lines.append("\n🎁 Сундук дня ждёт тебя!")
        elif not st["opened"]:
            lines.append(f"\n🔒 До сундука дня: ещё {st['left']} {_tasks_word(st['left'])}.")
        else:
            lines.append("\nСундук дня уже открыт — новый завтра.")
        lines.append("Повторка наклейки превращается в очки. Если 7 сундуков подряд без редкой — "
                     "следующая наклейка точно редкая ✨")
        rows.append([_back(f"subjects_{grade}" if grade else "back_to_menu")])
        await self._quiet(message, "\n".join(lines), keyboard=_kb(rows))


def _tasks_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "заданий"
    return {1: "задание", 2: "задания", 3: "задания", 4: "задания"}.get(n % 10, "заданий")
