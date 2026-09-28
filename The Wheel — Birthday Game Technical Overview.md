# 🎡 The Wheel --- Birthday Game

A private, custom version of the UK gameshow **The Wheel**, built as an
interactive party game for a birthday.

The game is designed around an **IRL experience supported by a digital
game engine**. The host, experts and players are all physically present.
A laptop connected to a TV shows the question on screen, the host runs
the game from their phone, and experts answer on their phones.

The physical wheel is represented by a **spinny desk chair**, rather than
attempting to recreate the wheel digitally. No wheel graphic is needed.

------------------------------------------------------------------------

# 🏗️ Architecture

The application will use a **Python-based local server** exposed
temporarily to the internet using **Cloudflare Tunnel**.

``` text
                         INTERNET
                            │
                            │
                    Cloudflare Tunnel
                            │
                            ▼
                      ┌─────────────┐
                      │   LAPTOP    │
                      │             │
                      │ Python      │
                      │ Flask       │
                      │ Game State  │
                      └──────┬──────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
           📱 Host       📱 Experts     📱 Players
              │              │              │
              └──────────────┼──────────────┘
                             │
                         WebSockets
                             │
                             ▼
                       Live Game State
                             │
                             ▼
                         HDMI / Display
                             │
                             ▼
                            📺 TV
```

The laptop runs the actual game server locally.

**Cloudflare Tunnel** provides a temporary public URL so guests can
connect to the application from their phones without requiring the
laptop to be publicly hosted.

Guests do not need to be connected to the same Wi-Fi as the laptop.

Example:

``` text
https://random-name.trycloudflare.com
```

The URL can be shared with guests before or during the game. It points
at the root (`/`) landing page, so guests only ever need **one link**.

------------------------------------------------------------------------

# 🧰 Technology Stack

## Backend

-   **Python**
-   **Flask**
-   **Flask-SocketIO / WebSockets**
-   CSV files for expert and question data (hand-edited, parsed on startup)

The backend is responsible for:

-   Maintaining the authoritative game state
-   Handling connections from guest devices
-   Processing player/expert interactions
-   Serving the web interfaces
-   Broadcasting state changes to connected clients

------------------------------------------------------------------------

## Frontend

The frontend should remain lightweight and primarily consist of:

-   HTML
-   CSS
-   JavaScript / TypeScript

There is **no requirement for React**.

The application should remain primarily a Python project, with the
browser providing the presentation and interaction layer.

------------------------------------------------------------------------

## Networking

-   **Cloudflare Tunnel**
-   Cloudflare Quick Tunnel for development/testing

Example:

``` bash
cloudflared tunnel --url http://localhost:5000
```

This avoids requiring:

-   Port forwarding
-   Router configuration
-   A public server
-   A purchased domain
-   All guests to be on the same Wi-Fi

------------------------------------------------------------------------

# 🖥️ Application Interfaces

The application has a landing page plus three distinct interfaces.

## 0. Landing Page

``` text
/
```

This is the **only link shared with guests**. Guests aren't technical,
so it's a single, obvious form. Nobody needs to know about `/host` or
`/player`.

``` text
WHO ARE YOU?

( ) Host
( ) Expert
( ) Player

            ↓ (fields appear based on choice)

Expert →  ( ) Josh  - Football
          ( ) Sarah - Music
          ( ) Amy   - Films

Player →  Your name: [ __________ ]

[ LET'S GO ]
```

-   **Host** option is hidden once a host has joined.
-   **Expert** shows radio buttons for unclaimed experts only (from
    `experts.csv`). Claimed names disappear live.
-   **Player** shows a name box. Blank or duplicate names get a friendly
    error on the same page.
-   On submit, the server validates, creates a session token (cookie)
    and **redirects automatically** to `/host` or `/player`.
-   If someone opens the link again with a valid token, they're sent
    straight to their page --- no re-picking.
-   Opening `/host` or `/player` without a valid token redirects back
    to `/`.

## 1. Main Display

``` text
/display
```

This is the laptop screen, mirrored to the TV.

It is deliberately minimal. Its main job is to show:

-   The current question
-   The four answer options (A / B / C / D)
-   The correct-answer reveal
-   Category progress (which categories are cleared / remaining)

