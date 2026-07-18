# DevAgent Local

[![CI](https://github.com/asg-55/devagent-local/actions/workflows/ci.yml/badge.svg)](https://github.com/asg-55/devagent-local/actions/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Локальный AI-агент, который создаёт и последовательно редактирует небольшие веб-проекты через Ollama. Он работает в Docker, хранит проекты на компьютере пользователя и проверяет результат статическими тестами и настоящим браузером.

> **Статус:** рабочий MVP. Основной сценарий — одностраничные HTML/CSS/JS и React/Vite-сайты. Агент не является заменой полноценной IDE или изолированной средой для запуска произвольного недоверенного кода.

## Что уже умеет

- создаёт отдельные проекты и продолжает их редактировать в следующих сообщениях;
- хранит историю сессий в SQLite и делает контрольные копии перед изменением файлов;
- использует инструменты чтения, поиска, записи, точечной замены, удаления и валидации;
- поддерживает нативный tool calling Ollama и резервный JSON-формат вызовов;
- проверяет HTML, CSS, JavaScript и Python без передачи модели произвольного shell-доступа;
- открывает результат в headless Chromium на desktop и mobile-размерах;
- находит ошибки JavaScript, отсутствующие ресурсы, пустые страницы, горизонтальный overflow и базовые проблемы доступности;
- собирает стандартные React 19 + Vite 8 проекты в управляемом runtime;
- сохраняет скриншоты проверок и показывает журнал действий в веб-интерфейсе;
- автоматически выбирает более мощную модель для запросов на premium/polished-дизайн, если она установлена.

## Быстрый запуск на Windows

### Требования

- Windows 10/11;
- [Docker Desktop](https://www.docker.com/products/docker-desktop/);
- [Ollama](https://ollama.com/);
- минимум одна установленная coding-модель.

Рекомендуемые модели:

```powershell
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:32b
```

`7b` используется по умолчанию. `32b` подключается для сложных визуальных запросов, если доступна; на видеокарте с 16 ГБ VRAM она может частично выгружаться в RAM и работать заметно медленнее.

### Запуск

1. Запустите Ollama и Docker Desktop.
2. Клонируйте репозиторий:

   ```powershell
   git clone https://github.com/asg-55/devagent-local.git
   cd devagent-local
   ```

3. Дважды щёлкните `start_agent.bat` или запустите его из терминала:

   ```powershell
   .\start_agent.bat
   ```

4. Дождитесь сообщения `DevAgent is running`. Интерфейс откроется по адресу [http://localhost:5000](http://localhost:5000).

Скрипт собирает образ `devagent-local`, запускает контейнер `ai-agent` и монтирует текущую директорию в `/workspace`. Созданные сайты находятся в локальной папке `projects/`, состояние агента — в `.devagent/`; обе директории исключены из Git.

Остановить агент:

```powershell
docker stop ai-agent
```

## Как это устроено

```text
Browser UI
    │
    ▼
Flask API ── SQLite history
    │
    ▼
Agent loop ── Ollama
    │
    ├── workspace tools + checkpoints
    ├── static validation
    └── Chromium desktop/mobile check
             │
             └── screenshots + production preview
```

Основные модули:

| Модуль | Назначение |
| --- | --- |
| `devagent/agent.py` | агентный цикл, вызовы инструментов и выбор профиля качества |
| `devagent/tools.py` | безопасные операции с файлами проекта |
| `devagent/workspace.py` | границы workspace, атомарная запись и checkpoints |
| `devagent/validation.py` | структурная проверка файлов и локальных ресурсов |
| `devagent/checks.py` | allowlist статических проверок Python, JS и CSS |
| `devagent/browser_check.py` | Chromium-проверка, Vite-сборка и скриншоты |
| `devagent/storage.py` | проекты, сессии и сообщения в SQLite |
| `devagent/web.py` | Flask API и публикация preview/artifacts |

## Настройка

Контейнер поддерживает переменные окружения:

| Переменная | Значение по умолчанию | Назначение |
| --- | --- | --- |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | адрес Ollama API |
| `DEVAGENT_MODEL` | `qwen2.5-coder:7b` | основная модель |
| `DEVAGENT_COMPLEX_MODEL` | `qwen2.5-coder:32b` | модель для polished-запросов |
| `DEVAGENT_MAX_STEPS` | `32` | максимум шагов агентного цикла |
| `DEVAGENT_MAX_FILE_BYTES` | `1000000` | максимальный размер одного файла |
| `DEVAGENT_MAX_CONTEXT_CHARS` | `60000` | лимит файлового контекста |

## Проверка проекта

Полный прогон в том же окружении, что используется приложением:

```powershell
docker build -t devagent-local .
docker run --rm devagent-local python -m unittest discover -s tests -v
```

Сейчас набор содержит 31 unit/integration-тест, включая реальные desktop/mobile-прогоны Chromium. Та же проверка автоматически запускается в GitHub Actions.

## Ограничения и следующий шаг

- поддерживается ограниченный набор зависимостей React/Vite;
- браузерная проверка не подтверждает всю бизнес-логику созданного сайта;
- качество результата зависит от выбранной локальной модели и доступного контекста;
- сейчас интерфейс и документация ориентированы в первую очередь на Windows.

Следующий этап — расширить browser check сценариями взаимодействия с формами, модальными окнами, вкладками и навигацией, а затем использовать результаты этих сценариев для автоматического цикла исправлений.

## Лицензия

[MIT](LICENSE) © 2026 Alex (`asg-55`).
