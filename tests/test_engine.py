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
            Question("birthday1", "Birthday", "Birthday question one?", ("A", "B", "C", "D"), "A"),
            Question("birthday2", "Birthday", "Birthday question two?", ("A", "B", "C", "D"), "B"),
            Question("birthday3", "Birthday", "Birthday question three?", ("A", "B", "C", "D"), "C"),
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
        self.assertEqual(self.game.phase, GamePhase.LANDED)
        self.game.confirm_landing()

    def test_category_specialist_cannot_be_shut_down(self) -> None:
        self.game.choose_category("Football")

        with self.assertRaisesRegex(GameError, "selected category cannot be shut down"):
            self.game.choose_shutdown("football")

        self.assertEqual(self.game.phase, GamePhase.SHUTDOWN_SELECT)
        self.game.choose_shutdown("music")
        self.assertEqual(self.game.phase, GamePhase.SPINNING)

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
        tiers = {option["tier"]: option for option in self.game.snapshot()["final_expert_options"]}
        self.assertEqual(tiers["best"]["expert_id"], "football")
        self.assertEqual(tiers["best"]["accuracy"], 1.0)
        self.assertEqual(tiers["second_best"]["expert_id"], "music")

    def test_worst_expert_tier_wins_with_one_correct_answer(self) -> None:
        self.start_question()
        self.game.reveal_answer("B")
        self.game.advance()
        self.game.choose_category("Music")
        self.game.choose_shutdown("football")
        self.game.resolve_landing("music")
        self.game.confirm_landing()
        self.game.reveal_answer("C")
        self.game.advance()

        self.assertEqual(self.game.phase, GamePhase.FINAL_EXPERT_SELECT)
        with self.assertRaisesRegex(GameError, "expert must join"):
            self.game.choose_final_expert("worst", set())
        self.game.choose_final_expert("worst")
        chosen_expert_id = self.game.final_expert_id
        self.game.submit_expert_answer(chosen_expert_id, self.game.current_question.correct)

        result = self.game.reveal_final_answer(self.game.current_question.correct)

        self.assertEqual(result["type"], "game_won")
        self.assertEqual(self.game.phase, GamePhase.GAME_WON)

    def test_second_best_expert_tier_requires_two_correct_answers(self) -> None:
        self.start_question()
        self.game.reveal_answer("B")
        self.game.advance()
        self.game.choose_category("Music")
        self.game.choose_shutdown("football")
        self.game.resolve_landing("music")
        self.game.confirm_landing()
        self.game.reveal_answer("C")
        self.game.advance()
        self.game.choose_final_expert("second_best")

        self.assertEqual(self.game.snapshot()["final_questions_required"], 2)
        for question_number in range(2):
            correct_answer = self.game.current_question.correct
            self.game.submit_expert_answer(self.game.final_expert_id, correct_answer)
            result = self.game.reveal_final_answer(correct_answer)
            if question_number == 0:
                self.assertEqual(result["type"], "final_correct")
                self.game.advance()

        self.assertEqual(result["type"], "game_won")
        self.assertEqual(self.game.phase, GamePhase.GAME_WON)

    def test_best_expert_requires_three_correct_answers(self) -> None:
        self.start_question()
        self.game.reveal_answer("B")
        self.game.advance()
        self.game.choose_category("Music")
        self.game.choose_shutdown("football")
        self.game.resolve_landing("music")
        self.game.confirm_landing()
        self.game.reveal_answer("C")
        self.game.advance()
        self.game.choose_final_expert("best")

        self.assertEqual(self.game.snapshot()["final_questions_required"], 3)
        other_expert_id = next(expert_id for expert_id in self.game.experts if expert_id != self.game.final_expert_id)
        with self.assertRaisesRegex(GameError, "chosen expert must answer"):
            self.game.reveal_final_answer(self.game.current_question.correct)
        with self.assertRaisesRegex(GameError, "Only the chosen expert"):
            self.game.submit_expert_answer(other_expert_id, "A")
        for question_number in range(3):
            correct_answer = self.game.current_question.correct
            self.game.submit_expert_answer(self.game.final_expert_id, correct_answer)
            result = self.game.reveal_final_answer(correct_answer)
            if question_number < 2:
                self.assertEqual(result["type"], "final_correct")
                self.game.advance()

        self.assertEqual(result["type"], "game_won")
        self.assertEqual(self.game.phase, GamePhase.GAME_WON)

    def powerups(self) -> dict[str, dict[str, object]]:
        return {powerup["id"]: powerup for powerup in self.game.snapshot()["powerups"]}

    def test_respin_returns_to_spinning_with_same_shutdowns(self) -> None:
        self.game.choose_category("Football")
        self.game.choose_shutdown("music")
        self.game.resolve_landing("football")
        self.assertTrue(self.powerups()["respin"]["available"])

        self.game.use_respin()

        self.assertEqual(self.game.phase, GamePhase.SPINNING)
        self.assertIsNone(self.game.current_expert_id)
        self.assertEqual(self.game.turn_shutdown_expert_id, "music")
        self.assertFalse(self.game.resolve_landing("music"))
        self.assertEqual(self.game.phase, GamePhase.CATEGORY_SELECT)

    def test_respin_lands_on_same_expert_again_and_skips_landed_step(self) -> None:
        self.game.choose_category("Football")
        self.game.choose_shutdown("music")
        self.game.resolve_landing("football")
        self.game.use_respin()

        self.assertTrue(self.game.resolve_landing("football"))

        self.assertEqual(self.game.phase, GamePhase.QUESTION)
        self.assertTrue(self.powerups()["respin"]["used"])

    def test_used_respin_does_not_skip_landing_on_later_category(self) -> None:
        self.game.choose_category("Football")
        self.game.choose_shutdown("music")
        self.game.resolve_landing("football")
        self.game.use_respin()
        self.game.resolve_landing("football")
        self.game.reveal_answer("B")
        self.game.advance()

        self.game.choose_category("Music")
        self.game.choose_shutdown("football")
        self.game.resolve_landing("music")

        self.assertEqual(self.game.phase, GamePhase.LANDED)
        self.game.confirm_landing()
        self.assertEqual(self.game.phase, GamePhase.QUESTION)

    def test_respin_not_offered_on_shutdown_landing(self) -> None:
        self.game.choose_category("Football")
        self.game.choose_shutdown("music")
        self.game.resolve_landing("music")

        with self.assertRaises(GameError):
            self.game.use_respin()
        self.assertNotIn("respin", self.game.players["player-1"].used_powerups)

    def test_auto_locked_experts_stay_locked_through_respin(self) -> None:
        experts = [
            Expert("football", "Alex", "Football"),
            Expert("music", "Sarah", "Music"),
            Expert("films", "Tom", "Films"),
        ]
        questions = [
            Question(f"{category}1", category, "Question?", ("A", "B", "C", "D"), "A")
            for category in ("Football", "Music", "Films", "Birthday")
        ]
        game = GameEngine(experts, questions, random.Random(1))
        game.add_player(Player("player-1", "Jamie"))
        game.start()
        game.select_player("player-1")
        game.locked_expert_ids = {"music"}
        game.choose_category("Football")
        game.choose_shutdown("films")
        game.resolve_landing("football")
        game.use_respin()

        self.assertEqual(game.locked_expert_ids, {"music"})
        self.assertFalse(game.resolve_landing("music"))
        self.assertEqual(game.locked_expert_ids, set())

    def test_fifty_fifty_removes_two_wrong_answers_once_per_player(self) -> None:
        self.start_question()

        removed = self.game.use_fifty_fifty()

        self.assertEqual(len(removed), 2)
        self.assertNotIn("B", removed)
        self.assertEqual(self.game.snapshot()["fifty_fifty_removed"], removed)
        with self.assertRaises(GameError):
            self.game.use_fifty_fifty()

    def test_powerups_are_never_restored_after_run_reset(self) -> None:
        self.start_question()
        self.game.use_fifty_fifty()
        self.game.reveal_answer("A")
        self.game.advance()
        self.game.select_player("player-1")
        self.game.choose_category("Football")
        self.game.choose_shutdown("music")
        self.game.resolve_landing("football")
        self.game.confirm_landing()

        self.assertEqual(self.game.snapshot()["fifty_fifty_removed"], [])
        self.assertFalse(self.powerups()["fifty_fifty"]["available"])
        with self.assertRaises(GameError):
            self.game.use_fifty_fifty()

    def test_powerups_are_per_player(self) -> None:
        self.start_question()
        self.game.use_fifty_fifty()
        self.game.reveal_answer("A")
        self.game.advance()
        self.game.select_player("player-2")
        self.game.choose_category("Football")
        self.game.choose_shutdown("music")
        self.game.resolve_landing("football")
        self.game.confirm_landing()

        self.assertEqual(len(self.game.use_fifty_fifty()), 2)

    def test_game_reset_restores_powerups(self) -> None:
        self.start_question()
        self.game.use_fifty_fifty()

        self.game.reset()

        self.assertEqual(self.game.players["player-1"].used_powerups, set())

    def test_peek_reveals_expert_answer_once_submitted(self) -> None:
        self.start_question()

        self.game.use_peek("music")
        self.assertIsNone(self.game.snapshot()["peek"]["answer"])
        self.game.submit_expert_answer("music", "D")
        self.game.submit_expert_answer("football", "B")

        snapshot = self.game.snapshot()
        self.assertEqual(snapshot["peek"], {"expert_id": "music", "expert_name": "Sarah", "answer": "D"})
        self.assertEqual(snapshot["expert_answers"], {})

    def test_ask_the_players_vote(self) -> None:
        self.start_question()
        self.game.start_audience_vote()

        with self.assertRaises(GameError):
            self.game.submit_audience_vote("player-1", "B")
        self.game.submit_audience_vote("player-2", "b")
        with self.assertRaises(GameError):
            self.game.submit_audience_vote("player-2", "C")
        self.assertIsNone(self.game.snapshot()["audience"]["counts"])

        self.game.close_audience_vote()

        audience = self.game.snapshot()["audience"]
        self.assertEqual(audience["counts"], {"A": 0, "B": 1, "C": 0, "D": 0})
        self.assertEqual(audience["voter_total"], 1)
        with self.assertRaises(GameError):
            self.game.submit_audience_vote("player-2", "C")

    def test_ask_the_players_needs_other_players(self) -> None:
        del self.game.players["player-2"]
        self.start_question()

        self.assertFalse(self.powerups()["ask_players"]["available"])
        with self.assertRaises(GameError):
            self.game.start_audience_vote()
        self.assertNotIn("ask_players", self.game.players["player-1"].used_powerups)

    def test_no_powerups_on_final_question(self) -> None:
        self.start_question()
        self.game.reveal_answer("B")
        self.game.advance()
        self.game.choose_category("Music")
        self.game.choose_shutdown("football")
        self.game.resolve_landing("music")
        self.game.confirm_landing()
        self.game.reveal_answer("C")
        self.game.advance()

        self.assertEqual(self.game.phase, GamePhase.FINAL_EXPERT_SELECT)
        self.game.choose_final_expert("worst")
        self.assertFalse(any(powerup["available"] for powerup in self.powerups().values()))
        with self.assertRaises(GameError):
            self.game.use_fifty_fifty()


if __name__ == "__main__":
    unittest.main()