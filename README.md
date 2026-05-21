# HH AI Job Matcher

AI-анализатор вакансий hh.ru: собирает вакансии, сохраняет их в SQLite, сравнивает с профилем кандидата через DeepSeek API и формирует список лучших вакансий для отклика.

## Возможности

- Сбор вакансий hh.ru через Playwright.
- Сохранение вакансий в SQLite.
- Дедупликация вакансий.
- AI-анализ вакансий через DeepSeek API.
- Скоринг соответствия вакансии профилю кандидата.
- Экспорт подходящих вакансий в JSON.
- Генерация Markdown-отчёта.
- Запуск пайплайна по стадиям.

## Стек

- Python 3.12+
- Playwright
- playwright-stealth
- SQLite
- DeepSeek API
- Pydantic
- httpx
- PyYAML
- Rich
- unittest

## Быстрый старт

```powershell
git clone https://github.com/Tajozhnik/hh-ai-job-matcher.git
cd hh-ai-job-matcher

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
playwright install chromium

copy config.example.yaml config.yaml
$env:DEEPSEEK_API_KEY="your-deepseek-api-key"

python main.py
```

## Конфигурация

Основной конфиг создаётся из шаблона:

```powershell
copy config.example.yaml config.yaml
```

В `config.yaml` настраиваются:

- `deepseek` — ключ из переменной окружения, base URL и модель.
- `search` — URL поиска hh.ru, количество страниц, задержки и headless-режим.
- `proxy` — включение и URL прокси.
- `profile` — профиль кандидата, навыки, пет-проекты, ограничения, зарплата и локации.
- `analysis` — размер пачки анализа, минимальный `fit_score` и путь экспорта.

Реальный API-ключ не нужно записывать в YAML. Передайте его через переменную окружения:

```powershell
$env:DEEPSEEK_API_KEY="your-deepseek-api-key"
```

## Запуск по стадиям

```powershell
python main.py --only scrape
python main.py --only analyze
python main.py --only export
python main.py --only report
```

Без `--only` запускается полный пайплайн: `scrape → analyze → export`.

## Отчёт

Markdown-отчёт создаётся командой:

```powershell
python main.py --only report
```

Файл сохраняется в `results/report-YYYY-MM-DD.md` и группирует вакансии по секциям:

- `Точно подавайся`
- `Подумай`
- `Интересно, но не мой уровень`
- `Отсеяно`

## Безопасность

- `config.yaml` не коммитится.
- `.env` не коммитится.
- `results/`, SQLite-базы, логи и виртуальное окружение не коммитятся.
- В репозитории лежат только безопасные шаблоны: `config.example.yaml` и `.env.example`.
- Реальный DeepSeek API key должен передаваться через `DEEPSEEK_API_KEY`.

## Проверка

```powershell
python -c "import scraper, analyzer, database, main"
python main.py --help
python -m unittest discover -s tests -v
```

Тесты не запускают реальный scraping и не требуют настоящего API-ключа.

## Статус проекта

Проект оформлен как pet-проект для портфолио: локальный скрапер, SQLite-хранилище, AI-анализ вакансий и Markdown-отчёт по результатам.
