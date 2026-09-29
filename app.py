import atexit
import io
import os
import re
import secrets
import shutil
import subprocess
import threading
import uuid
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from flask import Flask, abort, redirect, render_template, request, send_from_directory, session, url_for
from flask_socketio import SocketIO, emit, join_room
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from game.data import DataFileError, load_experts, load_questions
from game.engine import GameEngine, GameError
from game.models import Player


BASE_DIR = Path(__file__).resolve().parent
CLOUDFLARE_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
register_heif_opener()


def _save_participant_image(upload, upload_directory: Path) -> str | None:
    if not upload or not upload.filename:
        return None

    image_bytes = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Photos must be 15 MB or smaller.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(image_bytes))
            if image.format not in {"JPEG", "PNG", "WEBP", "HEIF"}:
                raise ValueError("Choose a JPG, PNG, WebP, or HEIC photo.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Photos must be 50 megapixels or smaller.")
            image.verify()

            image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            if "A" in image.getbands():
                rgba = image.convert("RGBA")
                normalized = Image.new("RGB", rgba.size, "#f0f1ff")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = image.convert("RGB")
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, UnidentifiedImageError) as error:
        raise ValueError("Choose a valid JPG, PNG, WebP, or HEIC photo.") from error

    filename = f"{secrets.token_hex(16)}.jpg"
    normalized.save(upload_directory / filename, format="JPEG", quality=85, optimize=True)
    return filename


def find_cloudflared() -> str | None:
    executable = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if executable:
        return executable

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    for path in (
        Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
        Path(local_app_data) / "Microsoft" / "WindowsApps" / "cloudflared.exe",
    ):
        if path.is_file():
            return str(path)

    packages_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if packages_dir.is_dir():
        for package_dir in packages_dir.glob("Cloudflare.cloudflared*"):
            for relative_path in (Path("cloudflared.exe"), Path("bin") / "cloudflared.exe"):
                executable_path = package_dir / relative_path
                if executable_path.is_file():
                    return str(executable_path)
    return None


def extract_cloudflare_url(output_line: str) -> str | None:
    match = CLOUDFLARE_URL_PATTERN.search(output_line)
    return match.group(0) if match else None


def _watch_cloudflare_tunnel(app: Flask, socketio: SocketIO, process: subprocess.Popen[str]) -> None:
    if process.stdout is not None:
        for output_line in process.stdout:
            public_url = extract_cloudflare_url(output_line)
            if public_url:
                link_state = {
                    "status": "ready",
                    "url": public_url,
                    "message": "Share this link with guests.",
                }
                app.config["SHARE_LINK"] = link_state
                print(f"Cloudflare guest link: {public_url}")
                socketio.emit("share_link", link_state, to="display")

    process.wait()
    if app.config["SHARE_LINK"]["status"] == "starting":
        link_state = {
            "status": "unavailable",
            "url": None,
            "message": "Cloudflare stopped before a guest link was created. Check the server terminal.",
        }
        app.config["SHARE_LINK"] = link_state
        socketio.emit("share_link", link_state, to="display")


