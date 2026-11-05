"""API мини-приложения: плюшки родителя (похвала, цель недели, «Как помочь дома», сигналы, график недель)
и учителя (тема урока одной кнопкой, срок сдачи, карта тем, звёзды недели, грамоты, объявление, Excel).

Вся логика — в обработчиках бота (FamilyMixin, TeacherExtrasMixin), здесь только проверки и ответы приложению.
"""
from typing import Any, Dict

from app.handlers.tutor.family import GOAL_CHOICES, PRAISES
from app.handlers.tutor.teacher_extra import CERT_REASONS, DUE_CHOICES


class ExtrasAPIMixin:

    # ---------- родитель ----------

    async def _child_or_403(self, h, uid: int, child: Any) -> int:
        from app.webapp.api import ApiError
        if not isinstance(child, int) or not await h._is_parent_of(uid, child):
            raise ApiError(403, "Этот ученик не подключён к вашему аккаунту", "not_parent")
        return child

    async def family_info(self, h, parent_uid: int, child: int) -> Dict[str, Any]:
        """Для экрана отчёта в приложении: цель, недели, слабые темы, можно ли сегодня похвалить."""
        today = (await h._child_today(child)).isoformat()
        return {
            "goal": await h.goal_state(child),
            "goal_choices": list(GOAL_CHOICES),
            "weeks": await h.child_weeks(child, 4),
            "help_topics": await h.child_weak_topics(child),
            "praises": PRAISES,
            "praised_today": await h.redis.get_state(f"praise_{parent_uid}_{child}") == today,
            "alerts": await h.alerts_on(parent_uid),
        }

    async def parent_praise(self, uid: int, child: Any, phrase: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            child = await self._child_or_403(h, uid, child)
            note = await h.praise_child(self._quiet(h, uid), child, phrase if isinstance(phrase, int) else 0)
            return {"note": note, "family": await self.family_info(h, uid, child)}

    async def parent_goal(self, uid: int, child: Any, target: Any, prize: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            child = await self._child_or_403(h, uid, child)
            if not target:
                await h.redis.delete_state(f"goal_{child}")
            elif not await h.set_goal(uid, child, target if isinstance(target, int) else 0, str(prize or "")):
                raise ApiError(400, "Выберите число заданий")
            return {"family": await self.family_info(h, uid, child)}

    async def parent_help(self, uid: int, child: Any, index: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            child = await self._child_or_403(h, uid, child)
            weak = await h.child_weak_topics(child)
            if not isinstance(index, int) or not 0 <= index < len(weak):
                raise ApiError(404, "Тема не найдена")
            w = weak[index]
            text = await h.help_advice(uid, child, w["subject_id"], w["topic"])
            if text is None:
                raise ApiError(429, "На сегодня советы закончились — загляните завтра 🙂", "limit")
            if not text:
                raise ApiError(503, "Не получилось составить совет — попробуйте ещё раз")
            return {"subject": w["subject"], "topic": w["topic"], "text": text}

    async def parent_alerts(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            return {"alerts": await h.toggle_alerts(uid)}

    # ---------- учитель ----------

    async def _tclass_or_404(self, h, uid: int, code: Any):
        from app.webapp.api import ApiError
        data = await h._own_tclass(uid, str(code or "").upper())
        if not data:
            raise ApiError(404, "Класс не найден", "no_class")
        return data

    async def class_extras(self, h, uid: int, data: Dict[str, Any]) -> Dict[str, Any]:
        stars = await h.class_stars(data)
        return {"today": await h.today_topics(data, uid), "map": await h.class_map(data, uid),
                "stars": stars, "cert_reasons": CERT_REASONS}

    async def teacher_quick(self, uid: int, code: Any, subject_id: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._tclass_or_404(h, uid, code)
            topic = next((t["topic"] for t in await h.today_topics(data, uid) if t["subject_id"] == subject_id), None)
            if not topic:
                raise ApiError(400, "Сегодня по программе темы нет")
            await h.create_assignment(self._quiet(h, uid), data["code"], subject_id, topic)
            fresh = await h._tclass(data["code"])
            mine = h._mine(fresh, uid)
            return {"aid": mine[-1]["id"] if mine else None, "topic": topic}

    async def teacher_due(self, uid: int, code: Any, aid: Any, choice: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            if choice not in DUE_CHOICES and choice not in ("", None):
                raise ApiError(400, "Неизвестный срок")
            data = await self._tclass_or_404(h, uid, code)
            if not any(a["id"] == str(aid) for a in h._mine(data, uid)):
                raise ApiError(404, "Задание не найдено")
            return {"due": await h.set_due(uid, data["code"], str(aid), choice or "")}

    async def teacher_cert(self, uid: int, code: Any, student: Any, reason: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._tclass_or_404(h, uid, code)
            name = await h.give_cert(uid, data["code"], student if isinstance(student, int) else -1,
                                     reason if isinstance(reason, int) else -1)
            if not name:
                raise ApiError(400, "Не получилось вручить грамоту")
            return {"note": f"🏅 Грамота «{CERT_REASONS[reason]}» отправлена: {name}"}

    async def teacher_announce(self, uid: int, code: Any, text: Any, parents: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._tclass_or_404(h, uid, code)
            res = await h.announce(uid, data["code"], str(text or ""), bool(parents))
            if res is None:
                raise ApiError(400, "Напишите текст объявления")
            return res

    async def teacher_stars_send(self, uid: int, code: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            data = await self._tclass_or_404(h, uid, code)
            text = h._stars_text(data, await h.class_stars(data)) + f"\n\nПоздравляю! — {await h._teacher_name(uid)}"
            return {"sent": await h._broadcast(data["students"], text)}

    async def teacher_export(self, uid: int, code: Any) -> Dict[str, Any]:
        """Excel файлом в чат с Маячком (в MAX скачивать файлы из приложения неудобно)."""
        from app.webapp.api import ApiError
        async with self._handler() as h:
            data = await self._tclass_or_404(h, uid, code)
            message = self._quiet(h, uid)
            if not await h.export_class(message, data["code"], to_chat=True):
                raise ApiError(503, "Не получилось отправить файл — попробуйте ещё раз")
            return {"note": "📥 Таблица Excel отправлена вам в чат с Маячком"}
