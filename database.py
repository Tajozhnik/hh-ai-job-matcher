from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "results" / "hh_scraper.sqlite3"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vacancies (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT,
                url TEXT NOT NULL,
                salary TEXT,
                location TEXT,
                description TEXT,
                skills TEXT NOT NULL DEFAULT '[]',
                published_at TEXT,
                scraped_at TEXT NOT NULL,
                raw_html TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis (
                vacancy_id TEXT PRIMARY KEY,
                fit_score INTEGER NOT NULL,
                my_fit_for_them TEXT NOT NULL,
                their_fit_for_me TEXT NOT NULL,
                reasons TEXT NOT NULL DEFAULT '[]',
                red_flags TEXT NOT NULL DEFAULT '[]',
                recommendation TEXT NOT NULL CHECK (
                    recommendation IN ('apply', 'maybe', 'skip')
                ),
                analyzed_at TEXT NOT NULL,
                FOREIGN KEY (vacancy_id)
                    REFERENCES vacancies(id)
                    ON DELETE CASCADE
            )
            """
        )
    return Path(db_path)


def _json_dumps(value: Any) -> str:
    if value is None:
        value = []
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None) -> Any:
    if not value:
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return []


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def _decode_vacancy(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["skills"] = _json_loads(data.get("skills"))
    return data


def _decode_match(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["skills"] = _json_loads(data.get("skills"))
    data["reasons"] = _json_loads(data.get("reasons"))
    data["red_flags"] = _json_loads(data.get("red_flags"))
    return data


def upsert_vacancy(vacancy: dict[str, Any], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    init(db_path)
    data = {
        "id": str(vacancy["id"]),
        "title": vacancy.get("title") or "Untitled vacancy",
        "company": vacancy.get("company"),
        "url": vacancy.get("url") or f"https://hh.ru/vacancy/{vacancy['id']}",
        "salary": vacancy.get("salary"),
        "location": vacancy.get("location"),
        "description": vacancy.get("description"),
        "skills": _json_dumps(vacancy.get("skills", [])),
        "published_at": vacancy.get("published_at"),
        "scraped_at": vacancy.get("scraped_at") or utc_now_iso(),
        "raw_html": vacancy.get("raw_html"),
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO vacancies (
                id, title, company, url, salary, location, description,
                skills, published_at, scraped_at, raw_html
            )
            VALUES (
                :id, :title, :company, :url, :salary, :location, :description,
                :skills, :published_at, :scraped_at, :raw_html
            )
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                url = excluded.url,
                salary = excluded.salary,
                location = excluded.location,
                description = excluded.description,
                skills = excluded.skills,
                published_at = excluded.published_at,
                scraped_at = excluded.scraped_at,
                raw_html = excluded.raw_html
            """,
            data,
        )


def vacancy_exists(vacancy_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    init(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM vacancies WHERE id = ? LIMIT 1",
            (str(vacancy_id),),
        ).fetchone()
    return row is not None


def iter_unanalyzed(
    limit: int | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> Iterator[dict[str, Any]]:
    init(db_path)
    params: tuple[Any, ...] = ()
    query = """
        SELECT v.*
        FROM vacancies v
        LEFT JOIN analysis a ON a.vacancy_id = v.id
        WHERE a.vacancy_id IS NULL
        ORDER BY v.scraped_at DESC
    """
    if limit is not None:
        query += " LIMIT ?"
        params = (int(limit),)

    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    for row in rows:
        yield _decode_vacancy(row)


def save_analysis(
    vacancy_id: str,
    analysis: dict[str, Any] | Any,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    init(db_path)
    data = _as_dict(analysis)
    params = {
        "vacancy_id": str(vacancy_id),
        "fit_score": int(data["fit_score"]),
        "my_fit_for_them": data["my_fit_for_them"],
        "their_fit_for_me": data["their_fit_for_me"],
        "reasons": _json_dumps(data.get("reasons", [])),
        "red_flags": _json_dumps(data.get("red_flags", [])),
        "recommendation": data["recommendation"],
        "analyzed_at": data.get("analyzed_at") or utc_now_iso(),
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO analysis (
                vacancy_id, fit_score, my_fit_for_them, their_fit_for_me,
                reasons, red_flags, recommendation, analyzed_at
            )
            VALUES (
                :vacancy_id, :fit_score, :my_fit_for_them, :their_fit_for_me,
                :reasons, :red_flags, :recommendation, :analyzed_at
            )
            ON CONFLICT(vacancy_id) DO UPDATE SET
                fit_score = excluded.fit_score,
                my_fit_for_them = excluded.my_fit_for_them,
                their_fit_for_me = excluded.their_fit_for_me,
                reasons = excluded.reasons,
                red_flags = excluded.red_flags,
                recommendation = excluded.recommendation,
                analyzed_at = excluded.analyzed_at
            """,
            params,
        )


def get_matches(
    min_score: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                v.id, v.title, v.company, v.url, v.salary, v.location,
                v.description, v.skills, v.published_at, v.scraped_at,
                a.fit_score, a.my_fit_for_them, a.their_fit_for_me,
                a.reasons, a.red_flags, a.recommendation, a.analyzed_at
            FROM vacancies v
            JOIN analysis a ON a.vacancy_id = v.id
            WHERE a.fit_score >= ?
            ORDER BY a.fit_score DESC, a.analyzed_at DESC
            """,
            (int(min_score),),
        ).fetchall()
    return [_decode_match(row) for row in rows]
