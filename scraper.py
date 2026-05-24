from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import database


BASE_DIR = Path(__file__).resolve().parent
LOG_SCREENSHOT_DIR = BASE_DIR / "logs" / "screenshots"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) "
    "Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:138.0) "
    "Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) "
    "Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
]

VACANCY_CARD_SELECTOR = ", ".join(
    [
        "[data-qa='vacancy-serp__vacancy']",
        "[data-qa='vacancy-serp__vacancy_standard']",
        "[data-qa='vacancy-serp__vacancy_premium']",
        ".vacancy-serp-item",
        ".serp-item",
    ]
)
SERP_TITLE_SELECTOR = "[data-qa='serp-item__title'], a[href*='/vacancy/']"
CAPTCHA_SELECTORS = [
    "iframe[src*='captcha']",
    "iframe[name*='captcha']",
    "iframe[title*='captcha']",
    "[data-qa*='captcha']",
    ".captcha",
]


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


@dataclass
class HttpThrottleState:
    consecutive_403: int = 0
    consecutive_429: int = 0

    def record_success(self) -> None:
        self.consecutive_403 = 0
        self.consecutive_429 = 0

    def record_403(self) -> None:
        self.consecutive_403 += 1
        self.consecutive_429 = 0

    def record_429(self) -> None:
        self.consecutive_429 += 1
        self.consecutive_403 = 0


def extract_vacancy_id(url: str) -> str | None:
    match = re.search(r"/vacancy/(\d+)", url)
    return match.group(1) if match else None


