"""Плюшки кабинета учителя: выгрузка в Excel, карта класса по темам, «⚡ тема урока» в одно касание,
срок сдачи с автонапоминанием, «⭐ Звёзды недели» и грамоты, объявление классу.

Кнопки — с префиксом tx_ (у старых кнопок кабинета — t_). Redis:
    tann_<учитель>            — объявление, пока учитель выбирает, кому отправить: [код класса, текст]
    awaiting_tann_<учитель>   — ждём текст объявления (код класса)
    due_rem_<ученик>_<код>_<id> — напоминание о сроке уже отправлено
    due_sum_<код>_<id>        — итог по сроку учителю уже отправлен
"""
from datetime import date, timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards
from app.services import xlsx
from app.services.programs import programs

STATUS_ICONS = {"known": "🟢", "mastered": "🟢", "learning": "🟠", "weak": "🔴"}
STATUS_WORDS = {"known": "знает", "mastered": "закреплено", "learning": "изучает", "weak": "подтянуть"}
CERT_REASONS = ["За старание", "За серию занятий", "За отличные оценки", "За большой прогресс", "За помощь классу"]
DUE_CHOICES = {"d": "завтра", "f": "пятница", "w": "неделя"}
MAP_TOPICS = 8


def _ru_day(d: date) -> str:
    return d.strftime("%d.%m") + " (" + ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d.weekday()] + ")"


def due_date(today: date, choice: str) -> Optional[date]:
    if choice == "d":
        return today + timedelta(days=1)
    if choice == "f":
        ahead = (4 - today.weekday()) % 7 or 7          # ближайшая пятница (сегодня пятница — следующая)
        return today + timedelta(days=ahead)
    if choice == "w":
        return today + timedelta(days=7)
    return None


