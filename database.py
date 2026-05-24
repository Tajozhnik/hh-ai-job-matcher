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


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _migrate_analysis_table(conn: sqlite3.Connection) -> None:
    """Ensure modern schema for the analysis table.

    Older versions stored my_fit_for_them and their_fit_for_me as TEXT and had
    no `track` column. Migrate to REAL and add `track` if needed.
    """
    columns = _existing_columns(conn, "analysis")
    if not columns:
        return  # table will be created by init() with the new schema

    needs_rebuild = False
    column_info = {row["name"]: row["type"] for row in conn.execute("PRAGMA table_info(analysis)")}
    if column_info.get("my_fit_for_them", "").upper() != "REAL":
        needs_rebuild = True
    if column_info.get("their_fit_for_me", "").upper() != "REAL":
        needs_rebuild = True

    if needs_rebuild:
        conn.executescript(
            """
            CREATE TABLE analysis_new (
                vacancy_id TEXT PRIMARY KEY,
                fit_score INTEGER NOT NULL,
                my_fit_for_them REAL NOT NULL,
                their_fit_for_me REAL NOT NULL,
                track TEXT NOT NULL DEFAULT 'other',
                reasons TEXT NOT NULL DEFAULT '[]',
                red_flags TEXT NOT NULL DEFAULT '[]',
                recommendation TEXT NOT NULL CHECK (
                    recommendation IN ('apply', 'maybe', 'skip')
                ),
                analyzed_at TEXT NOT NULL,
                FOREIGN KEY (vacancy_id) REFERENCES vacancies(id) ON DELETE CASCADE
            );
            INSERT INTO analysis_new (
                vacancy_id, fit_score, my_fit_for_them, their_fit_for_me,
                track, reasons, red_flags, recommendation, analyzed_at
            )
            SELECT
                vacancy_id,
                fit_score,
                CAST(my_fit_for_them AS REAL),
                CAST(their_fit_for_me AS REAL),
                'other',
                reasons,
                red_flags,
                recommendation,
                analyzed_at
            FROM analysis;
            DROP TABLE analysis;
            ALTER TABLE analysis_new RENAME TO analysis;
            """
        )
        return

    if "track" not in columns:
        conn.execute("ALTER TABLE analysis ADD COLUMN track TEXT NOT NULL DEFAULT 'other'")


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
                my_fit_for_them REAL NOT NULL,
                their_fit_for_me REAL NOT NULL,
                track TEXT NOT NULL DEFAULT 'other',
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
        _migrate_analysis_table(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_fit_score ON analysis(fit_score DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_recommendation ON analysis(recommendation)"
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
        "raw_html": vacancy.get("raw_html"),  # opt-in via scraper config
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
                raw_html = COALESCE(excluded.raw_html, vacancies.raw_html)
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
        SELECT v.id, v.title, v.company, v.url, v.salary, v.location,
               v.description, v.skills, v.published_at, v.scraped_at
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
        "my_fit_for_them": float(data["my_fit_for_them"]),
        "their_fit_for_me": float(data["their_fit_for_me"]),
        "track": str(data.get("track") or "other"),
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
                track, reasons, red_flags, recommendation, analyzed_at
            )
            VALUES (
                :vacancy_id, :fit_score, :my_fit_for_them, :their_fit_for_me,
                :track, :reasons, :red_flags, :recommendation, :analyzed_at
            )
            ON CONFLICT(vacancy_id) DO UPDATE SET
                fit_score = excluded.fit_score,
                my_fit_for_them = excluded.my_fit_for_them,
                their_fit_for_me = excluded.their_fit_for_me,
                track = excluded.track,
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
                a.fit_score, a.my_fit_for_them, a.their_fit_for_me, a.track,
                a.reasons, a.red_flags, a.recommendation, a.analyzed_at
            FROM vacancies v
            JOIN analysis a ON a.vacancy_id = v.id
            WHERE a.fit_score >= ?
            ORDER BY a.fit_score DESC, a.analyzed_at DESC
            """,
            (int(min_score),),
        ).fetchall()
    return [_decode_match(row) for row in rows]


def iter_all_vacancies(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> Iterator[dict[str, Any]]:
    """Yield every stored vacancy, regardless of analysis state."""
    init(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, company, url, salary, location,
                   description, skills, published_at, scraped_at
            FROM vacancies
            ORDER BY scraped_at DESC
            """
        ).fetchall()
    for row in rows:
        yield _decode_vacancy(row)


def find_duplicate_vacancy_id(
    title: str | None,
    company: str | None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> str | None:
    """Find an existing vacancy id with the same normalised title+company.

    HH often re-publishes the same job under different ids; this lets the scraper
    skip them without paying for another LLM analysis. SQLite's built-in LOWER()
    only handles ASCII, so we normalise on the Python side instead.
    """
    if not title or not company:
        return None
    init(db_path)
    norm_title = " ".join(title.casefold().split())
    norm_company = " ".join(company.casefold().split())
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, title, company FROM vacancies ORDER BY scraped_at DESC"
        ).fetchall()
    for row in rows:
        candidate_title = " ".join(str(row["title"] or "").casefold().split())
        candidate_company = " ".join(str(row["company"] or "").casefold().split())
        if candidate_title == norm_title and candidate_company == norm_company:
            return row["id"]
    return None


def clear_raw_html(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """Drop raw_html for every stored vacancy. Returns rows affected."""
    init(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE vacancies SET raw_html = NULL WHERE raw_html IS NOT NULL"
        )
        cleared = cursor.rowcount
    # VACUUM cannot run inside an explicit transaction, so issue it on a fresh
    # connection in autocommit mode.
    conn = sqlite3.connect(Path(db_path))
    try:
        conn.isolation_level = None
        conn.execute("VACUUM")
    finally:
        conn.close()
    return cleared


def database_stats(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Return a richer snapshot than get_database_stats: per-track and
    per-recommendation counts.
    """
    init(db_path)
    with connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
        analyzed = conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
        per_recommendation = {
            row["recommendation"]: int(row["c"])
            for row in conn.execute(
                "SELECT recommendation, COUNT(*) AS c FROM analysis GROUP BY recommendation"
            )
        }
        per_track = {
            row["track"]: int(row["c"])
            for row in conn.execute(
                "SELECT track, COUNT(*) AS c FROM analysis GROUP BY track ORDER BY c DESC"
            )
        }
        avg_fit = conn.execute(
            "SELECT AVG(fit_score) AS f FROM analysis"
        ).fetchone()["f"]
    return {
        "total": int(total),
        "analyzed": int(analyzed),
        "per_recommendation": per_recommendation,
        "per_track": per_track,
        "avg_fit_score": float(avg_fit / 100) if avg_fit else 0.0,
    }


def reset_analysis(
    vacancy_ids: list[str] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Delete analysis rows so they can be recomputed by --only analyze.

    If vacancy_ids is None, drop ALL existing analyses.
    Returns the number of rows deleted.
    """
    init(db_path)
    with connect(db_path) as conn:
        if vacancy_ids is None:
            cursor = conn.execute("DELETE FROM analysis")
            return cursor.rowcount
        deleted = 0
        for vacancy_id in vacancy_ids:
            cursor = conn.execute(
                "DELETE FROM analysis WHERE vacancy_id = ?", (str(vacancy_id),)
            )
            deleted += cursor.rowcount
        return deleted
