"""API мини-приложения: кабинет учителя — профиль, классы, ученики, слабые темы, задания, результаты, фото решений.

Данные те же, что у кабинета в чате (tclass_<код>, tclasses_<учитель>, tprofile_<учитель>), а действия
(создать класс, задание, напоминание…) выполняют те же методы бота через QuietApi: ученикам уведомления
приходят в чат как обычно, а ответы самому учителю показывает приложение.
"""
from typing import Any, Dict, List, Optional

from app.handlers.tutor.common import DEFAULT_TITLES, SUBJECTS, load_json, subject_title
from app.handlers.tutor.teacher import TEACHER_SUBJECTS
from app.services.programs import programs


class TeacherAPIMixin:

    async def _teacher_or_403(self, h, uid: int) -> None:
        from app.webapp.api import ApiError
        if not await h._is_teacher(uid):
            raise ApiError(403, "Кабинет учителя — только для учителей", "not_teacher")

    async def _class_or_404(self, h, uid: int, code: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        data = await h._own_tclass(uid, str(code or "").upper())
        if not data:
            raise ApiError(404, "Класс не найден", "no_class")
        return data

    def _mine_subjects(self, h, data: Dict[str, Any], uid: int, profile: Dict[str, Any]) -> List[str]:
        return data["teachers"].get(str(uid)) or h._class_subjects(profile, data["grade"])

    # ---------- кабинет: профиль и список классов ----------

    async def teacher_home(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            await self._teacher_or_403(h, uid)
            profile = await h._tprofile(uid)
            classes = []
            for code in load_json(await h.redis.get_state(f"tclasses_{uid}"), []):
                data = await h._tclass(code)
                if not data:
                    continue
                mine = h._mine(data, uid)
                classes.append({"code": code, "name": data["name"], "grade": data["grade"],
                                "students": len(data["students"]), "owner": data.get("owner") == uid,
                                "assignments": len(mine)})
            return {
                "profile": {"kind": profile.get("kind"), "subjects": profile.get("subjects") or [],
                            "name": profile.get("name") or "", "line": h._profile_line(profile) if profile.get("kind") else ""},
                "subject_options": [{"id": sid, "title": DEFAULT_TITLES[sid]} for sid in TEACHER_SUBJECTS],
                "grades": h._teacher_grades(profile) if profile.get("kind") else list(range(1, 12)),
                "classes": classes,
            }

    async def teacher_profile(self, uid: int, kind: Any, subjects: Any, name: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            await self._teacher_or_403(h, uid)
            if kind not in ("primary", "subject"):
                raise ApiError(400, "Выберите: начальные классы или предметник")
            subjects = [s for s in (subjects or []) if s in TEACHER_SUBJECTS] if kind == "subject" else []
            if kind == "subject" and not subjects:
                raise ApiError(400, "Отметьте хотя бы один предмет")
            name = " ".join(str(name or "").split())[:40]
            if not name:
                raise ApiError(400, "Напишите, как к вам обращаться")
            profile = await h._tprofile(uid)
            profile.update({"kind": kind, "subjects": subjects, "name": name})
            await h._save_tprofile(uid, profile)
        return await self.teacher_home(uid)

    async def teacher_class_new(self, uid: int, grade: Any, name: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            await self._teacher_or_403(h, uid)
            if not isinstance(grade, int) or grade not in SUBJECTS or not str(name or "").strip():
                raise ApiError(400, "Укажите класс и название")
            before = set(load_json(await h.redis.get_state(f"tclasses_{uid}"), []))
            await h.create_class(self._quiet(h, uid), str(name), grade)
            new = [c for c in load_json(await h.redis.get_state(f"tclasses_{uid}"), []) if c not in before]
            if not new:
                raise ApiError(400, "Не получилось создать класс")
            return {"code": new[0]}

    async def teacher_class_link(self, uid: int, code: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            await self._teacher_or_403(h, uid)
            clean = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
            await h.link_class(self._quiet(h, uid), clean)
            if clean not in load_json(await h.redis.get_state(f"tclasses_{uid}"), []):
                raise ApiError(400, self._last_note(h, "Класса с таким кодом нет"), "code")
            return {"code": clean}

    async def teacher_class_remove(self, uid: int, code: Any) -> Dict[str, Any]:
        """Владелец — удаляет класс, учитель-предметник — отключается от него."""
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            message = self._quiet(h, uid)
            if data.get("owner") == uid:
                await h.delete_class(message, data["code"])
            else:
                await h.unlink_class(message, data["code"])
        return await self.teacher_home(uid)

    # ---------- класс ----------

    async def teacher_class(self, uid: int, code: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            profile = await h._tprofile(uid)
            mine_subjects = self._mine_subjects(h, data, uid, profile)
            students, weak = [], {}
            for sid_ in data["students"]:
                s = await h._student_week(sid_)
                marks = [m["mark"] for m in s["marks"]]
                students.append({"id": sid_, "name": s["name"], "days": s["days"], "solved": s["solved"],
                                 "accuracy": round(100 * s["first"] / s["solved"]) if s["solved"] else None,
                                 "avg_mark": round(sum(marks) / len(marks), 1) if marks else None,
                                 "streak": s["streak"], "weak": len(s["weak"])})
                for e in s["weak"]:
                    if e.get("grade") == data["grade"] and e.get("subject_id") in mine_subjects:
                        key = (e["subject_id"], e["topic"])
                        weak[key] = weak.get(key, 0) + 1
            teachers = []
            for t, subj in data["teachers"].items():
                kind = (await h._tprofile(int(t))).get("kind")
                teachers.append({"name": await h._teacher_name(int(t)),
                                 "what": "начальные классы" if kind == "primary" else ", ".join(DEFAULT_TITLES[x] for x in subj)})
            today = await h._local_date(uid)
            assignments = []
            for task in reversed(h._mine(data, uid)[-20:]):
                res = list(task["results"].values())
                assignments.append({
                    "id": task["id"], "subject": subject_title(data["grade"], task["subject_id"]), "topic": task["topic"],
                    "created": task.get("created"), "done": len(res), "photos": sum(1 for r in res if r.get("photo")),
                    "avg_mark": round(sum(r["mark"] for r in res) / len(res), 1) if res else None,
                })
            return {
                "code": data["code"], "name": data["name"], "grade": data["grade"], "owner": data.get("owner") == uid,
                "teachers": teachers,
                "students": sorted(students, key=lambda s: s["name"]),
                "week": {"active": sum(1 for s in students if s["solved"]), "solved": sum(s["solved"] for s in students)},
                "weak": [{"subject_id": sid, "subject": subject_title(data["grade"], sid), "topic": topic, "count": n}
                         for (sid, topic), n in sorted(weak.items(), key=lambda kv: -kv[1])[:10]],
                "subjects": [{"id": sid, "title": title, "now": programs.current_topics(data["grade"], sid, today)[:4]}
                             for title, sid in SUBJECTS[data["grade"]] if sid in mine_subjects],
                "assignments": assignments,
            }

    async def teacher_student(self, uid: int, code: Any, student: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            if not isinstance(student, int) or student not in data["students"]:
                raise ApiError(404, "Ученика нет в этом классе")
            return {"report": await h.build_parent_report(student)}

    # ---------- задания ----------

    async def teacher_assign(self, uid: int, code: Any, subject_id: Any, topic: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            if subject_id not in self._mine_subjects(h, data, uid, await h._tprofile(uid)):
                raise ApiError(400, "Это не ваш предмет в этом классе")
            topic = " ".join(str(topic or "").split())[:100]
            if not topic:
                raise ApiError(400, "Напишите тему задания")
            await h.create_assignment(self._quiet(h, uid), data["code"], subject_id, topic)
            return {"ok": True, "note": self._last_note(h, "")}

    async def teacher_assignment(self, uid: int, code: Any, aid: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            task = next((a for a in h._mine(data, uid) if a["id"] == str(aid)), None)
            if not task:
                raise ApiError(404, "Задание не найдено")
            rows = []
            for student in data["students"]:
                r = task["results"].get(str(student))
                photo = (r or {}).get("photo")
                rows.append({"id": student, "name": (await h._student_week(student))["name"],
                             "done": bool(r), "mark": (r or {}).get("mark"), "right": (r or {}).get("right"),
                             "total": (r or {}).get("total"), "photo": bool(photo),
                             "photo_checked": bool(photo and photo.get("checked"))})
            return {"id": task["id"], "subject": subject_title(data["grade"], task["subject_id"]),
                    "topic": task["topic"], "created": task.get("created"),
                    "students": sorted(rows, key=lambda r: (not r["done"], r["name"]))}

    async def teacher_remind(self, uid: int, code: Any, aid: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            await h.remind_assignment(self._quiet(h, uid), data["code"], str(aid))
            note = next((n for n in h.api.notes if n.startswith("🔔")), "🔔 Напомнил")
            return {"note": note}

    async def teacher_photo(self, uid: int, code: Any, aid: Any, student: Any) -> Optional[bytes]:
        """Фото решения ученика (картинка скачивается из MAX и отдаётся приложению)."""
        async with self._handler() as h:
            data = await self._class_or_404(h, uid, code)
            task = next((a for a in h._mine(data, uid) if a["id"] == str(aid)), None)
            photo = ((task or {}).get("results", {}).get(str(student)) or {}).get("photo")
            if not photo or not photo.get("url"):
                return None
            return await h.api.download_image(photo["url"])
