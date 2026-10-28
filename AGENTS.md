# Image Editor (AI native)

## Что это

Веб-сервис для точечного редактирования изображений по маске через API нейросетей.
Пользователь загружает картинку, выделяет область (лассо/прямоугольник/кисть), описывает
изменение текстом, и модель генерирует правку. Минималистичный интерфейс, светлая тема,
без лишних инструментов рисования.

## Текущая фаза

MVP (Фаза 1) без многопользовательского режима. Все провайдеры идут через один шлюз
CloseRouter (`https://api.closerouter.dev`): один ключ, один баланс, 6 image-моделей.

## Стек

- Frontend: Vue 3 + Vite + TypeScript + HTML Canvas + CSS
- Backend: FastAPI (совместим с Python 3.10+), httpx, Pillow
- Палитра: светлая (#F8FAFC, #FFFFFF, #E2E8F0, акцент #2563EB)

## Структура

- `frontend/src/App.vue` - интерфейс, canvas, маска, история, выбор модели, генерация и композит
- `frontend/src/style.css` - адаптивный светлый интерфейс, включая карточки моделей
- `frontend/vite.config.ts` - Vite и proxy `/api` на `127.0.0.1:8000` с таймаутом 600 с
- `backend/app/main.py` - FastAPI app, CORS, загрузка `.env` и health route
- `backend/app/routers/inpaint.py` - `GET /api/models` (каталог) и `POST /api/inpaint`
- `backend/app/services/providers.py` - адаптер CloseRouter: каталог, edit, выгрузка результата
- `backend/tests/` - health и contract tests (провайдер замокан, сеть не нужна)

## Репозиторий и секреты

- Публичный репозиторий: https://github.com/GogikOrtey/image-editor-ai-native (ветка `main`)
- `Ключик и доступные модели.txt` - реальные ключи и список моделей, в git не попадает.
- `backend/.env` - рабочий ключ `CLOSEROUTER_API_KEY` и `CLOSEROUTER_BASE_URL`. Файл закрыт
  `.gitignore`, в репозиторий и логи не попадает. Ключ читается только на сервере,
  в браузер не передаётся.
- `.gitignore` закрывает: секреты (`*.env`, `*.pem`, `*.key`), `node_modules/`, `dist/`,
  `__pycache__/`, `venv/`, `.pytest_cache/`, системные файлы OneDrive.

## Как запускать

Обычный способ - двойной щелчок по `start-server.cmd` в корне проекта. Скрипт проверяет
зависимости (ставит недостающие), поднимает оба сервера, ждёт готовности, открывает браузер.
Порты живут, пока открыто окно консоли; закрытие окна гасит оба сервера.

Вручную (для отладки или из агента):

Backend:
  cd backend
  pip install -r requirements.txt
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Frontend:
  cd frontend
  npm install
  npx vite --host=127.0.0.1 --port=5173 --strictPort

## Принятые решения

- Vue 3 + Canvas 2D: лёгкий интерфейс и прямой контроль маски в координатах изображения
- FastAPI как прокси: ключ и выгрузка результата на сервере, браузер получает только байты
- Общий контракт: `POST /api/inpaint` multipart (image, mask, prompt, model), ответ - бинарная картинка
- Каталог моделей не хардкодится: `GET /api/models` проксирует живой список и фильтрует image-модели
- Окружение: Node.js 22.15, Python 3.10.10

### Маска: композит на клиенте

CloseRouter **не принимает mask** - ни одна из 24 операций каталога не имеет такого поля.
Поэтому маска уходит на сервер только для статистики покрытия и формулировки инструкции,
а после ответа клиент накладывает сгенерированный кадр на исходный через маску с blur 2 px.
Так сохраняется смысл ТЗ (меняется только выделенное) при честном использовании API.

### Разведка провайдеров с поддержкой маски (2026-10-28, не внедрено)

Проверено по официальным OpenAPI-спекам и живым запросам. Итог: маску из всех шлюзов
по-настоящему поддерживает только PoYo.

- **PoYo (`https://api.poyo.ai`, доки `https://docs.poyo.ai`)** - маска есть, поле `mask_url`
  внутри `input`. Только у двух семейств:
  - `ideogram-v4.5-edit` - полная поддержка: `mask_url` должен совпадать по размеру с основной
    картинкой и содержать **чёрные зоны правки и белые зоны сохранения**. Требует `size=auto`
    (или отсутствующий `size`). Есть `edit_precision: high`, который восстанавливает
    неизменённые пиксели. С маской максимум 3 референса вместо 4.
  - `gpt-4o-image-edit` - принимает `input.mask_url`, но требования к цветам не описаны.
  - Не принимают маску: Flux 2, Nano Banana, Seedream 4, Qwen Image 3, Kling O1,
    Grok Imagine - у них `-edit` без маски.
  - Задача асинхронная: `POST /api/generate/submit`, дальше `task-management/status`.
- **OpenRouter** - маски нет. `POST /images` (`ImageGenerationRequest`) поля маски не имеет,
  только `input_references` (до 16 референсов) и `background: transparent`. В Responses API
  есть `ImageGenerationServerTool.input_image_mask` (`file_id` / `image_url`), но это другой
  эндпоинт, единственное упоминание во всей спеке и формат маски не описан.
- **Atlas Cloud** - маски нет. В спеке (914 КБ) `mask` встречается 2 раза, оба - CSS
  `mask-image` в вёрстке сайта. `inpaint`/`outpaint`/`erase` - 0 вхождений.
- **Google Gemini напрямую** - см. раздел про free-ключ ниже.

Если возвращаться к PoYo: завести вторым провайдером рядом с CloseRouter с переключателем
в UI, для Ideogram слать `mask_url` напрямую и убрать клиентский композит. Единственный риск -
документация обещает чёрный/белый, но фактическое соблюдение маски надо проверить живьём
на одной дешёвой картинке.

### Google AI Studio: бесплатный ключ не годится

Проверенный free-ключ (`AQ.Ab8RN6...`) жив, но отдаёт 50 моделей, среди которых
**все image-модели с лимитом 0** на free-тарифе: `gemini-2.5-flash-image`,
`gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`, `gemini-nano-banana-2.1`,
`gemini-3-pro-image`, `nano-banana-pro-preview`. Метрика `generate_content_free_tier_requests`,
`limit: 0` по RPM/RPD/TPM. Через OpenAI-совместимый эндпоинт
`/v1beta/openai/images/generations` - то же самое. Imagen на `v1beta` не отдаётся вообще.
Текст на том же ключе работает. Вывод: `free` у Google - это бесплатный текст, картинки
только после привязки биллинга.

**Маски у Gemini нет.** В документации есть раздел "Inpainting (semantic masking)", но это
шаблон промпта словами ("change only the blue sofa..."), а не параметр. Поле `mask` в Part
даёт `400 Unknown name "mask": Cannot find field`.

## Состояние

Готово и проверено вживую: загрузка, инструменты маски, undo/redo, выбор модели из живого
каталога, генерация через прокси, композит по маске, выгрузка PNG. Backend `10 passed`,
`npm run build` проходит.

Проверенные модели и фактические замеры (2026-10-28, картинка 512x512):

- `openai/gpt-image-2.5-flare` - 24-97 с, ответ `b64_json`, ~$0.015
- `openai/gpt-image-2.5-sunburst` - 22-50 с, ответ `b64_json`, ~$0.015
- `google/gemini-3.1-flash-image-preview` - 35 с, ответ по URL, 2048x2048
- `google/gemini-3-pro-image` - 41-49 с, ответ по URL, 2048x2048
- `qwen/qwen-image-3.0` - 24-29 с, ответ по URL, 1344x768, ~$0.054
- `qwen/qwen-image-3.0-pro` - 68-86 с, самый дорогой (~$0.078), иногда 504 по таймауту

Генерация платная - при отладке проверять одной моделью, не прогонять весь список.

Следующий шаг: пользовательская проверка UI, затем Фаза 2 (авторизация, серверные пресеты,
история генераций в БД).

Грабли:

- `api.closerouter.com` (`.com`!) не существует: домен делегирован без рабочих NS, локальный
  DNS отдаёт мусорный IP. Рабочий хост - `api.closerouter.dev`.
- `/v1/models` живёт на `api.closerouter.dev`, а `closerouter.dev/v1/models` даёт 404.
- Поле `resolution` у Gemini принимает только `1K`/`2K`/`4K` в верхнем регистре; `2k` даёт
  400 `Invalid image operation input`.
- `response_format: b64_json` не принимается Gemini-моделями, поэтому для них он снимается.
- Ответы приходят в двух видах: `data[0].b64_json` у OpenAI и `data[0].url` у Gemini/Qwen.
  Ссылка живёт 30 дней и требует `Authorization` для хостов CloseRouter.
- Qwen edit ждёт поле `image_urls`, а не `image`; лишнее поле даёт 400.
- Медленные модели упирались в 504 на dev-proxy Vite - поднят `proxyTimeout` до 600 с.
- `npm run dev -- --port 5173` передаёт аргументы неверно (Vite падает с `Unused args`).
  Запускать `npx vite --host=127.0.0.1 --port=5173 --strictPort`.
- Vite по умолчанию слушает только IPv6 `::1`, поэтому `127.0.0.1` не отвечает.
- FastAPI с `@router.post("")` монтируется как `/api` без слэша - нужен явный `/inpaint`.
- Скриншоты headless Edge требуют явного `--user-data-dir`, иначе файл не создаётся.