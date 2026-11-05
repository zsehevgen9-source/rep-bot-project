"""Кабинет учителя: классы, ученики, слабые темы класса, задания всему классу и их результаты.

Учитель: /teacher (доступно администраторам, ID из TEACHER_IDS в .env и тем, кого добавил администратор:
/add_teacher <ID>). Создаёт класс — получает код из 6 символов.
Ученик: /join <код> или «⚙️ Настройки → 🏫 Учитель». Учитель видит его занятия, пока ученик в классе;
выйти из класса можно там же.

Хранится в Redis (с appendonly — переживает перезапуск):
    teachers                 — кого администратор сделал учителем
    tclasses_<учитель>       — коды классов учителя
    tprofile_<учитель>       — {kind: primary|subject, subjects: [...], name}: начальных классов или предметник
    tclass_<код>             — класс: {code, owner, teachers: {учитель: [предметы]}, grade, name, students, assignments}
                               Один класс (например, 5А) — общий для всех его учителей: ученики вступают один раз,
                               учителя-предметники подключаются к уже созданному классу по тому же коду.
    my_tclass_<ученик>       — код класса ученика
    asg_now_<ученик>         — задание учителя, которое ученик сейчас решает
    asg_photo_<ученик>       — задание, к которому ждём фото решения (черновик или тетрадь)
"""
import random
import string
from datetime import timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import schedule as schedule_rules
from app.services.programs import programs

CODE_CHARS = "".join(ch for ch in string.ascii_uppercase + string.digits if ch not in "0O1IL")
MAX_ASSIGNMENTS = 100
# Предметы для учителя-предметника (5–11 классы)
PRIMARY_SUBJECT_TEACHERS = {"eng"}   # в 1–4 классах эти предметы ведут отдельные учителя
TEACHER_SUBJECTS = ["math", "geo_math", "rus", "lit", "eng", "hist", "soc", "geo", "bio", "phys", "chem", "inf", "obzr"]
PHOTO_TTL = 3 * DAY       # сколько ждём фото решения после теста
PHOTO_MIN_CHARS = 15      # меньше знаков на фото — похоже, записей с решением нет


