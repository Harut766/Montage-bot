# Montage bot

Telegram-бот: присылаешь длинное видео (влог до ~20 минут) → получаешь вертикальные ролики 9:16 для TikTok
с красивыми субтитрами и надписью «Часть N».

## Как работает

1. Бот принимает видео файлом (до 2 ГБ) или ссылкой (YouTube и т. п.).
2. Задаёт 2 вопроса: длина роликов (~60 / ~120 / ~180 сек) и язык речи (русский / английский).
3. **Whisper** (локально) расшифровывает речь с таймкодами каждого слова.
4. Расшифровка уходит в **n8n → Gemini**, который выбирает самые интересные фрагменты и придумывает подписи.
   Если n8n недоступен — бот просто режет видео на равные части по границам фраз.
5. **FFmpeg** собирает каждый ролик: 1080×1920, размытый фон, видео по центру,
   белые субтитры с чёрной обводкой (Montserrat ExtraBold) под видео, «Часть N» над видео.
6. Бот присылает ролики по мере готовности.

Всё работает на вашем компьютере в Docker. Бот доступен, пока компьютер включён.
Задачи выполняются по очереди, по одной.

## Установка

### 1. Что нужно
- Docker (Docker Desktop на Windows/Mac).
- Токен бота от [@BotFather](https://t.me/BotFather).
- `api_id` и `api_hash` с [my.telegram.org](https://my.telegram.org) → *API development tools*
  (нужны для Local Bot API Server, чтобы принимать большие файлы).
- Ключ Gemini API: [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

### 2. Настройка
```bash
cp .env.example .env
# заполнить BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH
```

Один раз «отвязать» бота от облачного сервера Telegram, чтобы он работал через локальный
(открыть в браузере, подставив токен):
```
https://api.telegram.org/bot<BOT_TOKEN>/logOut
```

### 3. n8n
1. В n8n: *Workflows → Import from File* → `n8n/montage-segments.workflow.json`.
2. Узел **Webhook** → Credential → *Create new* → **Header Auth**:
   Name: `X-Montage-Secret`, Value: длинная случайная строка (`openssl rand -hex 24`).
   Эту же строку впишите в `.env` как `N8N_SECRET=`.
3. Узел **Gemini** → Credential → *Create new* → **Header Auth**:
   Name: `x-goog-api-key`, Value: ваш ключ Gemini.
4. Включить workflow (**Active**) и вписать в `.env` его Production URL:
   `N8N_WEBHOOK_URL=https://ваш-n8n-домен/webhook/montage-segments`.

Промпт, по которому Gemini выбирает моменты, лежит в узле **Build prompt** — его можно править прямо в n8n.
Модель задаётся в URL узла **Gemini** (`gemini-3.5-flash-lite`); если она не ответит, запрос уйдёт в **Gemini (fallback)** (`gemini-3.6-flash`).

### 4. Запуск
```bash
docker compose up -d --build
docker compose logs -f bot
```

При первой расшифровке скачается модель Whisper (~1.5 ГБ для `medium`), дальше она берётся из кэша.

## Настройки (`.env`)

| Переменная | Что делает |
|---|---|
| `ALLOWED_USERS` | Telegram ID через запятую, кому доступен бот |
| `WHISPER_MODEL` | `small` — быстрее, `medium` — баланс, `large-v3` — точнее, но медленнее |
| `WHISPER_DEVICE` | `auto` / `cpu` / `cuda` |
| `N8N_WEBHOOK_URL` | адрес webhook n8n; пусто — всегда резать на равные части |

Без видеокарты NVIDIA расшифровка 20-минутного видео моделью `medium` занимает ~10–20 минут,
рендер — ещё пару минут на каждый ролик. Если долго — поставьте `WHISPER_MODEL=small`.

## Разработка

```bash
pip install -r requirements-dev.txt   # нужен ffmpeg в системе
pytest
```

Структура:
- `bot/main.py` — Telegram-бот, диалог, очередь задач
- `bot/pipeline.py` — весь конвейер обработки, запрос в n8n
- `bot/transcribe.py` — Whisper
- `bot/clips.py` — выравнивание фрагментов по фразам, запасная нарезка
- `bot/subtitles.py` — раскладка кадра и ASS-субтитры (стиль тут)
- `bot/render.py` — FFmpeg

Шрифт Montserrat распространяется по лицензии SIL OFL (`licenses/Montserrat-OFL.txt`).
