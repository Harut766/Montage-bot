import asyncio
import logging
import random
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
from .live import LIVE_LENGTHS, find_backgrounds, has_audio, pick_percent, render_live_part
from .cleanup import sweep_tg_storage
from .clips import split_by_time
from .config import Config

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")
# Facebook links (lives) skip the menu and go straight to live mode.
FACEBOOK_RE = re.compile(r"^https?://([a-z0-9-]+\.)*(facebook\.com|fb\.watch|fb\.com)/", re.I)
LENGTHS = (60, 120, 180)
UPLOAD_TIMEOUT = 900
SWEEP_EVERY = 3600
# Older than any job can run, so a file still being processed is never removed.
SWEEP_MAX_AGE_HOURS = 6


MODES = {
    "clips": "✂️ Нарезать на части с субтитрами",
    "full": "📝 Только субтитры, не резать",
    "cut": "✂️ Просто нарезать, без субтитров",
    "live": "🔥 Лайв: по 10/15/20 мин, фон, громче",
}


@dataclass
class Job:
    chat_id: int
    file_id: str | None = None
    url: str | None = None
    mode: str | None = None
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
    h, rest = divmod(int(t), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


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
        "Я нарежу его на вертикальные ролики для TikTok с субтитрами.\n"
        "Ссылку на Facebook-лайв режу на части по 10/15/20 минут с фоном и громче (106–130%).\n"
        "Фон (костёр, листья…) — пришли видео с подписью «фон» или напиши «фон ссылка».\n\n"
        "/backgrounds — сколько фонов загружено\n"
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


async def _ask_mode(message: Message, job: Job) -> None:
    pending[message.chat.id] = job
    await message.answer(
        "Что сделать с видео?",
        reply_markup=_kb([[(title, f"mode:{code}")] for code, title in MODES.items()]),
    )


async def _ask_length(message: Message) -> None:
    await message.edit_text(
        "⏱ Какой длины нужны ролики?",
        reply_markup=_kb([[(f"~{s} сек", f"len:{s}") for s in LENGTHS]]),
    )


async def _ask_live_length(message: Message, edit: bool) -> None:
    text = "⏱ По сколько минут резать?"
    kb = _kb([[(f"{s // 60} минут", f"livelen:{s}") for s in LIVE_LENGTHS]])
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


async def _ask_language(message: Message) -> None:
    await message.edit_text(
        "🗣 На каком языке говорят в видео?",
        reply_markup=_kb([[("🇷🇺 Русский", "lang:ru"), ("🇬🇧 English", "lang:en")]]),
    )


async def _put(job: Job) -> str:
    pending.pop(job.chat_id, None)
    ahead = len(waiting) + (current is not None)
    waiting.append(job)
    await queue.put(job)
    text = f"{MODES[job.mode]}\n\n✅ Принято!"
    if ahead:
        text += f"\n⏳ Видео в очереди: сначала закончу предыдущие ({ahead})."
    return text


async def _enqueue(call: CallbackQuery, job: Job) -> None:
    await call.message.edit_text(await _put(job))


BG_CAPTIONS = ("фон", "fon", "bg", "background")
# A background loops under each part, so a few minutes of footage is plenty.
BG_SECONDS = 180


@router.message(Command("backgrounds"))
async def list_backgrounds(message: Message) -> None:
    bgs = find_backgrounds(cfg.backgrounds_dir)
    text = f"🔥 Фонов загружено: {len(bgs)}\n" if bgs else "Фонов пока нет — сверху и снизу будет размытое видео.\n"
    text += (
        "Добавить: пришли видео (костёр, листья…) с подписью «фон»\n"
        "или напиши «фон https://youtube.com/…» — скачаю первые 3 минуты.\n"
        "/backgrounds_clear — удалить все фоны"
    )
    await message.answer(text)


@router.message(Command("backgrounds_clear"))
async def clear_backgrounds(message: Message) -> None:
    for p in find_backgrounds(cfg.backgrounds_dir):
        p.unlink(missing_ok=True)
    await message.answer("🗑 Все фоны удалены.")


@router.message(
    (F.video | F.document.mime_type.startswith("video/") | F.animation)
    & F.func(lambda m: (m.caption or "").strip().lower() in BG_CAPTIONS)
)
async def on_background(message: Message, bot: Bot) -> None:
    media = message.video or message.animation or message.document
    file = await bot.get_file(media.file_id)
    cfg.backgrounds_dir.mkdir(parents=True, exist_ok=True)
    dst = cfg.backgrounds_dir / f"bg_{uuid.uuid4().hex[:8]}{Path(file.file_path).suffix or '.mp4'}"
    local = Path(file.file_path)
    if cfg.telegram_api_url and local.is_absolute() and local.exists():
        await asyncio.to_thread(shutil.copyfile, local, dst)
    else:
        await bot.download_file(file.file_path, dst, timeout=UPLOAD_TIMEOUT)
    total = len(find_backgrounds(cfg.backgrounds_dir))
    await message.answer(f"✅ Фон сохранён. Всего фонов: {total}")


@router.message(F.text.regexp(r"(?i)^\s*(фон|fon|bg)\s+https?://"))
async def on_background_url(message: Message) -> None:
    url = URL_RE.search(message.text).group(0)
    status = await message.answer("⬇️ Скачиваю фон (первые 3 минуты)…")
    try:
        await asyncio.to_thread(download_background, url, cfg.backgrounds_dir, cfg.cookies_file)
    except Exception as e:
        log.exception("background download failed")
        await status.edit_text(f"❌ Не удалось скачать фон: {e}", parse_mode=None)
        return
    total = len(find_backgrounds(cfg.backgrounds_dir))
    await status.edit_text(f"✅ Фон сохранён. Всего фонов: {total}")


@router.message(F.video | F.document.mime_type.startswith("video/"))
async def on_video(message: Message) -> None:
    media = message.video or message.document
    await _ask_mode(message, Job(chat_id=message.chat.id, file_id=media.file_id))


@router.message(F.text.regexp(URL_RE))
async def on_url(message: Message) -> None:
    url = URL_RE.search(message.text).group(0)
    job = Job(chat_id=message.chat.id, url=url)
    if FACEBOOK_RE.match(url):
        job.mode = "live"
        pending[message.chat.id] = job
        await _ask_live_length(message, edit=False)
        return
    await _ask_mode(message, job)


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Пришли видео файлом или ссылкой 🙂")


@router.callback_query(F.data.startswith("mode:"))
async def on_mode(call: CallbackQuery) -> None:
    job = pending.get(call.message.chat.id)
    if not job:
        await call.answer("Сначала пришли видео", show_alert=True)
        return
    job.mode = call.data.split(":")[1]
    if job.mode == "cut":
        job.language = "cut"
        await _ask_length(call.message)  # cut needs only length
    elif job.mode == "live":
        await _ask_live_length(call.message, edit=True)  # live needs only length
    elif job.mode == "full":
        await _ask_language(call.message)  # full video needs only language
    else:
        await _ask_length(call.message)  # clips need length, then language
    await call.answer()


@router.callback_query(F.data.startswith("len:"))
async def on_length(call: CallbackQuery) -> None:
    job = pending.get(call.message.chat.id)
    if not job or not job.mode:
        await call.answer("Сначала пришли видео", show_alert=True)
        return
    job.target = int(call.data.split(":")[1])
    if job.mode == "cut":
        await _enqueue(call, job)  # no subtitles, nothing more to ask
    else:
        await _ask_language(call.message)
    await call.answer()


@router.callback_query(F.data.startswith("livelen:"))
async def on_live_length(call: CallbackQuery) -> None:
    job = pending.get(call.message.chat.id)
    if not job or job.mode != "live":
        await call.answer("Сначала пришли видео", show_alert=True)
        return
    job.target = int(call.data.split(":")[1])
    await _enqueue(call, job)
    await call.answer()


@router.callback_query(F.data.startswith("lang:"))
async def on_language(call: CallbackQuery) -> None:
    job = pending.get(call.message.chat.id)
    if not job or not job.mode:
        await call.answer("Сначала пришли видео", show_alert=True)
        return
    job.language = call.data.split(":")[1]
    await _enqueue(call, job)
    await call.answer()


# ---------- processing ----------

async def fetch_source(bot: Bot, job: Job, work: Path) -> tuple[Path, bool]:
    """Returns the source path and whether it lives in the Bot API server storage (to delete afterwards)."""
    if job.url:
        return await asyncio.to_thread(download_url, job.url, work, cfg.cookies_file), False
    file = await bot.get_file(job.file_id)
    local = Path(file.file_path)
    if cfg.telegram_api_url and local.is_absolute() and local.exists():
        return local, True
    dst = work / f"source{Path(file.file_path).suffix or '.mp4'}"
    await bot.download_file(file.file_path, dst, timeout=UPLOAD_TIMEOUT)
    return dst, False


def download_url(url: str, work: Path, cookies_file: Path) -> Path:
    import yt_dlp

    opts = {
        "outtmpl": str(work / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    # Lets YouTube links that demand "sign in to confirm you're not a bot" download as a logged-in user.
    if cookies_file.is_file():
        opts["cookiefile"] = str(cookies_file)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return next(work.glob("source.*"))


def download_background(url: str, folder: Path, cookies_file: Path) -> None:
    """Ambient videos are often hours long: only the first BG_SECONDS are downloaded, picture only."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    folder.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(folder / f"bg_{uuid.uuid4().hex[:8]}.%(ext)s"),
        "format": "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(0, BG_SECONDS)]),
        "quiet": True,
        "noplaylist": True,
    }
    if cookies_file.is_file():
        opts["cookiefile"] = str(cookies_file)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


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
        if job.mode == "live":
            await send_live(bot, job, src, work, progress)
            return
        async for r in pipeline.run(cfg, src, work, job.mode, job.target, job.language, progress, lambda: job.cancelled):
            if job.mode == "full":
                caption = "📝 Видео с субтитрами"
            else:
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


async def send_live(bot: Bot, job: Job, src: Path, work: Path, progress) -> None:
    info = await asyncio.to_thread(pipeline.probe, src)
    clips = split_by_time(info.duration, job.target)
    audio = await asyncio.to_thread(has_audio, src)
    backgrounds = find_backgrounds(cfg.backgrounds_dir)
    if not backgrounds:
        await progress("⚠️ Фонов нет — сверху и снизу будет размытое видео. Добавить: видео с подписью «фон» или «фон ссылка».")
    total = len(clips)
    for n, clip in enumerate(clips, start=1):
        if job.cancelled:
            raise pipeline.Cancelled
        percent = pick_percent()
        await progress(f"🎬 Рендерю часть {n}/{total}…")
        out = work / f"live_{n:02d}.mp4"
        bg = random.choice(backgrounds) if backgrounds else None
        await asyncio.to_thread(
            render_live_part, src, clip.start, clip.end, info.width, info.height, bg, percent, audio, out
        )
        caption = f"Часть {n}/{total} · {_fmt(clip.start)}–{_fmt(clip.end)}"
        if audio:
            caption += f" · 🔊 {percent}%"
        await bot.send_video(
            job.chat_id,
            FSInputFile(out),
            caption=caption,
            width=1080,
            height=1920,
            duration=int(clip.duration),
            supports_streaming=True,
            request_timeout=UPLOAD_TIMEOUT,
        )
        out.unlink(missing_ok=True)
    await progress(f"✅ Готово! Частей: {total}")


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
        BotCommand(command="backgrounds", description="Фоны для лайвов"),
    ])
    tasks = [asyncio.create_task(worker(bot)), asyncio.create_task(janitor())]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
