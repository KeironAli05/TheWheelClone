# !THIS IS A VIBECODED PROJECT!
I opted to vibecode this Game as it's going to be used at a birthday party for max an hour so I didn't think it would be worth all the in depth dev work for me to create it completely by myself

# The Wheel


A local, real-time birthday party game inspired by the supplied rules. The laptop runs the game server; open `/display` on the TV, then share the root URL (`/`) with guests. The server owns the game state and the host controls it from a phone.

## Run It

On Windows, install Python 3.10 or newer, clone the repository, then open PowerShell in the cloned project folder. Create and activate a virtual environment and install the dependencies:

```powershell
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks the activation script, allow it for this terminal only, then activate the environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Start the game:

```powershell
python app.py
```

Run `python app.py` from the project folder whenever you want to host a game. This starts the local server and automatically starts a Cloudflare Quick Tunnel when `cloudflared` is installed and available on PATH. Open `http://localhost:5000/display` on the TV; the temporary guest URL will appear there when the tunnel is ready. The URL is also printed in the server terminal.

The tunnel URL changes between runs. Keep the server running for the game; press `Ctrl+C` to stop both the app and its tunnel. If the display says `cloudflared` was not found, install it with WinGet, open a new terminal, then restart the app:

```powershell
winget install --id Cloudflare.cloudflared --exact --accept-source-agreements --accept-package-agreements
```

The browser loads the Socket.IO client and display fonts from CDNs, so the laptop and guest phones need internet access. Game/session state lives in memory and resets when the server restarts.

Players and experts can optionally add a photo when joining. JPG, PNG, WebP, and HEIC images up to 15 MB are accepted; uploads are normalized to small JPEG portraits (maximum 512 pixels) and kept in temporary storage until the server stops. Photos appear on the TV during player selection and beside the active player and selected expert. Anyone who can view the TV can see those photos.

## Party Data

Edit the CSV files before starting the server:

- `data/experts.csv`: one row per expert, with `name,category` columns. Expert names must be unique. `Birthday` is reserved for the final question.
- `data/questions.csv`: one row per question, with `category,question,a,b,c,d,correct` columns. `correct` must be `A`, `B`, `C`, or `D`; question categories must match an expert category or `Birthday`. The finale needs up to three unique Birthday questions: three for the best expert, two for the second-best, and one for the worst.

Every configured category and `Birthday` need at least one question. Questions are drawn randomly without repeats until a category's pool is used up. The app reports invalid data at startup with the filename and row.

No wheel image is needed. The chair is the wheel, and the TV display is built from text and game state.

## Tests

```powershell
python -m unittest discover -v
```