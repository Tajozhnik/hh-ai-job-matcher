from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import analyzer
import database


BASE_DIR = Path(__file__).resolve().parent


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
        choices=("scrape", "analyze", "export", "report"),
        help="Run only one stage: scrape, analyze, export, or report.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml.",
    )
    return parser


async def run_scrape(config: dict[str, Any]) -> None:
    import scraper

    scraped = await scraper.scrape(config, database.DEFAULT_DB_PATH)
    console.print(f"[green]Scraped new vacancies:[/green] {scraped}")


async def run_analyze(config: dict[str, Any]) -> None:
    batch_size = int(config.get("analysis", {}).get("batch_size", 10))
    vacancies = list(database.iter_unanalyzed(limit=batch_size))
    if not vacancies:
        console.print("[yellow]No unanalyzed vacancies found.[/yellow]")
        return

    for vacancy in vacancies:
        console.print(
            f"[cyan]Analyze vacancy {vacancy['id']}:[/cyan] {vacancy.get('title')}"
        )
        result = await analyzer.analyze_vacancy(vacancy, config)
        database.save_analysis(vacancy["id"], analyzer.analysis_to_storage_dict(result))
        console.print(
            f"[green]Saved analysis:[/green] {vacancy['id']} "
            f"score={result.fit_score} recommendation={result.recommendation}"
        )


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
        table.add_column("Recommendation")
        table.add_column("Title")
        table.add_column("Company")
        table.add_column("URL")
        for match in matches[:20]:
            table.add_row(
                str(match["fit_score"]),
                match["recommendation"],
                match["title"] or "",
                match["company"] or "",
                match["url"] or "",
            )
        console.print(table)
    except ImportError:
        for match in matches[:20]:
            console.print(
                f"{match['fit_score']} {match['recommendation']} "
                f"{match['title']} - {match['url']}"
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
    console.print(
        "📊 Отчёт: "
        f"apply={counts['apply']}, maybe={counts['maybe']}, skip={counts['skip']} "
        f"| сохранён в {display_path}"
    )


async def run(args: argparse.Namespace) -> None:
    config = analyzer.load_config(args.config)
    database.init(database.DEFAULT_DB_PATH)

    stages = [args.only] if args.only else ["scrape", "analyze", "export"]
    for stage in stages:
        if stage == "scrape":
            await run_scrape(config)
        elif stage == "analyze":
            await run_analyze(config)
        elif stage == "export":
            run_export(config)
        elif stage == "report":
            run_report(config)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
