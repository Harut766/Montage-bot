"""Flatlay Outfit Composer — a second mode of the bot.

The user sends a background photo (or picks a plain one) and then photos of clothing items by
category; the bot cuts the background out of each item with rembg and lays them out as a flatlay
on a 1080×1350 (Instagram 4:5) canvas. Ported to aiogram from the standalone python-telegram-bot
spec so it shares this bot's token, whitelist and container.
"""
import asyncio
import io
import logging
from dataclasses import dataclass, field

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

CANVAS_W, CANVAS_H = 1080, 1350

CATEGORIES = {
    "top": "Верх (худи, футболка, рубашка)",
    "bottom": "Низ (штаны, джинсы, шорты)",
    "shoes": "Обувь",
    "cap": "Кепка / Шапка",
    "glasses": "Очки",
    "watch": "Часы",
    "bracelet": "Браслет",
    "bag": "Сумка",
    "chain": "Цепочка",
    "other": "Другое",
}

# (center_x%, center_y%, max_width%, max_height%, rotation_deg) of the canvas.
LAYOUT = {
    "top": (0.50, 0.30, 0.55, 0.38, -3),
    "bottom": (0.38, 0.72, 0.40, 0.45, 2),
    "shoes": (0.75, 0.82, 0.28, 0.22, -8),
    "cap": (0.78, 0.12, 0.22, 0.18, 12),
    "glasses": (0.22, 0.12, 0.18, 0.10, -5),
    "watch": (0.82, 0.55, 0.14, 0.14, 15),
    "bracelet": (0.18, 0.55, 0.14, 0.12, -10),
    "bag": (0.80, 0.42, 0.25, 0.25, 5),
    "chain": (0.50, 0.08, 0.25, 0.10, 0),
    "other": (0.20, 0.90, 0.20, 0.15, -5),
}

# Back to front, so trousers sit under the top, shoes over trousers, and so on.
DRAW_ORDER = ["bottom", "top", "shoes", "bag", "cap", "glasses", "watch", "bracelet", "chain", "other"]


@dataclass
class LookSession:
    state: str = "waiting_bg"  # waiting_bg -> menu -> waiting_item -> menu -> (compose)
    background: Image.Image | None = None
    items: dict[str, Image.Image] = field(default_factory=dict)
    current_category: str | None = None


# Per-chat sessions, kept in memory (one look is assembled in a single sitting).
sessions: dict[int, LookSession] = {}
# One heavy compose at a time: rembg + the whisper/render pipeline must not run together on a small Mac.
_compose_lock = asyncio.Lock()
_rembg_sessions: dict[str, object] = {}

# Garments are usually photographed worn or held, so plain background removal keeps the hands, body
# and mannequin. u2net_cloth_seg segments the CLOTHING itself and drops everything else. Shoes and
# accessories are normal objects, so the general u2net model is enough for them.
CLOTH_MODEL = "u2net_cloth_seg"
MODEL_BY_CATEGORY = {"top": CLOTH_MODEL, "bottom": CLOTH_MODEL}
DEFAULT_MODEL = "u2net"


# ---------- image processing ----------

def _remove_background(img: Image.Image, model: str = DEFAULT_MODEL) -> Image.Image:
    """Cut the item out with rembg; returns an RGBA cut-out trimmed to its bounding box."""
    from rembg import new_session, remove

    if model not in _rembg_sessions:
        _rembg_sessions[model] = new_session(model)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = remove(buf.getvalue(), session=_rembg_sessions[model])
    cut = Image.open(io.BytesIO(out)).convert("RGBA")
    # Trim fully transparent borders so the garment (not the original photo frame) drives its size.
    bbox = cut.split()[3].getbbox()
    return cut.crop(bbox) if bbox else cut


def _add_shadow(img: Image.Image, offset=(6, 6), blur=12, opacity=80) -> Image.Image:
    """Soft drop shadow behind a transparent cut-out, on an enlarged canvas."""
    alpha = img.split()[3]
    shadow = Image.new("RGBA", img.size, (0, 0, 0, opacity))
    shadow.putalpha(alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))

    w, h = img.size
    result = Image.new("RGBA", (w + abs(offset[0]) + blur * 2, h + abs(offset[1]) + blur * 2), (0, 0, 0, 0))
    sx, sy = blur + max(offset[0], 0), blur + max(offset[1], 0)
    ix, iy = blur + max(-offset[0], 0), blur + max(-offset[1], 0)
    result.paste(shadow, (sx, sy), shadow)
    result.paste(img, (ix, iy), img)
    return result


def _fit_and_place(canvas, item, cx_pct, cy_pct, max_w_pct, max_h_pct, rotation):
    cw, ch = canvas.size
    iw, ih = item.size
    scale = min(cw * max_w_pct / iw, ch * max_h_pct / ih)
    item = item.resize((max(1, int(iw * scale)), max(1, int(ih * scale))), Image.LANCZOS)
    item = _add_shadow(item)
    if rotation:
        item = item.rotate(rotation, expand=True, resample=Image.BICUBIC)
    pw, ph = item.size
    canvas.paste(item, (int(cw * cx_pct - pw / 2), int(ch * cy_pct - ph / 2)), item)
    return canvas


