import os
from dataclasses import dataclass
from pathlib import Path


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


def _list(raw: str) -> list[str]:
    return [x for x in raw.replace(" ", "").split(",") if x]


@dataclass(frozen=True)
class Config:
    bot_token: str
    # Base URL of the Local Bot API Server; empty means the public api.telegram.org (20 MB download limit).
    telegram_api_url: str
    allowed_users: frozenset[int]
    whisper_model: str
    whisper_device: str
    work_dir: Path
    # Local Bot API Server storage shared with the bot; old media there is swept periodically.
    tg_storage_dir: Path
    fonts_dir: Path
    # Optional Netscape-format cookies.txt for yt-dlp, so YouTube links that demand sign-in still download.
    cookies_file: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            bot_token=os.environ["BOT_TOKEN"],
            telegram_api_url=os.environ.get("TELEGRAM_API_URL", "").rstrip("/"),
            allowed_users=_ids(os.environ.get("ALLOWED_USERS", "")),
            whisper_model=os.environ.get("WHISPER_MODEL", "medium"),
            whisper_device=os.environ.get("WHISPER_DEVICE", "auto"),
            work_dir=Path(os.environ.get("WORK_DIR", "/data/work")),
            tg_storage_dir=Path(os.environ.get("TG_STORAGE_DIR", "/var/lib/telegram-bot-api")),
            fonts_dir=Path(os.environ.get("FONTS_DIR", Path(__file__).resolve().parent.parent / "fonts")),
            cookies_file=Path(os.environ.get("COOKIES_FILE", "/data/cookies.txt")),
        )