It also shows the random player-selection sequence (see *Choosing a
Player*) and plays audio (jingle, reveal stings).

There is **no wheel graphic**. The display contains **no controls** ---
it is purely a presentation layer driven by server events.

------------------------------------------------------------------------

## 2. Host Interface

``` text
/host
```

This is used on the **host's phone**, so the host can move around the
room and spin the chair while running the game.

It must be mobile-first with large buttons. The host controls include:

-   Starting and resetting the game
-   Triggering the random player selection
-   Entering the category chosen by the player
-   Entering the expert the player chose to shut down
-   Starting the spin (plays the jingle on the TV)
-   Entering which expert the chair landed on
-   Entering the player's final answer (A / B / C / D)
-   Revealing the correct answer
-   Advancing to the next round
-   Seeing which experts have submitted answers (and, after reveal, what
    they answered)

Only **one host** is allowed. Once a host has joined via the landing
page, the Host option is removed and any other attempt to open `/host`
is redirected to `/`. No PIN or password is required.

The controls should remain separate from the underlying game state so
that the host interface can change without changing the game engine.

------------------------------------------------------------------------

## 3. Guest Interface

``` text
/player
```

Experts and players arrive here automatically from the landing page.
The page adapts to their role:

-   **Experts** see their status and answer questions privately.
-   **Players** see their status (waiting / "YOU'RE UP!").

The interface should be intentionally simple and mobile-friendly.

------------------------------------------------------------------------

# 📱 Phone UI

A guest's phone should primarily communicate their current state and
provide interaction when required.

Example:

``` text
┌───────────────────────┐
│                       │
│        JOSH           │
│                       │
│      FOOTBALL         │
│                       │
└───────────────────────┘
```

The interface can change depending on the current game state.

Possible states include:

### Normal

The guest is waiting.

``` text
⚪ ACTIVE
```

### Category Selected

The guest's category has been selected.

``` text
🟡 SELECTED
```

### Locked

The guest is temporarily unavailable.

``` text
🔴 LOCKED
```

### Answering

(Experts only.) The phone displays the current multiple-choice question.

``` text
WHAT YEAR DID...?

[A]

[B]

[C]

[D]
```

After submitting:

``` text
ANSWER LOCKED ✓
```

The answer should not be changeable after submission.

------------------------------------------------------------------------

# 👥 Roles

There are three roles:

| Role   | Count     | Identity                              | Device use                          |
|--------|-----------|---------------------------------------|-------------------------------------|
| Host   | Exactly 1 | Not needed                            | Runs the whole game from their phone |
| Expert | Any       | Predefined name + category (CSV)      | Answers every question privately     |
| Player | Any       | Typed in on joining                   | Status only; answers out loud        |

The engine classes must be **data-agnostic**: no expert names,
categories or questions are hardcoded. Everything comes from the data
files, so the number of experts/categories is whatever the file
contains.

The current player answers **out loud**; the host enters their final
answer on the host phone.

## Expert Data

Experts are defined in `data/experts.csv`, filled in by the host before
the party. Each expert owns exactly one category.

``` csv
name,category
Josh,Football
Sarah,Music
Amy,Films
```

The repository ships with dummy data for testing.

------------------------------------------------------------------------

# ❓ Questions

Questions are stored in `data/questions.csv`, separately from the
application logic. Every question has **exactly four options** (A--D).

``` csv
category,question,a,b,c,d,correct
Football,Who won the 2022 World Cup?,France,Argentina,Brazil,Germany,B
Music,Which band released 'Wonderwall'?,Blur,Pulp,Oasis,Suede,C
Birthday,What is her favourite film?,Titanic,Shrek,Mamma Mia!,Clueless,C
```

-   `category` must match a category in `experts.csv`, or be the
    reserved category `Birthday` for the final question(s).
-   `correct` is the letter of the correct option.
-   Questions are drawn at random from the chosen category without
    repeats until the category's pool is exhausted.
-   The loader validates the file on startup (unknown categories,
    missing options, invalid `correct` letter) and fails loudly.

This allows the question bank to be edited without touching code. The
repository ships with dummy questions for testing.

------------------------------------------------------------------------

# 🎡 Physical Wheel

The wheel is **not rendered digitally**. The current player sits on a
spinning desk chair and the host physically spins them.

## Choosing a Player (random, with tension)

