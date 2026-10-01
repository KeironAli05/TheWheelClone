from dataclasses import dataclass, field
from enum import Enum, auto


class GamePhase(Enum):
    LOBBY = auto()
    PLAYER_SELECT = auto()
    CATEGORY_SELECT = auto()
    SHUTDOWN_SELECT = auto()
    SPINNING = auto()
    LANDED = auto()
    QUESTION = auto()
    ANSWER_REVEAL = auto()
    FINAL_EXPERT_SELECT = auto()
    FINAL_QUESTION = auto()
    GAME_WON = auto()


@dataclass(frozen=True)
class Expert:
    id: str
    name: str
    category: str


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    text: str
    options: tuple[str, str, str, str]
    correct: str


@dataclass
class Scorecard:
    answered: int = 0
    correct: int = 0
    streak: int = 0
    best_streak: int = 0
    by_category: dict[str, list[int]] = field(default_factory=dict)

    def record(self, category: str, correct: bool) -> None:
        self.answered += 1
        totals = self.by_category.setdefault(category, [0, 0])
        totals[0] += 1
        if correct:
            self.correct += 1
            totals[1] += 1
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            self.streak = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.answered if self.answered else 0.0


@dataclass
class Player:
    id: str
    name: str
    score: Scorecard = field(default_factory=Scorecard)
    chair_answered: int = 0
    chair_correct: int = 0
    used_powerups: set[str] = field(default_factory=set)

    @property
    def questions_answered(self) -> int:
        return self.score.answered

    @property
    def correct_answers(self) -> int:
        return self.score.correct

    @property
    def incorrect_answers(self) -> int:
        return self.questions_answered - self.correct_answers

    @property
    def accuracy(self) -> float:
        return self.score.accuracy