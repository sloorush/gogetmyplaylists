#!/usr/bin/env python3
"""
Spotify Playlist Downloader
Auto-discovers playlists from Spotify profile, downloads songs from YouTube as 320kbps MP3s.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import random
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import yt_dlp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
PLAYLISTS_JSON = BASE_DIR / "playlists.json"
SPOTIFY_CACHE = BASE_DIR / ".spotify_cache"
EXPORTS_DIR = BASE_DIR / "exports"
COOKIES_FILE = BASE_DIR / "cookies.txt"
LOG_DIR = BASE_DIR / ".logs"
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"

# Rate-limiting tunables
SONG_DELAY_MIN = 3
SONG_DELAY_MAX = 8
PLAYLIST_DELAY = 10
CONSECUTIVE_FAIL_PAUSE = 60       # seconds to wait after 3 consecutive failures
CONSECUTIVE_FAIL_THRESHOLD = 3
CONSECUTIVE_FAIL_ABORT = 10       # abort playlist after this many consecutive failures
THROTTLE_WAIT_1 = 60              # first 429
THROTTLE_WAIT_2 = 300             # second 429
MAX_DURATION_SECONDS = 900        # skip YouTube results longer than 15 min
DURATION_WARN_DELTA = 30          # warn if YT vs Spotify differ by >30s

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"sync_{time.strftime('%Y%m%d_%H%M%S')}.log"
latest_log = LOG_DIR / "latest.log"

logger = logging.getLogger("spotify_sync")
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Symlink latest log
try:
    latest_log.unlink(missing_ok=True)
    latest_log.symlink_to(log_file)
except OSError:
    pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_folder_name(name: str) -> str:
    """Convert a playlist name to a filesystem-safe folder name."""
    name = unicodedata.normalize("NFKD", name)
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "unnamed"


def sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe for filenames."""
    return re.sub(r'[\":\'\*\/\?\\\<\>\|]', "", name).strip()


