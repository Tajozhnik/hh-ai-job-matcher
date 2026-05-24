import asyncio
import json
import unittest


class AnalyzerTests(unittest.TestCase):
    def test_parse_json_response_handles_fenced_json(self):
        from analyzer import parse_json_response

        payload = {
            "fit_score": 0.82,
            "my_fit_for_them": 0.82,
            "their_fit_for_me": 0.9,
            "track": "backend",
            "reasons": ["Python", "API"],
            "red_flags": [],
            "recommendation": "apply",
        }
        content = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

        self.assertEqual(parse_json_response(content), payload)

    def test_system_prompt_is_tuned_for_junior_candidate(self):
        from analyzer import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.lower()

        self.assertTrue("пет-проект" in prompt or "pet" in prompt)
        self.assertTrue("стажир" in prompt or "intern" in prompt)
        self.assertTrue(
            "наставник" in prompt or "ментор" in prompt or "mentor" in prompt
        )
        self.assertIn("fit_score = min", SYSTEM_PROMPT)

    def test_build_user_message_includes_pet_projects_between_skills_and_vacancy(self):
        from analyzer import _build_user_message

        message = _build_user_message(
            {
                "name": "Фёдор",
                "summary": "Студент, Python backend.",
                "skills": ["python", "fastapi"],
                "pet_projects": ["Telegram-боты", "Хакатон с ChatGPT"],
                "hard_no": ["senior"],
                "min_salary_rub": 40000,
                "location_preference": ["remote"],
            },
            {
                "title": "Python intern",
                "company": "Example",
                "salary": "60000 руб.",
                "location": "remote",
                "skills": ["python"],
                "description": "Стажировка с наставником.",
            },
        )

        skills_index = message.index("## НАВЫКИ")
        pets_index = message.index("## ПЕТ-ПРОЕКТЫ")
        vacancy_index = message.index("## ВАКАНСИЯ")

        self.assertLess(skills_index, pets_index)
        self.assertLess(pets_index, vacancy_index)
        self.assertIn("- Telegram-боты", message)
        self.assertIn("- Хакатон с ChatGPT", message)

    def test_validate_analysis_result_enforces_score_invariant(self):
        from analyzer import validate_analysis_result

        # LLM cheats: claims fit=0.85 while my=0.4, their=0.8.
        result = validate_analysis_result(
            {
                "fit_score": 0.85,
                "my_fit_for_them": 0.4,
                "their_fit_for_me": 0.8,
                "track": "backend",
                "reasons": [],
                "red_flags": [],
                "recommendation": "apply",
            }
        )

        # fit_score must collapse to min(my, their) = 0.4 and recommendation -> skip.
        self.assertAlmostEqual(result.fit_score, 0.4, places=2)
        self.assertEqual(result.recommendation, "skip")

    def test_validate_analysis_result_normalises_track(self):
        from analyzer import validate_analysis_result

        result = validate_analysis_result(
            {
                "fit_score": 0.8,
                "my_fit_for_them": 0.8,
                "their_fit_for_me": 0.85,
                "track": "Telegram_Bot",
                "reasons": [],
                "red_flags": [],
                "recommendation": "apply",
            }
        )
        self.assertEqual(result.track, "telegram_bot")

    def test_recommendation_follows_score(self):
        from analyzer import _recommendation_from_score

        self.assertEqual(_recommendation_from_score(0.9), "apply")
        self.assertEqual(_recommendation_from_score(0.6), "maybe")
        self.assertEqual(_recommendation_from_score(0.3), "skip")

    def test_parse_salary_min_rub(self):
        from analyzer import parse_salary_min_rub

        self.assertEqual(parse_salary_min_rub("от 40 000 ₽ за месяц на руки"), 40000)
        self.assertEqual(parse_salary_min_rub("50 000 ₽"), 50000)
        self.assertEqual(parse_salary_min_rub("от 37 500 до 75 000 ₽"), 37500)
        self.assertEqual(parse_salary_min_rub("120000 руб."), 120000)
        self.assertIsNone(parse_salary_min_rub(None))
        self.assertIsNone(parse_salary_min_rub("salary not specified"))
        self.assertIsNone(parse_salary_min_rub("$3000 per month"))  # non-RUB ignored

    def test_deterministic_skip_catches_1c_vacancies(self):
        from analyzer import deterministic_skip

        result = deterministic_skip(
            {
                "title": "Программист 1С",
                "description": "Разработка конфигураций 1С предприятие.",
                "salary": None,
            },
            {
                "min_salary_rub": 40000,
                "hard_no": ["1С"],
            },
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.recommendation, "skip")
        self.assertTrue(any("1С" in flag for flag in result.red_flags))

    def test_deterministic_skip_passes_python_vacancies(self):
        from analyzer import deterministic_skip

        result = deterministic_skip(
            {
                "title": "Стажёр Python разработчик",
                "description": "FastAPI, REST API, Telegram-боты, наставник.",
                "salary": "от 50 000 руб.",
            },
            {
                "min_salary_rub": 40000,
                "hard_no": ["1С"],
            },
        )
        self.assertIsNone(result)

    def test_deterministic_skip_catches_low_salary(self):
        from analyzer import deterministic_skip

        result = deterministic_skip(
            {
                "title": "Python intern",
                "description": "Без обучения, в офисе.",
                "salary": "от 15 000 руб.",  # well below 60% of 40000
            },
            {"min_salary_rub": 40000, "hard_no": []},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.recommendation, "skip")

    def test_truncate_description_caps_long_text(self):
        from analyzer import _truncate_description

        long_text = "слово " * 5000
        truncated = _truncate_description(long_text, limit=200)
        self.assertLess(len(truncated), 250)
        self.assertTrue(truncated.endswith("[обрезано]"))

    def test_analyze_many_runs_concurrently(self):
        """analyze_many should call analyze_vacancy for every input."""
        from analyzer import analyze_many

        async def fake_analyze(vacancy, config):
            await asyncio.sleep(0)
            return type(
                "R",
                (),
                {
                    "model_dump": lambda self: {
                        "fit_score": 0.5,
                        "my_fit_for_them": 0.5,
                        "their_fit_for_me": 0.5,
                        "track": "backend",
                        "reasons": [],
                        "red_flags": [],
                        "recommendation": "maybe",
                    }
                },
            )()

        import analyzer as analyzer_module

        original = analyzer_module.analyze_vacancy
        analyzer_module.analyze_vacancy = fake_analyze
        try:
            vacancies = [{"id": str(i), "title": f"V{i}"} for i in range(5)]
            pairs = asyncio.run(analyze_many(vacancies, {"profile": {}}, concurrency=3))
        finally:
            analyzer_module.analyze_vacancy = original

        self.assertEqual(len(pairs), 5)
        for vacancy, outcome in pairs:
            self.assertNotIsInstance(outcome, Exception)


if __name__ == "__main__":
    unittest.main()
