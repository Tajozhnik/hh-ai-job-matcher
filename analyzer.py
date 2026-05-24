from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal

import database


BASE_DIR = Path(__file__).resolve().parent

try:
    from pydantic import BaseModel, Field

    PYDANTIC_AVAILABLE = True
except Exception:  # pragma: no cover - keeps module importable before install
    BaseModel = object  # type: ignore[assignment]
    Field = None  # type: ignore[assignment]
    PYDANTIC_AVAILABLE = False


SYSTEM_PROMPT = """
Ты строгий анализатор соответствия вакансии hh.ru профилю кандидата-джуна.
Кандидат — студент 3 курса РТУ МИРЭА без большого коммерческого опыта, но с
сильными пет-проектами, статьями на Хабре, hackathon и shipped-приложением в
RuStore. Считай пет-проекты полноценным релевантным опытом, особенно для Python
backend, AI automation, Telegram-ботов, API, LLM, RAG и автоматизаций.

Верни СТРОГО JSON без Markdown, комментариев и текста вокруг объекта.
Формат ответа НЕ МЕНЯТЬ:
{
  "my_fit_for_them": 0..1,
  "their_fit_for_me": 0..1,
  "fit_score": 0..1,
  "track": "backend" | "ai_automation" | "telegram_bot" | "data_analytics" | "ml" | "qa" | "devops" | "fullstack" | "mobile" | "other",
  "reasons": ["конкретные короткие причины"],
  "red_flags": ["конкретные короткие риски"],
  "recommendation": "apply" | "maybe" | "skip"
}

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА (нарушать нельзя):

1. fit_score = min(my_fit_for_them, their_fit_for_me). Никаких "общих ощущений".
   Если my_fit_for_them = 0.4, fit_score не может быть 0.85.

2. recommendation вычисляется по fit_score:
   - fit_score >= 0.75 -> "apply"
   - 0.55 <= fit_score < 0.75 -> "maybe"
   - fit_score < 0.55 -> "skip"
   Никаких "apply при fit_score 0.4".

3. Каждое значение my/their_fit_for_me должно ЯВНО следовать из текста вакансии,
   не додумывать. Если зарплата не указана — это умеренный red flag, а не плюс.

4. Различай разные оси:
   - my_fit_for_them: насколько кандидат закрывает требования вакансии
     (стек, опыт, грейд). Если в must-have указан стек, который не Python
     (Java/C++/PHP/1C/Go/.NET/C#/Kotlin как ОСНОВНОЙ), my_fit_for_them <= 0.25.
     Если требуют 3+ года коммерческого опыта строго — my_fit_for_them <= 0.4.
     Если стек Python/AI/automation совпадает и есть junior/intern формат —
     my_fit_for_them >= 0.7.
   - their_fit_for_me: насколько вакансия подходит кандидату.
     hard_no из профиля -> their_fit_for_me <= 0.3.
     Зарплата ниже min_salary_rub строго -> their_fit_for_me <= 0.4.
     Локация не из location_preference и не remote -> их_fit_for_me <= 0.5.
     Backend/AI/automation/Telegram-боты/LLM API -> their_fit_for_me >= 0.7.
     Чистый QA/Аналитика/DevOps/Frontend без Python-составляющей ->
     their_fit_for_me <= 0.6 (это не его трек).

5. track определяется по основному содержанию вакансии. Если вакансия "стажёр
   аналитик" — track = "data_analytics", даже если просят Python и SQL.
   Если "стажёр QA с автотестами" — track = "qa". Backend/AI/automation —
   приоритетные треки кандидата.

6. reasons и red_flags — короткие фразы, не предложения по 30 слов.
   reasons должны указывать конкретные факты из вакансии, а не домыслы.

7. Junior-friendly признаки усиливают my_fit_for_them: "стажировка",
   "обучение", "наставник", "ментор", "без опыта", "intern", "junior",
   "graduate", "будем учить".
   Hostile признаки снижают my_fit_for_them: "опыт от 3 лет строго",
   "самостоятельно с первого дня без ментора", "сразу в бой без обучения".
""".strip()

RECOMMENDATIONS = {"apply", "maybe", "skip"}
TRACKS = {
    "backend",
    "ai_automation",
    "telegram_bot",
    "data_analytics",
    "ml",
    "qa",
    "devops",
    "fullstack",
    "mobile",
    "other",
}
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
DESCRIPTION_LIMIT = 6000  # ~1.5k tokens; enough to capture stack and conditions

