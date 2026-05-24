import tempfile
import unittest
from pathlib import Path


class DatabaseTests(unittest.TestCase):
    def test_vacancy_lifecycle_and_matches(self):
        import database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            database.init(db_path)

            vacancy = {
                "id": "12345",
                "title": "Junior Python Developer",
                "company": "Example",
                "url": "https://hh.ru/vacancy/12345",
                "salary": "100000 руб.",
                "location": "Moscow",
                "description": "Python APIs",
                "skills": ["Python", "SQL"],
                "published_at": "today",
                "raw_html": "<html></html>",
            }

            database.upsert_vacancy(vacancy, db_path)

            self.assertTrue(database.vacancy_exists("12345", db_path))
            self.assertEqual(len(list(database.iter_unanalyzed(db_path=db_path))), 1)

            database.save_analysis(
                "12345",
                {
                    "fit_score": 91,
                    "my_fit_for_them": 0.91,
                    "their_fit_for_me": 0.95,
                    "track": "backend",
                    "reasons": ["Python", "junior"],
                    "red_flags": [],
                    "recommendation": "apply",
                },
                db_path,
            )

            self.assertEqual(list(database.iter_unanalyzed(db_path=db_path)), [])
            matches = database.get_matches(80, db_path)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["id"], "12345")
            self.assertEqual(matches[0]["skills"], ["Python", "SQL"])
            self.assertEqual(matches[0]["reasons"], ["Python", "junior"])
            self.assertEqual(matches[0]["track"], "backend")
            self.assertAlmostEqual(matches[0]["my_fit_for_them"], 0.91, places=3)

    def test_find_duplicate_vacancy_id_matches_normalised_strings(self):
        import database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            database.init(db_path)
            database.upsert_vacancy(
                {
                    "id": "1",
                    "title": "  Python Стажёр  ",
                    "company": "Ozon Tech",
                    "url": "https://hh.ru/vacancy/1",
                    "skills": [],
                },
                db_path,
            )
            duplicate = database.find_duplicate_vacancy_id(
                "python стажёр", "ozon tech", db_path
            )
            self.assertEqual(duplicate, "1")
            self.assertIsNone(
                database.find_duplicate_vacancy_id("Other title", "Ozon Tech", db_path)
            )
            self.assertIsNone(database.find_duplicate_vacancy_id(None, "Ozon Tech", db_path))

    def test_clear_raw_html_drops_payload(self):
        import database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            database.init(db_path)
            database.upsert_vacancy(
                {
                    "id": "1",
                    "title": "Python intern",
                    "company": "Example",
                    "url": "https://hh.ru/vacancy/1",
                    "skills": [],
                    "raw_html": "<html>" + "x" * 10000 + "</html>",
                },
                db_path,
            )
            cleared = database.clear_raw_html(db_path)
            self.assertEqual(cleared, 1)
            with database.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT raw_html FROM vacancies WHERE id = '1'"
                ).fetchone()
            self.assertIsNone(row["raw_html"])

    def test_database_stats_returns_per_track_and_recommendation(self):
        import database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            database.init(db_path)
            for vacancy_id, track, rec, score in (
                ("1", "backend", "apply", 90),
                ("2", "ai_automation", "apply", 80),
                ("3", "qa", "skip", 30),
            ):
                database.upsert_vacancy(
                    {
                        "id": vacancy_id,
                        "title": f"V{vacancy_id}",
                        "company": "Co",
                        "url": f"https://hh.ru/vacancy/{vacancy_id}",
                        "skills": [],
                    },
                    db_path,
                )
                database.save_analysis(
                    vacancy_id,
                    {
                        "fit_score": score,
                        "my_fit_for_them": score / 100,
                        "their_fit_for_me": score / 100,
                        "track": track,
                        "reasons": [],
                        "red_flags": [],
                        "recommendation": rec,
                    },
                    db_path,
                )

            stats = database.database_stats(db_path)
            self.assertEqual(stats["total"], 3)
            self.assertEqual(stats["analyzed"], 3)
            self.assertEqual(stats["per_recommendation"]["apply"], 2)
            self.assertEqual(stats["per_recommendation"]["skip"], 1)
            self.assertEqual(stats["per_track"]["backend"], 1)
            self.assertGreater(stats["avg_fit_score"], 0)

    def test_reset_analysis_clears_rows(self):
        import database

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            database.init(db_path)
            database.upsert_vacancy(
                {
                    "id": "1",
                    "title": "V",
                    "company": "Co",
                    "url": "https://hh.ru/vacancy/1",
                    "skills": [],
                },
                db_path,
            )
            database.save_analysis(
                "1",
                {
                    "fit_score": 50,
                    "my_fit_for_them": 0.5,
                    "their_fit_for_me": 0.5,
                    "track": "other",
                    "reasons": [],
                    "red_flags": [],
                    "recommendation": "maybe",
                },
                db_path,
            )
            self.assertEqual(database.reset_analysis(db_path=db_path), 1)
            self.assertEqual(database.get_matches(0, db_path), [])


if __name__ == "__main__":
    unittest.main()
