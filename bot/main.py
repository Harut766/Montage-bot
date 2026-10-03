import asyncio
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import pipeline
from .cleanup import sweep_tg_storage
from .config import Config

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")
LENGTHS = (60, 120, 180)
LANGUAGES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
UPLOAD_TIMEOUT = 900
SWEEP_EVERY = 3600
# Older than any job can run, so a file still being processed is never removed.
SWEEP_MAX_AGE_HOURS = 6


@dataclass
class Job:
    chat_id: int
    file_id: str | None = None
    url: str | None = None
    target: int | None = None
    language: str | None = None
    cancelled: bool = False


cfg = Config.from_env()
router = Router()
pending: dict[int, Job] = {}
queue: asyncio.Queue[Job] = asyncio.Queue()
# Jobs waiting in the queue and the one being processed, for /cancel and queue positions.
waiting: list[Job] = []
current: Job | None = None


def _fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


# ---------- access ----------

allowed = F.from_user.id.in_(cfg.allowed_users)
router.message.filter(allowed)
router.callback_query.filter(allowed)
denied = Router()


@denied.message()
async def no_access(message: Message) -> None:
    await message.answer(f"⛔ Нет доступа. Ваш Telegram ID: <code>{message.from_user.id}</code>")


# ---------- dialog ----------

@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Привет! Пришли видео (файлом, до 2 ГБ) или ссылку на него.\n"
        "Я нарежу его на вертикальные ролики для TikTok с субтитрами.\n\n"
        "/cancel — отменить обработку"
    )


@router.message(Command("cancel", "stop"))
async def cancel(message: Message) -> None:
    pending.pop(message.chat.id, None)
    jobs = [j for j in [current, *waiting] if j and j.chat_id == message.chat.id and not j.cancelled]
    for j in jobs:
        j.cancelled = True
    if jobs:
        await message.answer("⛔ Отменяю. Текущий шаг доделается, дальше обработка остановится.")
    else:
        await message.answer("Сейчас нечего отменять.")


async def _ask_length(message: Message, job: Job) -> None:
    pending[message.chat.id] = job
    await message.answer(
        "⏱ Какой длины нужны ролики?",
        reply_markup=_kb([[(f"~{s} сек", f"len:{s}") for s in LENGTHS]]),
    )


@router.message(F.video | F.document.mime_type.startswith("video/"))
async def on_video(message: Message) -> None:
    media = message.video or message.document
    await _ask_length(message, Job(chat_id=message.chat.id, file_id=media.file_id))


@router.message(F.text.regexp(URL_RE))
async def on_url(message: Message) -> None:
    url = URL_RE.search(message.text).group(0)
    await _ask_length(message, Job(chat_id=message.chat.id, url=url))


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Пришли видео файлом или ссылкой 🙂")


