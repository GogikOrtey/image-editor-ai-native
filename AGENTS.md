# Image Editor (AI native)

## Что это

Веб-сервис для точечного inpainting-редактирования изображений по маске через API нейросетей.
Пользователь загружает картинку, выделяет область (лассо/прямоугольник/кисть), описывает
изменение текстом, и API (ChatGPT Image, Gemini Nano Banana или роутер) генерирует правку
только в выделенной зоне. Минималистичный интерфейс, светлая тема, без лишних инструментов
рисования.

## Текущая фаза

MVP (Фаза 1) - базовый редактор без многопользовательского режима.
Настройки API (ключ, base URL, модель) хранятся в localStorage браузера.

## Стек

- Frontend: Vue 3 + Vite + TypeScript + HTML Canvas + CSS
- Backend: FastAPI (совместим с Python 3.10+)
- Палитра: светлая (#F8FAFC, #FFFFFF, #E2E8F0, акцент #2563EB)

## Структура

- `frontend/src/App.vue` - основной интерфейс, canvas, маска, история, настройки и генерация
- `frontend/src/style.css` - адаптивный светлый интерфейс
- `frontend/vite.config.ts` - Vite и proxy `/api` на `127.0.0.1:8000`
- `backend/app/main.py` - FastAPI app, CORS и health route
- `backend/app/routers/inpaint.py` - общий `/api/inpaint` и provider-specific endpoints
- `backend/app/services/providers.py` - HTTP adapters для OpenAI-compatible API и Gemini
- `backend/tests/` - health и API contract tests

## Репозиторий и секреты

- Публичный репозиторий: https://github.com/GogikOrtey/image-editor-ai-native (ветка `main`, remote `origin`)
- `Ключик и доступные модели.txt` - реальные API-ключи и список моделей. В git не попадает,
  закрыт в `.gitignore` первой строкой. Не добавлять `-f`, не переносить содержимое в коммиты,
  README и логи. Перед первым пушем индекс проверен: файла в репозитории нет.
- `.gitignore` в корне закрывает: секреты (`*.env`, `*.pem`, `*.key`), `node_modules/`, `dist/`,
  `__pycache__/`, `venv/`, `.pytest_cache/`, а также системные файлы OneDrive (`desktop.ini`
  и числовой `.ico` рядом с ним).

## Как запускать

Frontend:
  cd frontend
  npm install
  npm run dev

Backend:
  cd backend
  python -m venv venv
  venv\Scripts\activate
  pip install -r requirements.txt
  uvicorn app.main:app --reload --port 8000

## Принятые решения

- Vue 3 + Canvas 2D: лёгкий интерфейс и прямой контроль маски в координатах изображения
- FastAPI: backend proxy, Pillow-конвертация OpenAI mask, httpx для provider requests
- Общий контракт: `POST /api/inpaint`, request-scoped `X-Provider-API-Key`, binary image response
- OpenAI-compatible routers идут через OpenAI adapter; Gemini использует generateContent
- API-ключ хранится только в localStorage MVP; для production потребуется серверное secret storage
- Окружение разработки: Node.js 22.15, Python 3.10.10

## Состояние

Frontend и backend MVP реализованы. Upload, canvas tools, zoom/pan, mask, undo/redo, provider settings,
localStorage и PNG export работают на клиенте. FastAPI health и provider proxy routes готовы.
Проверены `npm run build` и backend tests; реальную генерацию с внешними API ещё нужно проверить
с рабочими ключом и моделью. Следующий шаг: пользовательская проверка UI и live provider smoke test.

Git: репозиторий инициализирован, первый коммит `6be83a5` (31 файл) отправлен в публичный
GitHub. Дальнейшая работа ведётся в `main` с пушами в `origin`.

Проверено вживую (2026-10-28): `npm run build` проходит, backend `5 passed`, страница
`http://127.0.0.1:5173` рендерит весь интерфейс, `/api/health` через Vite-proxy отвечает
`200`. Сквозной запрос Vite -> FastAPI -> OpenAI с невалидным ключом вернул `502`
`Provider authentication failed`, то есть конвертация маски и проксирование работают.
Ещё не проверено с рабочим ключом: фактическая генерация и разбор ответа Gemini.

Грабли:

- `npm run dev -- --port 5173` в этом окружении передаёт аргументы неверно (Vite падает
  с `Unused args`). Запускать `npx vite --host=127.0.0.1 --port=5173 --strictPort`.
- Vite по умолчанию слушает только IPv6 `::1`, поэтому `127.0.0.1` не отвечает.
  Нужен явный `--host=127.0.0.1`.
- OpenAI ждёт в маске прозрачность в редактируемой зоне, а UI рисует белую область.
  Конвертация в `make_openai_alpha_mask`; для Gemini маска уходит как есть.
- Скриншоты headless Edge требуют явного `--user-data-dir`, иначе файл не создаётся.
