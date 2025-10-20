# AI Review

## Обзор
- Консольная автоматизация, формирующая ревью merge/pull request в GitLab или Bitbucket с использованием модели большого языка, размещённой в Ollama.
- Сервис агрегирует диффы, вызывает LLM с детерминированными подсказками и публикует структурированные замечания в выбранной системе контроля версий.
- Поддерживается прицельная проверка одного merge request, пакетная обработка всех открытых запросов и предварительный просмотр в режиме dry-run.

## Структура репозитория
| Путь | Назначение |
| --- | --- |
| `review.py` | Точка входа CLI, координирующая доступ к GitLab и подсказки для Ollama. |
| `ai_prompt/` | Шаблоны подсказок для системного, пользовательского и итогового этапов. |
| `llm_client/` | HTTP-клиент Ollama с логикой повторных попыток. |
| `vcs_client/` | Минимальные REST-клиенты GitLab и Bitbucket для операций с запросами на слияние. |
| `requirements.txt` | Список зависимостей Python во время выполнения. |
| `Dockerfile` | Определение образа контейнера для сервиса ревью. |

## Предварительные требования
- Python версии 3.12 или новее.
- Доступ к серверу Ollama, предоставляющему конечную точку `/api/generate`.
- Персональный токен GitLab с областью `api` **или** учётная запись Bitbucket с app password, в зависимости от выбранного провайдера.
- Сетевое соединение из среды исполнения к GitLab и серверу Ollama.

## Установка
1. Клонируйте репозиторий и перейдите в каталог проекта.
2. (Необязательно) Создайте и активируйте виртуальное окружение.
3. Установите зависимости: `pip install -r requirements.txt`.

## Настройка
Задайте переменные окружения ниже или поместите их в файл `.env` в корне проекта (загружается автоматически через `python-dotenv`).

| Переменная | Описание |
| --- | --- |
| `OLLAMA_URL` | Базовый URL сервиса Ollama. Значение по умолчанию: `http://localhost:11434`. |
| `OLLAMA_MODEL` | Идентификатор модели, запрашиваемой у Ollama. Значение по умолчанию: `llama3.1:8b`. |
| `VCS_PROVIDER` | Провайдер VCS: `gitlab` (по умолчанию) или `bitbucket`. |
| `GITLAB_URL` | Базовый URL инстанса GitLab (например, `https://gitlab.example.com`). Требуется при `VCS_PROVIDER=gitlab`. |
| `GITLAB_TOKEN` | Персональный токен GitLab с областью `api`. Требуется при `VCS_PROVIDER=gitlab`. |
| `GITLAB_PROJECT_ID` | Числовой или URL-кодированный идентификатор проекта. Требуется при `VCS_PROVIDER=gitlab`. |
| `GITLAB_VERIFY_SSL` | Значение `true`/`false` для управления проверкой SSL GitLab. По умолчанию `false`. |
| `BITBUCKET_URL` | Базовый URL API Bitbucket. Значение по умолчанию: `https://api.bitbucket.org`. |
| `BITBUCKET_USERNAME` | Учётная запись Bitbucket. Требуется при `VCS_PROVIDER=bitbucket`. |
| `BITBUCKET_APP_PASSWORD` | App password для Bitbucket. Требуется при `VCS_PROVIDER=bitbucket`. |
| `BITBUCKET_WORKSPACE` | Пространство (workspace) репозитория. Требуется при `VCS_PROVIDER=bitbucket`. |
| `BITBUCKET_REPO_SLUG` | Слаг репозитория Bitbucket. Требуется при `VCS_PROVIDER=bitbucket`. |
| `BITBUCKET_VERIFY_SSL` | Значение `true`/`false` для управления проверкой SSL Bitbucket. По умолчанию `true`. |

## Использование
Запускайте инструмент после настройки переменных окружения.

```bash
python review.py --iid 42                       # Проверить merge request !42 в GitLab и опубликовать заметки
python review.py --iid 42 --dry-run             # Сформировать ревью для !42 без публикации
python review.py --list                         # Вывести список открытых запросов
python review.py --all                          # Проверить каждый открытый запрос
python review.py --vcs bitbucket --iid 12       # Проверить pull request #12 в Bitbucket
python review.py --all --model llama3           # Переопределить модель Ollama для текущего запуска
```

Инструмент дробит крупные диффы, чтобы оставаться в пределах ограничений выбранного провайдера по размеру заметок. При указании `--dry-run` Markdown ревью выводится в stdout вместо публикации комментариев.

## Запуск в Docker
Соберите и запустите контейнерный образ, чтобы выполнить ревью без локальной установки зависимостей.

```bash
docker build -t ai-review .
VCS_PROVIDER=gitlab GITLAB_URL=... GITLAB_TOKEN=... GITLAB_PROJECT_ID=... \
  OLLAMA_URL=... docker run --rm \
  -e VCS_PROVIDER \
  -e GITLAB_URL \
  -e GITLAB_TOKEN \
  -e GITLAB_PROJECT_ID \
  -e OLLAMA_URL \
  -e OLLAMA_MODEL \
  -e GITLAB_VERIFY_SSL \
  ai-review --iid 42

# Пример для Bitbucket
VCS_PROVIDER=bitbucket BITBUCKET_USERNAME=... BITBUCKET_APP_PASSWORD=... \
  BITBUCKET_WORKSPACE=... BITBUCKET_REPO_SLUG=... docker run --rm \
  -e VCS_PROVIDER \
  -e BITBUCKET_URL \
  -e BITBUCKET_USERNAME \
  -e BITBUCKET_APP_PASSWORD \
  -e BITBUCKET_WORKSPACE \
  -e BITBUCKET_REPO_SLUG \
  -e BITBUCKET_VERIFY_SSL \
  -e OLLAMA_URL \
  -e OLLAMA_MODEL \
  ai-review --iid 12 --vcs bitbucket
```

Точка входа контейнера запускает `python /app/review.py`, поэтому аргументы CLI передаются сразу после имени образа.