class TeacherExtrasMixin:

    async def _my_subjects(self, data: Dict[str, Any], uid: int) -> List[str]:
        return data["teachers"].get(str(uid)) or self._class_subjects(await self._tprofile(uid), data["grade"])

    async def _teacher_payload(self, message: NormalizedMessage, payload: str) -> bool:
        parts = payload.split("_", 3)
        kind = parts[1] if len(parts) > 1 else ""
        code = parts[2] if len(parts) > 2 else ""
        rest = parts[3] if len(parts) > 3 else ""
        try:
            if kind == "map":
                await self.show_class_map(message, code)
            elif kind == "star":
                await self.show_stars(message, code)
            elif kind == "stsend":
                await self.send_stars(message, code)
            elif kind == "xls":
                await self.export_class(message, code)
            elif kind == "qa":
                await self.quick_assign(message, code, rest)
            elif kind == "due":
                aid, choice = rest.split("_")
                await self.set_due_button(message, code, aid, choice)
            elif kind == "cert":
                await self.ask_cert(message, code, int(rest))
            elif kind == "certr":
                student, reason = rest.split("_")
                await self.cert_button(message, code, int(student), int(reason))
            elif kind == "ann":
                await self.ask_announce(message, code)
            elif kind == "annsend":
                await self.announce_button(message, code, rest == "p")
            else:
                return False
        except (ValueError, IndexError):
            await self.show_teacher_menu(message)
        return True

    # ---------- ⚡ тема урока в одно касание ----------

    async def today_topics(self, data: Dict[str, Any], uid: int) -> List[Dict[str, str]]:
        """Тема сегодняшнего урока по программе для каждого предмета учителя в этом классе (до 2)."""
        today = await self._local_date(uid)
        out = []
        for sid in await self._my_subjects(data, uid):
            if sid not in [s for _, s in SUBJECTS[data["grade"]]]:
                continue
            topics = programs.current_topics(data["grade"], sid, today)
            if topics:
                out.append({"subject_id": sid, "subject": subject_title(data["grade"], sid), "topic": topics[0]})
        return out[:2]

    async def quick_rows(self, data: Dict[str, Any], uid: int) -> List[List[Dict[str, str]]]:
        topics = await self.today_topics(data, uid)
        if len(topics) == 1:
            return [[_btn("⚡ Задать тему урока", f"tx_qa_{data['code']}_{topics[0]['subject_id']}")]]
        return [[_btn(f"⚡ {button_title(t['subject'])}", f"tx_qa_{data['code']}_{t['subject_id']}")] for t in topics]

    async def quick_lines(self, data: Dict[str, Any], uid: int) -> List[str]:
        return [f"⚡ Сегодня по программе ({t['subject']}): «{t['topic']}» — задать классу одной кнопкой."
                for t in await self.today_topics(data, uid)]

    async def quick_assign(self, message: NormalizedMessage, code: str, sid: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        topic = next((t["topic"] for t in await self.today_topics(data, message.user_id) if t["subject_id"] == sid),
                     None) if data else None
        if not topic:
            await self.show_teacher_menu(message)
            return
        await self.create_assignment(message, code, sid, topic)

    # ---------- ⏰ срок сдачи ----------

    def due_rows(self, code: str, aid: str) -> List[List[Dict[str, str]]]:
        return [[_btn("⏰ Завтра", f"tx_due_{code}_{aid}_d"), _btn("⏰ Пятница", f"tx_due_{code}_{aid}_f")],
                [_btn("⏰ Через неделю", f"tx_due_{code}_{aid}_w")]]

    async def set_due(self, uid: int, code: str, aid: str, choice: str) -> Optional[str]:
        """Срок задания (общая часть для чата и приложения). Возвращает дату или None."""
        data = await self._own_tclass(uid, code)
        task = next((a for a in self._mine(data, uid) if a["id"] == str(aid)), None) if data else None
        if not task:
            return None
        day = due_date(await self._local_date(uid), choice)
        task["due"] = day.isoformat() if day else None
        await self._save_tclass(data)
        if day:
            for student in data["students"]:
                if str(student) in task["results"]:
                    continue
                try:
                    await self.api.send_message(
                        user_id=student, text=f"⏰ Задание «{task['topic']}» нужно сделать до {_ru_day(day)}.",
                        keyboard=_kb([[_btn("📝 Начать задание", f"asg_{code}_{task['id']}")]]))
                except Exception as e:
                    logger.warning("Не удалось сообщить срок ученику %s: %s", student, e)
        return task["due"]

    async def set_due_button(self, message: NormalizedMessage, code: str, aid: str, choice: str) -> None:
        due = await self.set_due(message.user_id, code, aid, choice)
        if due is None and choice in DUE_CHOICES:
            await self.show_teacher_menu(message)
            return
        note = f"⏰ Срок: до {_ru_day(date.fromisoformat(due))}. За день я напомню тем, кто не сделал, а после срока " \
               "пришлю вам итог." if due else ""
        await self.show_class(message, code, note=note)

    async def class_check(self, message: NormalizedMessage) -> None:
        """Вечером (планировщик, от имени ученика): срок задания завтра — напоминаем; срок прошёл — итог учителю."""
        uid = message.user_id
        code = await self.redis.get_state(f"my_tclass_{uid}")
        data = await self._tclass(code) if code else None
        if not data or uid not in data["students"]:
            return
        today = await self._local_date(uid)
        for task in data["assignments"]:
            due = task.get("due")
            if not due:
                continue
            due_day = date.fromisoformat(due)
            key = f"due_rem_{uid}_{code}_{task['id']}"
            if due_day == today + timedelta(days=1) and str(uid) not in task["results"] \
                    and not await self.redis.get_state(key):
                await self.redis.set_state(key, "1", ex=14 * DAY)
                await self.api.send_message(
                    user_id=uid, text=f"⏰ Завтра срок задания учителя «{task['topic']}». Успей сегодня! 💪",
                    keyboard=_kb([[_btn("📝 Начать задание", f"asg_{code}_{task['id']}")]]))
            if due_day < today and (today - due_day).days <= 7 and not await self.redis.get_state(f"due_sum_{code}_{task['id']}"):
                await self.redis.set_state(f"due_sum_{code}_{task['id']}", "1", ex=30 * DAY)
                await self._due_summary(data, task)

    async def _due_summary(self, data: Dict[str, Any], task: Dict[str, Any]) -> None:
        res = list(task["results"].values())
        missing = [(await self._student_week(s))["name"] for s in data["students"] if str(s) not in task["results"]]
        avg = f", средняя оценка {sum(r['mark'] for r in res) / len(res):.1f}" if res else ""
        text = (f"📋 Срок задания «{task['topic']}» ({data['name']}) закончился.\n"
                f"Выполнили: {len(res)} из {len(data['students'])}{avg}."
                + (f"\nНе сделали: {', '.join(missing)}." if missing else "\nСделали все 🎉"))
        teacher = task.get("teacher", data.get("owner"))
        try:
            await self.api.send_message(user_id=teacher, text=text,
                                        keyboard=_kb([[_btn("📊 Результаты", f"t_resd_{data['code']}_{task['id']}")]]))
        except Exception as e:
            logger.warning("Не удалось отправить итог учителю %s: %s", teacher, e)

    # ---------- 🗺 карта класса по темам ----------

    async def class_map(self, data: Dict[str, Any], uid: int) -> Dict[str, Any]:
        """Ученик × тема: статус каждой темы (знает / изучает / подтянуть) по предметам учителя."""
        mine = set(await self._my_subjects(data, uid))
        per_student, count, last = [], {}, {}
        for student in data["students"]:
            profile = await self._load_profile(student)
            entries = {}
            for e in load_json(profile.get("topic_progress"), {}).values():
                if e.get("grade") != data["grade"] or e.get("subject_id") not in mine or not e.get("topic"):
                    continue
                key = (e["subject_id"], e["topic"])
                entries[key] = e.get("status")
                count[key] = count.get(key, 0) + 1
                last[key] = max(last.get(key, ""), e.get("last_seen") or "")
            per_student.append((profile.get("name") or await self.redis.get_state(f"name_{student}") or "Ученик", entries))
        keys = sorted(count, key=lambda k: (-count[k], last.get(k, "")), reverse=False)[:MAP_TOPICS]
        keys.sort(key=lambda k: last.get(k, ""), reverse=True)
        return {
            "topics": [{"subject_id": sid, "subject": subject_title(data["grade"], sid), "topic": t} for sid, t in keys],
            "rows": sorted([{"name": name, "cells": [entries.get(k) for k in keys]} for name, entries in per_student],
                           key=lambda r: r["name"]),
        }

    async def show_class_map(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        m = await self.class_map(data, message.user_id)
        lines = [f"🗺 {data['name']} — карта тем", "🟢 знает · 🟠 изучает · 🔴 стоит подтянуть", ""]
        for i, t in enumerate(m["topics"]):
            statuses = [(r["name"], r["cells"][i]) for r in m["rows"]]
            green = sum(1 for _, s in statuses if s in ("known", "mastered"))
            orange = sum(1 for _, s in statuses if s == "learning")
            red = [n for n, s in statuses if s == "weak"]
            lines.append(f"«{t['topic']}» ({t['subject']}): 🟢 {green} · 🟠 {orange} · 🔴 {len(red)}"
                         + (f" — {', '.join(red[:6])}" + ("…" if len(red) > 6 else "") if red else ""))
        if not m["topics"]:
            lines.append("Пока пусто: карта заполнится, когда ученики пройдут тесты и задания.")
        else:
            lines.append("\nПодробная таблица «ученик × тема» — в приложении или в «📥 Excel».")
        await self._quiet(message, "\n".join(lines), keyboard=_kb([[_back(f"t_cls_{code}")]]))

    # ---------- ⭐ звёзды недели и грамоты ----------

    async def class_stars(self, data: Dict[str, Any]) -> Dict[str, Any]:
        stats = []
        for student in data["students"]:
            s = await self._student_week(student)
            stats.append({"id": student, "name": s["name"], "days": s["days"], "solved": s["solved"],
                          "streak": s["streak"], "accuracy": round(100 * s["first"] / s["solved"]) if s["solved"] else None})
        active = [s for s in stats if s["solved"]]
        top = sorted(active, key=lambda s: (-s["days"], -s["solved"], -s["streak"]))[:3]
        streak = max(stats, key=lambda s: s["streak"], default=None)
        accurate = max([s for s in stats if s["solved"] >= 10], key=lambda s: s["accuracy"] or 0, default=None)
        return {"top": top, "streak": streak if streak and streak["streak"] > 1 else None, "accurate": accurate}

    def _stars_text(self, data: Dict[str, Any], st: Dict[str, Any]) -> str:
        lines = [f"⭐ Звёзды недели — {data['name']}", "Не за оценки, а за старание!", ""]
        for medal, s in zip(("🥇", "🥈", "🥉"), st["top"]):
            lines.append(f"{medal} {s['name']} — занимался(ась) {s['days']} дн. из 7, решено {s['solved']}")
        if st["streak"]:
            lines.append(f"🔥 Самая длинная серия: {st['streak']['name']} — {st['streak']['streak']} дн. подряд")
        if st["accurate"]:
            lines.append(f"🎯 Точнее всех: {st['accurate']['name']} — {st['accurate']['accuracy']}% с первой попытки")
        if not st["top"]:
            lines.append("На этой неделе пока никто не занимался.")
        return "\n".join(lines)

    async def show_stars(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        st = await self.class_stars(data)
        rows = [[_btn("📢 Поздравить класс", f"tx_stsend_{code}")]] if st["top"] else []
        rows += _fit_rows([_btn(f"🏅 {s['name'][:9]}", f"tx_cert_{code}_{s['id']}") for s in st["top"]], max_per_row=2)
        rows.append([_back(f"t_cls_{code}")])
        await self._quiet(message, self._stars_text(data, st) + ("\n\n🏅 — вручить грамоту." if st["top"] else ""),
                          keyboard=_kb(rows))

    async def send_stars(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        text = self._stars_text(data, await self.class_stars(data)) + \
            f"\n\nПоздравляю! — {await self._teacher_name(message.user_id)}"
        n = await self._broadcast(data["students"], text)
        await self.show_class(message, code, note=f"📢 Звёзды недели отправлены ученикам: {n}.")

    async def ask_cert(self, message: NormalizedMessage, code: str, student: int) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data or student not in data["students"]:
            await self.show_teacher_menu(message)
            return
        name = (await self._student_week(student))["name"]
        rows = [[_btn(f"🏅 {r}", f"tx_certr_{code}_{student}_{i}")] for i, r in enumerate(CERT_REASONS)]
        rows.append([_back(f"t_rep_{code}_{student}")])
        await self._quiet(message, f"🏅 Грамота: {name}. За что?", keyboard=_kb(rows))

    async def give_cert(self, uid: int, code: str, student: int, reason: int) -> Optional[str]:
        data = await self._own_tclass(uid, code)
        if not data or student not in data["students"] or not 0 <= reason < len(CERT_REASONS):
            return None
        name = (await self._student_week(student))["name"]
        teacher = await self._teacher_name(uid)
        text = f"🏅 {teacher} вручает тебе грамоту «{CERT_REASONS[reason]}»! Так держать 💪"
        msg = NormalizedMessage(chat_id=None, user_id=student, message_id="", text=None, payload=None)
        sent = await self._send_card(msg, cards.certificate_card, name, CERT_REASONS[reason],
                                     f"от учителя: {teacher} · {data['name']}", await self._local_date(uid),
                                     text=text, to_user=student)
        if not sent:
            await self.api.send_message(user_id=student, text=text)
        return name

    async def cert_button(self, message: NormalizedMessage, code: str, student: int, reason: int) -> None:
        name = await self.give_cert(message.user_id, code, student, reason)
        if not name:
            await self.show_teacher_menu(message)
            return
        await self.show_class(message, code, note=f"🏅 Грамота «{CERT_REASONS[reason]}» отправлена: {name}.")

    # ---------- 📢 объявление ----------

    async def _broadcast(self, users: List[int], text: str) -> int:
        n = 0
        for user in dict.fromkeys(users):
            try:
                await self.api.send_message(user_id=user, text=text)
                n += 1
            except Exception as e:
                logger.warning("Не удалось отправить %s: %s", user, e)
        return n

    async def ask_announce(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        await self.redis.set_state(f"awaiting_tann_{message.user_id}", code, ex=3600)
        await self._quiet(message, f"📢 Объявление для класса {data['name']}. Напишите текст сообщением — "
                                   "например: «Завтра контрольная по дробям, повторите правила!»",
                          keyboard=_kb([[_back(f"t_cls_{code}")]]))

    async def announce_text(self, message: NormalizedMessage, text: str) -> None:
        uid = message.user_id
        code = await self.redis.get_state(f"awaiting_tann_{uid}")
        await self.redis.delete_state(f"awaiting_tann_{uid}")
        text = (text or "").strip()[:1500]
        if not code or not text:
            await self.show_teacher_menu(message)
            return
        await self.redis.set_state(f"tann_{uid}", json.dumps([code, text], ensure_ascii=False), ex=3600)
        await self._quiet(message, f"📢 Проверьте объявление:\n\n{text}\n\nКому отправить?",
                          keyboard=_kb([[_btn("👥 Ученикам", f"tx_annsend_{code}_s")],
                                        [_btn("👥 + 👨‍👩‍👧 Ещё и родителям", f"tx_annsend_{code}_p")],
                                        [_back(f"t_cls_{code}")]]))

    async def announce(self, uid: int, code: str, text: str, parents: bool) -> Optional[Dict[str, int]]:
        data = await self._own_tclass(uid, code)
        text = (text or "").strip()[:1500]
        if not data or not text:
            return None
        head = f"📢 Объявление от учителя — {await self._teacher_name(uid)} ({data['name']}):\n\n"
        n_students = await self._broadcast(data["students"], head + text)
        n_parents = 0
        if parents:
            parent_ids = [p["user_id"] for s in data["students"] for p in await self._parents_of(s)]
            n_parents = await self._broadcast([p for p in parent_ids if p not in data["students"]], head + text)
        return {"students": n_students, "parents": n_parents}

    async def announce_button(self, message: NormalizedMessage, code: str, parents: bool) -> None:
        pending = load_json(await self.redis.get_state(f"tann_{message.user_id}"), None)
        await self.redis.delete_state(f"tann_{message.user_id}")
        if not pending or pending[0] != code:
            await self.show_class(message, code)
            return
        res = await self.announce(message.user_id, code, pending[1], parents)
        if res is None:
            await self.show_teacher_menu(message)
            return
        note = f"📢 Объявление отправлено: ученикам — {res['students']}" + \
            (f", родителям — {res['parents']}" if parents else "") + "."
        await self.show_class(message, code, note=note)

    # ---------- 📥 выгрузка в Excel ----------

    async def class_xlsx(self, data: Dict[str, Any], uid: int) -> bytes:
        mine = self._mine(data, uid)
        students = []
        for s in data["students"]:
            students.append((s, await self._student_week(s)))
        students.sort(key=lambda x: x[1]["name"])
        head = ["Ученик"] + [f"{(a.get('created') or '')[8:10]}.{(a.get('created') or '')[5:7]} "
                             f"{subject_title(data['grade'], a['subject_id'])}: {a['topic']}" for a in mine] + ["Средний балл"]
        marks_rows = [head]
        for sid, info in students:
            marks = [a["results"].get(str(sid), {}).get("mark") for a in mine]
            got = [m for m in marks if m]
            marks_rows.append([info["name"]] + marks + [round(sum(got) / len(got), 2) if got else None])
        if mine:
            marks_rows.append(["Сделали"] + [len(a["results"]) for a in mine] + [None])
        week_rows = [["Ученик", "Дней занятий (из 7)", "Решено заданий", "С первой попытки, %", "Серия, дней",
                      "Тем подтянуть", "Оценки за уроки (7 дней)"]]
        for sid, info in students:
            week_rows.append([info["name"], info["days"], info["solved"],
                              round(100 * info["first"] / info["solved"]) if info["solved"] else None,
                              info["streak"], len(info["weak"]), ", ".join(str(m["mark"]) for m in info["marks"])])
        m = await self.class_map(data, uid)
        map_rows = [["Ученик"] + [f"{t['subject']}: {t['topic']}" for t in m["topics"]]]
        for r in m["rows"]:
            map_rows.append([r["name"]] + [STATUS_WORDS.get(c, "") if c else "" for c in r["cells"]])
        return xlsx.build([("Задания", marks_rows), ("Неделя", week_rows), ("Темы", map_rows)])

    async def export_class(self, message: NormalizedMessage, code: str, to_chat: bool = False) -> bool:
        """Таблица класса файлом в чат учителю."""
        uid = message.user_id
        data = await self._own_tclass(uid, code)
        if not data:
            await self.show_teacher_menu(message)
            return False
        body = await self.class_xlsx(data, uid)
        filename = f"Маячок_{data['name']}_{(await self._local_date(uid)).strftime('%d.%m.%Y')}.xlsx".replace(" ", "_")
        sender = getattr(self.api, "real", self.api) if to_chat else self.api
        ok = await sender.send_file(uid, body, filename,
                                    text=f"📥 {data['name']}: оценки за задания, неделя учеников и карта тем.")
        if not to_chat:
            await self.show_class(message, code, note="📥 Таблица Excel — в сообщении выше." if ok else
                                  "Не получилось отправить файл — попробуйте ещё раз.")
        return ok
