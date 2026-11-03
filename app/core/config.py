from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- MAX API ---
    MAX_BOT_TOKEN: SecretStr = Field(description="Токен бота MAX (секрет)")
    MAX_API_URL: str = "https://platform-api.max.ru"
    # False отключает проверку TLS-сертификата ТОЛЬКО для запросов к MAX.
    # Это временный обход: правильное решение — положить сертификаты Минцифры в docker/certs (см. README там).
    MAX_SSL_VERIFY: bool = True

    # --- ИИ (Yandex Cloud) ---
    YANDEX_API_KEY: str = ""
    YANDEX_FOLDER_ID: str = ""
    # Модель: aliceai-llm (Alice AI — самая сильная), yandexgpt-5.1 (Pro 5.1), yandexgpt-5-pro (Pro 5)
    # или полный URI вида gpt://<folder_id>/<модель>. Если модель недоступна, бот сам переходит на запасную.
    YANDEX_GPT_MODEL: str = "aliceai-llm"
    YANDEX_GPT_FALLBACK_MODEL: str = "yandexgpt-5-pro"

    # --- Голос (Yandex SpeechKit) ---
    # Голос Маячка (мужской): alexander / anton / zahar / ermil / kirill + роль good (бодрый тон),
    # denis / vasily (LiveTTS, роль пустая). Послушать все голоса — команда /voices (для администратора).
    TTS_VOICE: str = "alexander"
    TTS_ROLE: str = "good"
    # Скорость: 1.0 — обычная. Для голосов LiveTTS (irina, sofia, vera…) SpeechKit скорость пока не меняет.
    TTS_SPEED: float = 1.0
    # Кому озвучивать сообщения: all — всем сразу, young — 1–4 классам сразу, button — всем по кнопке «🔊 Послушать», off — никому.
    # Ученик может сам включить или выключить голос: «⚙️ Настройки → 🔊 Голос»
    VOICE_MODE: str = "button"
    TTS_CACHE: bool = True   # одинаковые фразы озвучиваются один раз, дальше — из кеша бесплатно

    # --- Задания ---
    # JSON-файл с банком заданий (см. data/README.md)
    TASKS_FILE: str = "data/tasks.json"
    # Разрешить ИИ придумывать задание, если в банке подходящего нет. False — бот честно скажет, что заданий пока нет.
    ALLOW_AI_TASKS: bool = True
    # Сколько «тяжёлых» действий ИИ (новый тест, урок, разбор фото, новое задание) можно ученику в день.
    # Задания из своего банка и проверка ответов не считаются. Администраторам — без лимита. 0 — без лимита.
    AI_DAILY_LIMIT: int = 40
    # Дешёвая модель для простых действий (проверить ответ, разобрать расписание или фото). Пусто — всё на основной
    YANDEX_GPT_LIGHT_MODEL: str = "yandexgpt-lite"
    # Оформление: жирные заголовки в сообщениях и картинки-карточки (прогресс, результат теста, грамоты)
    # Согласие на обработку данных перед знакомством (обязательно для детей); контакт для /privacy
    CONSENT_REQUIRED: bool = True
    PRIVACY_CONTACT: str = ""
    PRETTY_MESSAGES: bool = True
    # Мини-приложение: веб-сервер внутри бота (порт), кнопка «📱 Открыть приложение» в меню (включить после
    # подключения адреса в настройках бота на платформе MAX) и имя бота (если пусто — бот узнает его сам)
    WEBAPP_ENABLED: bool = True
    WEBAPP_PORT: int = 8080
    MINIAPP_BUTTON: bool = False
    MAX_BOT_USERNAME: str = ""
    IMAGE_CARDS: bool = True

    # --- Администраторы ---
    # ID в MAX через запятую (свой ID можно узнать командой /whoami). Им доступны /stats и /demo
    ADMIN_IDS: str = ""
    # Учителя: ID в MAX через запятую — им открыт кабинет учителя (/teacher). Ещё можно добавить командой /add_teacher
    TEACHER_IDS: str = ""
    # true — кабинет учителя открывается только после подтверждения администратором; false — сразу по кнопке
    TEACHER_APPROVAL: bool = False
    # «🎨 Объясни по-другому»: сколько НОВЫХ объяснений (запросов к ИИ) в день на ученика; готовые — бесплатно
    VIA_DAILY_LIMIT: int = 5

    # --- Инфраструктура ---
    DATABASE_URL: PostgresDsn = Field(description="Строка подключения к PostgreSQL (postgresql+asyncpg://...)")
    REDIS_URL: RedisDsn = Field(description="Строка подключения к Redis")


    @property
    def admin_ids(self) -> set:
        return {int(x) for x in self.ADMIN_IDS.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


settings = Settings()
