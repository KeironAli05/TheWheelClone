import csv
import re
from pathlib import Path

from game.models import Expert, Question


class DataFileError(ValueError):
    """Raised when game content CSV files are invalid."""


def load_experts(path: Path) -> list[Expert]:
    rows = _read_csv(path, {"name", "category"})
    experts: list[Expert] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        name = _cell(row, "name")
        category = _cell(row, "category")
        if not name or not category:
            raise DataFileError(f"{path}:{row_number}: name and category are required.")
        if category.casefold() == "monica":
            raise DataFileError(f"{path}:{row_number}: {category} is reserved for special questions.")
        if name.casefold() in seen_names:
            raise DataFileError(f"{path}:{row_number}: expert name '{name}' is duplicated.")
        seen_names.add(name.casefold())
        expert_id = _slug(name) or f"expert-{len(experts) + 1}"
        if expert_id in seen_ids:
            raise DataFileError(f"{path}:{row_number}: expert name '{name}' conflicts with another expert ID.")
        seen_ids.add(expert_id)
        experts.append(Expert(expert_id, name, category))
    if not experts:
        raise DataFileError(f"{path}: at least one expert is required.")
    return experts


def load_questions(path: Path, experts: list[Expert]) -> list[Question]:
    rows = _read_csv(path, {"category", "question", "a", "b", "c", "d", "answer"})
    allowed_categories = {expert.category for expert in experts} | {"Monica"}
    questions: list[Question] = []
    for row_number, row in enumerate(rows, start=2):
        category = _cell(row, "category")
        text = _cell(row, "question")
        options = tuple(_cell(row, key) for key in ("a", "b", "c", "d"))
        correct = _cell(row, "answer").upper()
        if category not in allowed_categories:
            raise DataFileError(f"{path}:{row_number}: unknown category '{category}'.")
        if not text or any(not option for option in options):
            raise DataFileError(f"{path}:{row_number}: question and all four options are required.")
        if correct not in {"A", "B", "C", "D"}:
            raise DataFileError(f"{path}:{row_number}: correct must be A, B, C, or D.")
        question_id = f"{_slug(category) or 'question'}-{len(questions) + 1}"
        questions.append(Question(question_id, category, text, options, correct))
    if not questions:
        raise DataFileError(f"{path}: at least one question is required.")
    return questions


def _read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            columns = set(reader.fieldnames or [])
            missing = required_columns - columns
            if missing:
                names = ", ".join(sorted(missing))
                raise DataFileError(f"{path}: missing required column(s): {names}.")
            return list(reader)
    except OSError as error:
        raise DataFileError(f"Unable to read {path}: {error}") from error


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _cell(row: dict[str, str], column: str) -> str:
    return (row.get(column) or "").strip()