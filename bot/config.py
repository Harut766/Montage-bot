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
    gemini_api_key: str
    # Tried in order: the next one is used when the previous is overloaded or fails.
    gemini_models: list[str]
    whisper_model: str
    whisper_device: str
    work_dir: Path
    # Local Bot API Server storage shared with the bot; old media there is swept periodically.
    tg_storage_dir: Path
    fonts_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            bot_token=os.environ["BOT_TOKEN"],
            telegram_api_url=os.environ.get("TELEGRAM_API_URL", "").rstrip("/"),
            allowed_users=_ids(os.environ.get("ALLOWED_USERS", "")),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            gemini_models=_list(os.environ.get("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.6-flash")),
            whisper_model=os.environ.get("WHISPER_MODEL", "medium"),
            whisper_device=os.environ.get("WHISPER_DEVICE", "auto"),
            work_dir=Path(os.environ.get("WORK_DIR", "/data/work")),
            tg_storage_dir=Path(os.environ.get("TG_STORAGE_DIR", "/var/lib/telegram-bot-api")),
            fonts_dir=Path(os.environ.get("FONTS_DIR", Path(__file__).resolve().parent.parent / "fonts")),
        )
