"""Removes media left in the Local Bot API Server storage (e.g. by jobs interrupted with a restart)."""
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Only media folders inside <storage>/<bot token>/; the server's own database files stay untouched.
MEDIA_DIRS = ("videos", "documents", "animations", "video_notes", "voice", "music", "photos", "thumbnails", "temp")


def sweep_tg_storage(root: Path, max_age_hours: float) -> int:
    """Deletes media files older than max_age_hours. Returns the number of deleted files."""
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    deleted = 0
    for bot_dir in (d for d in root.iterdir() if d.is_dir()):
        for name in MEDIA_DIRS:
            media = bot_dir / name
            if not media.is_dir():
                continue
            for f in media.rglob("*"):
                try:
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink()
                        deleted += 1
                except OSError:
                    log.warning("could not delete %s", f)
    if deleted:
        log.info("removed %d old files from %s", deleted, root)
    return deleted
