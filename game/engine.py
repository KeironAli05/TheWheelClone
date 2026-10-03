import random
from collections import defaultdict

from game.models import Expert, GamePhase, Player, Question, Scorecard


POWERUPS = {
    "ask_players": "Ask the Players",
    "fifty_fifty": "50:50",
    "peek": "Peek at an Expert",
    "respin": "Re-spin",
}
FINAL_TIERS = {
    "best": {"label": "Best expert", "rank": 0, "questions": 3},
    "second_best": {"label": "Second-best expert", "rank": 1, "questions": 2},
    "worst": {"label": "Worst expert", "rank": -1, "questions": 1},
}
# Read aloud by the host before revealing each winner.
AWARD_INTROS = {
    "Questionable Credentials": "Every expert has a specialist subject. This award goes to the expert who struggled most "
    "in their own category. Awkward.",
    "Secret Polymath": "This one is for the expert with a hidden talent: the best score on questions outside "
    "their own specialist subject.",
    "Couch Genius": "Some people are brilliant from the sofa and freeze in the chair. This goes to the player with "
    "the biggest gap between their sofa score and their chair score.",
    "Hot Seat Hero": "The chair is where the pressure is. This goes to the player with the best record when it "
    "really counted.",
    "On Fire": "Players and experts both compete for this one: the longest run of correct answers in a row.",
    "Self-Proclaimed Expert": "They called themselves an expert. The numbers disagree. This goes to the expert "
    "with the lowest score of the night.",
    "Wooden Spoon": "Somebody has to come last. Counting every answer from the chair and the sofa, this goes to "
    "the player with the lowest score.",
    "The Expert's Expert": "Across every question tonight, this goes to the expert who got the most right. "
    "A true expert.",
    "Brain of the Party": "Our final award. Counting every answer, from the chair and from the sofa, this goes "
    "to the best player of the night.",
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

        self.final_category = "Monica"

        missing = [category for category in self.categories if not self.questions_by_category[category]]
        if missing:
            raise ValueError(f"No questions configured for categories: {', '.join(missing)}")
        if not self.questions_by_category[self.final_category]:
            raise ValueError("At least one final-round question is required.")

        self._rng = rng or random.Random()
        self.players: dict[str, Player] = {}
        self.expert_scores = {expert_id: Scorecard() for expert_id in self.experts}
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
        self.final_tier: str | None = None
        self.final_expert_id: str | None = None
        self.final_questions_required = 0
        self.final_questions_answered = 0
        self.final_correct_answers = 0
        self._respin_pending = False
        self.fifty_fifty_removed: list[str] = []
        self.peek_expert_id: str | None = None
        self.audience_status: str | None = None
        self.audience_counts: dict[str, int] | None = None
        self.player_guesses: dict[str, str] = {}
        self.awards_step: int | None = None

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
        if self.experts[expert_id].category == self.current_category:
            raise GameError("The expert for the selected category cannot be shut down.")
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
        if self._respin_pending:
            self._respin_pending = False
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
        self._respin_pending = True
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
        self.audience_counts = None

    def submit_guess(self, player_id: str, answer: str) -> None:
        """Players off the chair answer every question for their own score; Ask the Players tallies these."""
        if self.phase not in {GamePhase.QUESTION, GamePhase.FINAL_QUESTION}:
            raise GameError("There is no question to answer right now.")
        if player_id not in self.players:
            raise GameError("Only joined players can answer.")
        if player_id == self.current_player_id:
            raise GameError("The player in the chair answers out loud.")
        normalized = answer.upper()
        if normalized not in {"A", "B", "C", "D"}:
            raise GameError("Answers must be A, B, C, or D.")
        if player_id in self.player_guesses:
            raise GameError("Your answer is already locked.")
        self.player_guesses[player_id] = normalized

    def close_audience_vote(self) -> None:
        self._require_phase(GamePhase.QUESTION)
        if self.audience_status != "open":
            raise GameError("There is no vote open.")
        self._close_audience_vote()

    def start_awards(self) -> None:
        self.awards_step = 0

    def next_award(self) -> None:
        """Each award takes two steps: announce the title, then reveal the winner."""
        if self.awards_step is None:
            raise GameError("Start the awards show first.")
        if self.awards_step >= 2 * len(self.awards()):
            raise GameError("That was the last award.")
        self.awards_step += 1

    def previous_award(self) -> None:
        if self.awards_step is None:
            raise GameError("Start the awards show first.")
        self.awards_step = max(0, self.awards_step - 1)

    def end_awards(self) -> None:
        self.awards_step = None

    def submit_expert_answer(self, expert_id: str, answer: str) -> None:
        if self.phase is GamePhase.FINAL_QUESTION:
            if expert_id != self.final_expert_id:
                raise GameError(f"Only the chosen expert can answer the {self.final_category} questions.")
        else:
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
            self._close_audience_vote()
        self.phase = GamePhase.ANSWER_REVEAL
        question = self.current_question
        assert question is not None
        self._record_expert_results(question)
        player = self._current_player()
        player_won = normalized == question.correct
        self._record_player_answers(question, player, player_won)
        if player_won:
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
            if self.final_expert_id:
                self.current_question = self._draw_question(self.final_category)
                self.expert_answers.clear()
                self.player_guesses.clear()
                self.player_answer = None
                self._pending_final_question = False
                self.phase = GamePhase.FINAL_QUESTION
            else:
                self.current_category = self.final_category
                self.current_question = None
                self.current_expert_id = None
                self.expert_answers.clear()
                self.player_answer = None
                self._pending_final_question = False
                self._clear_powerup_effects()
                self.phase = GamePhase.FINAL_EXPERT_SELECT
            return
        if self.last_result and self.last_result.get("type") == "correct":
            self._continue_player()
            return
        self._finish_turn()

    def reveal_final_answer(self, player_answer: str) -> dict[str, object]:
        self._require_phase(GamePhase.FINAL_QUESTION)
        if self.final_expert_id not in self.expert_answers:
            raise GameError("The chosen expert must answer before the player locks in.")
        normalized = player_answer.upper()
        if normalized not in {"A", "B", "C", "D"}:
            raise GameError("Answers must be A, B, C, or D.")
        question = self.current_question
        assert question is not None
        player = self._current_player()
        self._record_player_answers(question, player, normalized == question.correct)
        self.final_questions_answered += 1
        self.player_answer = normalized
        if normalized == question.correct:
            self.final_correct_answers += 1
            if self.final_correct_answers == self.final_questions_required:
                self.last_result = {"type": "game_won", "player_id": player.id}
                self.phase = GamePhase.GAME_WON
            else:
                self.last_result = {"type": "final_correct", "player_id": player.id}
                self._pending_final_question = True
                self.phase = GamePhase.ANSWER_REVEAL
        else:
            self.last_result = {"type": "final_incorrect", "player_id": player.id}
            self.cleared_categories.clear()
            self._pending_final_question = False
            self.phase = GamePhase.ANSWER_REVEAL
        return self.last_result

    def choose_final_expert(self, tier: str, joined_expert_ids: set[str] | None = None) -> None:
        self._require_phase(GamePhase.FINAL_EXPERT_SELECT)
        if tier not in FINAL_TIERS:
            raise GameError("Choose the best, second-best, or worst expert.")
        tier_config = FINAL_TIERS[tier]
        question_count = len(self._all_questions_by_category(self.final_category))
        if question_count < tier_config["questions"]:
            raise GameError(
                f"The {tier_config['label'].lower()} option needs {tier_config['questions']} unique "
                f"{self.final_category} questions; "
                f"only {question_count} are configured."
            )
        ranked_experts = sorted(self.experts.values(), key=lambda expert: -self._expert_accuracy(expert.id))
        rank = tier_config["rank"]
        expert_index = min(rank, len(ranked_experts) - 1) if rank >= 0 else len(ranked_experts) - 1
        selected_expert = ranked_experts[expert_index]
        if joined_expert_ids is not None and selected_expert.id not in joined_expert_ids:
            raise GameError("The chosen expert must join before the final challenge.")
        self.final_tier = tier
        self.final_expert_id = selected_expert.id
        self.final_questions_required = tier_config["questions"]
        self.final_questions_answered = 0
        self.final_correct_answers = 0
        self.questions_by_category[self.final_category] = self._all_questions_by_category(self.final_category)
        self.current_question = self._draw_question(self.final_category)
        self.expert_answers.clear()
        self.player_guesses.clear()
        self.player_answer = None
        self.phase = GamePhase.FINAL_QUESTION

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
        self.final_tier = None
        self.final_expert_id = None
        self.final_questions_required = 0
        self.final_questions_answered = 0
        self.final_correct_answers = 0
        self._respin_pending = False
        self._clear_powerup_effects()
        self.questions_by_category = defaultdict(list)
        for question in self._all_questions:
            self.questions_by_category[question.category].append(question)
        self.awards_step = None
        for player in self.players.values():
            player.score = Scorecard()
            player.chair_answered = 0
            player.chair_correct = 0
            player.used_powerups.clear()
        self.expert_scores = {expert_id: Scorecard() for expert_id in self.experts}

    def snapshot(self, reveal_expert_answers: bool = False) -> dict[str, object]:
        question = self.current_question
        current_player = self.players.get(self.current_player_id) if self.current_player_id else None
        ranked_experts = sorted(self.experts.values(), key=lambda expert: -self._expert_accuracy(expert.id))
        final_question_count = len(self._all_questions_by_category(self.final_category))
        final_expert_options = []
        for tier, config in FINAL_TIERS.items():
            rank = config["rank"]
            expert_index = min(rank, len(ranked_experts) - 1) if rank >= 0 else len(ranked_experts) - 1
            expert = ranked_experts[expert_index]
            final_expert_options.append(
                {
                    "tier": tier,
                    "label": config["label"],
                    "expert_id": expert.id,
                    "expert_name": expert.name,
                    "accuracy": self._expert_accuracy(expert.id),
                    "questions_required": config["questions"],
                    "question_count": final_question_count,
                    "available": final_question_count >= config["questions"],
                }
            )
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
                    "chair_answered": player.chair_answered,
                    "chair_correct": player.chair_correct,
                    "best_streak": player.score.best_streak,
                    "guessed": player.id in self.player_guesses,
                }
                for player in self.players.values()
            ],
            "experts": [
                {
                    "id": expert.id,
                    "name": expert.name,
                    "category": expert.category,
                    "questions_answered": self.expert_scores[expert.id].answered,
                    "correct_answers": self.expert_scores[expert.id].correct,
                    "incorrect_answers": self.expert_scores[expert.id].answered - self.expert_scores[expert.id].correct,
                    "accuracy": self.expert_scores[expert.id].accuracy,
                    "best_streak": self.expert_scores[expert.id].best_streak,
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
            "final_tier": self.final_tier,
            "final_expert_id": self.final_expert_id,
            "final_expert_name": self.experts[self.final_expert_id].name if self.final_expert_id else None,
            "final_questions_required": self.final_questions_required,
            "final_questions_answered": self.final_questions_answered,
            "final_correct_answers": self.final_correct_answers,
            "final_expert_options": final_expert_options,
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
            "player_guess_count": len(self.player_guesses),
            "player_guess_total": len(self._audience_voter_ids()),
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
                    "vote_count": len(self.player_guesses),
                    "voter_total": len(self._audience_voter_ids()),
                    "counts": dict(self.audience_counts) if self.audience_status == "closed" and self.audience_counts else None,
                }
                if self.audience_status
                else None
            ),
            "awards_show": self._awards_show(),
        }

    def _awards_show(self) -> dict[str, object] | None:
        if self.awards_step is None:
            return None
        awards = self.awards()
        step = min(self.awards_step, 2 * len(awards))
        index = (step + 1) // 2
        revealed = step > 0 and step % 2 == 0
        award = awards[index - 1] if index else None
        return {
            "step": step,
            "number": index,
            "total": len(awards),
            "revealed": revealed,
            "finished": step == 2 * len(awards),
            "award": (
                {
                    "title": award["title"],
                    "description": award["description"],
                    "winners": award["winners"] if revealed else None,
                    "stat": award["stat"] if revealed else None,
                }
                if award
                else None
            ),
        }

    def awards(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []

        def give(title: str, description: str, entries: list[tuple[str, tuple, str]], intro: str = "") -> None:
            # Highest rank wins; equal ranks share the award.
            if not entries:
                return
            top = max(rank for _, rank, _ in entries)
            winners = [entry for entry in entries if entry[1] == top]
            results.append(
                {
                    "title": title,
                    "description": description,
                    "intro": intro or AWARD_INTROS.get(title, ""),
                    "winners": [name for name, _, _ in winners],
                    "stat": winners[0][2],
                }
            )

        def stat(correct: int, answered: int) -> str:
            return f"{round(100 * correct / answered)}% ({correct}/{answered})"

        scored_players = [player for player in self.players.values() if player.score.answered]
        scored_experts = [(expert, self.expert_scores[expert.id]) for expert in self.experts.values()]
        scored_experts = [(expert, card) for expert, card in scored_experts if card.answered]

        # Ceremony order: least important first, overall best player last.
        for category in [*self.categories, self.final_category]:
            expert_entries = []
            for e, c in scored_experts:
                answered, correct = c.by_category.get(category, [0, 0])
                if answered:
                    expert_entries.append((e.name, (correct / answered, correct), stat(correct, answered)))
            give(
                f"{category} Guru",
                f"Best expert at {category}",
                expert_entries,
                f"Our experts answered every question, whatever the category. This one goes to the expert "
                f"who did best on the {category} questions.",
            )
            player_entries = []
            for p in scored_players:
                answered, correct = p.score.by_category.get(category, [0, 0])
                if answered:
                    player_entries.append((p.name, (correct / answered, correct), stat(correct, answered)))
            give(
                f"{category} Champion",
                f"Best player at {category}",
                player_entries,
                f"Counting answers from the chair and from the sofa, this goes to the player "
                f"who did best on the {category} questions.",
            )

        own_category = []
        other_categories = []
        for expert, card in scored_experts:
            own_answered, own_correct = card.by_category.get(expert.category, [0, 0])
            if own_answered and own_correct < own_answered:
                own_category.append(
                    (expert.name, (-own_correct / own_answered, own_answered), f"{stat(own_correct, own_answered)} at {expert.category}")
                )
            other_answered = card.answered - own_answered
            other_correct = card.correct - own_correct
            if other_correct:
                other_categories.append(
                    (expert.name, (other_correct / other_answered, other_correct), f"{stat(other_correct, other_answered)} outside {expert.category}")
                )
        give("Questionable Credentials", "Worst expert in their own specialist category", own_category)
        give("Secret Polymath", "Best expert outside their own category", other_categories)

        couch = []
        for p in scored_players:
            sofa_answered = p.questions_answered - p.chair_answered
            if not p.chair_answered or not sofa_answered:
                continue
            sofa_accuracy = (p.correct_answers - p.chair_correct) / sofa_answered
            gap = sofa_accuracy - p.chair_correct / p.chair_answered
            if gap > 0:
                couch.append(
                    (p.name, (gap,), f"{round(100 * sofa_accuracy)}% on the sofa, {round(100 * p.chair_correct / p.chair_answered)}% in the chair")
                )
        give("Couch Genius", "Brilliant from the sofa, less so in the chair", couch)
        give(
            "Hot Seat Hero",
            "Best player in the chair",
            [
                (p.name, (p.chair_correct / p.chair_answered, p.chair_correct), stat(p.chair_correct, p.chair_answered))
                for p in scored_players
                if p.chair_correct
            ],
        )
        streaks = [(p.name, p.score.best_streak) for p in scored_players]
        streaks += [(e.name, c.best_streak) for e, c in scored_experts]
        give(
            "On Fire",
            "Longest run of correct answers",
            [(name, (best,), f"{best} in a row") for name, best in streaks if best >= 2],
        )

        if len(scored_experts) > 1:
            give(
                "Self-Proclaimed Expert",
                "Worst expert overall",
                [(e.name, (-c.accuracy, c.answered), stat(c.correct, c.answered)) for e, c in scored_experts],
            )
        if len(scored_players) > 1:
            give(
                "Wooden Spoon",
                "Worst player overall",
                [(p.name, (-p.accuracy, p.questions_answered), stat(p.correct_answers, p.questions_answered)) for p in scored_players],
            )
        give(
            "The Expert's Expert",
            "Best expert overall",
            [(e.name, (c.accuracy, c.correct), stat(c.correct, c.answered)) for e, c in scored_experts],
        )
        give(
            "Brain of the Party",
            "Best player overall",
            [(p.name, (p.accuracy, p.correct_answers), stat(p.correct_answers, p.questions_answered)) for p in scored_players],
        )
        return results

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
        self.audience_counts = None
        self.player_guesses.clear()

    def _close_audience_vote(self) -> None:
        self.audience_status = "closed"
        self.audience_counts = {letter: list(self.player_guesses.values()).count(letter) for letter in "ABCD"}

    def _draw_question(self, category: str) -> Question:
        pool = self.questions_by_category[category]
        if not pool:
            pool.extend(question for question in self._all_questions if question.category == category)
        if not pool:
            raise GameError(f"No questions are configured for {category}.")
        question = self._rng.choice(pool)
        pool.remove(question)
        option_order = list(range(4))
        self._rng.shuffle(option_order)
        options = tuple(question.options[index] for index in option_order)
        correct_index = option_order.index("ABCD".index(question.correct))
        return Question(
            question.id,
            question.category,
            question.text,
            options,
            "ABCD"[correct_index],
        )

    def _record_expert_results(self, question: Question) -> None:
        for expert_id, answer in self.expert_answers.items():
            self.expert_scores[expert_id].record(question.category, answer == question.correct)
        self.locked_expert_ids = {
            expert_id for expert_id, answer in self.expert_answers.items() if answer != question.correct
        }

    def _record_player_answers(self, question: Question, chair_player: Player, chair_correct: bool) -> None:
        chair_player.score.record(question.category, chair_correct)
        chair_player.chair_answered += 1
        if chair_correct:
            chair_player.chair_correct += 1
        for player_id, guess in self.player_guesses.items():
            self.players[player_id].score.record(question.category, guess == question.correct)

    def _expert_accuracy(self, expert_id: str) -> float:
        return self.expert_scores[expert_id].accuracy

    def _all_questions_by_category(self, category: str) -> list[Question]:
        return [question for question in self._all_questions if question.category == category]

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
        self.final_tier = None
        self.final_expert_id = None
        self.final_questions_required = 0
        self.final_questions_answered = 0
        self.final_correct_answers = 0
        self._respin_pending = False
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