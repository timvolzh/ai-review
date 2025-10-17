# AI Review

## Обзор
- Консольная автоматизация, формирующая ревью merge request в GitLab или pull request в GitHub с использованием модели большого языка, размещённой в Ollama.
- Сервис агрегирует диффы, вызывает LLM с детерминированными подсказками и публикует структурированные замечания в выбранной VCS.
- Поддерживается прицельная проверка одного merge/pull request, пакетная обработка всех открытых запросов и предварительный просмотр в режиме dry-run.

## Структура репозитория
| Путь | Назначение |
| --- | --- |
| `review.py` | Точка входа CLI, координирующая доступ к GitLab/GitHub и подсказки для Ollama. |
| `ai_prompt/` | Шаблоны подсказок для системного, пользовательского и итогового этапов. |
| `llm_client/` | HTTP-клиент Ollama с логикой повторных попыток. |
| `vcs_client/` | Минимальные REST-клиенты GitLab и GitHub для операций с merge/pull request. |
| `requirements.txt` | Список зависимостей Python во время выполнения. |
| `Dockerfile` | Определение образа контейнера для сервиса ревью. |

## Предварительные требования
- Python версии 3.12 или новее.
- Доступ к серверу Ollama, предоставляющему конечную точку `/api/generate`.
- Персональный токен GitLab с областью `api` или GitHub токен с правом на работу с pull request (минимум `repo`).
- Сетевое соединение из среды исполнения к GitLab/GitHub и серверу Ollama.

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
| `REVIEW_VCS` | (опционально) `gitlab` или `github`. Управляет провайдером по умолчанию (можно переопределить CLI-флагом `--vcs`). |
| `VCS_PROVIDER` | Устаревший синоним `REVIEW_VCS` для выбора провайдера по умолчанию. |
| `GITLAB_URL` | Базовый URL инстанса GitLab (например, `https://gitlab.example.com`). Обязательно при работе с GitLab. |
| `GITLAB_TOKEN` | Персональный токен GitLab с областью `api`. Обязательно при работе с GitLab. |
| `GITLAB_PROJECT_ID` | Числовой или URL-кодированный идентификатор проекта. Обязательно для получения списка или проверки merge request. |
| `GITLAB_VERIFY_SSL` | Значение `true`/`false` для управления проверкой SSL GitLab. По умолчанию `false`. При отключении клиент выводит предупреждение. |
| `GITHUB_URL` | Базовый URL GitHub API (по умолчанию `https://api.github.com`). Полезно для GitHub Enterprise. |
| `GITHUB_TOKEN` | Персональный токен GitHub. Обязательно при работе с GitHub. |
| `GITHUB_REPO` | Репозиторий в формате `owner/name` для таргетирования pull request. |
| `GITHUB_USER_AGENT` | Пользовательский `User-Agent` заголовок для GitHub API (по умолчанию `ai-review`). |
| `GITHUB_VERIFY_SSL` | Значение `true`/`false` для проверки SSL GitHub. По умолчанию `true`. |

## Использование
Запускайте инструмент после настройки переменных окружения.

```bash
# GitLab примеры
python review.py --iid 42             # Проверить merge request !42 и опубликовать заметки
python review.py --iid 42 --dry-run   # Сформировать ревью для !42 без публикации
python review.py --list               # Вывести список открытых merge request
python review.py --all                # Проверить каждый открытый merge request
python review.py --all --model llama3 # Переопределить модель Ollama для текущего запуска

# GitHub примеры
python review.py --vcs github --iid 17 --github-repo owner/repo  # Ревью pull request #17
python review.py --vcs github --list --github-repo owner/repo    # Показать открытые PR
python review.py --vcs github --all --github-repo owner/repo     # Проверить каждый открытый PR
```

Инструмент дробит крупные диффы, чтобы оставаться в пределах ограничений платформы по размеру заметок. При указании `--dry-run` Markdown ревью выводится в stdout вместо публикации комментариев.

## Запуск в Docker
Соберите и запустите контейнерный образ, чтобы выполнить ревью без локальной установки зависимостей.

```bash
docker build -t ai-review .
# GitLab запуск
GITLAB_URL=... GITLAB_TOKEN=... GITLAB_PROJECT_ID=... \
  OLLAMA_URL=... docker run --rm \
  -e GITLAB_URL \
  -e GITLAB_TOKEN \
  -e GITLAB_PROJECT_ID \
  -e OLLAMA_URL \
  -e OLLAMA_MODEL \
  -e GITLAB_VERIFY_SSL \
  ai-review --iid 42

# GitHub запуск
GITHUB_TOKEN=... GITHUB_REPO=owner/repo \
  OLLAMA_URL=... docker run --rm \
  -e REVIEW_VCS=github \
  -e GITHUB_TOKEN \
  -e GITHUB_REPO \
  -e GITHUB_URL \
  -e OLLAMA_URL \
  -e OLLAMA_MODEL \
  -e GITHUB_VERIFY_SSL \
  ai-review --iid 17
```

Точка входа контейнера запускает `python /app/review.py`, поэтому аргументы CLI передаются сразу после имени образа.
