"""Веб-сервер мини-приложения: отдаёт страницу (static/) и API (/api/...).

Запускается вместе с ботом (app/main.py) на порту WEBAPP_PORT (по умолчанию 8080).
Чтобы MAX мог открыть приложение, этот порт выводится в интернет по https — например, туннелем Tuna.

Каждый запрос к /api/ несёт заголовок X-Init-Data — данные запуска, подписанные MAX.
Сервер проверяет подпись токеном бота и только так узнаёт, кто открыл приложение.
"""
import asyncio
import io
import json
import logging
import os
from typing import Any, Dict, Optional

from aiohttp import web

from app.webapp.api import ApiError, MiniAppAPI
from app.webapp.auth import validate_init_data

logger = logging.getLogger(__name__)
STATIC = os.path.join(os.path.dirname(__file__), "static")
MOODS = ("happy", "cheer", "wink")
from app.services.lighthouse import MILESTONES as LH_MILESTONES  # noqa: E402  (картинки маяка: /img/lh0..lh9.png)


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False))


def _png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def create_app(api: MiniAppAPI, bot_token: str) -> web.Application:
    images: Dict[str, bytes] = {}

    def user_id(request: web.Request) -> int:
        data = validate_init_data(request.headers.get("X-Init-Data", ""), bot_token)
        if not data:
            raise ApiError(401, "Открой приложение из MAX", "auth")
        return data["user"]["id"]

    def child_of(value: Any) -> Optional[int]:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            raise ApiError(400, "Неверный номер ученика")

    async def body(request: web.Request) -> Dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            raise ApiError(400, "Неверный запрос")
        if not isinstance(data, dict):
            raise ApiError(400, "Неверный запрос")
        return data

    @web.middleware
    async def errors(request: web.Request, handler):
        try:
            response = await handler(request)
        except ApiError as e:
            response = _json({"error": e.message, "code": e.code}, status=e.status)
        except web.HTTPException:
            raise
        except Exception:
            logger.exception("Мини-приложение: ошибка в %s", request.path)
            response = _json({"error": "Что-то пошло не так. Попробуй ещё раз."}, status=500)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.path.startswith("/api/"):
            # Страница может лежать на другом сайте (например, GitHub Pages) — разрешаем ей обращаться к API.
            # Это безопасно: куки не используются, а пользователя подтверждает подпись MAX в X-Init-Data.
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "X-Init-Data, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        if request.path == "/" or request.path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def preflight(request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def index(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    async def picture(request: web.Request) -> web.Response:
        """Картинки Маячка рисуются кодом при первом запросе и дальше берутся из памяти."""
        name = request.match_info["name"]
        if name not in images:
            from app.services.mascot import avatar, mascot
            if name == "avatar":
                image = await asyncio.to_thread(avatar, 256)
            elif name.startswith("lh") and name[2:].isdigit() and int(name[2:]) <= len(LH_MILESTONES):
                from app.services.cards import lighthouse_scene
                body_ = await asyncio.to_thread(lighthouse_scene, int(name[2:]), LH_MILESTONES)
                images[name] = body_
                return web.Response(body=body_, content_type="image/jpeg",
                                    headers={"Cache-Control": "public, max-age=86400"})
            elif name in MOODS:
                image = await asyncio.to_thread(mascot, 360, name)
            else:
                raise web.HTTPNotFound()
            images[name] = _png(image)
        return web.Response(body=images[name], content_type="image/jpeg" if name.startswith("lh") else "image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    async def state(request: web.Request) -> web.Response:
        return _json(await api.state(user_id(request), child_of(request.query.get("child"))))

    async def schedule_get(request: web.Request) -> web.Response:
        return _json(await api.schedule(user_id(request), child_of(request.query.get("child"))))

    async def schedule_post(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.save_schedule(uid, data.get("schedule"), child_of(data.get("child"))))

    async def train_start(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.train_start(uid, str(data.get("subject_id") or ""), data.get("topic")))

    async def train_answer(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        if not isinstance(data.get("index"), int) or not isinstance(data.get("choice"), str):
            raise ApiError(400, "Неверный запрос")
        return _json(await api.train_answer(uid, data["index"], data["choice"]))

    async def chat(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.chat_action(uid, str(data.get("action") or ""), data))

    async def zap_start(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.zap_start(uid, str(data.get("mode") or "")))

    async def zap_finish(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.zap_finish(uid, data.get("game"), data.get("answers")))

    async def chest_open(request: web.Request) -> web.Response:
        return _json(await api.chest_open(user_id(request)))

    async def album(request: web.Request) -> web.Response:
        return _json(await api.album(user_id(request)))

    async def cards_get(request: web.Request) -> web.Response:
        return _json(await api.cards(user_id(request)))

    async def cards_topics(request: web.Request) -> web.Response:
        return _json(await api.cards_topics(user_id(request), str(request.query.get("subject_id") or "")))

    async def cards_new(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.cards_new(uid, str(data.get("subject_id") or ""), data.get("topic")))

    async def cards_grade(request: web.Request) -> web.Response:
        uid = user_id(request)
        data = await body(request)
        return _json(await api.cards_grade(uid, data.get("id"), data.get("remembered")))

    # ---------- знакомство и настройки ----------

    def post(method, *fields):
        """POST-обработчик: user_id из подписи MAX + поля из JSON-тела → api.method(uid, *поля)."""
        async def handler(request: web.Request) -> web.Response:
            uid = user_id(request)
            data = await body(request) if request.can_read_body else {}
            return _json(await method(uid, *[data.get(f) for f in fields]))
        return handler

    async def onboarding(request: web.Request) -> web.Response:
        return _json(await api.onboarding_info())

    async def settings_get(request: web.Request) -> web.Response:
        return _json(await api.settings(user_id(request)))

    async def settings_post(request: web.Request) -> web.Response:
        uid = user_id(request)
        return _json(await api.save_settings(uid, await body(request)))

    async def privacy(request: web.Request) -> web.Response:
        return _json(await api.privacy())

    # ---------- кабинет учителя ----------

    def query_int(request: web.Request, name: str) -> Optional[int]:
        value = request.query.get(name, "")
        return int(value) if value.lstrip("-").isdigit() else None

    async def teacher_home(request: web.Request) -> web.Response:
        return _json(await api.teacher_home(user_id(request)))

    async def teacher_class(request: web.Request) -> web.Response:
        return _json(await api.teacher_class(user_id(request), request.query.get("code")))

    async def teacher_student(request: web.Request) -> web.Response:
        return _json(await api.teacher_student(user_id(request), request.query.get("code"), query_int(request, "student")))

    async def teacher_assignment(request: web.Request) -> web.Response:
        return _json(await api.teacher_assignment(user_id(request), request.query.get("code"), request.query.get("aid")))

    async def teacher_photo(request: web.Request) -> web.Response:
        image = await api.teacher_photo(user_id(request), request.query.get("code"), request.query.get("aid"),
                                        request.query.get("student"))
        if not image:
            raise ApiError(404, "Фото не найдено — попросите ученика прислать ещё раз", "no_photo")
        return web.Response(body=image, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})

    async def health(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(middlewares=[errors], client_max_size=9 * 1024 * 1024)   # фото из приложения — до ~6 МБ
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/img/{name}.png", picture)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/schedule", schedule_get)
    app.router.add_post("/api/schedule", schedule_post)
    app.router.add_post("/api/train/start", train_start)
    app.router.add_post("/api/train/answer", train_answer)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/zap/start", zap_start)
    app.router.add_post("/api/zap/finish", zap_finish)
    app.router.add_post("/api/chest/open", chest_open)
    app.router.add_get("/api/album", album)
    app.router.add_get("/api/cards", cards_get)
    app.router.add_get("/api/cards/topics", cards_topics)
    app.router.add_post("/api/cards/new", cards_new)
    app.router.add_post("/api/cards/grade", cards_grade)
    app.router.add_get("/api/onboarding", onboarding)
    app.router.add_post("/api/onboard/student", post(api.onboard_student, "name", "grade", "tz_offset", "consent"))
    app.router.add_post("/api/onboard/parent", post(api.link_parent, "code"))
    app.router.add_post("/api/onboard/teacher", post(api.teacher_request))
    app.router.add_get("/api/settings", settings_get)
    app.router.add_post("/api/settings", settings_post)
    app.router.add_post("/api/parent/code", post(api.parent_code))
    app.router.add_post("/api/parent/unlink", post(api.unlink_parent, "parent_id"))
    app.router.add_post("/api/parent/leave", post(api.leave_child, "child"))
    app.router.add_post("/api/class/join", post(api.join_class, "code"))
    app.router.add_post("/api/class/leave", post(api.leave_class))
    app.router.add_get("/api/privacy", privacy)
    app.router.add_post("/api/talk", post(api.chat_send, "text", "payload"))
    app.router.add_post("/api/talk/photo", post(api.chat_photo, "image"))
    app.router.add_post("/api/talk/open", post(api.chat_open, "action", "params"))
    app.router.add_post("/api/delete_me", post(api.delete_me))
    app.router.add_get("/api/teacher", teacher_home)
    app.router.add_post("/api/teacher/profile", post(api.teacher_profile, "kind", "subjects", "name"))
    app.router.add_post("/api/teacher/class/new", post(api.teacher_class_new, "grade", "name"))
    app.router.add_post("/api/teacher/class/link", post(api.teacher_class_link, "code"))
    app.router.add_post("/api/teacher/class/remove", post(api.teacher_class_remove, "code"))
    app.router.add_get("/api/teacher/class", teacher_class)
    app.router.add_get("/api/teacher/student", teacher_student)
    app.router.add_post("/api/teacher/assign", post(api.teacher_assign, "code", "subject_id", "topic"))
    app.router.add_get("/api/teacher/assignment", teacher_assignment)
    app.router.add_post("/api/teacher/remind", post(api.teacher_remind, "code", "aid"))
    app.router.add_get("/api/teacher/photo", teacher_photo)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", preflight)
    app.router.add_static("/static/", STATIC)
    return app


async def start_webapp(api: MiniAppAPI, bot_token: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(create_app(api, bot_token), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Мини-приложение запущено: http://localhost:%s", port)
    return runner