def start_cloudflare_tunnel(app: Flask, socketio: SocketIO, port: int) -> subprocess.Popen[str] | None:
    executable = find_cloudflared()
    if not executable:
        app.config["SHARE_LINK"] = {
            "status": "unavailable",
            "url": None,
            "message": "cloudflared was not found. Install it or add it to PATH, then restart the app.",
        }
        print("Cloudflare tunnel not started: cloudflared was not found on PATH.")
        return None

    app.config["SHARE_LINK"] = {
        "status": "starting",
        "url": None,
        "message": "Starting the Cloudflare guest link...",
    }
    try:
        process = subprocess.Popen(
            [executable, "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        app.config["SHARE_LINK"] = {
            "status": "unavailable",
            "url": None,
            "message": f"Could not start cloudflared: {error}",
        }
        print(f"Cloudflare tunnel could not start: {error}")
        return None

    app.extensions["cloudflare_tunnel"] = process
    app.extensions["wheel_socketio"].emit("share_link", app.config["SHARE_LINK"], to="display")
    threading.Thread(
        target=_watch_cloudflare_tunnel,
        args=(app, socketio, process),
        daemon=True,
        name="cloudflare-tunnel-output",
    ).start()
    return process


def stop_cloudflare_tunnel(app: Flask) -> None:
    process = app.extensions.get("cloudflare_tunnel")
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def create_app(testing: bool = False, data_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("THE_WHEEL_SECRET", secrets.token_hex(32)),
        TESTING=testing,
        SHARE_LINK={"status": "disabled", "url": None, "message": ""},
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    content_dir = data_dir or BASE_DIR / "data"
    experts = load_experts(content_dir / "experts.csv")
    questions = load_questions(content_dir / "questions.csv", experts)
    game = GameEngine(experts, questions)
    socketio = SocketIO(app, async_mode="threading")
    upload_storage = TemporaryDirectory(prefix="the-wheel-uploads-")
    atexit.register(upload_storage.cleanup)
    upload_directory = Path(upload_storage.name)
    lock = threading.RLock()
    identities: dict[str, dict[str, str]] = {}
    expert_claims: dict[str, str] = {}
    host_identity_id: str | None = None

    app.extensions["wheel_game"] = game
    app.extensions["wheel_socketio"] = socketio
    app.extensions["wheel_identities"] = identities
    app.extensions["wheel_upload_storage"] = upload_storage
    app.extensions["wheel_upload_directory"] = upload_directory

    def current_identity() -> dict[str, str] | None:
        identity_id = session.get("identity_id")
        return identities.get(identity_id) if identity_id else None

    def uploaded_photo():
        return next((photo for photo in request.files.getlist("photo") if photo and photo.filename), None)

    def lobby_state() -> dict[str, object]:
        return {
            "host_available": host_identity_id is None,
            "experts": [
                {
                    "id": expert.id,
                    "name": expert.name,
                    "category": expert.category,
                    "available": expert.id not in expert_claims,
                }
                for expert in game.experts.values()
            ],
        }

    def state_for(role: str, participant_id: str | None = None) -> dict[str, object]:
        state = game.snapshot()
        state["role"] = role
        state["participant_id"] = participant_id
        expert_states = []
        expert_snapshots = cast(list[dict[str, object]], state["experts"])
        for expert in expert_snapshots:
            expert_state = {**expert, "joined": expert["id"] in expert_claims}
            if role == "display":
                identity = identities.get(expert_claims.get(cast(str, expert["id"]), ""))
                if identity and identity.get("avatar_filename"):
                    expert_state["avatar_url"] = f"/participant-images/{identity['avatar_filename']}"
            expert_states.append(expert_state)
        state["experts"] = expert_states
        if role == "display":
            player_snapshots = cast(list[dict[str, object]], state["players"])
            state["players"] = [
                {
                    **player,
                    **(
                        {"avatar_url": f"/participant-images/{identities[cast(str, player['id'])]['avatar_filename']}"}
                        if identities.get(cast(str, player["id"]), {}).get("avatar_filename")
                        else {}
                    ),
                }
                for player in player_snapshots
            ]
        if role == "player":
            state["current_question"] = None
            state["expert_answers"] = {}
            state["player_answer"] = None
            state["peek"] = None
            state["audience_voted"] = participant_id in game.audience_votes
        return state

    def broadcast_lobby() -> None:
        socketio.emit("lobby", lobby_state(), to="lobby")

    def broadcast_state() -> None:
        socketio.emit("state", state_for("display"), to="display")
        socketio.emit("state", state_for("host"), to="host")
        for identity in identities.values():
            if identity["role"] == "expert":
                socketio.emit("state", state_for("expert", identity["expert_id"]), to=f"expert:{identity['expert_id']}")
            elif identity["role"] == "player":
                socketio.emit("state", state_for("player", identity["id"]), to=f"player:{identity['id']}")

    @app.get("/")
    def landing():
        identity = current_identity()
        if identity:
            return redirect(url_for("host" if identity["role"] == "host" else "player"))
        return render_template("landing.html", lobby=lobby_state(), errors=[], form={})

    @app.post("/join")
    def join_game():
        nonlocal host_identity_id
        role = request.form.get("role", "").strip().lower()
        form = {key: request.form.get(key, "") for key in ("role", "name", "expert_id")}
        errors: list[str] = []
        with lock:
            identity_id = uuid.uuid4().hex
            identity: dict[str, str] | None = None
            if role == "host":
                if host_identity_id is not None:
                    errors.append("A host has already joined this game.")
                else:
                    identity = {"id": identity_id, "role": "host", "name": "Host"}
                    host_identity_id = identity_id
            elif role == "expert":
                expert_id = form["expert_id"]
                expert = game.experts.get(expert_id)
                if expert is None:
                    errors.append("Choose an expert from the list.")
                elif expert_id in expert_claims:
                    errors.append("That expert has already joined.")
                else:
                    try:
                        avatar_filename = _save_participant_image(uploaded_photo(), upload_directory)
                    except ValueError as error:
                        errors.append(str(error))
                    else:
                        identity = {
                            "id": identity_id,
                            "role": "expert",
                            "name": expert.name,
                            "expert_id": expert.id,
                        }
                        if avatar_filename:
                            identity["avatar_filename"] = avatar_filename
                        expert_claims[expert_id] = identity_id
            elif role == "player":
                name = form["name"].strip()
                if not name:
                    errors.append("Enter your name to join as a player.")
                elif len(name) > 32:
                    errors.append("Player names must be 32 characters or fewer.")
                elif any(player.name.casefold() == name.casefold() for player in game.players.values()):
                    errors.append("That player name is already in use.")
                else:
                    try:
                        avatar_filename = _save_participant_image(uploaded_photo(), upload_directory)
                    except ValueError as error:
                        errors.append(str(error))
                    else:
                        identity = {"id": identity_id, "role": "player", "name": name}
                        if avatar_filename:
                            identity["avatar_filename"] = avatar_filename
                        try:
                            game.add_player(Player(identity_id, name))
                        except GameError as error:
                            errors.append(str(error))
                            if avatar_filename:
                                (upload_directory / avatar_filename).unlink(missing_ok=True)
            else:
                errors.append("Choose Host, Expert, or Player.")

            if not errors:
                if identity is None:
                    raise RuntimeError("A successful join must create an identity.")
                identities[identity_id] = identity
                session["identity_id"] = identity_id
                broadcast_lobby()
                broadcast_state()
                return redirect(url_for("host" if role == "host" else "player"))

        return render_template("landing.html", lobby=lobby_state(), errors=errors, form=form), 400

    @app.get("/participant-images/<filename>")
    def participant_image(filename: str):
        if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
            abort(404)
        response = send_from_directory(upload_directory, filename, mimetype="image/jpeg", max_age=0)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/host")
    def host():
        identity = current_identity()
        if not identity or identity["role"] != "host":
            return redirect(url_for("landing"))
        return render_template("host.html", identity=identity)

    @app.get("/player")
    def player():
        identity = current_identity()
        if not identity or identity["role"] not in {"player", "expert"}:
            return redirect(url_for("landing"))
        return render_template("player.html", identity=identity)

    @app.get("/display")
    def display():
        return render_template("display.html")

    @socketio.on("connect")
    def on_connect():
        join_room("lobby")
        emit("lobby", lobby_state())
        identity = current_identity()
        if identity:
            if identity["role"] == "host":
                join_room("host")
                emit("state", state_for("host"))
            elif identity["role"] == "expert":
                join_room(f"expert:{identity['expert_id']}")
                emit("state", state_for("expert", identity["expert_id"]))
            else:
                join_room(f"player:{identity['id']}")
                emit("state", state_for("player", identity["id"]))

    @socketio.on("display_join")
    def on_display_join():
        join_room("display")
        emit("state", state_for("display"))
        emit("share_link", app.config["SHARE_LINK"])

    @socketio.on("host_command")
    def on_host_command(payload: dict[str, object]):
        identity = current_identity()
        if not identity or identity["role"] != "host":
            return {"ok": False, "error": "Only the host can control the game."}
        action = str(payload.get("action", ""))
        cue: str | None = None
        try:
            with lock:
                if action == "start":
                    game.start()
                elif action == "reset":
                    game.reset()
                elif action == "select_player":
                    chosen = game.select_player()
                    cue = "player_select"
                elif action == "choose_category":
                    game.choose_category(str(payload.get("category", "")))
                elif action == "choose_shutdown":
                    game.choose_shutdown(str(payload.get("expert_id", "")))
                elif action == "spin":
                    if game.phase.name != "SPINNING":
                        raise GameError("The spin is not ready yet.")
                    cue = "spin"
                elif action == "resolve_landing":
                    game.resolve_landing(str(payload.get("expert_id", "")))
                    cue = "question" if game.phase.name == "QUESTION" else "spin_stop"
                elif action == "confirm_landing":
                    game.confirm_landing()
                    cue = "question"
                elif action == "use_powerup":
                    powerup = str(payload.get("powerup", ""))
                    if powerup == "respin":
                        game.use_respin()
                    elif powerup == "fifty_fifty":
                        game.use_fifty_fifty()
                    elif powerup == "peek":
                        game.use_peek(str(payload.get("expert_id", "")))
                    elif powerup == "ask_players":
                        game.start_audience_vote()
                    else:
                        raise GameError("Unknown power-up.")
                    cue = "powerup"
                elif action == "close_vote":
                    game.close_audience_vote()
                elif action == "reveal_answer":
                    result = game.reveal_answer(str(payload.get("answer", "")))
                    cue = "correct" if result["type"] == "correct" else "incorrect"
                elif action == "advance":
                    game.advance()
                    cue = "final_reveal" if game.phase.name == "FINAL_QUESTION" else None
                elif action == "reveal_final_answer":
                    result = game.reveal_final_answer(str(payload.get("answer", "")))
                    cue = "victory" if result["type"] == "game_won" else "incorrect"
                else:
                    raise GameError("Unknown host action.")
            broadcast_state()
            broadcast_lobby()
            if cue:
                socketio.emit("cue", {"name": cue}, to="display")
            return {"ok": True}
        except (GameError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    @socketio.on("submit_answer")
    def on_submit_answer(payload: dict[str, object]):
        identity = current_identity()
        if not identity or identity["role"] != "expert":
            return {"ok": False, "error": "Only joined experts can answer."}
        try:
            with lock:
                game.submit_expert_answer(identity["expert_id"], str(payload.get("answer", "")))
            broadcast_state()
            return {"ok": True}
        except GameError as error:
            return {"ok": False, "error": str(error)}

    @socketio.on("submit_vote")
    def on_submit_vote(payload: dict[str, object]):
        identity = current_identity()
        if not identity or identity["role"] != "player":
            return {"ok": False, "error": "Only joined players can vote."}
        try:
            with lock:
                game.submit_audience_vote(identity["id"], str(payload.get("answer", "")))
            broadcast_state()
            return {"ok": True}
        except GameError as error:
            return {"ok": False, "error": str(error)}

    @app.context_processor
    def inject_game_name():
        return {"game_name": "The Wheel"}

    app.extensions["wheel_broadcast_state"] = broadcast_state
    return app


app = create_app()
socketio = app.extensions["wheel_socketio"]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"The Wheel is running at http://localhost:{port}")
    print("Open /display on the screen. The guest link will appear there when Cloudflare is ready.")
    start_cloudflare_tunnel(app, socketio, port)
    try:
        socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
    finally:
        stop_cloudflare_tunnel(app)