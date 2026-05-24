import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ReportTests(unittest.TestCase):
    def test_generate_report_writes_markdown_with_expected_sections(self):
        import analyzer

        matches = [
            {
                "id": "1",
                "title": "Python intern",
                "url": "https://hh.ru/vacancy/1",
                "company": "Green Co",
                "salary": "60000 руб.",
                "location": "remote",
                "fit_score": 0.82,
                "my_fit_for_them": 0.78,
                "their_fit_for_me": 0.88,
                "track": "backend",
                "reasons": ["стажировка", "python"],
                "red_flags": [],
                "recommendation": "apply",
            },
            {
                "id": "2",
                "title": "AI automation junior",
                "url": "https://hh.ru/vacancy/2",
                "company": "Yellow Co",
                "salary": "не указана",
                "location": "moscow",
                "fit_score": 0.63,
                "my_fit_for_them": 0.58,
                "their_fit_for_me": 0.72,
                "track": "ai_automation",
                "reasons": ["LLM API"],
                "red_flags": ["неясная зарплата"],
                "recommendation": "maybe",
            },
            {
                "id": "3",
                "title": "RAG developer",
                "url": "https://hh.ru/vacancy/3",
                "company": "Red Co",
                "salary": "120000 руб.",
                "location": "hybrid",
                "fit_score": 0.46,
                "my_fit_for_them": 0.35,
                "their_fit_for_me": 0.82,
                "track": "ai_automation",
                "reasons": ["интересные AI задачи"],
                "red_flags": ["много production-опыта"],
                "recommendation": "maybe",
            },
            {
                "id": "4",
                "title": "Senior Python",
                "url": "https://hh.ru/vacancy/4",
                "company": "Black Co",
                "salary": "200000 руб.",
                "location": "office",
                "fit_score": 0.21,
                "my_fit_for_them": 0.18,
                "their_fit_for_me": 0.31,
                "track": "backend",
                "reasons": [],
                "red_flags": ["опыт от 3 лет строго", "офис 5/2"],
                "recommendation": "skip",
            },
            {
                "id": "5",
                "title": "PHP developer",
                "url": "https://hh.ru/vacancy/5",
                "company": "Black Co",
                "salary": "30000 руб.",
                "location": "office",
                "fit_score": 0.12,
                "my_fit_for_them": 0.1,
                "their_fit_for_me": 0.2,
                "track": "other",
                "reasons": [],
                "red_flags": ["основной стек не Python", "офис 5/2"],
                "recommendation": "skip",
            },
        ]

        cfg = {"analysis": {"min_fit_score": 0.55}}

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(analyzer, "BASE_DIR", Path(tmpdir)):
                with mock.patch.object(analyzer.database, "get_matches", return_value=matches):
                    with mock.patch.object(
                        analyzer,
                        "get_database_stats",
                        return_value={"total": 5, "analyzed": 5},
                    ):
                        report_path = Path(analyzer.generate_report(cfg))

            content = report_path.read_text(encoding="utf-8")

        self.assertIn("# 📊 HH.ru отчёт", content)
        self.assertIn("## Общая статистика", content)
        self.assertIn("Распределение по трекам", content)
        self.assertIn("## 🟢 Точно подавайся", content)
        self.assertIn("### Трек: backend", content)
        self.assertIn("## 🟡 Подумай", content)
        self.assertIn("## 🔴 Интересно, но не мой уровень", content)
        self.assertIn("## ⚫ Отсеяно", content)
        self.assertIn("[Python intern](https://hh.ru/vacancy/1)", content)
        self.assertIn("fit=0.82, я→них=0.78, они→я=0.88", content)
        self.assertIn("Топ-10 самых частых red_flags", content)
        self.assertIn("- офис 5/2: 2", content)
        self.assertIn("Топ-5 компаний с наибольшим числом отсеянных вакансий", content)
        self.assertIn("- Black Co: 2", content)


if __name__ == "__main__":
    unittest.main()
