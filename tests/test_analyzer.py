import json
import unittest


class AnalyzerTests(unittest.TestCase):
    def test_parse_json_response_handles_fenced_json(self):
        from analyzer import parse_json_response

        payload = {
            "fit_score": 82,
            "my_fit_for_them": "Candidate matches Python/API tasks.",
            "their_fit_for_me": "Junior-friendly backend role.",
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
        self.assertTrue("3 лет" in SYSTEM_PROMPT or "3 года" in SYSTEM_PROMPT)

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

    def test_validate_analysis_result_accepts_float_scores(self):
        from analyzer import validate_analysis_result

        result = validate_analysis_result(
            {
                "fit_score": 0.82,
                "my_fit_for_them": 0.78,
                "their_fit_for_me": 0.91,
                "reasons": ["junior-friendly"],
                "red_flags": [],
                "recommendation": "apply",
            }
        )

        self.assertEqual(result.fit_score, 0.82)
        self.assertEqual(result.my_fit_for_them, 0.78)
        self.assertEqual(result.their_fit_for_me, 0.91)


if __name__ == "__main__":
    unittest.main()
