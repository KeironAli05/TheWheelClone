import tempfile
import unittest
from pathlib import Path

from game.data import DataFileError, load_experts, load_questions


class DataLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.experts_path = self.root / "experts.csv"
        self.questions_path = self.root / "questions.csv"
        self.experts_path.write_text("name,category\nKashish,Pop Culture\n", encoding="utf-8")
        self.questions_path.write_text(
            "category,question,a,b,c,d,answer\n"
            "Pop Culture,Question?,One,Two,Three,Four,A\n"
            "Monica,Personal?,One,Two,Three,Four,C\n"
            "Monica,Final?,One,Two,Three,Four,B\n",
            encoding="utf-8",
        )

    def test_loads_experts_and_questions(self) -> None:
        experts = load_experts(self.experts_path)
        questions = load_questions(self.questions_path, experts)

        self.assertEqual(experts[0].id, "kashish")
        self.assertEqual([question.category for question in questions], ["Pop Culture", "Monica", "Monica"])
        self.assertEqual(questions[0].options, ("One", "Two", "Three", "Four"))

    def test_rejects_unknown_question_category(self) -> None:
        self.questions_path.write_text(
            "category,question,a,b,c,d,answer\n"
            "Unknown,Question?,One,Two,Three,Four,A\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(DataFileError, "unknown category"):
            load_questions(self.questions_path, load_experts(self.experts_path))

    def test_rejects_missing_answer_option(self) -> None:
        self.questions_path.write_text(
            "category,question,a,b,c,d,answer\n"
            "Pop Culture,Question?,One,Two,,Four,A\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(DataFileError, "all four options"):
            load_questions(self.questions_path, load_experts(self.experts_path))

    def test_rejects_reserved_special_expert_category(self) -> None:
        self.experts_path.write_text("name,category\nKashish,Monica\n", encoding="utf-8")

        with self.assertRaisesRegex(DataFileError, "reserved"):
            load_experts(self.experts_path)

    def test_rejects_expert_names_that_collide_as_ids(self) -> None:
        self.experts_path.write_text("name,category\nA B,Pop Culture\nA-B,Music\n", encoding="utf-8")

        with self.assertRaisesRegex(DataFileError, "conflicts"):
            load_experts(self.experts_path)

    def test_missing_csv_cell_reports_validation_error(self) -> None:
        self.questions_path.write_text(
            "category,question,a,b,c,d,answer\n"
            "Pop Culture,Question?,One,Two,Three\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(DataFileError, "all four options"):
            load_questions(self.questions_path, load_experts(self.experts_path))


if __name__ == "__main__":
    unittest.main()