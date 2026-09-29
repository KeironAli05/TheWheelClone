import random
from collections import defaultdict

from game.models import Expert, GamePhase, Player, Question


POWERUPS = {
    "ask_players": "Ask the Players",
    "fifty_fifty": "50:50",
    "peek": "Peek at an Expert",
    "respin": "Re-spin",
}


class GameError(ValueError):
    """Raised when an action is invalid for the current game phase."""


class GameEngine:
    def __init__(
        self,
        experts: list[Expert],
        questions: list[Question],
        rng: random.Random | None = None,
    ) -> None:
        if not experts:
            raise ValueError("At least one expert is required.")
        if len({expert.id for expert in experts}) != len(experts):
            raise ValueError("Expert IDs must be unique.")

        self.experts = {expert.id: expert for expert in experts}
        self.categories = list(dict.fromkeys(expert.category for expert in experts))
        self._all_questions = list(questions)
        self.questions_by_category: dict[str, list[Question]] = defaultdict(list)
        for question in self._all_questions:
            self.questions_by_category[question.category].append(question)

        missing = [category for category in self.categories if not self.questions_by_category[category]]
        if missing:
            raise ValueError(f"No questions configured for categories: {', '.join(missing)}")
        if not self.questions_by_category["Birthday"]:
            raise ValueError("At least one Birthday question is required.")

        self._rng = rng or random.Random()
        self.players: dict[str, Player] = {}
        self.expert_stats = {
            expert_id: {"questions_answered": 0, "correct_answers": 0}
            for expert_id in self.experts
        }
        self.phase = GamePhase.LOBBY
        self.cleared_categories: set[str] = set()
        self.locked_expert_ids: set[str] = set()
        self.current_player_id: str | None = None
        self.current_category: str | None = None
        self.current_question: Question | None = None
        self.current_expert_id: str | None = None
        self.turn_shutdown_expert_id: str | None = None
        self.expert_answers: dict[str, str] = {}
        self.player_answer: str | None = None
        self.last_result: dict[str, object] | None = None
        self._pending_final_question = False
        self.fifty_fifty_removed: list[str] = []
        self.peek_expert_id: str | None = None
        self.audience_status: str | None = None
        self.audience_votes: dict[str, str] = {}

    def add_player(self, player: Player) -> None:
        if player.id in self.players:
            raise GameError("That player is already registered.")
        if any(existing.name.casefold() == player.name.casefold() for existing in self.players.values()):
            raise GameError("That player name is already in use.")
        self.players[player.id] = player

    def start(self) -> None:
        self._require_phase(GamePhase.LOBBY)
        if not self.players:
            raise GameError("At least one player must join before the game starts.")
        self.phase = GamePhase.PLAYER_SELECT

    def select_player(self, player_id: str | None = None) -> Player:
        self._require_phase(GamePhase.PLAYER_SELECT)
        if player_id is None:
            player = self._rng.choice(list(self.players.values()))
        else:
            try:
                player = self.players[player_id]
            except KeyError as error:
                raise GameError("Choose a player who has joined the game.") from error
        self.current_player_id = player.id
        self.phase = GamePhase.CATEGORY_SELECT
        return player

    def choose_category(self, category: str) -> None:
        self._require_phase(GamePhase.CATEGORY_SELECT)
        if category not in self.categories:
            raise GameError("Choose a configured category.")
        if category in self.cleared_categories:
            raise GameError("That category has already been cleared.")
        self.current_category = category
        self.phase = GamePhase.SHUTDOWN_SELECT

    def choose_shutdown(self, expert_id: str) -> None:
        self._require_phase(GamePhase.SHUTDOWN_SELECT)
        self._require_expert(expert_id)
        if expert_id in self.locked_expert_ids:
            raise GameError("That expert is already locked out for this spin.")
        self.turn_shutdown_expert_id = expert_id
        self.phase = GamePhase.SPINNING

    def resolve_landing(self, expert_id: str) -> bool:
        """Returns False when the chair landed on a shut-down expert and the turn ended."""
        self._require_phase(GamePhase.SPINNING)
        self._require_expert(expert_id)
        if expert_id in self.locked_expert_ids or expert_id == self.turn_shutdown_expert_id:
            self.locked_expert_ids.clear()
            self.last_result = {"type": "shutdown_landing", "expert_id": expert_id}
            self._continue_player()
            return False

        self.current_expert_id = expert_id
        if "respin" in self._current_player().used_powerups:
            self._show_question()
        else:
            self.phase = GamePhase.LANDED
        return True

    def confirm_landing(self) -> None:
        self._require_phase(GamePhase.LANDED)
        self._show_question()

    def use_respin(self) -> None:
        self._require_phase(GamePhase.LANDED)
        self._use_powerup("respin")
        self.current_expert_id = None
        self.phase = GamePhase.SPINNING

    def use_fifty_fifty(self) -> list[str]:
        self._require_phase(GamePhase.QUESTION)
        question = self.current_question
        assert question is not None
        self._use_powerup("fifty_fifty")
        wrong = [letter for letter in "ABCD" if letter != question.correct]
        self.fifty_fifty_removed = sorted(self._rng.sample(wrong, 2))
        return self.fifty_fifty_removed

    def use_peek(self, expert_id: str) -> None:
        self._require_phase(GamePhase.QUESTION)
        self._require_expert(expert_id)
        self._use_powerup("peek")
        self.peek_expert_id = expert_id

    def start_audience_vote(self) -> None:
        self._require_phase(GamePhase.QUESTION)
        if not self._audience_voter_ids():
            raise GameError("There are no other players to ask.")
        self._use_powerup("ask_players")
        self.audience_status = "open"
        self.audience_votes.clear()

    def submit_audience_vote(self, player_id: str, answer: str) -> None:
        self._require_phase(GamePhase.QUESTION)
        if self.audience_status != "open":
            raise GameError("There is no vote open.")
        if player_id not in self.players:
            raise GameError("Only joined players can vote.")
        if player_id == self.current_player_id:
            raise GameError("The player in the chair cannot vote.")
        normalized = answer.upper()
        if normalized not in {"A", "B", "C", "D"}:
            raise GameError("Answers must be A, B, C, or D.")
        if player_id in self.audience_votes:
            raise GameError("Your vote is already locked.")
        self.audience_votes[player_id] = normalized

    def close_audience_vote(self) -> None:
        self._require_phase(GamePhase.QUESTION)
        if self.audience_status != "open":
            raise GameError("There is no vote open.")
        self.audience_status = "closed"

    def submit_expert_answer(self, expert_id: str, answer: str) -> None:
        self._require_phase(GamePhase.QUESTION)
        self._require_expert(expert_id)
        normalized = answer.upper()
        if normalized not in {"A", "B", "C", "D"}:
            raise GameError("Answers must be A, B, C, or D.")
        if expert_id in self.expert_answers:
            raise GameError("Your answer is already locked.")
        self.expert_answers[expert_id] = normalized

    def reveal_answer(self, player_answer: str) -> dict[str, object]:
        self._require_phase(GamePhase.QUESTION)
        normalized = player_answer.upper()
        if normalized not in {"A", "B", "C", "D"}:
            raise GameError("Answers must be A, B, C, or D.")
        self.player_answer = normalized
        if self.audience_status == "open":
            self.audience_status = "closed"
        self.phase = GamePhase.ANSWER_REVEAL
        question = self.current_question
        assert question is not None
        self._record_expert_results(question)
        player = self._current_player()
        player.questions_answered += 1
        player_won = normalized == question.correct
        if player_won:
            player.correct_answers += 1
            self.cleared_categories.add(question.category)
            self.last_result = {"type": "correct", "player_id": player.id}
            self._pending_final_question = self.cleared_categories == set(self.categories)
        else:
            self.last_result = {"type": "incorrect", "player_id": player.id}
            self.cleared_categories.clear()
            self._pending_final_question = False
        self.phase = GamePhase.ANSWER_REVEAL
        return self.last_result

    def advance(self) -> None:
        self._require_phase(GamePhase.ANSWER_REVEAL)
        if self._pending_final_question:
            self.current_category = "Birthday"
            self.current_question = self._draw_question("Birthday")
            self.current_expert_id = None
            self.expert_answers.clear()
            self.player_answer = None
            self._pending_final_question = False
            self._clear_powerup_effects()
            self.phase = GamePhase.FINAL_QUESTION
            return
        if self.last_result and self.last_result.get("type") == "correct":
            self._continue_player()
            return
        self._finish_turn()

    def reveal_final_answer(self, player_answer: str) -> dict[str, object]:
        self._require_phase(GamePhase.FINAL_QUESTION)
        normalized = player_answer.upper()
        if normalized not in {"A", "B", "C", "D"}:
            raise GameError("Answers must be A, B, C, or D.")
        question = self.current_question
        assert question is not None
        player = self._current_player()
        player.questions_answered += 1
        if normalized == question.correct:
            player.correct_answers += 1
            self.last_result = {"type": "game_won", "player_id": player.id}
            self.phase = GamePhase.GAME_WON
        else:
            self.last_result = {"type": "final_incorrect", "player_id": player.id}
            self.cleared_categories.clear()
            self.phase = GamePhase.ANSWER_REVEAL
            self._pending_final_question = False
        self.player_answer = normalized
        return self.last_result

    def reset(self) -> None:
        self.phase = GamePhase.LOBBY
        self.cleared_categories.clear()
        self.locked_expert_ids.clear()
        self.current_player_id = None
        self.current_category = None
        self.current_question = None
        self.current_expert_id = None
        self.turn_shutdown_expert_id = None
        self.expert_answers.clear()
        self.player_answer = None
        self.last_result = None
        self._pending_final_question = False
        self._clear_powerup_effects()
        self.questions_by_category = defaultdict(list)
        for question in self._all_questions:
            self.questions_by_category[question.category].append(question)
        for player in self.players.values():
            player.questions_answered = 0
            player.correct_answers = 0
            player.used_powerups.clear()
        for stats in self.expert_stats.values():
            stats["questions_answered"] = 0
            stats["correct_answers"] = 0

    def snapshot(self, reveal_expert_answers: bool = False) -> dict[str, object]:
        question = self.current_question
        current_player = self.players.get(self.current_player_id) if self.current_player_id else None
        return {
            "phase": self.phase.name,
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "questions_answered": player.questions_answered,
                    "correct_answers": player.correct_answers,
                    "incorrect_answers": player.incorrect_answers,
                    "accuracy": player.accuracy,
                }
                for player in self.players.values()
            ],
            "experts": [
                {
                    "id": expert.id,
                    "name": expert.name,
                    "category": expert.category,
                    "questions_answered": self.expert_stats[expert.id]["questions_answered"],
                    "correct_answers": self.expert_stats[expert.id]["correct_answers"],
                    "incorrect_answers": (
                        self.expert_stats[expert.id]["questions_answered"]
                        - self.expert_stats[expert.id]["correct_answers"]
                    ),
                    "accuracy": (
                        self.expert_stats[expert.id]["correct_answers"]
                        / self.expert_stats[expert.id]["questions_answered"]
                        if self.expert_stats[expert.id]["questions_answered"]
                        else 0.0
                    ),
                    "locked": expert.id in self.locked_expert_ids,
                    "selected": expert.id == self.current_expert_id,
                    "turn_shutdown": expert.id == self.turn_shutdown_expert_id,
                    "answered": expert.id in self.expert_answers,
                    "answer": self.expert_answers.get(expert.id) if reveal_expert_answers else None,
                }
                for expert in self.experts.values()
            ],
            "categories": [
                {"name": category, "cleared": category in self.cleared_categories}
                for category in self.categories
            ],
            "available_categories": [category for category in self.categories if category not in self.cleared_categories],
            "current_player_id": self.current_player_id,
            "current_player_name": self._current_player().name if self.current_player_id else None,
            "current_category": self.current_category,
            "current_expert_id": self.current_expert_id,
            "current_expert_name": self.experts[self.current_expert_id].name if self.current_expert_id else None,
            "pending_final_question": self._pending_final_question,
            "current_question": (
                {
                    "id": question.id,
                    "category": question.category,
                    "text": question.text,
                    "options": list(question.options),
                    "correct": question.correct
                    if reveal_expert_answers or self.phase in {GamePhase.ANSWER_REVEAL, GamePhase.GAME_WON}
                    else None,
                }
                if question
                else None
            ),
            "expert_answer_count": len(self.expert_answers),
            "expert_answer_total": len(self.experts),
            "expert_answers": dict(self.expert_answers)
            if reveal_expert_answers or self.phase in {GamePhase.ANSWER_REVEAL, GamePhase.GAME_WON}
            else {},
            "player_answer": self.player_answer
            if reveal_expert_answers or self.phase in {GamePhase.ANSWER_REVEAL, GamePhase.GAME_WON}
            else None,
            "last_result": self.last_result,
            "powerups": [
                {
                    "id": powerup_id,
                    "name": name,
                    "used": bool(current_player and powerup_id in current_player.used_powerups),
                    "available": self._powerup_available(powerup_id, current_player),
                }
                for powerup_id, name in POWERUPS.items()
            ],
            "fifty_fifty_removed": list(self.fifty_fifty_removed),
            "peek": (
                {
                    "expert_id": self.peek_expert_id,
                    "expert_name": self.experts[self.peek_expert_id].name,
                    "answer": self.expert_answers.get(self.peek_expert_id),
                }
                if self.peek_expert_id
                else None
            ),
            "audience": (
                {
                    "status": self.audience_status,
                    "vote_count": len(self.audience_votes),
                    "voter_total": len(self._audience_voter_ids()),
                    "counts": (
                        {letter: list(self.audience_votes.values()).count(letter) for letter in "ABCD"}
                        if self.audience_status == "closed"
                        else None
                    ),
                }
                if self.audience_status
                else None
            ),
        }

    def _show_question(self) -> None:
        self.locked_expert_ids.clear()
        self.current_question = self._draw_question(self.current_category or "")
        self.expert_answers.clear()
        self.player_answer = None
        self._clear_powerup_effects()
        self.phase = GamePhase.QUESTION

    def _use_powerup(self, powerup_id: str) -> None:
        player = self._current_player()
        if powerup_id in player.used_powerups:
            raise GameError(f"{player.name} has already used {POWERUPS[powerup_id]}.")
        player.used_powerups.add(powerup_id)

    def _powerup_available(self, powerup_id: str, player: Player | None) -> bool:
        if player is None or powerup_id in player.used_powerups:
            return False
        if powerup_id == "respin":
            return self.phase is GamePhase.LANDED
        if self.phase is not GamePhase.QUESTION:
            return False
        if powerup_id == "ask_players":
            return self.audience_status is None and bool(self._audience_voter_ids())
        return True

    def _audience_voter_ids(self) -> list[str]:
        return [player_id for player_id in self.players if player_id != self.current_player_id]

    def _clear_powerup_effects(self) -> None:
        self.fifty_fifty_removed = []
        self.peek_expert_id = None
        self.audience_status = None
        self.audience_votes.clear()

    def _draw_question(self, category: str) -> Question:
        pool = self.questions_by_category[category]
        if not pool:
            pool.extend(question for question in self._all_questions if question.category == category)
        if not pool:
            raise GameError(f"No questions are configured for {category}.")
        question = self._rng.choice(pool)
        pool.remove(question)
        return question

    def _record_expert_results(self, question: Question) -> None:
        for expert_id, answer in self.expert_answers.items():
            stats = self.expert_stats[expert_id]
            stats["questions_answered"] += 1
            if answer == question.correct:
                stats["correct_answers"] += 1
        self.locked_expert_ids = {
            expert_id for expert_id, answer in self.expert_answers.items() if answer != question.correct
        }

    def _finish_turn(self) -> None:
        self.phase = GamePhase.PLAYER_SELECT
        self.current_player_id = None
        self._clear_turn()

    def _continue_player(self) -> None:
        self.phase = GamePhase.CATEGORY_SELECT
        self._clear_turn()

    def _clear_turn(self) -> None:
        self.current_category = None
        self.current_question = None
        self.current_expert_id = None
        self.turn_shutdown_expert_id = None
        self.expert_answers.clear()
        self.player_answer = None
        self._pending_final_question = False
        self._clear_powerup_effects()

    def _current_player(self) -> Player:
        if self.current_player_id is None:
            raise GameError("There is no active player.")
        return self.players[self.current_player_id]

    def _require_expert(self, expert_id: str) -> None:
        if expert_id not in self.experts:
            raise GameError("Choose a configured expert.")

    def _require_phase(self, phase: GamePhase) -> None:
        if self.phase is not phase:
            raise GameError(f"This action is not available during {self.phase.name}.")