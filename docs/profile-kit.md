# Профіль-кіт Bayesly (Threads · Instagram · X)

Готові тексти й картинки для оформлення профілів. Тексти англійською (публічні), пояснення українською.
Картинки: `design/profile/export/` (генерує `python design/profile/render_profile.py`).

Ліміти полів у дужках; кількість символів у кожному тексті вже перевірена.

---

## 1. Аватар (усі три мережі)
`design/logo/final/bayesly-avatar-lime.png` (1000×1000, лаймовий фон, чорне [b]).
Мережі обрізають його в коло; знак уже стоїть по центру з запасом.

## 2. Instagram (@bayesly.ai)

**Name** (64; це поле шукається в пошуку Instagram, тому з ключовими словами):
```
Bayesly | AI & Tech News
```

**Bio** (150, зараз 127):
```
AI & tech news as a Bayesian update.
Before → Now → how sure we are.
A source on every post. No hype.
AI-drafted, rule-checked.
```

**Category:** `Media/news company` (Edit profile → Category).
**Link:** поки немає домену — посилання на Threads-профіль `https://www.threads.com/@bayesly.ai`
(у Threads-профілі є джерело кожного поста; в Instagram посилання в підписах не клікаються).
**Contact options / email:** не показувати, поки немає скриньки бренду.

**Highlights** (необов'язково, з'являються лише після перших Stories):
обкладинки `highlight-about.png`, `highlight-tested.png`, `highlight-updates.png`.

## 3. Threads (@bayesly.ai)

Ім'я й аватар Threads бере з Instagram.

**Bio** (150) — той самий текст, що в Instagram:
```
AI & tech news as a Bayesian update.
Before → Now → how sure we are.
A source on every post. No hype.
AI-drafted, rule-checked.
```
**Link:** `https://x.com/bayesly_ai` (або домен, коли буде).

## 4. X (@bayesly_ai)

**Name** (50, зараз 26):
```
Bayesly — AI news, updated
```

**Bio** (160, зараз 143):
```
AI & tech news as a Bayesian update: what we assumed, what changed, how sure we are. A source on every post. No hype. AI-drafted, rule-checked.
```

**Header:** `design/profile/export/x-header.png` (1500×500). Важливий зміст стоїть у центрі й праворуч:
аватар на X перекриває лівий нижній кут, а на телефонах шапку обрізає зверху й знизу.
**Location:** лишити порожнім (або `Worldwide`).
**Website:** `https://www.threads.com/@bayesly.ai`.

**Мітка автоматизованого акаунта.** Акаунт постить через API за розкладом, тож чесно ввімкнути
мітку "Automated" (X: Settings → Your account → Account information → Automation → вказати
керуючий акаунт). Перевір наявність цього пункту у своєму інтерфейсі: X змінює налаштування.

## 5. Закріплений пост "Що таке Bayesly"

Картинки: `intro-01.jpg` … `intro-04.jpg` (1080×1350, JPEG підходить для всіх трьох мереж).

**Threads** (500, зараз 451):
```
What is Bayesly?

We post AI and tech news as a Bayesian update.

Before: what most people assumed.
Now: what changed, with the number if there is one.
Confidence: low, medium or high, and why.

Every post links its primary source. Vendor claims stay claims. When we don't know yet, we say so.

Drafts are written with AI from the source text and checked by rules. Doubtful ones go to a human.

Our take: fewer, better-sourced updates beat a firehose.
```

**X** (280, зараз 238; без посилань — так пост коштує $0.015):
```
Bayesly = AI & tech news as a Bayesian update.

Before: what most people assumed.
Now: what changed, with the number if there is one.
Confidence: low, medium or high, and why.

Vendor claims stay claims. When we don't know yet, we say so.
```

**Instagram** — текст Threads, з останнім рядком замість посилань:
```
Sources for every post: link in bio.
```

Після публікації: закріпити (Threads і X: "Pin to profile"; Instagram: "Pin to your profile").