class TeacherMixin:
    # ---------- кто учитель ----------

    async def _is_teacher(self, uid: int, explicit: bool = False) -> bool:
        """Учитель: из TEACHER_IDS в .env или добавлен командой /add_teacher. Администратору кабинет тоже открыт,
        но explicit=True — только «настоящие» учителя (их /start сразу ведёт в кабинет)."""
        env = {int(x) for x in str(getattr(settings, "TEACHER_IDS", "") or "").replace(" ", "").split(",")
               if x.strip().lstrip("-").isdigit()}
        if uid in env or uid in load_json(await self.redis.get_state("teachers"), []):
            return True
        return not explicit and uid in settings.admin_ids

    async def add_teacher(self, message: NormalizedMessage, arg: str) -> None:
        if not self._is_admin(message.user_id):
            await self.answer(message, "Эта команда доступна только администратору бота.")
            return
        if not arg.strip().isdigit():
            await self.answer(message, "Напиши ID учителя: /add_teacher 123456789\n"
                                       "Свой ID учитель узнает командой /whoami.")
            return
        teachers = load_json(await self.redis.get_state("teachers"), [])
        tid = int(arg.strip())
        if tid not in teachers:
            teachers.append(tid)
            await self.redis.set_state("teachers", json.dumps(teachers))
        await self.answer(message, f"✅ Готово: {tid} теперь учитель. Пусть напишет боту /teacher.")
        try:
            await self.api.send_message(user_id=tid, text="👩‍🏫 Вам открыт кабинет учителя. Напишите /teacher.")
        except Exception as e:
            logger.warning("Не удалось сообщить учителю %s: %s", tid, e)

    async def del_teacher(self, message: NormalizedMessage, arg: str) -> None:
        """/del_teacher ID — администратор закрывает кабинет (если кабинет открыл не учитель)."""
        if not self._is_admin(message.user_id):
            await self.answer(message, "Эта команда доступна только администратору бота.")
            return
        if not arg.strip().isdigit():
            await self.answer(message, "Напиши ID: /del_teacher 123456789")
            return
        tid = int(arg.strip())
        teachers = [t for t in load_json(await self.redis.get_state("teachers"), []) if t != tid]
        await self.redis.set_state("teachers", json.dumps(teachers))
        await self.answer(message, f"✅ Кабинет учителя для {tid} закрыт.")

    # ---------- профиль учителя ----------

    async def _tprofile(self, uid: int) -> Dict[str, Any]:
        data = load_json(await self.redis.get_state(f"tprofile_{uid}"), {})
        return data if isinstance(data, dict) else {}

    async def _save_tprofile(self, uid: int, profile: Dict[str, Any]) -> None:
        await self.redis.set_state(f"tprofile_{uid}", json.dumps(profile, ensure_ascii=False))

    @staticmethod
    def _teacher_grades(profile: Dict[str, Any]) -> List[int]:
        """Классы, которые может вести учитель: начальных — 1–4, предметник — 5–11, где есть его предметы."""
        if profile.get("kind") == "primary":
            return [1, 2, 3, 4]
        subjects = set(profile.get("subjects") or [])
        # В 1–4 классах у предметника может быть только английский (остальное ведёт учитель начальных классов)
        return [g for g in range(1, 12)
                if (subjects if g >= 5 else subjects & PRIMARY_SUBJECT_TEACHERS) & {sid for _, sid in SUBJECTS[g]}]

    @staticmethod
    def _class_subjects(profile: Dict[str, Any], grade: int) -> List[str]:
        """Предметы, по которым этот учитель даёт задания в классе: у начальных — все предметы класса."""
        all_ids = [sid for _, sid in SUBJECTS.get(grade, [])]
        if profile.get("kind") == "primary":
            # английский в 2–4 классах ведёт отдельный учитель-предметник
            return [sid for sid in all_ids if sid not in PRIMARY_SUBJECT_TEACHERS]
        mine = profile.get("subjects") or []
        return [sid for sid in all_ids if sid in mine and (grade >= 5 or sid in PRIMARY_SUBJECT_TEACHERS)]

    async def ask_teacher_kind(self, message: NormalizedMessage) -> None:
        await self._quiet(message, "👩‍🏫 Добро пожаловать в кабинет учителя!\n\nКакие классы вы ведёте?",
                          keyboard=_kb([[_btn("🧸 Начальные классы", "t_kind_primary")],
                                        [_btn("📐 Учитель-предметник", "t_kind_subject")]]))

    async def set_teacher_kind(self, message: NormalizedMessage, kind: str) -> None:
        uid = message.user_id
        if not await self._is_teacher(uid) or kind not in ("primary", "subject"):
            await self.show_teacher_menu(message)
            return
        profile = await self._tprofile(uid)
        profile["kind"] = kind
        if kind == "primary":
            profile["subjects"] = []
        await self._save_tprofile(uid, profile)
        if kind == "subject":
            await self.ask_teacher_subjects(message)
        else:
            await self.ask_teacher_name(message)

    async def ask_teacher_subjects(self, message: NormalizedMessage, note: str = "") -> None:
        profile = await self._tprofile(message.user_id)
        chosen = profile.get("subjects") or []
        buttons = [_btn(("✅ " if sid in chosen else "▫️ ") + button_title(DEFAULT_TITLES[sid]), f"t_subj_{sid}")
                   for sid in TEACHER_SUBJECTS]
        rows = _fit_rows(buttons, max_per_row=2)
        if chosen:
            rows.append([_btn(f"👍 Готово (выбрано {len(chosen)})", "t_subj_done")])
        await self._quiet(message, (f"{note}\n\n" if note else "") + "📐 Какие предметы вы ведёте? "
                                   "Отметьте один или несколько (например, алгебру и геометрию) и нажмите «Готово».",
                          keyboard=_kb(rows))

    async def toggle_teacher_subject(self, message: NormalizedMessage, sid: str) -> None:
        uid = message.user_id
        profile = await self._tprofile(uid)
        if sid in TEACHER_SUBJECTS:
            chosen = profile.get("subjects") or []
            profile["subjects"] = [s for s in chosen if s != sid] if sid in chosen else chosen + [sid]
            await self._save_tprofile(uid, profile)
        await self.ask_teacher_subjects(message)

    async def teacher_subjects_done(self, message: NormalizedMessage) -> None:
        profile = await self._tprofile(message.user_id)
        if not profile.get("subjects"):
            await self.ask_teacher_subjects(message, note="Отметьте хотя бы один предмет.")
            return
        if profile.get("name"):
            await self.show_teacher_menu(message, note="✅ Предметы сохранены.")
        else:
            await self.ask_teacher_name(message)

    async def ask_teacher_name(self, message: NormalizedMessage) -> None:
        await self.redis.set_state(f"awaiting_tname_{message.user_id}", "true", ex=3600)
        await self._quiet(message, "Как к вам обращаться ученикам? Напишите имя и отчество, например: Мария Ивановна")

    async def set_teacher_name(self, message: NormalizedMessage, name: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_tname_{uid}")
        profile = await self._tprofile(uid)
        profile["name"] = " ".join(name.split())[:40] or "Учитель"
        await self._save_tprofile(uid, profile)
        await self.show_teacher_menu(message, note=f"✅ Готово, {profile['name']}!")

    def _profile_line(self, profile: Dict[str, Any]) -> str:
        if profile.get("kind") == "primary":
            return f"{profile.get('name') or 'Учитель'} · начальные классы"
        titles = ", ".join(DEFAULT_TITLES[s] for s in profile.get("subjects") or [])
        return f"{profile.get('name') or 'Учитель'} · {titles}"

    async def show_teacher_profile(self, message: NormalizedMessage) -> None:
        profile = await self._tprofile(message.user_id)
        rows = [[_btn("🔄 Начальные/предметник", "t_kind")]]
        if profile.get("kind") == "subject":
            rows.append([_btn("📐 Мои предметы", "t_subjects")])
        rows += [[_btn("✏️ Имя для учеников", "t_rename")], [_back("t_menu")]]
        await self._quiet(message, "👤 Профиль учителя\n\n" + self._profile_line(profile), keyboard=_kb(rows))

    # ---------- данные класса ----------

    async def _tclass(self, code: str) -> Optional[Dict[str, Any]]:
        data = load_json(await self.redis.get_state(f"tclass_{code}"), None)
        if not isinstance(data, dict) or data.get("code") != code:
            return None
        if "teachers" not in data:   # класс из первой версии кабинета: один учитель
            data["owner"] = data.get("teacher")
            data["teachers"] = {str(data.get("teacher")): []}
        return data

    async def _save_tclass(self, data: Dict[str, Any]) -> None:
        await self.redis.set_state(f"tclass_{data['code']}", json.dumps(data, ensure_ascii=False))

    async def _own_tclass(self, uid: int, code: str) -> Optional[Dict[str, Any]]:
        """Класс, если этот учитель в нём работает (чужие классы не показываем)."""
        data = await self._tclass(code)
        return data if data and str(uid) in data["teachers"] else None

    @staticmethod
    def _mine(data: Dict[str, Any], uid: int) -> List[Dict[str, Any]]:
        """Задания этого учителя в классе (у каждого учителя-предметника — свои)."""
        return [a for a in data["assignments"] if a.get("teacher", data.get("owner")) == uid]

    async def _teacher_name(self, uid: int) -> str:
        return (await self._tprofile(uid)).get("name") or "учитель"

    async def _student_week(self, uid: int) -> Dict[str, Any]:
        profile = await self._load_profile(uid)
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        week = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        activity = load_json(profile.get("activity"), {})
        solved = sum(activity.get(d, {}).get("solved", 0) for d in week)
        first = sum(activity.get(d, {}).get("first_try", 0) for d in week)
        progress = load_json(profile.get("topic_progress"), {})
        return {
            "name": profile.get("name") or await self.redis.get_state(f"name_{uid}") or "Ученик",
            "solved": solved, "first": first,
            "days": sum(1 for d in week if activity.get(d, {}).get("solved")),
            "streak": profile.get("streak_days") or 0,
            "weak": [e for e in progress.values() if e.get("status") == "weak"],
            "marks": [m for m in load_json(profile.get("lesson_marks"), []) if m.get("date") in week],
        }

    # ---------- кабинет ----------

    async def show_teacher_menu(self, message: NormalizedMessage, note: str = "") -> None:
        uid = message.user_id
        if not await self._is_teacher(uid):
            await self.answer(message, "👩‍🏫 Кабинет учителя — для учителей. Если вы учитель, нажмите кнопку.",
                              keyboard=_kb([[_btn("👩‍🏫 Я учитель", "role_teacher")]]))
            return
        await self._reset_modes(uid)
        profile = await self._tprofile(uid)
        if not profile.get("kind"):
            await self.ask_teacher_kind(message)
            return
        if not profile.get("name"):
            await self.ask_teacher_name(message)
            return
        rows, lines = [], []
        for code in load_json(await self.redis.get_state(f"tclasses_{uid}"), []):
            data = await self._tclass(code)
            if data:
                lines.append(f"• {data['name']} — учеников: {len(data['students'])}, код {code}")
                rows.append([_btn(f"🏫 {data['name']}", f"t_cls_{code}")])
        rows = _fit_rows([b for r in rows for b in r], max_per_row=2)
        rows.append([_btn("➕ Добавить класс", "t_add")])
        rows.append([_btn("👤 Мой профиль", "t_prof")])
        text = (f"{note}\n\n" if note else "") + "👩‍🏫 Кабинет учителя\n" + self._profile_line(profile) + "\n\n" + (
            "\n".join(lines) if lines else "Классов пока нет. Нажмите «➕ Добавить класс»."
            + (" Предметники обычно ведут 3–4 класса — добавьте каждый." if profile.get("kind") == "subject" else ""))
        await self._quiet(message, text, keyboard=_kb(rows))

    async def ask_add_class(self, message: NormalizedMessage) -> None:
        if not await self._is_teacher(message.user_id):
            await self.show_teacher_menu(message)
            return
        await self._quiet(
            message,
            "➕ Добавить класс\n\n"
            "Если класс уже есть в боте (его создал классный руководитель или другой учитель) — введите его код: "
            "ученикам не придётся вступать заново, а вы будете видеть их и давать задания по своему предмету.\n\n"
            "Если класса ещё нет — создайте новый и дайте ученикам код.",
            keyboard=_kb([[_btn("🔑 Подключиться по коду", "t_link")], [_btn("🆕 Создать новый класс", "t_new")],
                          [_back("t_menu")]]))

    async def ask_link_class(self, message: NormalizedMessage) -> None:
        await self.redis.set_state(f"awaiting_tlink_{message.user_id}", "true", ex=3600)
        await self._quiet(message, "🔑 Напишите код класса (6 букв и цифр) — его можно узнать у учителя, который создал класс, "
                                   "или у учеников («⚙️ Настройки → 🏫 Учитель»).", keyboard=_kb([[_back("t_add")]]))

    async def link_class(self, message: NormalizedMessage, code: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_tlink_{uid}")
        code = "".join(ch for ch in code.upper() if ch.isalnum())
        data = await self._tclass(code) if len(code) == 6 else None
        if not await self._is_teacher(uid):
            await self.show_teacher_menu(message)
            return
        if not data:
            await self._quiet(message, "Класса с таким кодом нет 🙈 Проверьте буквы и цифры.",
                              keyboard=_kb([[_btn("🔑 Ввести ещё раз", "t_link")], [_back("t_menu")]]))
            return
        profile = await self._tprofile(uid)
        subjects = self._class_subjects(profile, data["grade"])
        if data["grade"] not in self._teacher_grades(profile) or not subjects:
            await self.show_teacher_menu(message, note=f"Класс «{data['name']}» — {data['grade']} класс: ваших предметов "
                                                       "в нём нет. Проверьте предметы в «👤 Мой профиль».")
            return
        data["teachers"][str(uid)] = subjects
        await self._save_tclass(data)
        classes = load_json(await self.redis.get_state(f"tclasses_{uid}"), [])
        if code not in classes:
            await self.redis.set_state(f"tclasses_{uid}", json.dumps(classes + [code]))
        await self.show_class(message, code, note=f"✅ Вы подключились к классу «{data['name']}».")

    async def ask_new_class(self, message: NormalizedMessage) -> None:
        if not await self._is_teacher(message.user_id):
            await self.show_teacher_menu(message)
            return
        grades = self._teacher_grades(await self._tprofile(message.user_id)) or list(range(1, 12))
        buttons = [_btn(f"{g} класс", f"t_new_{g}") for g in grades]
        await self._quiet(message, "Для какого класса? Задания и программа будут по этому классу.",
                          keyboard=_kb(_in_rows(buttons, 3) + [[_back("t_add")]]))

    async def ask_class_name(self, message: NormalizedMessage, grade: int) -> None:
        if not await self._is_teacher(message.user_id) or grade not in SUBJECTS:
            await self.show_teacher_menu(message)
            return
        await self.redis.set_state(f"awaiting_tclass_{message.user_id}", str(grade), ex=3600)
        await self._quiet(message, f"Как назвать класс? Например: {grade}А",
                          keyboard=_kb([[_btn(f"{grade}А", f"t_name_{grade}_{grade}А"), _btn(f"{grade}Б", f"t_name_{grade}_{grade}Б")],
                                        [_back("t_menu")]]))

    async def create_class(self, message: NormalizedMessage, name: str, grade: Optional[int] = None) -> None:
        """Название класса: текстом (класс ждём в awaiting_tclass) или кнопкой «5А» (класс — в самой кнопке:
        нажатие кнопки сбрасывает режимы ожидания)."""
        uid = message.user_id
        grade = grade or int(await self.redis.get_state(f"awaiting_tclass_{uid}") or 0)
        await self.redis.delete_state(f"awaiting_tclass_{uid}")
        name = " ".join(name.split())[:20]
        if not await self._is_teacher(uid) or grade not in SUBJECTS or not name:
            await self.show_teacher_menu(message)
            return
        for _ in range(20):
            code = "".join(random.choice(CODE_CHARS) for _ in range(6))
            if not await self.redis.get_state(f"tclass_{code}"):
                break
        subjects = self._class_subjects(await self._tprofile(uid), grade)
        data = {"code": code, "owner": uid, "teachers": {str(uid): subjects}, "grade": grade, "name": name,
                "students": [], "assignments": [], "created": topic_progress.now().date().isoformat()}
        await self._save_tclass(data)
        classes = load_json(await self.redis.get_state(f"tclasses_{uid}"), []) + [code]
        await self.redis.set_state(f"tclasses_{uid}", json.dumps(classes))
        await self.show_class(message, code, note=f"✅ Класс «{name}» создан.\n\n" + self._join_hint(code) +
                              "\n\nДругие учителя этого класса подключаются по тому же коду: «➕ Добавить класс → 🔑 Подключиться по коду».")

    @staticmethod
    def _join_hint(code: str) -> str:
        return (f"Код для учеников: {code}\n"
                f"Ученик пишет боту: /join {code}\n"
                "(или «⚙️ Настройки → 🏫 Учитель» и вводит код).")

    async def show_class(self, message: NormalizedMessage, code: str, note: str = "",
                         extra: Optional[List[List[Dict[str, str]]]] = None) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        stats = [await self._student_week(s) for s in data["students"]]
        active = sum(1 for s in stats if s["solved"])
        solved = sum(s["solved"] for s in stats)
        first = sum(s["first"] for s in stats)
        lines = [f"🏫 {data['name']} · {data['grade']} класс · код {code}", "",
                 f"Учеников: {len(stats)}, занимались за неделю: {active}",
                 f"Решено заданий за неделю: {solved}" + (f", с первой попытки: {round(100 * first / solved)}%" if solved else "")]
        names = []
        for t, subj in data["teachers"].items():
            kind = (await self._tprofile(int(t))).get("kind")
            what = "начальные классы" if kind == "primary" else ", ".join(DEFAULT_TITLES[x] for x in subj)
            names.append(f"{await self._teacher_name(int(t))}" + (f" ({what})" if what else ""))
        lines.append("Учителя: " + "; ".join(names))
        mine = self._mine(data, message.user_id)
        if mine:
            last = mine[-1]
            lines.append(f"Последнее задание: {subject_title(data['grade'], last['subject_id'])}, «{last['topic']}» — "
                         f"выполнили {len(last['results'])} из {len(stats)}")
        quick = await self.quick_lines(data, message.user_id)
        if quick:
            lines += [""] + quick
        if not stats:
            lines += ["", self._join_hint(code)]
        rows = list(extra or []) + await self.quick_rows(data, message.user_id) + [
            [_btn("👥 Ученики", f"t_stu_{code}"), _btn("🎯 Слабые", f"t_weak_{code}")],
            [_btn("📝 Задать", f"t_asg_{code}"), _btn("📊 Итоги", f"t_res_{code}")],
            [_btn("🗺 Карта тем", f"tx_map_{code}"), _btn("⭐ Звёзды", f"tx_star_{code}")],
            [_btn("📢 Объявить", f"tx_ann_{code}"), _btn("📥 Excel", f"tx_xls_{code}")],
            [_btn("🔑 Код", f"t_code_{code}"),
             _btn("🗑 Удалить", f"t_del_{code}") if data.get("owner") == message.user_id else _btn("🚪 Выйти", f"t_unlink_{code}")],
            [_back("t_menu")],
        ]
        await self._quiet(message, (f"{note}\n\n" if note else "") + "\n".join(lines), keyboard=_kb(rows))

    async def show_class_code(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        await self._quiet(message, f"🔑 {data['name']}\n\n" + self._join_hint(code) +
                          "\n\nУченик увидит, что учитель будет видеть его занятия, и сможет выйти из класса сам.",
                          keyboard=_kb([[_back(f"t_cls_{code}")]]))

    async def show_students(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        lines, buttons = [f"👥 {data['name']} — ученики за 7 дней", ""], []
        rows = []
        for sid_ in data["students"]:
            s = await self._student_week(sid_)
            marks = [m["mark"] for m in s["marks"]]
            avg = f", ср. оценка {sum(marks) / len(marks):.1f}" if marks else ""
            lines.append(f"• {s['name']}: дней {s['days']}/7, решено {s['solved']}{avg}"
                         + (f", слабых тем {len(s['weak'])}" if s["weak"] else ""))
            label = s["name"] if _vis_len(s["name"]) <= 10 else s["name"][:9] + "."
            buttons.append(_btn(label, f"t_rep_{code}_{sid_}"))
        if not data["students"]:
            lines.append("Пока никого. " + self._join_hint(code))
        rows = _fit_rows(buttons, max_per_row=2)
        rows.append([_back(f"t_cls_{code}")])
        text = "\n".join(lines) + ("\n\nНажмите на ученика — подробный отчёт." if buttons else "")
        await self._quiet(message, text, keyboard=_kb(rows))

    async def show_student_report(self, message: NormalizedMessage, code: str, student: int) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data or student not in data["students"]:
            await self.show_teacher_menu(message)
            return
        text = await self.build_parent_report(student)
        await self._quiet(message, text, keyboard=_kb([[_btn("🏅 Грамота", f"tx_cert_{code}_{student}")],
                                                       [_back(f"t_stu_{code}")]]))

    async def show_class_weak(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        my_subjects = set(data["teachers"].get(str(message.user_id)) or
                          self._class_subjects(await self._tprofile(message.user_id), data["grade"]))
        counts: Dict[tuple, int] = {}
        for sid_ in data["students"]:
            for e in (await self._student_week(sid_))["weak"]:
                if e.get("grade") == data["grade"] and e.get("subject_id") in my_subjects:
                    key = (e["subject_id"], e["topic"])
                    counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        lines = [f"🎯 {data['name']} — темы, которые чаще всего стоит подтянуть", ""]
        lines += [f"• {subject_title(data['grade'], sid)}: «{topic}» — у {n} уч." for (sid, topic), n in top]
        if not top:
            lines.append("Слабых тем пока нет — или ученики ещё не проходили тесты.")
        rows = [[_btn(f"📝 Задание: тема {i + 1}", f"t_asgw_{code}_{i}")] for i in range(min(3, len(top)))]
        await self.redis.set_state(f"t_weak_{message.user_id}", json.dumps([list(k) for k, _ in top], ensure_ascii=False),
                                   ex=3600)
        rows.append([_back(f"t_cls_{code}")])
        if top:
            lines.append("\nМожно сразу дать классу задание по самой частой теме.")
        await self._quiet(message, "\n".join(lines), keyboard=_kb(rows))

    # ---------- задание классу ----------

    async def ask_assignment_subject(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        mine = data["teachers"].get(str(message.user_id)) or \
            self._class_subjects(await self._tprofile(message.user_id), data["grade"])
        if len(mine) == 1:   # предметник с одним предметом — сразу к теме
            await self.ask_assignment_topic(message, code, mine[0])
            return
        buttons = [_btn(button_title(name), f"t_asgs_{code}_{sid}") for name, sid in SUBJECTS[data["grade"]]
                   if sid in mine]
        await self._quiet(message, f"📝 Задание классу {data['name']}. Выберите предмет:",
                          keyboard=_kb(_fit_rows(buttons, max_per_row=2) + [[_back(f"t_cls_{code}")]]))

    async def ask_assignment_topic(self, message: NormalizedMessage, code: str, sid: str) -> None:
        uid = message.user_id
        data = await self._own_tclass(uid, code)
        mine = (data or {}).get("teachers", {}).get(str(uid)) or \
            (self._class_subjects(await self._tprofile(uid), data["grade"]) if data else [])
        if not data or sid not in [s for _, s in SUBJECTS[data["grade"]]] or sid not in mine:
            await self.show_teacher_menu(message)
            return
        topics = programs.current_topics(data["grade"], sid, await self._local_date(uid))[:4]
        await self.redis.set_state(f"awaiting_tasg_{uid}", json.dumps([code, sid, topics], ensure_ascii=False), ex=3600)
        # копия для кнопок с темами: нажатие кнопки сбрасывает режимы ожидания (awaiting_*)
        await self.redis.set_state(f"t_asgopts_{uid}", json.dumps([code, sid, topics], ensure_ascii=False), ex=3600)
        title = subject_title(data["grade"], sid)
        text = f"📝 {title}, {data['name']}. Напишите тему задания"
        rows = []
        if topics:
            text += " или выберите тему по программе:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(topics, 1))
            rows = [[_btn(t if _vis_len(t) <= BUTTON_MAX else f"Тема {i + 1}", f"t_asgt_{i}")] for i, t in enumerate(topics)]
        rows.append([_back(f"t_asg_{code}")])
        await self._quiet(message, text, keyboard=_kb(rows))

    async def assignment_topic_index(self, message: NormalizedMessage, index: int) -> None:
        pending = load_json(await self.redis.get_state(f"t_asgopts_{message.user_id}"), None)
        if not pending or not 0 <= index < len(pending[2]):
            await self.show_teacher_menu(message)
            return
        await self.create_assignment(message, pending[0], pending[1], pending[2][index])

    async def assignment_from_weak(self, message: NormalizedMessage, code: str, index: int) -> None:
        top = load_json(await self.redis.get_state(f"t_weak_{message.user_id}"), [])
        if not 0 <= index < len(top):
            await self.show_class_weak(message, code)
            return
        await self.create_assignment(message, code, top[index][0], top[index][1])

    async def create_assignment(self, message: NormalizedMessage, code: str, sid: str, topic: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_tasg_{uid}")
        data = await self._own_tclass(uid, code)
        topic = " ".join((topic or "").split())[:100]
        if not data or not topic:
            await self.show_teacher_menu(message)
            return
        aid = f"{len(data['assignments']) + 1}"
        while any(a["id"] == aid for a in data["assignments"]):
            aid = str(int(aid) + 1)
        data["assignments"] = (data["assignments"] + [{
            "id": aid, "subject_id": sid, "topic": topic, "created": topic_progress.now().date().isoformat(),
            "results": {}, "teacher": uid,
        }])[-MAX_ASSIGNMENTS:]
        await self._save_tclass(data)
        title = subject_title(data["grade"], sid)
        sent = 0
        for student in data["students"]:
            try:
                await self.api.send_message(
                    user_id=student,
                    text=f"📝 Задание от учителя — {await self._teacher_name(uid)} ({data['name']}): {title}, тема «{topic}».\n"
                         "Несколько вопросов без подсказок, в конце — оценка. Результат увидит учитель.",
                    keyboard=_kb([[_btn("📝 Начать задание", f"asg_{code}_{aid}")]]),
                )
                sent += 1
            except Exception as e:
                logger.warning("Не удалось отправить задание ученику %s: %s", student, e)
        await self.show_class(message, code, note=f"✅ Задание отправлено ученикам: {sent}. "
                                                  "Результаты — в «📊 Итоги».\n⏰ Поставить срок сдачи?",
                              extra=self.due_rows(code, aid))

    async def start_assignment(self, message: NormalizedMessage, code: str, aid: str) -> None:
        """Ученик нажал «📝 Начать задание»: тест по теме (как задания на оценку после урока)."""
        uid = message.user_id
        data = await self._tclass(code)
        task = next((a for a in (data or {}).get("assignments", []) if a["id"] == aid), None)
        if not data or uid not in data["students"] or not task:
            await self.answer(message, "Это задание уже недоступно 🙂")
            return
        if str(uid) in task["results"]:
            r = task["results"][str(uid)]
            text = f"Ты уже выполнил это задание: {r['right']} из {r['total']}, оценка {r['mark']} 👍"
            if r.get("photo"):
                await self.answer(message, text + "\nФото решения учитель тоже получил ✅")
            else:
                await self.ask_assignment_photo(message, code, aid, prefix=text + "\n\n")
            return
        await self.redis.set_state(f"asg_now_{uid}", json.dumps([code, aid, task["topic"]], ensure_ascii=False), ex=DAY)
        await self.redis.set_state(f"lesson_{uid}", json.dumps(
            {"grade": data["grade"], "subject_id": task["subject_id"], "topic": task["topic"]}, ensure_ascii=False), ex=DAY)
        await self.start_subject_test(message, data["grade"], task["subject_id"], focus=[task["topic"]], mode="lesson")

    async def _assignment_done(self, uid: int, topic: str, right: int, total: int) -> str:
        """Вызывается в конце теста: если это задание учителя — сохраняем результат для учителя."""
        pending = load_json(await self.redis.get_state(f"asg_now_{uid}"), None)
        if not pending or pending[2] != topic:
            return ""
        await self.redis.delete_state(f"asg_now_{uid}")
        code, aid, _ = pending
        data = await self._tclass(code)
        task = next((a for a in (data or {}).get("assignments", []) if a["id"] == aid), None)
        if not data or not task:
            return ""
        task["results"][str(uid)] = {"right": right, "total": total, "mark": schedule_rules.mark_for(right, total),
                                     "date": topic_progress.now().date().isoformat()}
        await self._save_tclass(data)
        await self.redis.set_state(f"asg_photo_{uid}", json.dumps([code, aid]), ex=PHOTO_TTL)
        return ("\n📨 Результат отправлен учителю.\n\n📸 Последний шаг: сфотографируй черновик или тетрадь с решением "
                "и пришли фото сюда — так учитель увидит, что ты решал сам.")

    # ---------- фото решения ----------

    async def ask_assignment_photo(self, message: NormalizedMessage, code: str, aid: str, prefix: str = "") -> None:
        uid = message.user_id
        await self.redis.set_state(f"asg_photo_{uid}", json.dumps([code, aid]), ex=PHOTO_TTL)
        await self.answer(message, prefix + "📸 Пришли фото черновика или тетради с решением этого задания — "
                                            "учитель увидит, что ты решал сам.")

    async def assignment_photo_pending(self, uid: int) -> bool:
        return bool(await self.redis.get_state(f"asg_photo_{uid}"))

    async def assignment_photo(self, message: NormalizedMessage) -> None:
        """Ученик прислал фото решения задания учителя: проверяем, что на фото есть записи, и сохраняем для учителя."""
        uid = message.user_id
        pending = load_json(await self.redis.get_state(f"asg_photo_{uid}"), None)
        data = await self._tclass(pending[0]) if pending else None
        task = next((a for a in (data or {}).get("assignments", []) if a["id"] == pending[1]), None) if data else None
        if not task or str(uid) not in task["results"]:
            await self.redis.delete_state(f"asg_photo_{uid}")
            await self.answer(message, "Это задание уже недоступно 🙂")
            return
        if message.file_kind != "image":
            await self.answer(message, "Нужно именно фото тетради или черновика 📸")
            return
        await self.answer(message, "Смотрю фото… 🔎")
        raw, image = await self.read_upload(message.file_url, message.file_name, message.file_kind)
        tries = int(await self.redis.get_state(f"asg_photo_try_{uid}") or 0) + 1
        checked = len(re.sub(r"\s+", "", raw or "")) >= PHOTO_MIN_CHARS
        if not checked and tries < 2:
            # На фото не видно записей (размыто, темно, не та картинка) — просим переснять, но не бесконечно
            await self.redis.set_state(f"asg_photo_try_{uid}", str(tries), ex=PHOTO_TTL)
            await self.answer(message, "Не вижу на фото записей с решением 🤔 Сфотографируй тетрадь поближе, ровно и при "
                                       "хорошем свете — и пришли ещё раз.")
            return
        token = await self.api.upload_image(image, filename="solution.jpg") if image else None
        task["results"][str(uid)]["photo"] = {"token": token, "url": message.file_url, "checked": checked,
                                              "date": topic_progress.now().date().isoformat()}
        await self._save_tclass(data)
        await self.redis.delete_state(f"asg_photo_{uid}")
        await self.redis.delete_state(f"asg_photo_try_{uid}")
        await self.answer(message, "✅ Фото решения получил — учитель его увидит. Молодец! 💪")

    async def show_assignment_photo(self, message: NormalizedMessage, code: str, aid: str, student: int) -> None:
        data = await self._own_tclass(message.user_id, code)
        task = next((a for a in self._mine(data, message.user_id) if a["id"] == aid), None) if data else None
        result = (task or {}).get("results", {}).get(str(student)) or {}
        photo = result.get("photo")
        if not data or not photo:
            await self.show_teacher_menu(message)
            return
        name = (await self._student_week(student))["name"]
        caption = (f"📸 {name} — задание {aid}, «{task['topic']}»: оценка {result['mark']} "
                   f"({result['right']} из {result['total']})" + ("" if photo.get("checked") else
                                                                    "\n⚠️ Записи на фото не распознались — посмотрите сами."))
        sent = False
        for token in (photo.get("token"), None):
            if token is None and photo.get("url"):
                # Сохранённый токен не подошёл — скачиваем фото заново
                image = await self.api.download_image(photo["url"])
                token = await self.api.upload_image(image, filename="solution.jpg") if image else None
            if not token:
                continue
            try:
                await self.api.send_message(user_id=message.user_id, text=caption,
                                            attachments=[{"type": "image", "payload": {"token": token}}],
                                            keyboard=_kb([[_back(f"t_resd_{code}_{aid}")]]))
                sent = True
                break
            except Exception as e:
                logger.warning("Не удалось показать фото решения: %s", e)
        if not sent:
            await self.answer(message, caption + "\n\nФото не получилось загрузить — попросите ученика прислать ещё раз.",
                              keyboard=_kb([[_back(f"t_resd_{code}_{aid}")]]))

    async def show_results(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data:
            await self.show_teacher_menu(message)
            return
        lines = [f"📊 {data['name']} — задания", ""]
        rows = []
        for task in reversed(self._mine(data, message.user_id)[-8:]):
            res = list(task["results"].values())
            avg = f", ср. оценка {sum(r['mark'] for r in res) / len(res):.1f}" if res else ""
            photos = sum(1 for r in res if r.get("photo"))
            lines.append(f"{task['id']}. {subject_title(data['grade'], task['subject_id'])}, «{task['topic']}» — "
                         f"выполнили {len(res)} из {len(data['students'])}{avg}, с фото решения: {photos}")
            rows.append(_btn(f"Задание {task['id']}", f"t_resd_{code}_{task['id']}"))
        if not self._mine(data, message.user_id):
            lines.append("Заданий пока не было — нажмите «📝 Задание классу».")
        await self._quiet(message, "\n".join(lines),
                          keyboard=_kb(_fit_rows(rows, max_per_row=3) + [[_back(f"t_cls_{code}")]]))

    async def show_result_detail(self, message: NormalizedMessage, code: str, aid: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        task = next((a for a in self._mine(data, message.user_id) if a["id"] == aid), None) if data else None
        if not data or not task:
            await self.show_teacher_menu(message)
            return
        lines = [f"📊 Задание {aid}: {subject_title(data['grade'], task['subject_id'])}, «{task['topic']}»"
                 + (f" · срок до {task['due'][8:10]}.{task['due'][5:7]}" if task.get("due") else ""), ""]
        not_done, no_photo, photo_btns = [], [], []
        for student in data["students"]:
            name = (await self._student_week(student))["name"]
            r = task["results"].get(str(student))
            if not r:
                not_done.append(name)
                continue
            photo = r.get("photo")
            status = ("📸 фото есть" if photo.get("checked") else "📸 фото (записи не распознались)") if photo else "⏳ ждём фото"
            lines.append(f"• {name}: {r['mark']} ({r['right']} из {r['total']}) · {status}")
            if photo:
                label = name if _vis_len(name) <= 9 else name[:8] + "."
                photo_btns.append(_btn(f"📸 {label}", f"t_ph_{code}_{aid}_{student}"))
            else:
                no_photo.append(name)
        if not_done:
            lines += ["", "Ещё не выполнили: " + ", ".join(not_done)]
        if no_photo:
            lines += ["", "Тест сдали, но фото решения не прислали: " + ", ".join(no_photo)]
        if photo_btns:
            lines += ["", "Нажмите на ученика, чтобы посмотреть фото решения."]
        rows = _fit_rows(photo_btns, max_per_row=2)
        if not_done or no_photo:
            rows.append([_btn("🔔 Напомнить", f"t_rem_{code}_{aid}")])
        await self._quiet(message, "\n".join(lines), keyboard=_kb(rows + [[_back(f"t_res_{code}")]]))

    async def remind_assignment(self, message: NormalizedMessage, code: str, aid: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        task = next((a for a in self._mine(data, message.user_id) if a["id"] == aid), None) if data else None
        if not data or not task:
            await self.show_teacher_menu(message)
            return
        n = 0
        for student in data["students"]:
            r = task["results"].get(str(student))
            if r and r.get("photo"):
                continue
            try:
                if not r:
                    await self.api.send_message(
                        user_id=student, text=f"🔔 Напоминание: задание от учителя — «{task['topic']}». Ждём тебя!",
                        keyboard=_kb([[_btn("📝 Начать задание", f"asg_{code}_{aid}")]]))
                else:
                    await self.redis.set_state(f"asg_photo_{student}", json.dumps([code, aid]), ex=PHOTO_TTL)
                    await self.api.send_message(
                        user_id=student, text=f"🔔 Учитель ждёт фото решения задания «{task['topic']}» 📸 "
                                              "Сфотографируй тетрадь или черновик и пришли сюда.")
                n += 1
            except Exception as e:
                logger.warning("Не удалось напомнить ученику %s: %s", student, e)
        await self.answer(message, f"🔔 Напомнил: {n}.")
        await self.show_result_detail(message, code, aid)

    async def unlink_class(self, message: NormalizedMessage, code: str) -> None:
        """Учитель-предметник отключается от чужого класса (класс и ученики остаются)."""
        uid = message.user_id
        data = await self._own_tclass(uid, code)
        if data and data.get("owner") != uid:
            data["teachers"].pop(str(uid), None)
            await self._save_tclass(data)
            classes = [c for c in load_json(await self.redis.get_state(f"tclasses_{uid}"), []) if c != code]
            await self.redis.set_state(f"tclasses_{uid}", json.dumps(classes))
        await self.show_teacher_menu(message, note="🚪 Вы отключились от класса.")

    async def ask_delete_class(self, message: NormalizedMessage, code: str) -> None:
        data = await self._own_tclass(message.user_id, code)
        if not data or data.get("owner") != message.user_id:
            await self.show_teacher_menu(message)
            return
        await self._quiet(message, f"Удалить класс «{data['name']}»? Ученики останутся в боте, но выйдут из класса, "
                                   "результаты заданий удалятся.",
                          keyboard=_kb([[_btn("🗑 Да, удалить", f"t_delyes_{code}")], [_back(f"t_cls_{code}")]]))

    async def delete_class(self, message: NormalizedMessage, code: str) -> None:
        uid = message.user_id
        data = await self._own_tclass(uid, code)
        if data and data.get("owner") == uid:
            for student in data["students"]:
                await self.redis.delete_state(f"my_tclass_{student}")
            for teacher in data["teachers"]:
                classes = [c for c in load_json(await self.redis.get_state(f"tclasses_{teacher}"), []) if c != code]
                await self.redis.set_state(f"tclasses_{teacher}", json.dumps(classes))
            await self.redis.delete_state(f"tclass_{code}")
        await self.show_teacher_menu(message, note="🗑 Класс удалён.")

    async def forget_classes(self, uid: int) -> None:
        """«Удалить мои данные»: ученик выходит из класса, учитель удаляет свои классы."""
        code = await self.redis.get_state(f"my_tclass_{uid}")
        if code:
            data = await self._tclass(code)
            if data:   # результаты и фото решений ученика удаляются вместе с его данными
                for task in data.get("assignments", []):
                    task["results"].pop(str(uid), None)
                await self._save_tclass(data)
            await self._leave_tclass(uid, code)
        for code in load_json(await self.redis.get_state(f"tclasses_{uid}"), []):
            data = await self._tclass(code)
            if not data:
                continue
            if data.get("owner") == uid:
                for student in data["students"]:
                    await self.redis.delete_state(f"my_tclass_{student}")
                await self.redis.delete_state(f"tclass_{code}")
            else:
                data["teachers"].pop(str(uid), None)
                await self._save_tclass(data)
        await self.redis.delete_state(f"tclasses_{uid}")
        await self.redis.delete_state(f"tprofile_{uid}")

    # ---------- ученик ----------

    async def show_my_class(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        code = await self.redis.get_state(f"my_tclass_{uid}")
        data = await self._tclass(code) if code else None
        back = [_back(f"settings_{grade}" if grade else "back_to_menu")]
        if data and uid in data["students"]:
            await self._quiet(message, f"🏫 Ты в классе «{data['name']}». Учитель видит твои занятия, оценки за уроки "
                                       "и результаты его заданий.",
                              keyboard=_kb([[_btn("🚪 Выйти из класса", "sc_leave")], back]))
            return
        await self.redis.set_state(f"awaiting_join_{uid}", "true", ex=3600)
        await self._quiet(message, "🏫 Учитель может создать класс в боте и дать код. Напиши код класса "
                                   "(6 букв и цифр) — и учитель сможет давать тебе задания и видеть результаты.",
                          keyboard=_kb([back]))

    async def join_class(self, message: NormalizedMessage, code: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_join_{uid}")
        code = "".join(ch for ch in code.upper() if ch.isalnum())
        data = await self._tclass(code) if len(code) == 6 else None
        if not data:
            await self.answer(message, "Код класса не подошёл 🙈 Проверь буквы и цифры — их даёт учитель.")
            return
        grade = await self._get_grade(uid)
        if not grade:
            await self.answer(message, "Сначала познакомимся: напиши /start и выбери класс, потом введи код ещё раз.")
            return
        if grade != data["grade"]:
            await self.answer(message, f"Этот код — для {data['grade']} класса, а у тебя выбран {grade}. "
                                       "Если ошибся классом — смени его: /class, потом введи код ещё раз.")
            return
        old = await self.redis.get_state(f"my_tclass_{uid}")
        if old and old != code:
            await self._leave_tclass(uid, old)
        if uid not in data["students"]:
            data["students"].append(uid)
            await self._save_tclass(data)
        await self.redis.set_state(f"my_tclass_{uid}", code)
        name = (await self._student_week(uid))["name"]
        for teacher in data["teachers"]:
            try:
                await self.api.send_message(user_id=int(teacher), text=f"🏫 В класс «{data['name']}» вступил(а): {name}.")
            except Exception as e:
                logger.warning("Не удалось сообщить учителю: %s", e)
        await self.show_subjects(message, grade, intro=f"✅ Ты в классе «{data['name']}»! Учитель будет давать задания "
                                                       "и видеть твои результаты. Выйти — «⚙️ Настройки → 🏫 Учитель».")

    async def _leave_tclass(self, uid: int, code: str) -> None:
        data = await self._tclass(code)
        if data and uid in data["students"]:
            data["students"].remove(uid)
            await self._save_tclass(data)
        await self.redis.delete_state(f"my_tclass_{uid}")

    async def leave_class(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        code = await self.redis.get_state(f"my_tclass_{uid}")
        if code:
            data = await self._tclass(code)
            await self._leave_tclass(uid, code)
            if data:
                name = (await self._student_week(uid))["name"]
                for teacher in data["teachers"]:
                    try:
                        await self.api.send_message(user_id=int(teacher), text=f"🏫 {name} вышел(ла) из класса «{data['name']}».")
                    except Exception as e:
                        logger.warning("Не удалось сообщить учителю: %s", e)
        await self.answer(message, "🚪 Ты вышел из класса: учитель больше не видит твои занятия.")
        grade = await self._get_grade(uid)
        if grade:
            await self.show_settings(message, grade)
