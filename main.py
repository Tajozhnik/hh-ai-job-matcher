from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import analyzer
import database


BASE_DIR = Path(__file__).resolve().parent

STAGES = ("scrape", "analyze", "reanalyze", "export", "report", "stats", "purge-html")


def _console():
    try:
        from rich.console import Console

        return Console()
    except ImportError:  # pragma: no cover
        class PlainConsole:
            def print(self, *args: Any, **_: Any) -> None:
                print(*args)

        return PlainConsole()


console = _console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape hh.ru vacancies and analyze fit via DeepSeek API."
    )
    parser.add_argument(
        "--only",
        help=(
            "Run only one stage (or comma-separated list). "
            f"Available: {', '.join(STAGES)}."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml.",
    )
    return parser


def _parse_stages(arg: str | None) -> list[str]:
    if not arg:
        # Default end-to-end pipeline used most frequently.
        return ["scrape", "analyze", "export", "report"]
    requested = [stage.strip() for stage in arg.split(",") if stage.strip()]
    invalid = [stage for stage in requested if stage not in STAGES]
    if invalid:
        raise SystemExit(
            f"Unknown stage(s): {', '.join(invalid)}. Allowed: {', '.join(STAGES)}"
        )
    return requested


async def run_scrape(config: dict[str, Any]) -> None:
    import scraper

    scraped = await scraper.scrape(config, database.DEFAULT_DB_PATH)
    console.print(f"[green]Scraped new vacancies:[/green] {scraped}")


async def _run_analyze_batch(
    config: dict[str, Any], vacancies: list[dict[str, Any]]
) -> None:
    if not vacancies:
        console.print("[yellow]No vacancies to analyze.[/yellow]")
        return

    concurrency = int(config.get("analysis", {}).get("concurrency", 4))
    console.print(
        f"[cyan]Analyzing {len(vacancies)} vacancies "
        f"with concurrency={concurrency}…[/cyan]"
    )
    pairs = await analyzer.analyze_many(vacancies, config, concurrency=concurrency)
    saved = 0
    failed = 0
    for vacancy, outcome in pairs:
        if isinstance(outcome, Exception):
            failed += 1
            console.print(
                f"[red]Failed {vacancy['id']} ({vacancy.get('title')}): {outcome}[/red]"
            )
            continue
        database.save_analysis(vacancy["id"], analyzer.analysis_to_storage_dict(outcome))
        saved += 1
        console.print(
            f"[green]Saved {vacancy['id']} fit={outcome.fit_score:.2f} "
            f"track={outcome.track} rec={outcome.recommendation}[/green] "
            f"— {vacancy.get('title')}"
        )
    console.print(
        f"[green]Done. Saved {saved}.[/green]"
        + (f" [yellow]Failed {failed}.[/yellow]" if failed else "")
    )


async def run_analyze(config: dict[str, Any]) -> None:
    batch_size = int(config.get("analysis", {}).get("batch_size", 10))
    vacancies = list(database.iter_unanalyzed(limit=batch_size))
    if not vacancies:
        console.print("[yellow]No unanalyzed vacancies found.[/yellow]")
        return
    await _run_analyze_batch(config, vacancies)


async def run_reanalyze(config: dict[str, Any]) -> None:
    """Recompute analysis for ALL stored vacancies under the current logic.

    Useful after prompt or scoring changes when the database already holds
    legacy analyses.
    """
    deleted = database.reset_analysis()
    console.print(f"[yellow]Cleared {deleted} previous analyses.[/yellow]")

    batch_size = int(config.get("analysis", {}).get("batch_size", 10))
    all_vacancies = list(database.iter_all_vacancies())
    if not all_vacancies:
        console.print("[yellow]No vacancies in DB.[/yellow]")
        return

    console.print(
        f"[cyan]Reanalyzing {len(all_vacancies)} vacancies "
        f"in chunks of {batch_size}…[/cyan]"
    )
    for chunk_start in range(0, len(all_vacancies), batch_size):
        chunk = all_vacancies[chunk_start : chunk_start + batch_size]
        console.print(
            f"[cyan]Chunk {chunk_start // batch_size + 1}: "
            f"{len(chunk)} vacancies[/cyan]"
        )
        await _run_analyze_batch(config, chunk)


def run_export(config: dict[str, Any]) -> None:
    analysis_config = config.get("analysis", {})
    min_score = float(analysis_config.get("min_fit_score", 0.55))
    storage_min_score = int(round(min_score * 100)) if min_score <= 1 else int(min_score)
    output_path = BASE_DIR / analysis_config.get("output", "results/matches.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    matches = database.get_matches(storage_min_score)
    for match in matches:
        if match.get("fit_score", 0) > 1:
            match["fit_score"] = float(match["fit_score"]) / 100
    output_path.write_text(
        json.dumps(matches, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(
        f"[green]Exported {len(matches)} matches >= {min_score} to:[/green] "
        f"{output_path}"
    )

    try:
        from rich.table import Table

        table = Table(title=f"Matches with fit_score >= {min_score}")
        table.add_column("Score", justify="right")
        table.add_column("Rec.")
        table.add_column("Track")
        table.add_column("Title")
        table.add_column("Company")
        table.add_column("URL")
        for match in matches[:20]:
            table.add_row(
                f"{match['fit_score']:.2f}",
                str(match.get("recommendation", "")),
                str(match.get("track", "other")),
                match.get("title") or "",
                match.get("company") or "",
                match.get("url") or "",
            )
        console.print(table)
    except ImportError:
        for match in matches[:20]:
            console.print(
                f"{match['fit_score']:.2f} {match.get('recommendation','')} "
                f"[{match.get('track','other')}] {match.get('title','')} "
                f"- {match.get('url','')}"
            )


def run_report(config: dict[str, Any]) -> None:
    report_path = Path(analyzer.generate_report(config))
    matches = database.get_matches(0)
    counts = {"apply": 0, "maybe": 0, "skip": 0}
    for match in matches:
        recommendation = str(match.get("recommendation", "skip"))
        if recommendation in counts:
            counts[recommendation] += 1
    try:
        display_path = report_path.relative_to(BASE_DIR)
    except ValueError:
        display_path = report_path
    summary = (
        f"Report: apply={counts['apply']}, maybe={counts['maybe']}, "
        f"skip={counts['skip']} | saved to {display_path}"
    )
    try:
        console.print(summary)
    except UnicodeEncodeError:
        # Legacy Windows code pages can't render some Unicode glyphs.
        print(summary.encode("ascii", "replace").decode("ascii"))


def run_stats(_: dict[str, Any]) -> None:
    stats = database.database_stats()
    console.print("[bold]База:[/bold]")
    console.print(f"  всего вакансий: {stats['total']}")
    console.print(f"  проанализировано: {stats['analyzed']}")
    console.print(f"  средний fit_score: {stats['avg_fit_score']:.2f}")
    if stats["per_recommendation"]:
        console.print("[bold]По рекомендациям:[/bold]")
        for rec, count in stats["per_recommendation"].items():
            console.print(f"  {rec}: {count}")
    if stats["per_track"]:
        console.print("[bold]По трекам:[/bold]")
        for track, count in stats["per_track"].items():
            console.print(f"  {track}: {count}")


def run_purge_html(_: dict[str, Any]) -> None:
    cleared = database.clear_raw_html()
    console.print(
        f"[green]Cleared raw_html for {cleared} vacancies. "
        f"Database vacuumed.[/green]"
    )


async def run(args: argparse.Namespace) -> None:
    config = analyzer.load_config(args.config)
    database.init(database.DEFAULT_DB_PATH)

    stages = _parse_stages(args.only)
    for stage in stages:
        if stage == "scrape":
            await run_scrape(config)
        elif stage == "analyze":
            await run_analyze(config)
        elif stage == "reanalyze":
            await run_reanalyze(config)
        elif stage == "export":
            run_export(config)
        elif stage == "report":
            run_report(config)
        elif stage == "stats":
            run_stats(config)
        elif stage == "purge-html":
            run_purge_html(config)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