# Regex patterns used by the deterministic pre-filter to skip vacancies that
# obviously do not match the candidate before paying for an LLM call.
HARD_SKIP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Основной стек 1С", re.compile(r"\b1[cс]\b|\b1c-разработчик|программист 1[cс]", re.IGNORECASE)),
    ("Основной стек Java", re.compile(r"java[- ]разработчик|\bjava\s+(senior|middle)\b|опыт.*java\s+от\s+3", re.IGNORECASE)),
    ("Основной стек PHP", re.compile(r"php[- ]разработчик|\bphp\s+(senior|middle)\b", re.IGNORECASE)),
    ("Основной стек C\\+\\+", re.compile(r"c\+\+\s*-?\s*разработчик|разработчик\s*c\+\+", re.IGNORECASE)),
    ("Senior/Lead grade", re.compile(r"\b(senior|lead|teamlead|тимлид|главный\s+разработчик)\b", re.IGNORECASE)),
)
HARD_SKIP_FALLBACK_REASON = "Несоответствие стеку/грейду по основному тексту вакансии"


if PYDANTIC_AVAILABLE:

    class AnalysisResult(BaseModel):
        my_fit_for_them: float = Field(ge=0, le=1)  # type: ignore[misc]
        their_fit_for_me: float = Field(ge=0, le=1)  # type: ignore[misc]
        fit_score: float = Field(ge=0, le=1)  # type: ignore[misc]
        track: str = "other"
        reasons: list[str]
        red_flags: list[str]
        recommendation: Literal["apply", "maybe", "skip"]

else:

    @dataclass
    class AnalysisResult:  # type: ignore[no-redef]
        my_fit_for_them: float
        their_fit_for_me: float
        fit_score: float
        track: str
        reasons: list[str]
        red_flags: list[str]
        recommendation: str

        def model_dump(self) -> dict[str, Any]:
            return asdict(self)


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError("expected a list of strings")


def _coerce_score(value: Any) -> float:
    score = float(value)
    if score < 0 or score > 1:
        raise ValueError("score must be between 0 and 1")
    return score


