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
                    "my_fit_for_them": "Strong Python/API fit.",
                    "their_fit_for_me": "Matches junior backend goals.",
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


if __name__ == "__main__":
    unittest.main()