def with_page_param(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def parse_delay(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if not value:
        return default
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    return default


async def random_delay(delay_range: tuple[float, float]) -> None:
    await asyncio.sleep(random.uniform(*delay_range))


def random_viewport() -> dict[str, int]:
    return {
        "width": random.randint(1280, 1920),
        "height": random.randint(720, 1080),
    }


def build_launch_args(search: dict[str, Any], proxy: dict[str, Any]) -> dict[str, Any]:
    launch_args: dict[str, Any] = {"headless": bool(search.get("headless", False))}
    proxy_url = str(proxy.get("url", "")).strip()
    if proxy.get("enabled") and proxy_url:
        launch_args["proxy"] = {"server": proxy_url}
    return launch_args


def make_error_screenshot_path(timestamp: datetime | None = None) -> Path:
    value = timestamp or datetime.now(timezone.utc)
    filename = value.strftime("%Y%m%d-%H%M%S-%f") + ".png"
    return LOG_SCREENSHOT_DIR / filename


async def save_error_screenshot(page: Any, reason: str) -> Path | None:
    try:
        LOG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = make_error_screenshot_path()
        await page.screenshot(path=str(path), full_page=True)
        console.print(f"[yellow]Saved error screenshot ({reason}):[/yellow] {path}")
        return path
    except Exception as exc:
        console.print(f"[yellow]Could not save error screenshot ({reason}): {exc}[/yellow]")
        return None


def http_backoff_range_seconds(
    status_code: int,
    state: HttpThrottleState,
) -> tuple[float, float]:
    if status_code == 403:
        return 5 * 60, 10 * 60
    if status_code == 429:
        upper = min(2 * 60 * (2 ** max(state.consecutive_429, 1)), 20 * 60)
        lower = max(2 * 60, upper / 2)
        return lower, upper
    return 0, 0


async def human_delay_after_load() -> None:
    await random_delay((1, 3))


async def human_mouse_movements(page: Any) -> None:
    viewport = getattr(page, "viewport_size", None) or {"width": 1366, "height": 900}
    width = int(viewport.get("width") or 1366)
    height = int(viewport.get("height") or 900)
    for _ in range(random.randint(2, 5)):
        x = random.randint(20, max(21, width - 20))
        y = random.randint(20, max(21, height - 20))
        await page.mouse.move(x, y, steps=random.randint(6, 18))
        await random_delay((0.08, 0.35))


async def human_random_scroll(page: Any) -> None:
    vertical_delta = random.randint(180, 850)
    if random.random() < 0.2:
        vertical_delta = -random.randint(80, 320)
    await page.mouse.wheel(0, vertical_delta)
    await random_delay((0.4, 1.5))


async def handle_http_response(
    page: Any,
    response: Any,
    state: HttpThrottleState,
) -> None:
    if response is None:
        return

    status = getattr(response, "status", None)
    if status == 403:
        state.record_403()
        await save_error_screenshot(page, "http-403")
        if state.consecutive_403 >= 3:
            raise RuntimeError(
                "Stopped after 3 consecutive HTTP 403 responses from hh.ru. "
                "Check proxy quality, captcha state, and browser fingerprint settings."
            )
        delay_range = http_backoff_range_seconds(403, state)
        console.print(
            "[red]hh.ru returned HTTP 403. "
            f"Pausing for {delay_range[0] / 60:.1f}-{delay_range[1] / 60:.1f} minutes.[/red]"
        )
        await random_delay(delay_range)
        return

    if status == 429:
        state.record_429()
        await save_error_screenshot(page, "http-429")
        delay_range = http_backoff_range_seconds(429, state)
        console.print(
            "[yellow]hh.ru returned HTTP 429. "
            f"Backoff for {delay_range[0] / 60:.1f}-{delay_range[1] / 60:.1f} minutes.[/yellow]"
        )
        await random_delay(delay_range)
        return

    state.record_success()


async def goto_with_http_handling(
    page: Any,
    url: str,
    state: HttpThrottleState,
    max_status_retries: int = 3,
) -> Any:
    last_response = None
    for attempt in range(1, max_status_retries + 1):
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await save_error_screenshot(page, "goto-error")
            raise

        last_response = response
        status = getattr(response, "status", None)
        if status not in (403, 429):
            await handle_http_response(page, response, state)
            await human_delay_after_load()
            return response

        await handle_http_response(page, response, state)
        console.print(
            f"[yellow]Retrying {url} after HTTP {status} "
            f"({attempt}/{max_status_retries}).[/yellow]"
        )

    raise RuntimeError(
        f"Stopped after {max_status_retries} HTTP retry attempts for {url}. "
        f"Last status: {getattr(last_response, 'status', 'unknown')}."
    )


async def text_or_none(locator: Any, timeout: int = 2500) -> str | None:
    try:
        if await locator.count() == 0:
            return None
        text = await locator.first.inner_text(timeout=timeout)
        return " ".join(text.split()) or None
    except Exception:
        return None


async def attr_or_none(locator: Any, attr: str, timeout: int = 2500) -> str | None:
    try:
        if await locator.count() == 0:
            return None
        value = await locator.first.get_attribute(attr, timeout=timeout)
        return value or None
    except Exception:
        return None


async def all_texts(locator: Any) -> list[str]:
    values: list[str] = []
    try:
        count = await locator.count()
        for index in range(count):
            text = await locator.nth(index).inner_text(timeout=2500)
            cleaned = " ".join(text.split())
            if cleaned:
                values.append(cleaned)
    except Exception:
        return values
    return values


async def detect_captcha(page: Any) -> bool:
    for selector in CAPTCHA_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return any("captcha" in (frame.url or "").lower() for frame in page.frames)


async def pause_for_captcha(page: Any, config: dict[str, Any]) -> None:
    if not await detect_captcha(page):
        return
    base_seconds = int(config.get("search", {}).get("captcha_pause_seconds", 60))
    delay_range = (base_seconds, base_seconds + max(5, base_seconds * 0.25))
    console.print(
        "[yellow]Captcha detected on "
        f"{page.url}. Pausing for {delay_range[0]:.0f}-{delay_range[1]:.0f} seconds.[/yellow]"
    )
    await random_delay(delay_range)


async def collect_vacancy_links(page: Any) -> list[dict[str, str]]:
    items: dict[str, dict[str, str]] = {}
    cards = page.locator(VACANCY_CARD_SELECTOR)
    card_count = await cards.count()

    for index in range(card_count):
        card = cards.nth(index)
        title_link = card.locator(SERP_TITLE_SELECTOR)
        href = await attr_or_none(title_link, "href")
        if not href:
            continue
        vacancy_id = extract_vacancy_id(href)
        if not vacancy_id:
            continue
        title = await text_or_none(title_link) or ""
        items[vacancy_id] = {"id": vacancy_id, "url": href, "title": title}

    if items:
        return list(items.values())

    links = page.locator(SERP_TITLE_SELECTOR)
    link_count = await links.count()
    for index in range(link_count):
        link = links.nth(index)
        href = await link.get_attribute("href")
        if not href:
            continue
        vacancy_id = extract_vacancy_id(href)
        if not vacancy_id:
            continue
        title = " ".join((await link.inner_text()).split())
        items[vacancy_id] = {"id": vacancy_id, "url": href, "title": title}

    return list(items.values())


async def scrape_vacancy_detail(
    context: Any,
    item: dict[str, str],
    config: dict[str, Any],
    stealth_async: Any,
    throttle_state: HttpThrottleState,
) -> dict[str, Any]:
    page = await context.new_page()
    await stealth_async(page)
    save_raw_html = bool(config.get("search", {}).get("save_raw_html", False))
    try:
        await goto_with_http_handling(page, item["url"], throttle_state)
        await pause_for_captcha(page, config)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        title = await text_or_none(page.locator("[data-qa='vacancy-title'], h1"))
        company = await text_or_none(
            page.locator(
                "[data-qa='vacancy-company-name'], "
                "[data-qa='vacancy-company-name'] a, "
                ".vacancy-company-name"
            )
        )
        salary = await text_or_none(
            page.locator("[data-qa='vacancy-salary'], [data-qa='vacancy-compensation']")
        )
        location = await text_or_none(
            page.locator(
                "[data-qa='vacancy-view-location'], "
                "[data-qa='vacancy-view-raw-address'], "
                "[data-qa='vacancy-view-location-source'], "
                "[data-qa='vacancy-view-address']"
            )
        )
        description = await text_or_none(
            page.locator("[data-qa='vacancy-description'], .vacancy-description"),
            timeout=5000,
        )
        skills = await all_texts(page.locator("[data-qa='skills-element']"))
        published_at = await text_or_none(
            page.locator(
                "[data-qa='vacancy-creation-time'], "
                "[data-qa='vacancy-view-published-at']"
            )
        )
        raw_html = await page.content() if save_raw_html else None
        vacancy_id = item["id"]

        return {
            "id": vacancy_id,
            "title": title or item.get("title") or "Untitled vacancy",
            "company": company,
            "url": item["url"].split("?")[0],
            "salary": salary,
            "location": location,
            "description": description,
            "skills": skills,
            "published_at": published_at,
            "raw_html": raw_html,
        }
    except Exception:
        await save_error_screenshot(page, "vacancy-detail-error")
        raise
    finally:
        await page.close()


async def scrape(
    config: dict[str, Any] | None = None,
    db_path: str | Path = database.DEFAULT_DB_PATH,
) -> int:
    if config is None:
        from analyzer import load_config

        config = load_config("config.yaml")

    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import stealth_async
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Install Playwright dependencies and browsers first: "
            "pip install -r requirements.txt && playwright install chromium"
        ) from exc

    search = config.get("search", {})
    proxy = config.get("proxy", {})
    # Support both single `url` and a list `urls` for multi-track scraping.
    search_urls: list[str] = []
    if search.get("urls"):
        search_urls = [str(u) for u in search["urls"] if u]
    elif search.get("url"):
        search_urls = [str(search["url"])]
    if not search_urls:
        raise RuntimeError("config.search.url or config.search.urls must be set")

    max_pages = int(search.get("max_pages", 1))
    page_delay = parse_delay(search.get("delay_between_pages"), (2, 7))
    vacancy_delay = parse_delay(search.get("delay_between_vacancy"), (2, 7))
    scraped_count = 0
    throttle_state = HttpThrottleState()

    database.init(db_path)

    launch_args = build_launch_args(search, proxy)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport=random_viewport(),
        )
        page = await context.new_page()
        await stealth_async(page)

        try:
            for search_url in search_urls:
                console.print(f"[magenta]=== Scraping query: {search_url}[/magenta]")
                for page_number in range(max_pages):
                    page_url = with_page_param(search_url, page_number)
                    console.print(f"[cyan]Open search page:[/cyan] {page_url}")
                    await goto_with_http_handling(page, page_url, throttle_state)
                    await pause_for_captcha(page, config)
                    await human_mouse_movements(page)
                    vacancies = await collect_vacancy_links(page)
                    console.print(
                        f"[cyan]Found {len(vacancies)} vacancy links "
                        f"on page {page_number}.[/cyan]"
                    )

                    if not vacancies:
                        # Empty page is a hint that we have walked past the
                        # last page of results for this query — move on.
                        break

                    for item in vacancies:
                        vacancy_id = item["id"]
                        if database.vacancy_exists(vacancy_id, db_path):
                            console.print(f"[dim]Skip existing vacancy {vacancy_id}[/dim]")
                            continue

                        await human_random_scroll(page)
                        await human_mouse_movements(page)
                        vacancy = await scrape_vacancy_detail(
                            context,
                            item,
                            config,
                            stealth_async,
                            throttle_state,
                        )

                        # Cross-id deduplication: HH frequently reposts the same
                        # job under different ids. If we already have one with
                        # the same title+company, keep the original analysis
                        # and skip the duplicate.
                        duplicate_of = database.find_duplicate_vacancy_id(
                            vacancy.get("title"),
                            vacancy.get("company"),
                            db_path,
                        )
                        if duplicate_of and duplicate_of != vacancy["id"]:
                            console.print(
                                f"[dim]Skip duplicate of {duplicate_of}: "
                                f"{vacancy['title']} @ {vacancy.get('company')}[/dim]"
                            )
                            continue

                        database.upsert_vacancy(vacancy, db_path)
                        scraped_count += 1
                        console.print(
                            f"[green]Saved vacancy {vacancy['id']}:[/green] "
                            f"{vacancy['title']}"
                        )
                        await random_delay(vacancy_delay)

                    await random_delay(page_delay)
        finally:
            await context.close()
            await browser.close()

    return scraped_count