def _normalize_score(value: Any) -> float:
    """Accept either 0..1 floats or legacy 0..100 ints stored in DB."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1:
        score = score / 100
    return max(0.0, min(score, 1.0))


def _recommendation_from_score(score: float) -> str:
    if score >= 0.75:
        return "apply"
    if score >= 0.55:
        return "maybe"
    return "skip"


def _enforce_score_invariants(data: dict[str, Any]) -> dict[str, Any]:
    """Make sure scores follow the rules from the prompt even if LLM cheats."""
    my = _normalize_score(data.get("my_fit_for_them", 0))
    their = _normalize_score(data.get("their_fit_for_me", 0))
    fit = _normalize_score(data.get("fit_score", 0))
    expected_fit = min(my, their)
    if abs(fit - expected_fit) > 0.05:
        fit = expected_fit
    data["my_fit_for_them"] = my
    data["their_fit_for_me"] = their
    data["fit_score"] = fit
    data["recommendation"] = _recommendation_from_score(fit)
    track = str(data.get("track", "other")).strip().lower() or "other"
    data["track"] = track if track in TRACKS else "other"
    return data


def validate_analysis_result(data: dict[str, Any]) -> AnalysisResult:
    data = dict(data)
    data = _enforce_score_invariants(data)
    if PYDANTIC_AVAILABLE:
        if hasattr(AnalysisResult, "model_validate"):
            return AnalysisResult.model_validate(data)  # type: ignore[attr-defined]
        return AnalysisResult.parse_obj(data)  # type: ignore[attr-defined]

    recommendation = str(data["recommendation"])
    if recommendation not in RECOMMENDATIONS:
        raise ValueError("recommendation must be apply, maybe, or skip")
    return AnalysisResult(
        my_fit_for_them=_coerce_score(data["my_fit_for_them"]),
        their_fit_for_me=_coerce_score(data["their_fit_for_me"]),
        fit_score=_coerce_score(data["fit_score"]),
        track=str(data.get("track", "other")),
        reasons=_coerce_string_list(data.get("reasons")),
        red_flags=_coerce_string_list(data.get("red_flags")),
        recommendation=recommendation,
    )


def parse_json_response(content: str) -> dict[str, Any]:
    text = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("DeepSeek response JSON must be an object")
    return data


def expand_env_vars(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    if not isinstance(value, str):
        return value

    return ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required: pip install -r requirements.txt") from exc

    # Auto-load .env from the project root if python-dotenv is available so users
    # don't have to export DEEPSEEK_API_KEY manually each session.
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env", override=False)
    except ImportError:  # pragma: no cover - optional dependency
        pass

    try:
        with open(path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Config file '{path}' not found.\n"
            "Create it from the example:  copy config.example.yaml config.yaml\n"
            "Then edit it to match your profile and set the DEEPSEEK_API_KEY env variable."
        ) from None
    return expand_env_vars(config)


def _format_list(items: list[Any] | None) -> str:
    if not items:
        return "- не указано"
    return "\n".join(f"- {item}" for item in items)


def _truncate_description(description: Any, limit: int = DESCRIPTION_LIMIT) -> str:
    if not description:
        return "не указано"
    text = str(description).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …[обрезано]"


def parse_salary_min_rub(salary: Any) -> int | None:
    """Best-effort extraction of the lower bound of the salary in RUB.

    Handles HH formats like "от 40 000 ₽ за месяц", "50 000 ₽", "120000 руб.".
    Returns None if the salary is missing or unparsable.
    """
    if not salary:
        return None
    text = str(salary).lower().replace("\xa0", " ")
    if any(token in text for token in ("usd", "$", "eur", "€", "kzt", "тенге", "byn")):
        return None  # non-RUB salaries are out of scope for the candidate
    numbers = [int(value.replace(" ", "")) for value in re.findall(r"\d[\d\s]{2,}", text)]
    if not numbers:
        return None
    if "от" in text:
        return numbers[0]
    if "до" in text and len(numbers) >= 1:
        return numbers[0]  # "до 100000" — use the visible bound as proxy
    return numbers[0]


def deterministic_skip(vacancy: dict[str, Any], profile: dict[str, Any]) -> AnalysisResult | None:
    """Cheap pre-filter that rejects obviously unsuitable vacancies without
    hitting the LLM. Returns None if the vacancy should be sent to LLM.
    """
    title = str(vacancy.get("title") or "")
    description = str(vacancy.get("description") or "")
    haystack = f"{title}\n{description}"

    red_flags: list[str] = []
    for flag, pattern in HARD_SKIP_PATTERNS:
        if pattern.search(haystack):
            red_flags.append(flag)

    min_salary = profile.get("min_salary_rub")
    if min_salary:
        salary_min = parse_salary_min_rub(vacancy.get("salary"))
        if salary_min is not None and salary_min < int(min_salary) * 0.6:
            red_flags.append(f"Зарплата {salary_min} ниже комфортного порога")

    if not red_flags:
        return None

    return validate_analysis_result(
        {
            "my_fit_for_them": 0.1,
            "their_fit_for_me": 0.1,
            "fit_score": 0.1,
            "track": "other",
            "reasons": [],
            "red_flags": red_flags or [HARD_SKIP_FALLBACK_REASON],
            "recommendation": "skip",
        }
    )


def _build_user_message(profile: dict[str, Any], vacancy: dict[str, Any]) -> str:
    return f"""
## ПРОФИЛЬ КАНДИДАТА
Имя: {profile.get("name", "не указано")}

{profile.get("summary", "не указано")}

## НАВЫКИ
{_format_list(profile.get("skills", []))}

## ПЕТ-ПРОЕКТЫ
{_format_list(profile.get("pet_projects", []))}

## ОГРАНИЧЕНИЯ
hard_no:
{_format_list(profile.get("hard_no", []))}

min_salary_rub: {profile.get("min_salary_rub", "не указано")}
location_preference:
{_format_list(profile.get("location_preference", []))}

## ВАКАНСИЯ
title: {vacancy.get("title", "не указано")}
company: {vacancy.get("company", "не указано")}
salary: {vacancy.get("salary", "не указано")}
location: {vacancy.get("location", "не указано")}
url: {vacancy.get("url", "не указано")}
published_at: {vacancy.get("published_at", "не указано")}

