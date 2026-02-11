# Spotify Playlist Downloader

Auto-discovers your public Spotify playlists and downloads songs as 320kbps MP3s via YouTube using yt-dlp. Supports incremental sync — only downloads new songs, never deletes existing files.

## Prerequisites

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg`)
- [deno](https://deno.land/) (`brew install deno`) — required by yt-dlp for YouTube signature solving
- A [Spotify Developer App](https://developer.spotify.com/dashboard)

## Setup

1. **Create a Spotify app** at https://developer.spotify.com/dashboard
   - Set the redirect URI to `http://127.0.0.1:8888/callback`

2. **Configure credentials** — edit `.env`:
   ```
   SPOTIFY_CLIENT_ID=your_client_id_here
   SPOTIFY_CLIENT_SECRET=your_client_secret_here
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
   ```

3. **Install dependencies** (uses a virtual environment):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Export YouTube cookies** (needed for age-restricted content):
   - Install a browser extension like "Get cookies.txt LOCALLY" for Chrome
   - Go to youtube.com while logged in, export cookies to `cookies.txt`
   - Place the file at `~/Music/Spotify/cookies.txt`

5. **First run** — a browser window will open for Spotify OAuth. Log in and authorize the app. The token is cached in `.spotify_cache` for future runs.

## Usage

```bash
# Activate the venv first (if not already active)
source .venv/bin/activate

# Full sync: discover all playlists + download missing songs
python3 sync.py

# Only update playlists.json (no downloads)
python3 sync.py --discover-only

# Only download from existing playlists.json (skip discovery)
python3 sync.py --download-only

# Sync a single playlist by folder name
python3 sync.py --playlist boombap

# Combine flags: download a single playlist without discovery
python3 sync.py --download-only --playlist boombap

# Preview what would be downloaded
python3 sync.py --dry-run

# Limit downloads per session (useful for initial sync)
python3 sync.py --max-downloads 100

# Use a specific cookies file
python3 sync.py --cookies cookies.txt

# Manually add a followed/editorial playlist
python3 sync.py --add "https://open.spotify.com/playlist/37i9dQZF1DX5cZuAHLNjGz"

# Add with a custom folder name
python3 sync.py --add "https://open.spotify.com/playlist/37i9dQZF1DX5cZuAHLNjGz" --name punjabi101
```

## How It Works

1. **Authenticate** with Spotify via OAuth (scopes: `playlist-read-private`, `playlist-read-collaborative`)
2. **Discover** your public, user-owned playlists (use `--add` for followed/editorial ones)
3. **Merge** with existing `playlists.json`, preserving custom folder names
4. **For each playlist**: fetch track metadata, compare against existing files, download missing tracks
5. **Download** via yt-dlp: search YouTube for `"Artist - Song official audio"`, extract audio as 320kbps MP3
6. **Rate limiting**: multi-layer protection (random delays, cookies, 429 backoff, session limits)

## Troubleshooting

**Auth fails / token expired**: Delete `.spotify_cache` and run again to re-authenticate.

**"Signature solving failed" / no formats available**: yt-dlp requires deno + yt-dlp-ejs to solve YouTube's JS challenges. Install deno (`brew install deno`) and ensure `yt-dlp-ejs` is in your venv (`pip install yt-dlp-ejs`).

**Age-restricted videos skipped**: Place a `cookies.txt` file (Netscape format, exported from your browser while logged into YouTube) in the project root. It's auto-detected, or pass `--cookies path/to/cookies.txt`.

**Rate limited by YouTube (429 errors)**: Use cookies to reduce throttling. Spread large syncs across sessions with `--max-downloads 100`.

**Wrong song downloaded**: Check `.failed_tracks.json` in the playlist folder. Duration mismatches are logged as warnings.

**ffmpeg not found**: Install with `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux).

**Safari cookies don't work**: Safari's cookie store is sandboxed on macOS. Export cookies from Chrome instead using a browser extension.

## File Structure

```
~/Music/Spotify/
├── sync.py                 # Main script
├── requirements.txt        # Python dependencies
├── .env                    # Spotify credentials (not committed)
├── .spotify_cache          # OAuth token cache (not committed)
├── cookies.txt             # YouTube cookies for auth (not committed)
├── .venv/                  # Python virtual environment (not committed)
├── .gitignore
├── playlists.json          # Auto-generated playlist config
├── updateall.sh            # Legacy spotdl script
├── .logs/                  # Sync logs
│   ├── sync_YYYYMMDD_HHMMSS.log
│   └── latest.log -> (symlink)
└── exports/                # All downloaded playlists
    ├── boombap/
    │   ├── Artist - Song.mp3
    │   └── .failed_tracks.json
    ├── 2hard/
    └── ...
```
