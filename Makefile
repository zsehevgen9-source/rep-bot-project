.PHONY: help up down restart build logs logs-db psql migrate

DB_USER ?= user
DB_NAME ?= bot_db

help: ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  %-12s %s\n", $$1, $$2}'

up: ## Собрать и запустить всё в фоне
	docker compose up -d --build

down: ## Остановить и удалить контейнеры
	docker compose down

restart: down up ## Перезапустить

build: ## Пересобрать образ бота
	docker compose build bot

logs: ## Логи бота
	docker compose logs -f bot

logs-db: ## Логи PostgreSQL
	docker compose logs -f db

psql: ## Консоль PostgreSQL
	docker compose exec db psql -U $(DB_USER) -d $(DB_NAME)

migrate: ## Применить миграции Alembic
	docker compose run --rm bot alembic upgrade head