skills:
{_format_list(vacancy.get("skills", []))}

description:
{_truncate_description(vacancy.get("description"))}
""".strip()


def build_user_prompt(profile: dict[str, Any], vacancy: dict[str, Any]) -> str:
    return _build_user_message(profile, vacancy)


async def analyze_vacancy(
    vacancy: dict[str, Any],
    config: dict[str, Any] | str = "config.yaml",
) -> AnalysisResult:
    if isinstance(config, str):
        config = load_config(config)

    profile = config.get("profile", {})

    skip_result = deterministic_skip(vacancy, profile)
    if skip_result is not None:
        return skip_result

    deepseek = config.get("deepseek", {})
    api_key = deepseek.get("api_key")
    if not api_key:
        raise RuntimeError(
            "DeepSeek API key is empty. Set the DEEPSEEK_API_KEY environment variable."
        )

    try:
        import httpx
        from tenacity import (
            retry,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install dependencies first: pip install -r requirements.txt") from exc

    base_url = str(deepseek.get("base_url", "https://api.deepseek.com")).rstrip("/")
    url = f"{base_url}/chat/completions"
    model = deepseek.get("model", "deepseek-chat")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(profile, vacancy)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, ValueError, json.JSONDecodeError)),
        reraise=True,
    )
    async def call_deepseek() -> AnalysisResult:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            response_payload = response.json()

        content = response_payload["choices"][0]["message"]["content"]
        parsed = parse_json_response(content)
        return validate_analysis_result(parsed)

    return await call_deepseek()


async def analyze_many(
    vacancies: Iterable[dict[str, Any]],
    config: dict[str, Any],
    concurrency: int = 4,
) -> list[tuple[dict[str, Any], AnalysisResult | Exception]]:
    """Run analyze_vacancy concurrently with a semaphore."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _runner(vacancy: dict[str, Any]) -> tuple[dict[str, Any], AnalysisResult | Exception]:
        async with semaphore:
            try:
                result = await analyze_vacancy(vacancy, config)
                return vacancy, result
            except Exception as exc:  # noqa: BLE001 - returned to caller
                return vacancy, exc

    return await asyncio.gather(*(_runner(vac) for vac in vacancies))


def analysis_to_storage_dict(result: AnalysisResult | dict[str, Any]) -> dict[str, Any]:
    """Return a flat dict suitable for database.save_analysis.

    Stores fit_score as int 0..100 (existing schema) and the two component
    scores as floats 0..1.
    """
    if hasattr(result, "model_dump"):
        data = result.model_dump()  # type: ignore[union-attr]
    elif hasattr(result, "dict"):
        data = result.dict()  # type: ignore[union-attr]
    else:
        data = dict(result)

    stored = dict(data)
    stored["fit_score"] = int(round(_normalize_score(data.get("fit_score", 0)) * 100))
    stored["my_fit_for_them"] = round(_normalize_score(data.get("my_fit_for_them", 0)), 3)
    stored["their_fit_for_me"] = round(_normalize_score(data.get("their_fit_for_me", 0)), 3)
    stored["track"] = str(data.get("track", "other"))
    return stored


def _display_match(match: dict[str, Any]) -> dict[str, Any]:
    data = dict(match)
    data["fit_score"] = _normalize_score(match.get("fit_score"))
    data["my_fit_for_them"] = _normalize_score(match.get("my_fit_for_them"))
    data["their_fit_for_me"] = _normalize_score(match.get("their_fit_for_me"))
    data["reasons"] = _coerce_string_list(match.get("reasons", []))
    data["red_flags"] = _coerce_string_list(match.get("red_flags", []))
    data["recommendation"] = str(match.get("recommendation", "skip"))
    data["track"] = str(match.get("track") or "other")
    return data


def _format_vacancy_block(vacancy: dict[str, Any]) -> str:
    reasons = _format_list(vacancy.get("reasons", []))
    red_flags = _format_list(vacancy.get("red_flags", []))
    red_flags_block = f"\n**Red flags:**\n{red_flags}\n" if vacancy.get("red_flags") else ""
    track = vacancy.get("track") or "other"
    return f"""
## [{vacancy.get("title") or "Без названия"}]({vacancy.get("url") or ""})
**Компания:** {vacancy.get("company") or "не указана"} · **ЗП:** {vacancy.get("salary") or "не указана"} · **Локация:** {vacancy.get("location") or "не указана"} · **Трек:** {track}
**Scores:** fit={vacancy["fit_score"]:.2f}, я→них={vacancy["my_fit_for_them"]:.2f}, они→я={vacancy["their_fit_for_me"]:.2f}
**Рекомендация:** {vacancy["recommendation"]}

**Почему подходит:**
{reasons}
{red_flags_block}
---
""".strip()


