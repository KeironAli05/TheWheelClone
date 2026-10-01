from io import BytesIO
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from werkzeug.datastructures import MultiDict

from app import create_app, extract_cloudflare_url, find_cloudflared, start_cloudflare_tunnel


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(testing=True)
        self.client = self.app.test_client()
        self.socketio = self.app.extensions["wheel_socketio"]

    def test_private_pages_redirect_without_joined_identity(self) -> None:
        self.assertEqual(self.client.get("/host").status_code, 302)
        self.assertEqual(self.client.get("/player").status_code, 302)
        self.assertEqual(self.client.get("/display").status_code, 200)

    def test_display_receives_current_share_link_on_connect(self) -> None:
        self.app.config["SHARE_LINK"] = {
            "status": "ready",
            "url": "https://test-wheel.trycloudflare.com",
            "message": "Share this link with guests.",
        }
        socket_client = self.socketio.test_client(self.app)

        socket_client.emit("display_join")
        received = socket_client.get_received()

        share_events = [event for event in received if event["name"] == "share_link"]
        self.assertEqual(share_events[-1]["args"][0]["url"], "https://test-wheel.trycloudflare.com")

    def test_extracts_quick_tunnel_url_from_cloudflared_output(self) -> None:
        output_line = "Your quick Tunnel has been created: https://bright-wheel.trycloudflare.com"

        self.assertEqual(
            extract_cloudflare_url(output_line),
            "https://bright-wheel.trycloudflare.com",
        )

    def test_finds_cloudflared_in_winget_package_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable = Path(temporary_dir) / "Microsoft" / "WinGet" / "Packages" / "Cloudflare.cloudflared_test" / "cloudflared.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch("app.shutil.which", return_value=None), patch.dict(os.environ, {"LOCALAPPDATA": temporary_dir}):
                self.assertEqual(find_cloudflared(), str(executable))

    def test_start_tunnel_reports_url_when_cloudflared_outputs_it(self) -> None:
        fake_process = Mock()
        fake_process.stdout = ["https://bright-wheel.trycloudflare.com\n"]
        fake_process.wait.return_value = 0
        socketio = Mock()

        with patch("app.find_cloudflared", return_value="cloudflared"), patch("app.subprocess.Popen", return_value=fake_process), patch("app.threading.Thread") as thread_class:
            process = start_cloudflare_tunnel(self.app, socketio, 5000)
            target = thread_class.call_args.kwargs["target"]
            target(*thread_class.call_args.kwargs["args"])

        self.assertIs(process, fake_process)
        self.assertEqual(self.app.config["SHARE_LINK"]["url"], "https://bright-wheel.trycloudflare.com")
        socketio.emit.assert_any_call("share_link", self.app.config["SHARE_LINK"], to="display")

    def test_only_one_host_can_join(self) -> None:
        first = self.client.post("/join", data={"role": "host"})
        self.assertEqual(first.status_code, 302)
        second_client = self.app.test_client()

        second = second_client.post("/join", data={"role": "host"})

        self.assertEqual(second.status_code, 400)
        self.assertIn(b"A host has already joined", second.data)

    def test_expert_can_only_be_claimed_once(self) -> None:
        expert_id = next(iter(self.app.extensions["wheel_game"].experts))
        first = self.client.post("/join", data={"role": "expert", "expert_id": expert_id})
        second = self.app.test_client().post("/join", data={"role": "expert", "expert_id": expert_id})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertIn(b"already joined", second.data)

    def test_player_names_are_case_insensitively_unique(self) -> None:
        first = self.client.post("/join", data={"role": "player", "name": "Jamie"})
        second = self.app.test_client().post("/join", data={"role": "player", "name": "jamie"})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertIn(b"already in use", second.data)

    def test_participant_photos_are_normalized_and_only_sent_to_display(self) -> None:
        display_socket = self.socketio.test_client(self.app)
        display_socket.emit("display_join")
        display_socket.get_received()

        player_client = self.app.test_client()
        player_photo = BytesIO()
        Image.new("RGB", (900, 600), "red").save(player_photo, format="PNG")
        player_photo.seek(0)
        self.assertEqual(
            player_client.post(
                "/join",
                data=MultiDict([
                    ("role", "player"),
                    ("name", "Jamie"),
                    ("photo", (BytesIO(), "", "application/octet-stream")),
                    ("photo", (player_photo, "portrait.png", "image/png")),
                ]),
                content_type="multipart/form-data",
            ).status_code,
            302,
        )

        expert_id = next(iter(self.app.extensions["wheel_game"].experts))
        expert_client = self.app.test_client()
        expert_photo = BytesIO()
        Image.new("RGB", (300, 700), "blue").save(expert_photo, format="HEIF")
        expert_photo.seek(0)
        self.assertEqual(
            expert_client.post(
                "/join",
            data={"role": "expert", "expert_id": expert_id, "photo": (expert_photo, "portrait.heic", "image/heic")},
                content_type="multipart/form-data",
            ).status_code,
            302,
        )

        display_states = [event["args"][0] for event in display_socket.get_received() if event["name"] == "state"]
        display_state = display_states[-1]
        player_photo_url = display_state["players"][0]["avatar_url"]
        expert_photo_url = next(expert["avatar_url"] for expert in display_state["experts"] if expert["id"] == expert_id)
        response = player_client.get(player_photo_url)
        self.assertEqual(response.status_code, 200)
        normalized_image = Image.open(BytesIO(response.data))
        self.assertEqual(normalized_image.format, "JPEG")
        self.assertLessEqual(max(normalized_image.size), 512)
        response.close()
        self.assertTrue(expert_photo_url.startswith("/participant-images/"))

        host_client = self.app.test_client()
        host_client.post("/join", data={"role": "host"})
        host_socket = self.socketio.test_client(self.app, flask_test_client=host_client)
        host_state = next(event["args"][0] for event in host_socket.get_received() if event["name"] == "state")
        self.assertNotIn("avatar_url", host_state["players"][0])
        self.assertNotIn("avatar_url", next(expert for expert in host_state["experts"] if expert["id"] == expert_id))

        player_socket = self.socketio.test_client(self.app, flask_test_client=player_client)
        player_state = next(event["args"][0] for event in player_socket.get_received() if event["name"] == "state")
        self.assertNotIn("avatar_url", player_state["players"][0])

        game = self.app.extensions["wheel_game"]
        shutdown_id = next(candidate for candidate in game.experts if candidate != expert_id)
        for payload in (
            {"action": "start"},
            {"action": "select_player"},
            {"action": "choose_category", "category": game.categories[0]},
            {"action": "choose_shutdown", "expert_id": shutdown_id},
            {"action": "resolve_landing", "expert_id": expert_id},
        ):
            self.assertTrue(host_socket.emit("host_command", payload, callback=True)["ok"])

        display_state = [event["args"][0] for event in display_socket.get_received() if event["name"] == "state"][-1]
        selected_expert = next(expert for expert in display_state["experts"] if expert["id"] == expert_id)
        self.assertTrue(selected_expert["selected"])
        self.assertEqual(selected_expert["avatar_url"], expert_photo_url)

    def test_invalid_and_oversized_photos_are_rejected(self) -> None:
        invalid = self.client.post(
            "/join",
            data={"role": "player", "name": "Jamie", "photo": (BytesIO(b"not an image"), "portrait.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"valid JPG, PNG, WebP, or HEIC", invalid.data)
        self.assertEqual(self.app.extensions["wheel_game"].players, {})
        invalid.close()

        with patch("app.MAX_IMAGE_BYTES", 10):
            oversized = self.client.post(
                "/join",
                data={"role": "player", "name": "Jamie", "photo": (BytesIO(b"x" * 11), "portrait.jpg", "image/jpeg")},
                content_type="multipart/form-data",
            )
        self.assertEqual(oversized.status_code, 400)
        self.assertIn(b"15 MB or smaller", oversized.data)
        self.assertEqual(self.app.extensions["wheel_game"].players, {})
        oversized.close()

    def test_non_host_socket_cannot_control_game(self) -> None:
        socket_client = self.socketio.test_client(self.app, flask_test_client=self.client)

        result = socket_client.emit("host_command", {"action": "start"}, callback=True)

        self.assertFalse(result["ok"])
        self.assertEqual(self.app.extensions["wheel_game"].phase.name, "LOBBY")

    def test_host_controls_question_and_expert_answer_is_revealed_later(self) -> None:
        host_client = self.app.test_client()
        host_client.post("/join", data={"role": "host"})
        self.app.test_client().post("/join", data={"role": "player", "name": "Jamie"})
        expert_id = next(iter(self.app.extensions["wheel_game"].experts))
        expert_client = self.app.test_client()
        expert_client.post("/join", data={"role": "expert", "expert_id": expert_id})
        host_socket = self.socketio.test_client(self.app, flask_test_client=host_client)
        expert_socket = self.socketio.test_client(self.app, flask_test_client=expert_client)

        for payload in (
            {"action": "start"},
            {"action": "select_player"},
            {"action": "choose_category", "category": "Football"},
            {"action": "choose_shutdown", "expert_id": "sarah"},
            {"action": "resolve_landing", "expert_id": expert_id},
            {"action": "confirm_landing"},
        ):
            self.assertTrue(host_socket.emit("host_command", payload, callback=True)["ok"])

        answer_result = expert_socket.emit("submit_answer", {"answer": "B"}, callback=True)
        self.assertTrue(answer_result["ok"])
        before_reveal = self.app.extensions["wheel_game"].snapshot()
        self.assertEqual(before_reveal["expert_answers"], {})
        self.assertTrue(before_reveal["experts"][0]["answered"])

        correct_answer = self.app.extensions["wheel_game"].current_question.correct
        self.assertTrue(host_socket.emit("host_command", {"action": "reveal_answer", "answer": correct_answer}, callback=True)["ok"])
        after_reveal = self.app.extensions["wheel_game"].snapshot()
        self.assertEqual(after_reveal["expert_answers"], {expert_id: "B"})

        host_socket.disconnect()
        expert_socket.disconnect()

    def test_host_confirms_powerups_and_players_vote(self) -> None:
        game = self.app.extensions["wheel_game"]
        host_client = self.app.test_client()
        host_client.post("/join", data={"role": "host"})
        chair_client = self.app.test_client()
        chair_client.post("/join", data={"role": "player", "name": "Jamie"})
        voter_client = self.app.test_client()
        voter_client.post("/join", data={"role": "player", "name": "Taylor"})
        chair_id = next(player.id for player in game.players.values() if player.name == "Jamie")
        voter_id = next(player.id for player in game.players.values() if player.name == "Taylor")
        expert_ids = list(game.experts)
        host_socket = self.socketio.test_client(self.app, flask_test_client=host_client)
        voter_socket = self.socketio.test_client(self.app, flask_test_client=voter_client)
        send = lambda payload: host_socket.emit("host_command", payload, callback=True)

        self.assertTrue(send({"action": "start"})["ok"])
        game.select_player(chair_id)
        for payload in (
            {"action": "choose_category", "category": game.categories[0]},
            {"action": "choose_shutdown", "expert_id": expert_ids[1]},
            {"action": "resolve_landing", "expert_id": expert_ids[0]},
            {"action": "use_powerup", "powerup": "respin"},
            {"action": "resolve_landing", "expert_id": expert_ids[0]},
        ):
            self.assertTrue(send(payload)["ok"], payload)
        self.assertEqual(game.phase.name, "QUESTION")
        self.assertFalse(send({"action": "use_powerup", "powerup": "respin"})["ok"])

        self.assertTrue(send({"action": "use_powerup", "powerup": "fifty_fifty"})["ok"])
        self.assertTrue(send({"action": "use_powerup", "powerup": "peek", "expert_id": expert_ids[0]})["ok"])
        self.assertTrue(send({"action": "use_powerup", "powerup": "ask_players"})["ok"])

        voter_socket.get_received()
        chair_socket = self.socketio.test_client(self.app, flask_test_client=chair_client)
        chair_state = [event for event in chair_socket.get_received() if event["name"] == "state"][-1]["args"][0]
        self.assertIsNone(chair_state["current_question"])
        self.assertFalse(chair_socket.emit("submit_guess", {"answer": "A"}, callback=True)["ok"])
        self.assertFalse(host_socket.emit("submit_guess", {"answer": "A"}, callback=True)["ok"])
        self.assertTrue(voter_socket.emit("submit_guess", {"answer": "C"}, callback=True)["ok"])

        voter_state = [event for event in voter_socket.get_received() if event["name"] == "state"][-1]["args"][0]
        self.assertEqual(voter_state["my_guess"], "C")
        self.assertIsNone(voter_state["peek"])
        self.assertEqual(voter_state["current_question"]["text"], game.current_question.text)
        self.assertIsNone(voter_state["current_question"]["correct"])
        self.assertNotIn("host_correct", voter_state)
        host_state = [event for event in host_socket.get_received() if event["name"] == "state"][-1]["args"][0]
        self.assertEqual(host_state["host_correct"], game.current_question.correct)

        self.assertTrue(send({"action": "close_vote"})["ok"])
        self.assertEqual(game.snapshot()["audience"]["counts"]["C"], 1)
        self.assertEqual(game.players[voter_id].used_powerups, set())
        self.assertEqual(game.players[chair_id].used_powerups, {"respin", "fifty_fifty", "peek", "ask_players"})

        self.assertTrue(send({"action": "reveal_answer", "answer": "A"})["ok"])
        self.assertTrue(send({"action": "awards_start"})["ok"])
        self.assertTrue(send({"action": "awards_next"})["ok"])
        voter_state = [event for event in voter_socket.get_received() if event["name"] == "state"][-1]["args"][0]
        self.assertNotIn("awards", voter_state)
        self.assertIsNone(voter_state["awards_show"]["award"]["winners"])
        self.assertTrue(send({"action": "awards_end"})["ok"])
        self.assertIsNone(game.awards_step)

        for client in (host_socket, voter_socket, chair_socket):
            client.disconnect()


if __name__ == "__main__":
    unittest.main()