def normalize_for_comparison(name: str) -> str:
    """Normalize a filename for fuzzy matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_track_filename(track_name: str, artists: list[str]) -> str:
    """Build 'Artist1, Artist2 - Song Title.mp3' matching spotdl convention."""
    artist_str = ", ".join(artists)
    raw = f"{artist_str} - {track_name}"
    return sanitize_filename(raw) + ".mp3"


def existing_files_normalized(folder: Path) -> dict[str, str]:
    """Return {normalized_name: actual_filename} for all mp3s in a folder."""
    result = {}
    if folder.exists():
        for f in folder.iterdir():
            if f.suffix.lower() == ".mp3":
                result[normalize_for_comparison(f.stem)] = f.name
    return result


# ---------------------------------------------------------------------------
# Spotify auth & API
# ---------------------------------------------------------------------------

def get_spotify_client() -> spotipy.Spotify:
    load_dotenv(ENV_FILE)
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret or client_id == "your_client_id_here":
        logger.error("Spotify credentials not configured. Edit .env with your app credentials.")
        logger.error("Create an app at https://developer.spotify.com/dashboard")
        sys.exit(1)

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=str(SPOTIFY_CACHE),
    )
    return spotipy.Spotify(auth_manager=auth_manager, requests_timeout=10)


def discover_playlists(sp: spotipy.Spotify) -> list[dict]:
    """Fetch public, user-owned playlists only (skips followed/editorial playlists)."""
    user_id = sp.current_user()["id"]
    playlists = []
    offset = 0
    while True:
        batch = sp.current_user_playlists(limit=50, offset=offset)
        if not batch or not batch["items"]:
            break
        for item in batch["items"]:
            if item["owner"]["id"] != user_id:
                continue
            if not item.get("public", False):
                continue
            playlists.append({
                "id": item["id"],
                "name": item["name"],
                "url": item["external_urls"]["spotify"],
                "owner": item["owner"]["display_name"],
                "total_tracks": item["tracks"]["total"],
            })
        offset += len(batch["items"])
        if offset >= batch["total"]:
            break
    return playlists


def merge_playlists_json(discovered: list[dict]) -> dict:
    """
    Merge discovered playlists with existing playlists.json.
    Preserves custom folder names; adds new playlists.
    Returns {folder_path: spotify_url} dict (the playlists.json format).
    """
    existing = {}
    if PLAYLISTS_JSON.exists():
        try:
            existing = json.loads(PLAYLISTS_JSON.read_text())
        except json.JSONDecodeError:
            logger.warning("Could not parse existing playlists.json, starting fresh")

    # Build reverse map: spotify_url -> folder_path from existing
    url_to_folder = {}
    for folder, url in existing.items():
        # Strip query params for comparison
        clean = url.split("?")[0]
        url_to_folder[clean] = folder

    merged = dict(existing)  # start with everything we already have

    for pl in discovered:
        clean_url = pl["url"].split("?")[0]
        if clean_url not in url_to_folder:
            folder_name = sanitize_folder_name(pl["name"])
            folder_path = f"~/Music/Spotify/exports/{folder_name}"
            # Deduplicate folder names
            if folder_path in merged:
                folder_path = f"{folder_path}-{pl['id'][:8]}"
            merged[folder_path] = pl["url"]
            logger.info(f"  NEW: {pl['name']} -> {folder_name}")

    return merged


# ---------------------------------------------------------------------------
# Track extraction
# ---------------------------------------------------------------------------

def get_playlist_tracks(sp: spotipy.Spotify, playlist_url: str) -> list[dict]:
    """Fetch all tracks from a playlist, handling pagination."""
    # Extract playlist ID from URL
    playlist_id = playlist_url.split("/playlist/")[-1].split("?")[0]
    tracks = []
    offset = 0
    while True:
        batch = sp.playlist_tracks(playlist_id, limit=100, offset=offset)
        if not batch or not batch["items"]:
            break
        for item in batch["items"]:
            track = item.get("track")
            if not track or not track.get("name"):
                continue
            if track.get("is_local", False):
                continue
            artists = [a["name"] for a in track.get("artists", []) if a.get("name")]
            if not artists:
                continue
            tracks.append({
                "name": track["name"],
                "artists": artists,
                "album": track.get("album", {}).get("name", ""),
                "duration_ms": track.get("duration_ms", 0),
                "spotify_url": track.get("external_urls", {}).get("spotify", ""),
            })
        offset += len(batch["items"])
        if offset >= batch["total"]:
            break
    return tracks


# ---------------------------------------------------------------------------
# YouTube download
# ---------------------------------------------------------------------------

class ThrottleState:
    """Tracks 429 / throttle events across the session."""
    def __init__(self):
        self.count_429 = 0
        self.session_aborted = False


def is_age_restricted(error_msg: str) -> bool:
    """Check if a yt-dlp error is an age restriction (not a rate limit)."""
    return "sign in to confirm your age" in error_msg.lower()


def is_throttle_error(error_msg: str) -> bool:
    """Check if a yt-dlp error indicates YouTube throttling."""
    if is_age_restricted(error_msg):
        return False
    indicators = ["429", "too many requests", "http error 429"]
    msg_lower = error_msg.lower()
    return any(ind in msg_lower for ind in indicators)


def download_track(
    track: dict,
    output_dir: Path,
    cookies_browser: str | None,
    cookies_file: Path | None,
    throttle: ThrottleState,
    dry_run: bool = False,
) -> bool:
    """
    Search YouTube and download a single track as 320kbps MP3.
    Returns True on success, False on failure.
    """
    if throttle.session_aborted:
        return False

    primary_artist = track["artists"][0]
    query = f"{primary_artist} - {track['name']} official audio"
    filename = build_track_filename(track["name"], track["artists"])

    if dry_run:
        logger.info(f"  [DRY RUN] Would download: {filename}")
        return True

    output_template = str(output_dir / sanitize_filename(f"{', '.join(track['artists'])} - {track['name']}"))

    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }],
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "sleep_interval": 3,
        "max_sleep_interval": 8,
        "sleep_interval_requests": 1,
        "default_search": "ytsearch1",
        "match_filter": yt_dlp.utils.match_filter_func(f"duration < {MAX_DURATION_SECONDS}"),
    }

    if cookies_file and cookies_file.exists():
        ydl_opts["cookiefile"] = str(cookies_file)
    elif cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if info and track["duration_ms"]:
                yt_duration = info.get("duration", 0)
                sp_duration = track["duration_ms"] / 1000
                if yt_duration and abs(yt_duration - sp_duration) > DURATION_WARN_DELTA:
                    logger.warning(
                        f"  Duration mismatch: YouTube={yt_duration:.0f}s vs Spotify={sp_duration:.0f}s "
                        f"for {track['name']}"
                    )
        return True

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.debug(f"  yt-dlp error: {error_msg}")

        if is_age_restricted(error_msg):
            logger.warning(f"  Skipping (age-restricted): {track['name']}")
            return False

        if is_throttle_error(error_msg):
            throttle.count_429 += 1
            if throttle.count_429 == 1:
                logger.warning(f"  Rate limited (429). Waiting {THROTTLE_WAIT_1}s...")
                time.sleep(THROTTLE_WAIT_1)
            elif throttle.count_429 == 2:
                logger.warning(f"  Rate limited again (429). Waiting {THROTTLE_WAIT_2}s...")
                time.sleep(THROTTLE_WAIT_2)
            else:
                logger.error("  Third 429 detected. Aborting downloads for this session.")
                throttle.session_aborted = True
        return False

    except Exception as e:
        logger.debug(f"  Unexpected error downloading {track['name']}: {e}")
        return False


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync_playlist(
    sp: spotipy.Spotify,
    folder_path: str,
    playlist_url: str,
    cookies_browser: str | None,
    cookies_file: Path | None,
    throttle: ThrottleState,
    dry_run: bool = False,
    max_downloads: int | None = None,
    download_count: list[int] | None = None,
) -> dict:
    """
    Sync a single playlist. Returns stats dict.
    download_count is a mutable [int] tracking total downloads across playlists.
    """
    stats = {"skipped": 0, "downloaded": 0, "failed": 0, "total": 0}

    # Resolve folder path
    resolved = Path(os.path.expanduser(folder_path))
    resolved.mkdir(parents=True, exist_ok=True)
    playlist_name = resolved.name

    logger.info(f"\n{'='*60}")
    logger.info(f"Playlist: {playlist_name}")
    logger.info(f"Folder:   {resolved}")
    logger.info(f"URL:      {playlist_url}")

    # Fetch tracks from Spotify
    try:
        tracks = get_playlist_tracks(sp, playlist_url)
    except Exception as e:
        logger.error(f"  Failed to fetch tracks: {e}")
        return stats

    stats["total"] = len(tracks)
    logger.info(f"Tracks:   {len(tracks)}")

    # Build map of existing files for fuzzy matching
    existing = existing_files_normalized(resolved)

    consecutive_failures = 0
    failed_tracks = []

    for i, track in enumerate(tracks, 1):
        if throttle.session_aborted:
            logger.warning("  Session aborted due to rate limiting. Stopping.")
            break

        if max_downloads is not None and download_count and download_count[0] >= max_downloads:
            logger.info(f"  Reached max downloads ({max_downloads}). Stopping.")
            break

        filename = build_track_filename(track["name"], track["artists"])
        normalized = normalize_for_comparison(Path(filename).stem)

        # Check if already exists (fuzzy match)
        if normalized in existing:
            stats["skipped"] += 1
            logger.debug(f"  [{i}/{len(tracks)}] SKIP (exists): {filename}")
            continue

        logger.info(f"  [{i}/{len(tracks)}] Downloading: {filename}")

        success = download_track(track, resolved, cookies_browser, cookies_file, throttle, dry_run)

        if success:
            stats["downloaded"] += 1
            consecutive_failures = 0
            if download_count is not None:
                download_count[0] += 1
            # Random delay between downloads
            if not dry_run and i < len(tracks):
                delay = random.uniform(SONG_DELAY_MIN, SONG_DELAY_MAX)
                time.sleep(delay)
        else:
            stats["failed"] += 1
            consecutive_failures += 1
            failed_tracks.append({
                "name": track["name"],
                "artists": track["artists"],
                "spotify_url": track.get("spotify_url", ""),
            })

            if consecutive_failures >= CONSECUTIVE_FAIL_ABORT:
                logger.error(f"  {CONSECUTIVE_FAIL_ABORT} consecutive failures. Skipping rest of playlist.")
                break
            elif consecutive_failures >= CONSECUTIVE_FAIL_THRESHOLD:
                logger.warning(
                    f"  {consecutive_failures} consecutive failures. "
                    f"Pausing {CONSECUTIVE_FAIL_PAUSE}s..."
                )
                time.sleep(CONSECUTIVE_FAIL_PAUSE)

    # Save failed tracks for manual review
    if failed_tracks:
        failed_file = resolved / ".failed_tracks.json"
        try:
            failed_file.write_text(json.dumps(failed_tracks, indent=2, ensure_ascii=False))
            logger.info(f"  Failed tracks saved to {failed_file}")
        except OSError as e:
            logger.warning(f"  Could not save failed tracks: {e}")

    logger.info(
        f"  Result: {stats['downloaded']} downloaded, "
        f"{stats['skipped']} skipped, {stats['failed']} failed"
    )
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Spotify playlists to local MP3 files via YouTube"
    )
    parser.add_argument(
        "--add",
        type=str,
        metavar="URL",
        help="Add a Spotify playlist URL to playlists.json (optionally use --name for folder name)",
    )
    parser.add_argument(
        "--name",
        type=str,
        help="Folder name to use with --add (default: derived from playlist name)",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Skip discovery, only download from existing playlists.json",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only update playlists.json (no downloads)",
    )
    parser.add_argument(
        "--playlist",
        type=str,
        help="Sync a single playlist by folder name (e.g., 'boombap')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without actually downloading",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        default=None,
        help="Maximum number of downloads per session",
    )
    parser.add_argument(
        "--cookies",
        type=str,
        default=None,
        help="Path to a Netscape-format cookies.txt file for YouTube auth. "
             "Auto-detects ./cookies.txt if present.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        type=str,
        default=None,
        help="Browser to extract cookies from (e.g., 'chrome'). "
             "Note: Safari is sandboxed on macOS and won't work.",
    )
    return parser.parse_args()


def add_playlist(sp: spotipy.Spotify, url: str, folder_name: str | None) -> None:
    """Add a single playlist URL to playlists.json."""
    # Load existing
    existing = {}
    if PLAYLISTS_JSON.exists():
        try:
            existing = json.loads(PLAYLISTS_JSON.read_text())
        except json.JSONDecodeError:
            pass

    # Check if URL already exists
    clean_url = url.split("?")[0]
    for folder, u in existing.items():
        if u.split("?")[0] == clean_url:
            logger.info(f"Playlist already in playlists.json as: {folder}")
            return

    # Resolve folder name
    if folder_name:
        name = sanitize_folder_name(folder_name)
    else:
        playlist_id = url.split("/playlist/")[-1].split("?")[0]
        try:
            pl = sp.playlist(playlist_id, fields="name")
            name = sanitize_folder_name(pl["name"])
        except Exception as e:
            logger.error(f"Could not fetch playlist info: {e}")
            sys.exit(1)

    folder_path = f"~/Music/Spotify/exports/{name}"
    if folder_path in existing:
        logger.error(f"Folder name '{name}' already exists in playlists.json")
        sys.exit(1)

    existing[folder_path] = url
    PLAYLISTS_JSON.write_text(json.dumps(existing, indent=4, ensure_ascii=False) + "\n")
    logger.info(f"Added: {name} -> {url}")


def resolve_cookies_browser(requested: str | None) -> str | None:
    """Only use browser cookies if explicitly requested."""
    return requested


def main():
    args = parse_args()

    logger.info("Spotify Playlist Sync")
    logger.info(f"Log file: {log_file}")

    # Step 1: Authenticate
    logger.info("\nAuthenticating with Spotify...")
    sp = get_spotify_client()
    user = sp.current_user()
    logger.info(f"Logged in as: {user['display_name']} ({user['id']})")

    # Handle --add (exit early)
    if args.add:
        add_playlist(sp, args.add, args.name)
        return

    # Step 2: Discover playlists (skip if --download-only)
    if args.download_only:
        if not PLAYLISTS_JSON.exists():
            logger.error("playlists.json not found. Run without --download-only first.")
            sys.exit(1)
        try:
            merged = json.loads(PLAYLISTS_JSON.read_text())
        except json.JSONDecodeError:
            logger.error("Could not parse playlists.json")
            sys.exit(1)
        logger.info(f"Loaded {len(merged)} playlists from playlists.json (skipping discovery)")
    else:
        logger.info("\nDiscovering playlists...")
        discovered = discover_playlists(sp)
        logger.info(f"Found {len(discovered)} playlists on profile")

        # Merge with existing playlists.json
        merged = merge_playlists_json(discovered)
        PLAYLISTS_JSON.write_text(json.dumps(merged, indent=4, ensure_ascii=False) + "\n")
        logger.info(f"playlists.json updated ({len(merged)} playlists)")

        if args.discover_only:
            logger.info("\n--discover-only flag set. Exiting without downloading.")
            return

    # Step 3: Determine which playlists to sync
    if args.playlist:
        matching = {k: v for k, v in merged.items() if args.playlist in k}
        if not matching:
            logger.error(f"No playlist matching '{args.playlist}' found in playlists.json")
            sys.exit(1)
        to_sync = matching
    else:
        to_sync = merged

    # Step 4: Resolve cookies
    cookies_browser = resolve_cookies_browser(args.cookies_from_browser)
    cookies_file = Path(args.cookies) if args.cookies else COOKIES_FILE
    if not cookies_file.exists():
        cookies_file = None

    if cookies_file:
        logger.info(f"Using cookies file: {cookies_file}")
    elif cookies_browser:
        logger.info(f"Using browser cookies from: {cookies_browser}")
    else:
        logger.info("No cookies configured (age-restricted videos will be skipped)")

    # Step 6: Sync each playlist
    throttle = ThrottleState()
    download_count = [0]  # mutable counter shared across playlists
    total_stats = {"skipped": 0, "downloaded": 0, "failed": 0, "total": 0}

    for i, (folder, url) in enumerate(to_sync.items()):
        if throttle.session_aborted:
            logger.warning("Session aborted due to rate limiting. Stopping all playlists.")
            break

        stats = sync_playlist(
            sp, folder, url, cookies_browser, cookies_file, throttle,
            dry_run=args.dry_run,
            max_downloads=args.max_downloads,
            download_count=download_count,
        )

        for key in total_stats:
            total_stats[key] += stats[key]

        # Delay between playlists
        if not args.dry_run and i < len(to_sync) - 1 and not throttle.session_aborted:
            logger.info(f"\nWaiting {PLAYLIST_DELAY}s before next playlist...")
            time.sleep(PLAYLIST_DELAY)

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("SYNC COMPLETE")
    logger.info(f"  Total tracks:  {total_stats['total']}")
    logger.info(f"  Downloaded:    {total_stats['downloaded']}")
    logger.info(f"  Skipped:       {total_stats['skipped']}")
    logger.info(f"  Failed:        {total_stats['failed']}")
    if args.max_downloads:
        logger.info(f"  Download limit: {download_count[0]}/{args.max_downloads}")
    logger.info(f"  Log: {log_file}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