The **server** picks the next player at random from all joined players.
The result is decided server-side the moment the host triggers it; the
display then plays it out for suspense:

1.  Drumroll / tension audio starts.
2.  Player names flash on the TV in rapid succession.
3.  The cycling slows down, with a few near-misses on other names.
4.  It stops on the chosen player --- big reveal + sting.
5.  The chosen player's phone lights up: **"YOU'RE UP!"**. Everyone
    else's phone briefly shows the result too.

A player can be selected multiple times over the game.

## The Spin Sequence

``` text
Host enters category chosen by player
        │
        ▼
Host enters expert the player shut down
        │
        ▼
Host presses SPIN → jingle plays on TV
        │
        ▼
Host physically spins the player in the chair
        │
        ▼
Host enters the expert the chair landed on
        │
        ▼
Server checks: is that expert shut down?
   ├── Yes → turn ends, back to player selection
   └── No  → question is shown
```

An expert is shut down for this spin if either:

-   the player chose to shut them down this turn, or
-   they answered the previous question wrong (automatic).

The host's expert pickers show automatically shut-down experts clearly
so the host (and player) can see who is already out.

------------------------------------------------------------------------

# 🟡 Participant Status

Each expert should have a state represented by the game engine.

For example:

``` text
ACTIVE
SELECTED
LOCKED
ANSWERING
```

The exact states can be expanded as the game develops.

Example display:

``` text
Josh      🟡 SELECTED
Sarah     ⚪ ACTIVE
Amy       🔴 LOCKED
Tom       ⚪ ACTIVE
Megan     ⚪ ACTIVE
Callum    ⚪ ACTIVE
Keiron    ⚪ ACTIVE
```

Participant state is synchronised to the relevant phone and the main
display using WebSockets.

------------------------------------------------------------------------

# 📝 Answer Submission

During a question, **all experts** (including the one helping the
player) submit their answers privately through their phones. The
player answers out loud and the host enters the player's final answer.

The server records submissions against the current question and
participant.

Example:

``` text
Question 12

Josh    → B
Sarah   → B
Amy     → D
Tom     → B
Megan   → C
Callum  → B
Keiron  → B
```

Answers should remain hidden from other participants until the
appropriate reveal event is triggered by the game.

The server should be authoritative over whether an answer has been
submitted and whether it can still be changed.

------------------------------------------------------------------------

# 📊 Participant Performance

The application should maintain performance information for participants
throughout a game.

Useful statistics include:

``` text
questions_answered
correct_answers
incorrect_answers
accuracy
```

Example:

``` text
Josh      9 / 10
Sarah     8 / 10
Amy       7 / 10
Tom       6 / 10
```

These statistics can be used by the game engine or presentation layer
where relevant.

------------------------------------------------------------------------

# 🔄 Game State

The game should use an explicit state machine rather than relying on a
large collection of independent boolean variables.

A possible structure is:

``` python
class GamePhase(Enum):
    LOBBY = auto()             # people joining
    PLAYER_SELECT = auto()     # random player reveal
    CATEGORY_SELECT = auto()   # host enters player's category
    SHUTDOWN_SELECT = auto()   # host enters shut-down expert
    SPINNING = auto()          # jingle playing, chair spinning
    LANDED = auto()            # host enters landed expert
    QUESTION = auto()          # experts answering on phones
    ANSWER_REVEAL = auto()     # host entered player answer, reveal
    FINAL_QUESTION = auto()    # Birthday question
    GAME_WON = auto()
```

A landed-on shut-down expert goes straight from `LANDED` back to
`PLAYER_SELECT`. A wrong answer at `ANSWER_REVEAL` or `FINAL_QUESTION`
resets all cleared categories.

The exact states can evolve alongside the game, but the important
principle is that the backend should have **one authoritative
representation of the current game state**.

This makes the display, host interface and phones all representations of
the same game rather than separate applications with their own state.

------------------------------------------------------------------------

# 🌐 Real-Time Communication

WebSockets should be used for live game events.

For example:

``` text
Host changes participant state
        │
        ▼
Python server
        │
        ├──► Participant phone
        │
        └──► Main display
```

Or:

``` text
Host selects category
        │
        ▼
Python server
        │
        ├──► Relevant phone → category selected
        └──► Main display → category selected
```

