import random
import unittest

from game.engine import GameEngine, GameError
from game.models import Expert, GamePhase, Player, Question


class GameEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        experts = [
            Expert("football", "Alex", "Football"),
            Expert("music", "Sarah", "Music"),
        ]
        questions = [
            Question("f1", "Football", "Football question?", ("A", "B", "C", "D"), "B"),
            Question("m1", "Music", "Music question?", ("A", "B", "C", "D"), "C"),
            Question("birthday", "Birthday", "Birthday question?", ("A", "B", "C", "D"), "A"),
        ]
        self.game = GameEngine(experts, questions, random.Random(1))
        self.game.add_player(Player("player-1", "Jamie"))
        self.game.add_player(Player("player-2", "Taylor"))
        self.game.start()
        self.game.select_player("player-1")

    def start_question(self, category: str = "Football") -> None:
        self.game.choose_category(category)
        self.game.choose_shutdown("music")
        self.assertTrue(self.game.resolve_landing("football"))

    def test_correct_answer_clears_category_and_advances(self) -> None:
        self.start_question()
        self.game.submit_expert_answer("football", "B")
        self.game.submit_expert_answer("music", "A")

        result = self.game.reveal_answer("B")

        self.assertEqual(result["type"], "correct")
        self.assertEqual(self.game.phase, GamePhase.ANSWER_REVEAL)
        self.assertEqual(self.game.cleared_categories, {"Football"})
        self.assertEqual(self.game.locked_expert_ids, {"music"})
        self.assertEqual(self.game.snapshot()["current_question"]["correct"], "B")

        self.game.advance()
        self.assertEqual(self.game.phase, GamePhase.CATEGORY_SELECT)
        self.assertEqual(self.game.current_player_id, "player-1")
        self.game.choose_category("Music")
        self.assertEqual(self.game.phase, GamePhase.SHUTDOWN_SELECT)

    def test_incorrect_answer_hands_off_to_next_player(self) -> None:
        self.start_question()

        self.game.reveal_answer("A")
        self.assertEqual(self.game.current_player_id, "player-1")
        self.game.advance()

        self.assertEqual(self.game.phase, GamePhase.PLAYER_SELECT)
        self.assertIsNone(self.game.current_player_id)
        self.assertEqual(self.game.select_player("player-2").name, "Taylor")

    def test_incorrect_answer_resets_categories_and_lockouts_next_spin(self) -> None:
        self.start_question()
        self.game.submit_expert_answer("music", "A")

        result = self.game.reveal_answer("A")

        self.assertEqual(result["type"], "incorrect")
        self.assertEqual(self.game.phase, GamePhase.ANSWER_REVEAL)
        self.assertEqual(self.game.cleared_categories, set())
        self.assertEqual(self.game.locked_expert_ids, {"music"})

    def test_locked_landing_ends_turn_without_question(self) -> None:
        self.start_question()
        self.game.submit_expert_answer("music", "A")
        self.game.reveal_answer("B")
        self.game.advance()
        self.game.choose_category("Music")
        self.game.choose_shutdown("football")

        self.assertFalse(self.game.resolve_landing("music"))
        self.assertEqual(self.game.phase, GamePhase.CATEGORY_SELECT)
        self.assertEqual(self.game.current_player_id, "player-1")
        self.assertEqual(self.game.locked_expert_ids, set())

    def test_expert_answers_are_private_until_reveal(self) -> None:
        self.start_question()
        self.game.submit_expert_answer("football", "B")

        snapshot = self.game.snapshot()

        self.assertEqual(snapshot["experts"][0]["answer"], None)
        self.assertEqual(snapshot["expert_answers"], {})
        self.assertEqual(snapshot["expert_answer_count"], 1)

    def test_answer_cannot_be_changed_after_submission(self) -> None:
        self.start_question()
        self.game.submit_expert_answer("football", "B")

        with self.assertRaises(GameError):
            self.game.submit_expert_answer("football", "C")

    def test_question_pool_repeats_only_after_exhaustion(self) -> None:
        first_question = self.game._draw_question("Football")

        self.assertEqual(self.game.questions_by_category["Football"], [])
        self.assertEqual(self.game._draw_question("Football"), first_question)

    def test_expert_performance_is_recorded(self) -> None:
        self.start_question()
        self.game.submit_expert_answer("football", "B")
        self.game.submit_expert_answer("music", "A")
        self.game.reveal_answer("B")

        stats_by_id = {expert["id"]: expert for expert in self.game.snapshot()["experts"]}

        self.assertEqual(stats_by_id["football"]["accuracy"], 1.0)
        self.assertEqual(stats_by_id["music"]["incorrect_answers"], 1)

    def test_final_correct_answer_wins(self) -> None:
        self.start_question()
        self.game.reveal_answer("B")
        self.game.advance()
        self.game.choose_category("Music")
        self.game.choose_shutdown("football")
        self.game.resolve_landing("music")
        self.game.reveal_answer("C")
        self.game.advance()

        self.assertEqual(self.game.phase, GamePhase.FINAL_QUESTION)

        result = self.game.reveal_final_answer("A")

        self.assertEqual(result["type"], "game_won")
        self.assertEqual(self.game.phase, GamePhase.GAME_WON)


if __name__ == "__main__":
    unittest.main()