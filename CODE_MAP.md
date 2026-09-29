# The Wheel Code Map

A quick guide to where behavior lives, how the app is wired, and where to look before making a change.

## Start Here

| If you need to change... | Start with... |
| --- | --- |
| Game rules, phases, scoring, turn flow, question drawing | `game/engine.py` |
| CSV validation or content loading | `game/data.py` |
| Core data types and phase names | `game/models.py` |
| Routes, joining, identity, Socket.IO events, state visibility, audio cues, Cloudflare tunnel | `app.py` |
| Host controls, participant phone behavior, TV rendering, client Socket.IO events | `static/js/game.js` |
| Page structure and element IDs used by JavaScript | `templates/*.html` |
| Layout, responsive styling, colors, animation | `static/css/game.css` |
| Expert/category content | `data/experts.csv` |
| Questions and answers | `data/questions.csv` |
| Data loader behavior | `tests/test_data.py` |
| Game rule behavior | `tests/test_engine.py` |
| Routes, role permissions, Socket.IO, tunnel behavior | `tests/test_web.py` |

## Request-to-Display Flow

1. `app.py` loads experts and questions through `game/data.py`, then creates one `GameEngine`.
2. A guest posts to `/join`. Flask validates the role and creates an in-memory identity stored in the Flask session; player registration and expert claims are also held in memory.
3. The browser connects through Socket.IO. The host sends `host_command`; experts send `submit_answer`; the display joins with `display_join`.
4. `app.py` checks the caller's role, calls the matching `GameEngine` method, then broadcasts fresh state to the host, TV, experts, and players.
5. `state_for()` applies role-specific state shaping. In particular, player clients do not receive the current question, expert answers, or the player's answer. The engine snapshot also withholds correct answers and expert choices until the reveal.
6. `static/js/game.js` renders the received state for the current page. Host actions and expert answers are sent back over Socket.IO.

## File Responsibilities

### Server and game logic

- `app.py`: Flask app factory (`create_app`), HTTP routes (`/`, `/join`, `/host`, `/player`, `/display`), session identity, host/expert authorization, Socket.IO rooms and events, state broadcasts, and optional Cloudflare Quick Tunnel startup/shutdown. It translates browser actions into engine calls; game rules belong in the engine.
- `game/engine.py`: authoritative game state and transitions. `GamePhase` gates available actions; methods cover joining players, selecting players/categories, shutting down experts, resolving the chair landing, collecting/revealing answers, advancing turns, final question, reset, and snapshots. It also draws questions without replacement until a category's pool runs out and tracks player/expert stats.
- `game/data.py`: reads CSV files, trims cells, creates stable IDs, and raises `DataFileError` with file/row context for invalid content. Category matching is case-sensitive for question categories; expert names and generated IDs are checked for collisions.
- `game/models.py`: `Expert`, `Question`, and `Player` dataclasses plus the `GamePhase` enum. `Player` derives incorrect-answer count and accuracy.
- `game/__init__.py`: package marker; game behavior is in the modules above.

### Browser interface

- `templates/landing.html`: role selection and join form. The form posts to `/join`; its `data-page` value tells the shared JavaScript to update the live lobby.
- `templates/host.html`: host control page and player/expert rosters. JavaScript builds phase-specific controls into `#host-controls`.
- `templates/player.html`: shared phone page for both players and experts. `data-role`, `data-id`, and `data-expert-id` identify the participant to the client script.
- `templates/display.html`: TV layout, category/expert progress, guest-link panel, and optional audio element.
- `static/js/game.js`: shared client controller selected by each body's `data-page`. It renders host, participant, and display state; submits host commands and expert answers; handles lobby updates, connection status, audio cues, player reveal animation, and the share link. If changing an element ID in a template, update its JavaScript lookup too.
- `static/css/game.css`: all page styles, responsive layouts, colors, and visual states. It also imports the display/body fonts from Google Fonts.
- `static/audio/`: optional MP3 cues. Expected filenames are listed in `static/audio/audio_files_needed.txt` and mapped to cue names in `game.js`.

### Content and project docs

- `data/experts.csv`: expert names and categories; `Birthday` is reserved for the final question.
- `data/questions.csv`: question, four options, and correct answer (`A`-`D`), grouped by category.
- `README.md`: setup, run instructions, guest tunnel, content editing, optional audio, and test command.
- `The Wheel — Birthday Game Technical Overview.md`: broader product/technical overview.
- `the_wheel_birthday_party_rules.md`: source game rules and intended play behavior.
- `requirements.txt`: Python runtime dependencies (Flask and Flask-SocketIO stack).
- `app.py`: the executable entry point when run directly; loads on port `5000` by default and starts `cloudflared` if available.

## State and Behavior Notes

- Game state, joined identities, expert claims, scores, and question pools are in memory. Restarting the server resets the game; there is no persistence layer.
- `app.py` creates a module-level app as well as exposing `create_app()` for tests. CSV loading happens during app creation, so invalid/missing CSV content can stop startup.
- The server is authoritative: client rendering should not implement game rules. Validate new actions in `GameEngine`, authorize/route them in `app.py`, then reflect them in `game.js` and the relevant template.
- Socket.IO room names are `lobby`, `display`, `host`, `expert:<expert_id>`, and `player:<identity_id>`. A state-changing host action broadcasts state and lobby data; cue events go only to the display.
- When changing snapshots or broadcast targeting, preserve role-specific privacy. The TV and host get reveal information; players get a deliberately reduced snapshot.
- `resolve_landing()` returns `False` when the chair lands on a shut-down expert: the turn ends without a question, which is not the same path as answering incorrectly. Otherwise the game enters `LANDED` (keep the expert or use Re-spin) and the host calls `confirm_landing()`; if the player has already used Re-spin, it goes straight to `QUESTION`.
- Power-ups (`POWERUPS` in `game/engine.py`): Ask the Players, 50:50, Peek at an Expert, Re-spin. Each player gets one use of each for the whole game (`Player.used_powerups`); only a full game reset restores them. The host confirms every use via `host_command` `use_powerup`; players vote with the `submit_vote` event. Players' state omits `peek`. They are not available on the Birthday question.

## Tests and Commands

Run the full suite from the repository root:

```powershell
python -m unittest discover -v
```

For a focused test run, use `python -m unittest tests.test_engine`, `python -m unittest tests.test_data`, or `python -m unittest tests.test_web`.

When changing a game rule, add or update an engine test first. For role checks, routes, socket events, state privacy, or the tunnel, use the web tests. For CSV schema/validation, use the data tests. The Flask-SocketIO tests exercise the server event contract without launching a browser.