import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()