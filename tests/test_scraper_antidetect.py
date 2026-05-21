import unittest
from pathlib import Path


class ScraperAntidetectTests(unittest.TestCase):
    def test_user_agent_pool_has_modern_browser_and_os_mix(self):
        import scraper

        joined = "\n".join(scraper.USER_AGENTS)

        self.assertGreaterEqual(len(scraper.USER_AGENTS), 10)
        self.assertIn("Chrome/", joined)
        self.assertIn("Firefox/", joined)
        self.assertIn("Edg/", joined)
        self.assertIn("Windows NT 10.0", joined)
        self.assertIn("Macintosh", joined)
        self.assertIn("X11; Linux x86_64", joined)

    def test_random_viewport_stays_in_desktop_range(self):
        import scraper

        for _ in range(100):
            viewport = scraper.random_viewport()

            self.assertGreaterEqual(viewport["width"], 1280)
            self.assertLessEqual(viewport["width"], 1920)
            self.assertGreaterEqual(viewport["height"], 720)
            self.assertLessEqual(viewport["height"], 1080)

    def test_launch_args_skip_empty_proxy_when_disabled(self):
        import scraper

        launch_args = scraper.build_launch_args(
            {"headless": True},
            {"enabled": False, "url": ""},
        )

        self.assertEqual(launch_args, {"headless": True})

    def test_launch_args_accept_residential_proxy_url(self):
        import scraper

        launch_args = scraper.build_launch_args(
            {"headless": False},
            {"enabled": True, "url": "http://user:pass@example.com:8080"},
        )

        self.assertEqual(
            launch_args["proxy"],
            {"server": "http://user:pass@example.com:8080"},
        )

    def test_http_delay_ranges_are_randomized_and_bounded(self):
        import scraper

        status = scraper.HttpThrottleState()
        status.record_429()
        first_min, first_max = scraper.http_backoff_range_seconds(429, status)
        self.assertEqual((first_min, first_max), (120, 240))

        for _ in range(10):
            status.record_429()
        capped_min, capped_max = scraper.http_backoff_range_seconds(429, status)
        self.assertEqual((capped_min, capped_max), (600, 1200))

        status.record_403()
        forbidden_min, forbidden_max = scraper.http_backoff_range_seconds(403, status)
        self.assertEqual((forbidden_min, forbidden_max), (300, 600))

    def test_error_screenshot_path_uses_logs_screenshots(self):
        import scraper

        path = scraper.make_error_screenshot_path()
        normalized = Path(path)

        self.assertEqual(normalized.parts[-3:-1], ("logs", "screenshots"))
        self.assertEqual(normalized.suffix, ".png")


if __name__ == "__main__":
    unittest.main()