def compose_look(session: LookSession) -> bytes:
    """Lay every item onto the background in flatlay style; returns JPEG bytes."""
    bg = session.background.copy().convert("RGBA").resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    for category in DRAW_ORDER:
        if category not in session.items:
            continue
        log.info("outfit: processing %s", category)
        item = _remove_background(session.items[category], MODEL_BY_CATEGORY.get(category, DEFAULT_MODEL))
        bg = _fit_and_place(bg, item, *LAYOUT[category])
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


# ---------- keyboards ----------

def _menu_kb(session: LookSession) -> InlineKeyboardMarkup:
    from .main import _kb  # shared helper

    rows, row = [], []
    for cat_id, name in CATEGORIES.items():
        mark = " ✅" if cat_id in session.items else ""
        short = name.split("(")[0].strip()
        row.append((f"{short}{mark}", f"of:add:{cat_id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if session.items:
        rows.append([("🎨 Собрать лук!", "of:compose")])
    rows.append([("❌ Отмена", "of:cancel")])
    return _kb(rows)


def _back_kb() -> InlineKeyboardMarkup:
    from .main import _kb

    return _kb([[("⬅️ Назад в меню", "of:menu")]])


# ---------- flow ----------

router = Router(name="outfit")


async def start_session(message: Message) -> None:
    """Begin a new look (called from the app chooser or /outfit)."""
    sessions[message.chat.id] = LookSession()
    await message.answer(
        "👕 <b>Новый лук!</b>\n\n"
        "Сначала пришли фото <b>фона</b> (ковёр, ткань, пол — на чём разложить вещи).\n"
        "Или /skipbg — белый фон."
    )


@router.message(Command("outfit", "newlook"))
async def cmd_outfit(message: Message) -> None:
    await start_session(message)


@router.message(Command("skipbg"))
async def cmd_skipbg(message: Message) -> None:
    session = sessions.get(message.chat.id)
    if not session:
        await message.answer("Сначала начни лук: /outfit")
        return
    session.background = Image.new("RGB", (CANVAS_W, CANVAS_H), (245, 245, 245))
    session.state = "menu"
    await message.answer("✅ Белый фон. Добавляй предметы 👇", reply_markup=_menu_kb(session))


def _has_session(message: Message) -> bool:
    return message.chat.id in sessions


@router.message(F.photo, _has_session)
async def on_photo(message: Message, bot: Bot) -> None:
    """A photo while an outfit session is active: the background, or an item for the chosen category."""
    session = sessions[message.chat.id]
    buf = await bot.download(message.photo[-1])
    img = Image.open(buf).convert("RGB")

    if session.state == "waiting_bg":
        session.background = img
        session.state = "menu"
        await message.answer("✅ Фон установлен! Добавляй предметы 👇", reply_markup=_menu_kb(session))
    elif session.state == "waiting_item" and session.current_category:
        cat = session.current_category
        session.items[cat] = img
        session.current_category = None
        session.state = "menu"
        await message.answer(
            f"✅ {CATEGORIES[cat].split('(')[0].strip()} добавлен! ({len(session.items)} предм.)\n"
            "Добавь ещё или жми «Собрать лук» 👇",
            reply_markup=_menu_kb(session),
        )
    else:
        await message.answer("Сначала выбери категорию в меню 👇", reply_markup=_menu_kb(session))


@router.callback_query(F.data.startswith("of:"))
async def on_callback(call: CallbackQuery, bot: Bot) -> None:
    session = sessions.get(call.message.chat.id)
    if not session:
        await call.answer("Лук не начат. Нажми /outfit", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[1]

    if action == "cancel":
        sessions.pop(call.message.chat.id, None)
        await call.message.edit_text("❌ Лук отменён. /outfit — начать заново.")
    elif action == "menu":
        session.state = "menu"
        session.current_category = None
        await call.message.edit_text("Выбери категорию 👇", reply_markup=_menu_kb(session))
    elif action == "add":
        cat = parts[2]
        session.current_category = cat
        session.state = "waiting_item"
        again = " (заменит текущее)" if cat in session.items else ""
        await call.message.edit_text(
            f"📷 Пришли фото: <b>{CATEGORIES[cat]}</b>{again}\n\n"
            "Лучше на однотонном фоне — задний план уберу сам.",
            reply_markup=_back_kb(),
        )
    elif action == "compose":
        await _compose(call, bot, session)
    await call.answer()


async def _compose(call: CallbackQuery, bot: Bot, session: LookSession) -> None:
    if not session.items:
        await call.answer("Добавь хотя бы один предмет!", show_alert=True)
        return
    await call.message.edit_text(
        f"🎨 Собираю лук из {len(session.items)} предметов…\n"
        "Убираю фоны и компоную — 20–90 сек ⏳"
    )
    try:
        async with _compose_lock:
            data = await asyncio.to_thread(compose_look, session)
    except Exception as e:
        log.exception("outfit compose failed")
        await call.message.answer(f"😔 Ошибка при сборке: {e}\n/outfit — попробовать снова.")
        return
    await bot.send_photo(
        call.message.chat.id,
        BufferedInputFile(data, filename="look.jpg"),
        caption="✨ Твой лук готов!\n\n/outfit — новый образ.",
    )
    sessions.pop(call.message.chat.id, None)


def cancel_session(chat_id: int) -> bool:
    """Drop an active outfit session (used by the shared /cancel)."""
    return sessions.pop(chat_id, None) is not None