def get_database_stats() -> dict[str, int]:
    database.init(database.DEFAULT_DB_PATH)
    with database.connect(database.DEFAULT_DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
        analyzed = conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
    return {"total": int(total), "analyzed": int(analyzed)}


def build_report_markdown(
    cfg: dict[str, Any],
    matches: list[dict[str, Any]],
    stats: dict[str, int],
    report_date: date | None = None,
) -> str:
    current_date = report_date or date.today()
    min_fit_score = float(cfg.get("analysis", {}).get("min_fit_score", 0.55))
    normalized = [_display_match(match) for match in matches]
    passed = [match for match in normalized if match["fit_score"] >= min_fit_score]
    average_passed = (
        sum(match["fit_score"] for match in passed) / len(passed) if passed else 0.0
    )

    green: list[dict[str, Any]] = []
    yellow: list[dict[str, Any]] = []
    red: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for match in normalized:
        if match["fit_score"] >= 0.75 and match["recommendation"] == "apply":
            green.append(match)
        elif match["fit_score"] >= min_fit_score:
            yellow.append(match)
        elif match["their_fit_for_me"] > 0.7 and match["my_fit_for_them"] < 0.5:
            red.append(match)
        else:
            rejected.append(match)

    track_counts = Counter(match.get("track", "other") for match in normalized)

    parts = [
        f"# 📊 HH.ru отчёт — {current_date.isoformat()}",
        "## Общая статистика",
        f"- Всего вакансий в БД: {stats.get('total', 0)}",
        f"- Проанализировано: {stats.get('analyzed', len(normalized))}",
        f"- Прошло минимальный порог ({min_fit_score}): {len(passed)}",
        f"- Средний fit_score среди прошедших порог: {average_passed:.2f}",
    ]
    if track_counts:
        track_lines = ", ".join(
            f"{track}={count}" for track, count in track_counts.most_common()
        )
        parts.append(f"- Распределение по трекам: {track_lines}")
    parts.append("---")

    sections = [
        ("## 🟢 Точно подавайся", green),
        ("## 🟡 Подумай", yellow),
        ("## 🔴 Интересно, но не мой уровень", red),
    ]
    for title, vacancies in sections:
        if not vacancies:
            continue
        parts.append(title)
        # Within each section group by track to make scanning easier.
        vacancies_by_track: dict[str, list[dict[str, Any]]] = {}
        for vacancy in vacancies:
            vacancies_by_track.setdefault(vacancy.get("track", "other"), []).append(vacancy)
        for track in sorted(vacancies_by_track):
            parts.append(f"### Трек: {track}")
            parts.extend(_format_vacancy_block(vacancy) for vacancy in vacancies_by_track[track])

    red_flag_counts = Counter(
        flag for vacancy in rejected for flag in vacancy.get("red_flags", [])
    )
    company_counts = Counter(
        vacancy.get("company") or "не указана" for vacancy in rejected
    )
    parts.append("## ⚫ Отсеяно")
    parts.append("### Топ-10 самых частых red_flags")
    if red_flag_counts:
        parts.extend(f"- {flag}: {count}" for flag, count in red_flag_counts.most_common(10))
    else:
        parts.append("- Нет данных")
    parts.append("### Топ-5 компаний с наибольшим числом отсеянных вакансий")
    if company_counts:
        parts.extend(f"- {company}: {count}" for company, count in company_counts.most_common(5))
    else:
        parts.append("- Нет данных")

    return "\n\n".join(parts) + "\n"


def generate_report(cfg: dict[str, Any]) -> str:
    matches = database.get_matches(min_score=0)
    stats = get_database_stats()
    current_date = date.today()
    markdown = build_report_markdown(cfg, matches, stats, current_date)
    output_path = BASE_DIR / "results" / f"report-{current_date.isoformat()}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return str(output_path)
