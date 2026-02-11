# Spotify Playlist Downloader

## Project Overview
Python script that auto-discovers Spotify playlists and downloads songs as 320kbps MP3s via YouTube (yt-dlp). Replaces the previous spotdl-based approach (`updateall.sh`).

## Key Files
- `sync.py` — main script (auth, discovery, download, sync)
- `playlists.json` — auto-generated mapping of folder paths to Spotify URLs (paths point into `exports/`)
- `exports/` — all downloaded playlist folders (not committed)
- `.env` — Spotify API credentials (never commit)
- `cookies.txt` — YouTube cookies for age-restricted content (never commit)
- `requirements.txt` — Python deps: spotipy, yt-dlp, yt-dlp-ejs, python-dotenv, mutagen
- `updateall.sh` — legacy spotdl script (kept for reference)

## Conventions
- **File naming**: `Artist1, Artist2 - Song Title.mp3` (matches spotdl convention)
- **Folder naming**: lowercase, hyphenated inside `exports/` (e.g., `exports/boombap`, `exports/chill-dhh`)
- **Incremental sync**: fuzzy filename matching (lowercase, alphanumeric only) to detect existing files
- **Metadata**: ID3 tags (title, artist, album, track number, album art) auto-embedded from Spotify after download
- **No pruning**: songs removed from Spotify playlist are never deleted from disk

## How to Run
```bash
source .venv/bin/activate                # activate venv first
python3 sync.py                          # download from existing playlists.json
python3 sync.py --discover               # re-discover playlists from Spotify + download
python3 sync.py --discover-only          # just update playlists.json (no downloads)
python3 sync.py --playlist boombap       # single playlist
python3 sync.py --dry-run                # preview mode
python3 sync.py --max-downloads 100      # cap downloads
python3 sync.py --cookies cookies.txt    # use YouTube cookies file
python3 sync.py --add "https://open.spotify.com/playlist/..." # add a specific playlist
python3 sync.py --add "..." --name my-folder   # add with custom folder name
python3 sync.py --tag-only                # embed metadata into existing files (no download)
python3 sync.py --tag-only --playlist boombap  # tag a single playlist
python3 sync.py --upgrade --dry-run      # preview low-bitrate files to re-download
python3 sync.py --upgrade                # re-download files below 256kbps at 320kbps
python3 sync.py --upgrade --upgrade-threshold 192  # only re-download ≤192kbps files
```

## Dependencies
- **Python**: spotipy, yt-dlp, yt-dlp-ejs, python-dotenv, mutagen (installed in `.venv/`)
- **System**: ffmpeg (for audio extraction), deno (for yt-dlp JS signature solving), Python 3.10+
- **Setup**: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

## Testing / Verification
1. `python3 sync.py --discover-only` — verifies Spotify auth + playlist discovery
2. `python3 sync.py --playlist 2hard --dry-run` — shows existing tracks as skipped
3. `python3 sync.py --playlist boombap --max-downloads 3` — downloads a few tracks to verify
4. `python3 sync.py --tag-only --playlist boombap` — tags existing files without re-downloading
5. `python3 sync.py --upgrade --dry-run` — shows which files would be re-downloaded
6. `ffprobe <file>.mp3` — verify 320kbps bitrate
7. `python3 -c "from mutagen.id3 import ID3; print(ID3('exports/boombap/somefile.mp3').pprint())"` — verify metadata

## Important Notes
- Never commit `.env`, `.spotify_cache`, or `cookies.txt`
- `.spotify_cache` contains the OAuth token; delete it to force re-auth
- `cookies.txt` is auto-detected if present in project root; needed for age-restricted YouTube videos
- Logs go to `.logs/` with a `latest.log` symlink
- Failed tracks are saved per-playlist in `.failed_tracks.json`
- YouTube rate limiting is the main operational concern — use cookies and `--max-downloads`
- deno is required for yt-dlp's YouTube signature solving (`brew install deno`)
