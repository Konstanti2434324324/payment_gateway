# Payment Gateway

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)

**Repository:** https://github.com/Konstanti2434324324/payment_gateway.git

FastAPI-сервис платёжного шлюза с интеграцией мок-провайдера. Поддерживает аутентификацию мерчантов, управление балансом, создание платежей с проверкой подписи HMAC-SHA256 и обработку вебхуков.

## Архитектура

- **Основной сервис** (`app/`) — FastAPI-приложение на порту 8000. Обрабатывает аутентификацию мерчантов, создание платежей и вебхуки провайдера. Использует PostgreSQL для хранения данных и Redis для кэширования профилей мерчантов.
- **Мок провайдера** (`provider/`) — лёгкое FastAPI-приложение на порту 8001. Симулирует внешний платёжный провайдер, асинхронно отправляющий вебхук-коллбэки.
- **PostgreSQL** — основное хранилище данных: мерчанты, балансы и платежи.
- **Redis** — кэш профилей мерчантов (TTL 60 секунд), сбрасывается при любом изменении баланса.

## Быстрый старт

```bash
git clone https://github.com/Konstanti2434324324/payment_gateway.git
cd payment_gateway
docker compose up --build
```

После автоматического запуска миграций API будет доступно по адресу `http://localhost:8000/api/v1/`.

## Запуск тестов

> **Важно:** тесты запускаются **на хост-машине** (не внутри Docker-контейнера).
> Python 3.12 должен быть установлен локально.

### Шаг 1 — Поднять PostgreSQL и Redis через Docker

```bash
docker compose up db redis -d
```

Это запустит два контейнера в фоне:
- **PostgreSQL** — будет доступен на `localhost:5433`
- **Redis** — будет доступен на `localhost:6379`

Убедитесь, что контейнеры запустились и здоровы:
```bash
docker compose ps
```
Статус обоих должен быть `healthy`.

---

### Шаг 2 — Создать тестовую базу данных

Тесты используют отдельную БД `payment_gateway_test`, чтобы не трогать основную.
Создайте её командой:

```bash
docker compose exec db psql -U postgres -c "CREATE DATABASE payment_gateway_test;"
```

Эту команду нужно выполнить **один раз**. При повторных запусках тестов она не нужна.

---

### Шаг 3 — Установить зависимости Python

Установите зависимости в виртуальное окружение:

```bash
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Если окружение уже создано, достаточно его активировать:
```bash
source .venv/bin/activate
```

---

### Шаг 4 — Запустить тесты

```bash
pytest
```

`conftest.py` автоматически подгружает `.env.test`, где прописаны адреса тестовых БД и Redis.
Создавать или экспортировать переменные окружения вручную **не нужно**.

Для подробного вывода (имена каждого теста):
```bash
pytest -v
```

Запустить только один файл:
```bash
pytest tests/test_payments.py -v
```

## Начальные данные

Миграция Alembic создаёт двух мерчантов:

| Мерчант       | API Token           | Secret Key            | Начальный баланс |
|---------------|---------------------|-----------------------|------------------|
| Merchant One  | `token-merchant-1`  | `secret-merchant-1`   | 10 000,00        |
| Merchant Two  | `token-merchant-2`  | `secret-merchant-2`   | 5 000,00         |

## Как проверить API вручную

Открыть эндпоинт в браузере напрямую **не получится** — API требует заголовки (`X-API-Token`, `X-Signature`), которые браузер не передаёт. Вместо этого используйте один из способов ниже.

### Способ 1 — Swagger UI (проще всего)

1. Откройте `http://localhost:8000/docs` в браузере
2. Выберите нужный эндпоинт (например `GET /api/v1/merchant/profile`)
3. Нажмите **Try it out**
4. Введите значения заголовков и нажмите **Execute**

### Способ 2 — curl в терминале

## Примеры запросов

### Получить профиль мерчанта

```bash
curl http://localhost:8000/api/v1/merchant/profile \
  -H "X-API-Token: token-merchant-1"
```

### Создать платёж

Сначала сгенерируйте подпись HMAC-SHA256:

```bash
# Однострочная генерация подписи на Python
BODY='{"amount": "250.00"}'
SECRET='secret-merchant-1'
python3 -c "
import hmac, hashlib, json
body = '$BODY'.encode()
secret = '$SECRET'
sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
print(sig)
"
```

Затем выполните запрос:

```bash
BODY='{"amount": "250.00"}'
SIG=$(python3 -c "
import hmac, hashlib
body = b'$BODY'
secret = 'secret-merchant-1'
print(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())
")

curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Token: token-merchant-1" \
  -H "X-Signature: $SIG" \
  -d "$BODY"
```

### Полный пример на Python

```python
import hmac
import hashlib
import json
import httpx

api_token = "token-merchant-1"
secret_key = "secret-merchant-1"

body = json.dumps({"amount": "250.00"}).encode()
signature = hmac.new(secret_key.encode(), body, hashlib.sha256).hexdigest()

response = httpx.post(
    "http://localhost:8000/api/v1/payments",
    content=body,
    headers={
        "Content-Type": "application/json",
        "X-API-Token": api_token,
        "X-Signature": signature,
    },
)
print(response.json())
```

## Переменные окружения

| Переменная           | Описание                                            | Пример                                                     |
|----------------------|-----------------------------------------------------|------------------------------------------------------------|
| `DATABASE_URL`       | Асинхронный DSN PostgreSQL (драйвер asyncpg)        | `postgresql+asyncpg://postgres:password@db:5432/payment_gateway` |
| `REDIS_URL`          | URL подключения к Redis                             | `redis://redis:6379/0`                                     |
| `PROVIDER_BASE_URL`  | Базовый URL мок-сервиса провайдера                  | `http://provider:8001`                                     |
| `CALLBACK_BASE_URL`  | Базовый URL для вебхук-коллбэков провайдера         | `http://app:8000`                                          |

## Справочник API

| Метод | Путь                          | Аутентификация            | Описание                                  |
|-------|-------------------------------|---------------------------|-------------------------------------------|
| GET   | `/api/v1/merchant/profile`    | X-API-Token               | Получить профиль мерчанта с балансом      |
| POST  | `/api/v1/payments`            | X-API-Token + X-Signature | Создать новый платёж                      |
| POST  | `/api/v1/webhooks/provider`   | Нет (внутренний)          | Получить вебхук-коллбэк от провайдера     |
