from dataclasses import dataclass
from enum import Enum, auto


class GamePhase(Enum):
    LOBBY = auto()
    PLAYER_SELECT = auto()
    CATEGORY_SELECT = auto()
    SHUTDOWN_SELECT = auto()
    SPINNING = auto()
    QUESTION = auto()
    ANSWER_REVEAL = auto()
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
class Player:
    id: str
    name: str
    questions_answered: int = 0
    correct_answers: int = 0

    @property
    def incorrect_answers(self) -> int:
        return self.questions_answered - self.correct_answers

    @property
    def accuracy(self) -> float:
        if not self.questions_answered:
            return 0.0
        return self.correct_answers / self.questions_answered