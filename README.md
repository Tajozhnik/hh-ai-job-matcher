# HH AI Job Matcher

AI-анализатор вакансий hh.ru: собирает вакансии, сохраняет их в SQLite, сравнивает с профилем кандидата через DeepSeek API и формирует список лучших вакансий для отклика.

## Возможности

- Сбор вакансий hh.ru через Playwright по списку поисковых запросов под разные треки.
- Сохранение вакансий в SQLite, дедупликация по id и по `title + company`.
- AI-анализ вакансий через DeepSeek API с детерминированным скорингом.
- Pre-filter без LLM для очевидно неподходящих вакансий (1С / PHP / Senior / низкая ЗП).
- Параллельный анализ через `asyncio.Semaphore`.
- Web UI на Streamlit с фильтрами, графиками и кнопками управления пайплайном.
- Экспорт в JSON и Markdown-отчёт со сводной статистикой.
- Запуск пайплайна по стадиям: `scrape / analyze / reanalyze / export / report / stats / purge-html`.

## Стек

- Python 3.12+
- Playwright + playwright-stealth (скрапер)
- SQLite (хранилище)
- DeepSeek API (анализатор)
- Pydantic, httpx, tenacity (сетевая часть)
- PyYAML, python-dotenv (конфиг)
- Streamlit, pandas, plotly (UI)
- Rich (консольный вывод)
- unittest (тесты)

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

Альтернатива: создайте файл `.env` в корне проекта (он в `.gitignore`):

```
DEEPSEEK_API_KEY=your-deepseek-api-key
```

`.env` подхватывается автоматически через `python-dotenv`.

## Сеть и VPN

Перед запуском **выключи VPN**, особенно с европейским/американским exit-нодом.

- **hh.ru** агрессивно фильтрует не-российский трафик: на зарубежном IP ты получишь капчу на каждой странице или HTTP 403, скрапер уйдёт в долгие паузы и в итоге остановится.
- **DeepSeek API** обычно нормально отвечает с российского IP. Если у твоего провайдера проблемы с маршрутом до `api.deepseek.com` — настрой VPN только для Python-процесса (например, через split tunneling), а Chromium от Playwright оставь на прямом соединении с hh.ru.

Признаки, что VPN мешает:
- На каждой вакансии всплывает капча.
- В логе подряд `hh.ru returned HTTP 403. Pausing for 5–10 minutes`.
- Скрапер собирает 0–2 вакансии и зависает.

Решение: выключи VPN полностью, подожди 10–15 минут (hh мог пометить твой прошлый IP), запусти `python main.py --only scrape` ещё раз.

## Запуск по стадиям

```powershell
python main.py --only scrape
python main.py --only analyze
python main.py --only export
python main.py --only report
python main.py --only stats
python main.py --only reanalyze
python main.py --only purge-html
```

Можно запускать несколько стадий списком: `python main.py --only analyze,export,report`.

Без `--only` запускается полный пайплайн: `scrape → analyze → export → report`.

## Web UI (Streamlit)

Помимо CLI есть веб-интерфейс на Streamlit — карточки вакансий, графики распределения, фильтры по треку и рекомендации, кнопки для запуска стадий пайплайна.

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Важно: запускай через venv, иначе Streamlit возьмёт системный Python и упадёт с `ModuleNotFoundError: No module named 'plotly'`. Проверка — в начале строки терминала должно быть `(.venv)`. Альтернатива без активации:

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

UI откроется на http://localhost:8501 и читает ту же SQLite-базу, что и CLI. Кнопки в сайдбаре запускают `scrape / analyze / reanalyze / report / purge-html` через тот же `main.py`.

Что внутри:

- Метрики сверху: всего в БД, проанализировано, apply / maybe, средний fit_score.
- Вкладка **Карточки** — каждая вакансия отдельной карточкой с цветовой меткой по рекомендации, прогресс-барами по трём осям скоринга, разворачивающимися причинами и red flags.
- Вкладка **Графики** — гистограмма fit_score, распределение по трекам, scatter «я → них vs они → я» с диагональю.
- Вкладка **Таблица** — сортируемая таблица с прогресс-барами и кликабельными ссылками на hh.ru.
- Сайдбар: фильтры по треку, рекомендации, минимальному fit_score и поиск по тексту.

