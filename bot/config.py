import os
from dataclasses import dataclass
from pathlib import Path


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    bot_token: str
    # Base URL of the Local Bot API Server; empty means the public api.telegram.org (20 MB download limit).
    telegram_api_url: str
    allowed_users: frozenset[int]
    n8n_webhook_url: str
    # Sent as the X-Montage-Secret header; the n8n Webhook node checks it (Header Auth).
    n8n_secret: str
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
            n8n_webhook_url=os.environ.get("N8N_WEBHOOK_URL", ""),
            n8n_secret=os.environ.get("N8N_SECRET", ""),
            whisper_model=os.environ.get("WHISPER_MODEL", "medium"),
            whisper_device=os.environ.get("WHISPER_DEVICE", "auto"),
            work_dir=Path(os.environ.get("WORK_DIR", "/data/work")),
            tg_storage_dir=Path(os.environ.get("TG_STORAGE_DIR", "/var/lib/telegram-bot-api")),
            fonts_dir=Path(os.environ.get("FONTS_DIR", Path(__file__).resolve().parent.parent / "fonts")),
        )