Participant answers should also be sent back to the server:

``` text
Phone
  │
  ▼
WebSocket
  │
  ▼
Python server
  │
  ▼
Game state
```

This allows all connected devices to stay synchronised without
repeatedly refreshing the page.

------------------------------------------------------------------------

# 🔐 Guest Identity

Authentication does not need to be sophisticated --- it's an intimate
party.

``` text
/  (landing page)
   Choose Host / Expert (pick name) / Player (type name)
              ↓
   Server validates (host free? expert unclaimed? name unique?)
              ↓
   Create session token (cookie)
              ↓
   Redirect → /host  or  /player
              ↓
   Receive game updates
```

If a phone refreshes or reconnects, its stored token restores the same
identity (including the host).

A simple session/token system should prevent one guest from accidentally
controlling another guest's phone state.

------------------------------------------------------------------------

# 📁 Suggested Project Structure

``` text
the-wheel/
│
├── app.py
├── requirements.txt
├── README.md
│
├── game/
│   ├── game.py
│   ├── participant.py
│   ├── question.py
│   └── game_state.py
│
├── data/
│   ├── experts.csv
│   └── questions.csv
│
├── templates/
│   ├── landing.html
│   ├── display.html
│   ├── host.html
│   └── player.html
│
├── static/
│   ├── css/
│   ├── js/
│   ├── images/
│   └── audio/
│
└── tests/
    ├── test_game.py
    ├── test_questions.py
    └── test_websocket.py
```

The structure is intentionally simple. The game engine, data,
presentation and tests are separated without introducing unnecessary
infrastructure.

------------------------------------------------------------------------

# 🔊 Audio and Presentation

Audio should be treated as part of the presentation layer.

Potential assets include:

``` text
audio/
├── jingle.mp3
├── spin-start.mp3
├── spin-stop.mp3
├── question-reveal.mp3
├── correct.mp3
├── incorrect.mp3
├── lock.mp3
├── final-reveal.mp3
└── victory.mp3
```

Audio and animations should be triggered by game events rather than
requiring the host to manually operate them wherever possible.

The TV display should provide the visual identity of the game, while the
phones should remain functional and minimal.

------------------------------------------------------------------------

# 🖥️ Presentation Design

The application has two fundamentally different UI requirements.

## Main Display

The TV should feel like a **gameshow interface**.

It should prioritise:

-   Large typography
-   Clear participant/category information
-   Animations
-   Transitions
-   Question presentation
-   Answer reveals
-   Status indicators
-   Audio feedback
-   Dramatic game-state changes

## Mobile Interface

Phones should prioritise:

-   Large touch targets
-   Minimal information
-   Clear status
-   Fast interaction
-   Readability
-   Mobile responsiveness

The mobile interface should not attempt to replicate the TV
presentation.

------------------------------------------------------------------------

# 🔌 Runtime Environment

The intended runtime environment is a **single laptop running the
application locally**.

The laptop provides:

-   Python runtime
-   Flask server
-   WebSocket server
-   Game state
-   Question data
-   Static assets
-   Main display output

The TV/projector connects to the laptop for the main display.

Guests connect to the same application through the temporary Cloudflare
URL.

No dedicated production server is required.

------------------------------------------------------------------------

# 💷 Cost and Infrastructure

The initial application should be effectively free to run.

Expected infrastructure cost:

**£0**

The project is designed around:

-   Python
-   Flask
-   WebSockets
-   Cloudflare Quick Tunnel
-   A local laptop
-   An existing TV/projector
-   Guests' existing phones

No dedicated server, purchased domain, database, paid hosting or
specialist hardware is required for the initial version.

------------------------------------------------------------------------

# 💡 Design Principle

The project should be treated as a **physical party game with a digital
game engine**, rather than as a conventional web game.

The physical environment provides:

-   The Wheel → spinning chair
-   Participants → actual people
-   Discussion → actual conversation
-   Main player → physically present
-   TV → shared gameshow presentation

The software provides:

-   Questions
-   Participant status
-   Private answers
-   Game state
-   Real-time phone interaction
-   Presentation
-   Audio
-   Synchronisation between devices

The software's job is to make the physical game feel polished,
responsive and cohesive without trying to replace the physical parts of
the experience.