@router.callback_query(F.data.startswith("len:"))
async def on_length(call: CallbackQuery) -> None:
    job = pending.get(call.message.chat.id)
    if not job:
        await call.answer("Сначала пришли видео", show_alert=True)
        return
    job.target = int(call.data.split(":")[1])
    await call.message.edit_text(
        f"⏱ Длина: ~{job.target} сек\n\n🗣 На каком языке говорят в видео?",
        reply_markup=_kb([[(title, f"lang:{code}") for code, title in LANGUAGES.items()]]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("lang:"))
async def on_language(call: CallbackQuery) -> None:
    job = pending.pop(call.message.chat.id, None)
    if not job or not job.target:
        await call.answer("Сначала пришли видео", show_alert=True)
        return
    job.language = call.data.split(":")[1]
    ahead = len(waiting) + (current is not None)
    waiting.append(job)
    await queue.put(job)
    text = f"⏱ ~{job.target} сек · {LANGUAGES[job.language]}\n\n✅ Принято!"
    if ahead:
        text += f"\n⏳ Видео в очереди: сначала закончу предыдущие ({ahead})."
    await call.message.edit_text(text)
    await call.answer()


# ---------- processing ----------

async def fetch_source(bot: Bot, job: Job, work: Path) -> tuple[Path, bool]:
    """Returns the source path and whether it lives in the Bot API server storage (to delete afterwards)."""
    if job.url:
        return await asyncio.to_thread(download_url, job.url, work), False
    file = await bot.get_file(job.file_id)
    local = Path(file.file_path)
    if cfg.telegram_api_url and local.is_absolute() and local.exists():
        return local, True
    dst = work / f"source{Path(file.file_path).suffix or '.mp4'}"
    await bot.download_file(file.file_path, dst, timeout=UPLOAD_TIMEOUT)
    return dst, False


def download_url(url: str, work: Path) -> Path:
    import yt_dlp

    opts = {
        "outtmpl": str(work / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return next(work.glob("source.*"))


async def process(bot: Bot, job: Job) -> None:
    status = await bot.send_message(job.chat_id, "⬇️ Загружаю видео…")

    async def progress(text: str) -> None:
        # Warnings go to a separate message so the next status update does not hide them.
        # Plain text: error messages may contain "<" and would break HTML parsing.
        if text.startswith("⚠️"):
            await bot.send_message(job.chat_id, text, parse_mode=None)
            return
        try:
            await status.edit_text(text, parse_mode=None)
        except TelegramBadRequest:
            pass

    work = cfg.work_dir / uuid.uuid4().hex
    work.mkdir(parents=True)
    src, from_server = None, False
    try:
        try:
            src, from_server = await fetch_source(bot, job, work)
        except TelegramBadRequest as e:
            if "too big" in str(e).lower():
                await progress("❌ Файл больше 20 МБ, а Local Bot API Server не настроен. См. README.")
                return
            raise
        sent = 0
        if job.cancelled:
            raise pipeline.Cancelled
        async for r in pipeline.run(cfg, src, work, job.target, job.language, progress, lambda: job.cancelled):
            caption = f"Часть {r.part}/{r.total} · {_fmt(r.clip.start)}–{_fmt(r.clip.end)}"
            if r.clip.title:
                caption += f"\n{r.clip.title}"
            await bot.send_video(
                job.chat_id,
                FSInputFile(r.path),
                caption=caption,
                width=1080,
                height=1920,
                duration=int(r.clip.duration),
                supports_streaming=True,
                request_timeout=UPLOAD_TIMEOUT,
            )
            r.path.unlink(missing_ok=True)
            sent += 1
        await progress(f"✅ Готово! Роликов: {sent}")
    except pipeline.Cancelled:
        await progress("⛔ Обработка отменена.")
    except Exception as e:
        log.exception("job failed")
        await progress(f"❌ Ошибка: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if from_server and src:
            src.unlink(missing_ok=True)


async def worker(bot: Bot) -> None:
    # One job at a time: transcription and rendering already use the whole machine.
    global current
    while True:
        job = await queue.get()
        waiting.remove(job)
        current = job
        try:
            if not job.cancelled:
                await process(bot, job)
        finally:
            current = None
            queue.task_done()


async def janitor() -> None:
    while True:
        await asyncio.to_thread(sweep_tg_storage, cfg.tg_storage_dir, SWEEP_MAX_AGE_HOURS)
        await asyncio.sleep(SWEEP_EVERY)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Leftovers of jobs interrupted by a restart; the queue lives in memory, so nothing will resume them.
    shutil.rmtree(cfg.work_dir, ignore_errors=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    session = None
    if cfg.telegram_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(cfg.telegram_api_url, is_local=True))
    bot = Bot(cfg.bot_token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_routers(router, denied)
    await bot.set_my_commands([
        BotCommand(command="start", description="Как пользоваться"),
        BotCommand(command="cancel", description="Отменить обработку"),
    ])
    tasks = [asyncio.create_task(worker(bot)), asyncio.create_task(janitor())]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
