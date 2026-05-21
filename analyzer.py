from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

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
сильными пет-проектами и хакатонами. Считай пет-проекты полноценным релевантным
опытом, особенно для Python backend, AI automation, Telegram-ботов, API, LLM и RAG.

Верни СТРОГО JSON без Markdown, комментариев и текста вокруг объекта.
Формат ответа НЕ МЕНЯТЬ:
{
  "fit_score": float 0..1,
  "my_fit_for_them": float 0..1,
  "their_fit_for_me": float 0..1,
  "reasons": ["конкретные причины"],
  "red_flags": ["конкретные риски"],
  "recommendation": "apply" | "maybe" | "skip"
}

Правила оценки:
- Не отсеивай автоматически вакансии с требованием "опыт 1-2 года": джун со
  strong pet projects может подходить.
- Добавляй в reasons junior-friendly признаки: "стажировка", "обучение",
  "наставник", "ментор", "без опыта", "будем учить", "intern", "junior",
  "graduate", развитие, курсы, менторство, готовность команды растить специалиста.
- Критичные red flags для джуна: "опыт от 3 лет строго" или "3 года" как жесткий
  коммерческий must-have, "самостоятельно с первого дня", "без обучения",
  "сразу в бой без ментора", Senior/Middle без упоминания junior/intern,
  основной стек не Python (Java/C++/PHP/1C), чистый Data Science без engineering
  задач, зарплата ниже 40 000 RUB для стажировки или явно "до 30к".

my_fit_for_them оценивает, насколько кандидат подходит работодателю:
- требуемые скиллы есть в skills или pet_projects хотя бы на уровне pet/учебной практики;
- опыт не требуется или указан 1-2 года, что можно частично закрыть пет-проектами;
- нет жестких must-have, которых у кандидата совсем нет, например Go 5 лет.

their_fit_for_me оценивает, насколько вакансия подходит кандидату:
- зарплата >= 40 000 RUB или не указана, но не ниже минимума;
- локация remote/moscow/hybrid;
- задачи связаны с Python/backend/AI/ботами/API/LLM/RAG;
- нет hard_no из профиля;
- есть обучение, наставник/ментор или другие junior-friendly признаки.
""".strip()

RECOMMENDATIONS = {"apply", "maybe", "skip"}
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


if PYDANTIC_AVAILABLE:

    class AnalysisResult(BaseModel):
        fit_score: float = Field(ge=0, le=1)  # type: ignore[misc]
        my_fit_for_them: float = Field(ge=0, le=1)  # type: ignore[misc]
        their_fit_for_me: float = Field(ge=0, le=1)  # type: ignore[misc]
        reasons: list[str]
        red_flags: list[str]
        recommendation: Literal["apply", "maybe", "skip"]

else:

    @dataclass
    class AnalysisResult:  # type: ignore[no-redef]
        fit_score: float
        my_fit_for_them: float
        their_fit_for_me: float
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
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1:
        score = score / 100
    return max(0.0, min(score, 1.0))


def _storage_score(value: Any) -> int:
    score = _normalize_score(value)
    return int(round(score * 100))


def validate_analysis_result(data: dict[str, Any]) -> AnalysisResult:
    if PYDANTIC_AVAILABLE:
        if hasattr(AnalysisResult, "model_validate"):
            return AnalysisResult.model_validate(data)  # type: ignore[attr-defined]
        return AnalysisResult.parse_obj(data)  # type: ignore[attr-defined]

    recommendation = str(data["recommendation"])
    if recommendation not in RECOMMENDATIONS:
        raise ValueError("recommendation must be apply, maybe, or skip")
    return AnalysisResult(
        fit_score=_coerce_score(data["fit_score"]),
        my_fit_for_them=_coerce_score(data["my_fit_for_them"]),
        their_fit_for_me=_coerce_score(data["their_fit_for_me"]),
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
{vacancy.get("description", "не указано")}
""".strip()


def build_user_prompt(profile: dict[str, Any], vacancy: dict[str, Any]) -> str:
    return _build_user_message(profile, vacancy)


async def analyze_vacancy(
    vacancy: dict[str, Any],
    config: dict[str, Any] | str = "config.yaml",
) -> AnalysisResult:
    if isinstance(config, str):
        config = load_config(config)

    deepseek = config.get("deepseek", {})
    profile = config.get("profile", {})
    api_key = deepseek.get("api_key")
    if not api_key:
        raise RuntimeError(
            "DeepSeek API key is empty. Set the environment variable used in config.yaml."
        )

    try:
        import httpx
        from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
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
        "temperature": 0.1,
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


def analysis_to_storage_dict(result: AnalysisResult | dict[str, Any]) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        data = result.model_dump()  # type: ignore[union-attr]
    elif hasattr(result, "dict"):
        data = result.dict()  # type: ignore[union-attr]
    else:
        data = dict(result)

    stored = dict(data)
    stored["fit_score"] = _storage_score(data.get("fit_score", 0))
    return stored


def _display_match(match: dict[str, Any]) -> dict[str, Any]:
    data = dict(match)
    data["fit_score"] = _normalize_score(match.get("fit_score"))
    data["my_fit_for_them"] = _normalize_score(match.get("my_fit_for_them"))
    data["their_fit_for_me"] = _normalize_score(match.get("their_fit_for_me"))
    data["reasons"] = _coerce_string_list(match.get("reasons", []))
    data["red_flags"] = _coerce_string_list(match.get("red_flags", []))
    data["recommendation"] = str(match.get("recommendation", "skip"))
    return data


def _format_vacancy_block(vacancy: dict[str, Any]) -> str:
    reasons = _format_list(vacancy.get("reasons", []))
    red_flags = _format_list(vacancy.get("red_flags", []))
    red_flags_block = f"\n**Red flags:**\n{red_flags}\n" if vacancy.get("red_flags") else ""
    return f"""
## [{vacancy.get("title") or "Без названия"}]({vacancy.get("url") or ""})
**Компания:** {vacancy.get("company") or "не указана"} · **ЗП:** {vacancy.get("salary") or "не указана"} · **Локация:** {vacancy.get("location") or "не указана"}
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

    parts = [
        f"# 📊 HH.ru отчёт — {current_date.isoformat()}",
        "## Общая статистика",
        f"- Всего вакансий в БД: {stats.get('total', 0)}",
        f"- Проанализировано: {stats.get('analyzed', len(normalized))}",
        f"- Прошло минимальный порог ({min_fit_score}): {len(passed)}",
        f"- Средний fit_score среди прошедших порог: {average_passed:.2f}",
        "---",
    ]

    sections = [
        ("## 🟢 Точно подавайся", green),
        ("## 🟡 Подумай", yellow),
        ("## 🔴 Интересно, но не мой уровень", red),
    ]
    for title, vacancies in sections:
        if not vacancies:
            continue
        parts.append(title)
        parts.extend(_format_vacancy_block(vacancy) for vacancy in vacancies)

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