### Что делают стадии

- `scrape` — собирает вакансии по списку запросов из `config.search.urls` (или одиночному `config.search.url`). Вакансии дедуплицируются по id и по нормализованной паре «title + company», чтобы не платить за анализ одного и того же объявления, опубликованного под разными id.
- `analyze` — отправляет на DeepSeek все необработанные вакансии в БД, ограничено `analysis.batch_size`.
- `reanalyze` — стирает все существующие анализы и пересчитывает их под текущей логикой и промптом. Полезно после правок в `analyzer.py`.
- `export` — выгружает вакансии с `fit_score >= analysis.min_fit_score` в `results/matches.json` и печатает таблицу в консоли.
- `report` — генерирует Markdown-отчёт `results/report-YYYY-MM-DD.md`, сгруппированный по статусу (apply / maybe / интересно но не уровень / отсеяно) и треку.
- `stats` — печатает в консоль распределение по рекомендациям, трекам и средний `fit_score`.
- `purge-html` — обнуляет колонку `raw_html` у всех вакансий и делает VACUUM. Раньше скрапер сохранял весь HTML; теперь это опционально через `search.save_raw_html`, но в старой базе мусор может остаться.

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

## Скоринг и анти-cheating

LLM-анализатор раскладывает соответствие на две независимые оси и одну производную:

- `my_fit_for_them` (0..1) — насколько кандидат закрывает требования вакансии (стек, грейд, опыт).
- `their_fit_for_me` (0..1) — насколько вакансия подходит кандидату (зарплата, локация, трек, hard_no).
- `fit_score` = `min(my_fit_for_them, their_fit_for_me)` — пересечение двух осей. Это инвариант, который форсится в коде на стороне Python, даже если LLM попытается выдать «общий вайб».
- `recommendation` детерминированно вычисляется из `fit_score`:
  - `>= 0.75` → `apply`
  - `0.55..0.75` → `maybe`
  - `< 0.55` → `skip`
- `track` — основной трек вакансии: `backend`, `ai_automation`, `telegram_bot`, `data_analytics`, `ml`, `qa`, `devops`, `fullstack`, `mobile`, `other`. В отчёте подходящие вакансии группируются по треку, чтобы не смешивать backend и QA в одной секции.

## Pre-filter

Перед обращением к LLM запускается дешёвый детерминированный фильтр (`analyzer.deterministic_skip`):

- Регекспы по тексту вакансии отлавливают «1С», PHP/Java/C++ как основной стек, Senior/Lead.
- Парсер зарплаты (`parse_salary_min_rub`) ловит вакансии с зарплатой существенно ниже `min_salary_rub`.
- Если фильтр сработал — LLM не вызывается, расход токенов экономится.

## Параллельный анализ

Стадия `analyze` запускает запросы к DeepSeek параллельно через `asyncio.Semaphore`. Уровень параллелизма контролируется в `config.yaml`:

```yaml
analysis:
  batch_size: 20
  concurrency: 4
  min_fit_score: 0.70
```

## Безопасность

- `config.yaml` не коммитится.
- `.env` не коммитится.
- `results/`, SQLite-базы, логи и виртуальное окружение не коммитятся.
- В репозитории лежат только безопасные шаблоны: `config.example.yaml` и `.env.example`.
- **Никогда не пиши реальный DeepSeek API key прямо в `config.yaml`.** Используй переменную окружения `DEEPSEEK_API_KEY`. В `config.yaml` оставляй placeholder `"${DEEPSEEK_API_KEY}"`.

## Проверка

```powershell
python -c "import scraper, analyzer, database, main"
python main.py --help
python -m unittest discover -s tests -v
```

Тесты не запускают реальный scraping и не требуют настоящего API-ключа.

## Статус проекта

Проект оформлен как pet-проект для портфолио: локальный скрапер, SQLite-хранилище, AI-анализ вакансий и Markdown-отчёт по результатам.
