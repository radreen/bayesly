# Налаштування автопостингу (для власника)

Що робить бот, коли все налаштовано:
1. 3 рази на день (07:00, 13:00, 19:00 UTC) GitHub Actions знаходить свіжі новини, Gemini пише тексти, рендеряться слайди.
2. **Quality gate** перевіряє пост. Якщо все чисто, пост публікується в Threads, Instagram і X.
3. Якщо є сумнів (low confidence, задовгий текст, слабке джерело, немає картинок тощо), пост **не публікується**, а приходить тобі в Telegram з кнопками **Publish / Skip**.
4. Кожна дія записується в `ci-state/posted.json`, тому повторний запуск ніколи не дублює пост.

**За замовчуванням усе працює в dry-run** і нічого не публікує, доки ти сам не увімкнеш `BAYESLY_LIVE=true` (крок 7).

Секрети (токени, ключі) ніколи не пишемо в код і не шлемо в чат. Вони лежать тільки в `.env` (локально) і в GitHub Secrets.

---

## 1. GitHub-репозиторій (безкоштовно)
1. Створи **приватний** репозиторій `bayesly` на github.com і запуш туди цей код:
   ```powershell
   git remote add origin https://github.com/<твій-логін>/bayesly.git
   ```
   ```powershell
   git push -u origin main
   ```
2. Створи **публічний** репозиторій `bayesly-media`. Він порожній, туди бот складатиме тільки JPEG-слайди. Threads та Instagram забирають картинки за публічним URL, тому без нього ніяк.
3. Створи **fine-grained token**: GitHub → Settings → Developer settings → Fine-grained tokens.
   - Repository access: тільки `bayesly-media`.
   - Permissions: **Contents: Read and write**.
   - Це `MEDIA_GITHUB_TOKEN`.
4. Створи ще один fine-grained token:
   - Repository access: тільки `bayesly`.
   - Permissions: **Secrets: Read and write**.
   - Це `SECRETS_PAT`. З ним бот раз на тиждень сам оновлює токени Meta.

## 2. Threads (безкоштовно)
1. Перейди на developers.facebook.com → My Apps → Create App → use case **"Access the Threads API"**.
2. Увімкни дозволи **threads_basic** і **threads_content_publish**.
3. Додай @bayesly.ai як **Threads Tester**: App roles → Roles. Потім прийми запрошення в застосунку Threads: Settings → Account → Website permissions. Для нашого власного акаунта App Review не потрібен.
4. Отримай токен через **Graph API Explorer** або User Token Generator у налаштуваннях use case.
5. Обміняй його на **long-lived** токен (60 днів): `GET https://graph.threads.com/access_token?grant_type=th_exchange_token&client_secret=...&access_token=...`.
6. `THREADS_USER_ID`: його повертає `GET https://graph.threads.com/v1.0/me?fields=id,username&access_token=...`.

## 3. Instagram (безкоштовно)
1. Instagram @bayesly.ai має бути **професійним** акаунтом: Settings → Account type → Professional (Creator або Business). Facebook-сторінка не потрібна.
2. У застосунку Bayesly use case **"Manage messaging & content on Instagram"** → **API setup with Unified login** (старий варіант "Instagram login" для нових застосунків уже недоступний).
3. @bayesly.ai має роль **Instagram Tester** (запрошення приймається в Instagram: Settings → Apps and websites).
4. Крок **"Generate access tokens"**: додай @bayesly.ai і згенеруй токен (system-user token, належить портфоліо bayesly.ai). Термін дії: обирай "Never", якщо пропонують.
5. `INSTAGRAM_USER_ID`: `GET https://graph.facebook.com/v27.0/me/assigned_instagram_accounts?fields=id,username&access_token=...` → поле `id`.

## 4. X (платно, але копійки)
1. Зайди на console.x.com під @bayesly_ai і створи App.
2. Поповни кредити. Публікація без посилання коштує **$0.015**, тобто 3 пости на день ≈ **$1.4/міс**. Бот **відмовляється** публікувати текст з посиланням, бо такий пост коштує $0.20.
3. User authentication settings → **Read and write**.
4. Keys and tokens: **API Key/Secret** і **Access Token/Secret**. Токени згенеруй **після** того, як увімкнув Read and write.
5. Для першого live-запуску постимо з 1 картинкою (`X_MAX_IMAGES=1`). Ціну завантаження медіа перевіримо на першому пості.

## 5. Telegram-бот (безкоштовно)
1. Напиши @BotFather → `/newbot` і отримаєш `TELEGRAM_BOT_TOKEN`.
2. Напиши своєму боту будь-що. Потім відкрий `https://api.telegram.org/bot<TOKEN>/getUpdates`: число `chat.id` і є `TELEGRAM_CHAT_ID`.
3. Бот слухає **тільки** цей chat id. Команди: `/pause` (стоп-кран), `/resume`, `/status`.

## 6. Секрети в GitHub
Репозиторій `bayesly` → Settings → Secrets and variables → Actions.

**Secrets:**
- `GEMINI_API_KEY` (бажано новий: старий світився в чаті)
- `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN`
- `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`
- `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`
- `MEDIA_GITHUB_TOKEN`, `SECRETS_PAT`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

**Variables:**
- `MEDIA_GITHUB_REPO` = `<логін>/bayesly-media`
- `PLATFORMS` = `threads,instagram,x`. Можна почати тільки з `threads`.
- `POSTS_PER_RUN` = `1` (3 запуски на день дають 3 пости).
- `BAYESLY_LIVE`: **поки не створюй**.

## 7. Перший запуск
1. Actions → autopost → **Run workflow** (command: autopost). Це dry-run: у лозі буде видно, що б опублікувалось, а що пішло б на перевірку.
2. Коли лог виглядає добре, скажи мені, і ми разом зробимо **перший live-пост**. Тільки після цього створюємо змінну `BAYESLY_LIVE` = `true`.

## Стоп-кран (будь-який із варіантів)
- `/pause` у Telegram-боті;
- змінна `BAYESLY_PAUSED` = `1` у GitHub Variables;
- файл `ci-state/PAUSED` у репозиторії;
- локально: `python -m bayesly pause`.

## Скільки це коштує
| Що | Ціна |
|---|---|
| GitHub Actions (приватний репо, ~1000 хв/міс з 2000 безкоштовних) | $0 |
| Gemini (безкоштовний ліміт AI Studio) | $0 |
| Threads, Instagram API | $0 |
| Хостинг картинок (публічний GitHub-репо) | $0 |
| Telegram-бот | $0 |
| X: 90 постів на місяць без посилань | ≈ $1.4 |
