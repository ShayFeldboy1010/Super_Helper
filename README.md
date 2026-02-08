# 🤖 Super Helper - AI Personal Assistant Bot

## 📋 תיאור הפרויקט

**Super Helper** הוא עוזר אישי חכם מבוסס AI שעובד דרך Telegram. הבוט מסוגל להבין שפה טבעית (עברית ואנגלית), לנהל משימות, ליצור אירועים ביומן Google, לשמור הערות, ולענות על שאלות על סמך המידע שלך.

### ⭐ יכולות עיקריות
- **🧠 Smart Router** - מסווג אוטומטית את ההודעות שלך (משימה / אירוע / הערה / שאלה)
- **📅 Google Calendar** - יצירת אירועים ביומן וצפייה בלוח הזמנים
- **✅ Task Management** - ניהול משימות עם תאריכי יעד ועדיפויות
- **📝 Notes** - שמירת הערות עם תגיות
- **💬 Query** - שאלות על המשימות, האירועים וההערות שלך
- **⏰ Automated Reminders** - התראות על משימות שעבר זמנן
- **☀️ Daily Briefing** - סיכום יומי של לוח הזמנים והמשימות

---

## 🏗️ ארכיטקטורת הפרויקט

```
AI_Super_man/
├── 📁 api/                          # Vercel Serverless Entry Point
│   └── index.py                     # נקודת הכניסה ל-Vercel
│
├── 📁 app/                          # קוד האפליקציה הראשי
│   ├── main.py                      # 🚀 FastAPI App + Webhook Handler
│   │
│   ├── 📁 bot/                      # לוגיקת הבוט
│   │   ├── loader.py                # אתחול Bot + Dispatcher (aiogram)
│   │   ├── middleware.py            # IDGuardMiddleware - הרשאות משתמש
│   │   └── 📁 routers/              # הנתבים של הבוט
│   │       ├── tasks.py             # 🎯 Handler ראשי - מעבד הודעות
│   │       ├── auth.py              # 🔐 Google OAuth Flow
│   │       ├── google_routes.py     # /login, /today commands
│   │       └── cron.py              # ⏰ Cron Jobs (reminders, daily brief)
│   │
│   ├── 📁 core/                     # תשתית
│   │   ├── config.py                # ⚙️ Settings (env vars)
│   │   ├── database.py              # 🗄️ Supabase Client
│   │   └── security.py              # 🔒 Token Encryption
│   │
│   ├── 📁 models/                   # מודלים
│   │   ├── schemas.py               # Pydantic schemas (TaskCreate, etc.)
│   │   └── router_models.py         # Router response models
│   │
│   └── 📁 services/                 # שירותים (Business Logic)
│       ├── router_service.py        # 🧠 Smart Router - LLM Classification
│       ├── llm_engine.py            # 🤖 LLM for Task Parsing
│       ├── task_service.py          # ✅ CRUD for Tasks
│       ├── google_svc.py            # 📅 Google Calendar API
│       ├── archive_service.py       # 📝 Notes Storage
│       └── query_service.py         # 💬 RAG-lite Query Handler
│
├── 📁 .github/workflows/            # GitHub Actions
│   └── scheduler.yml                # ⏰ Cron Jobs (External Trigger)
│
├── .env                             # 🔑 Environment Variables (לא ב-Git)
├── .gitignore                       # קבצים שלא נכנסים ל-Git
├── requirements.txt                 # 📦 Python Dependencies
└── vercel.json                      # ☁️ Vercel Configuration
```

---

## 🔗 חיבורים חיצוניים (External Services)

### 1. 🤖 Telegram Bot API
- **מטרה:** ממשק המשתמש - קבלת ושליחת הודעות
- **סוג חיבור:** Webhook
- **URL:** `https://super-helper-theta.vercel.app/webhook`
- **משתני סביבה:**
  - `TELEGRAM_BOT_TOKEN` - Token של הבוט
  - `TELEGRAM_USER_ID` - ID של המשתמש המורשה
  - `M_WEBHOOK_SECRET` - סוד לאימות הבקשות

### 2. 🗄️ Supabase (PostgreSQL)
- **מטרה:** בסיס נתונים - שמירת משימות, הערות, ו-tokens
- **טבלאות:**
  - `users` - משתמשים ו-Google refresh tokens
  - `tasks` - משימות (title, due_at, priority, status)
  - `archive` - הערות ותגיות
- **משתני סביבה:**
  - `SUPABASE_URL`
  - `SUPABASE_KEY`

### 3. 🧠 Groq API (LLM)
- **מטרה:** הבנת שפה טבעית וסיווג הודעות
- **מודל:** `moonshotai/kimi-k2-instruct-0905`
- **משתני סביבה:**
  - `GROQ_API_KEY`

### 4. 📅 Google Calendar API
- **מטרה:** יצירת אירועים וקריאת לוח הזמנים
- **OAuth Scopes:**
  - `https://www.googleapis.com/auth/calendar`
  - `https://www.googleapis.com/auth/gmail.readonly`
- **משתני סביבה:**
  - `GOOGLE_CLIENT_ID`
  - `GOOGLE_CLIENT_SECRET`
  - `GOOGLE_REDIRECT_URI`

### 5. ☁️ Vercel (Hosting)
- **מטרה:** אירוח האפליקציה כ-Serverless Functions
- **URL:** `https://super-helper-theta.vercel.app`
- **Auto Deploy:** מ-GitHub (main branch)

### 6. 🔄 GitHub Actions
- **מטרה:** הפעלת Cron Jobs (הגבלת Vercel Hobby)
- **Jobs:**
  - `check-reminders` - כל 30 דקות
  - `daily-brief` - כל יום ב-6:00 בבוקר
- **Secrets נדרשים:**
  - `VERCEL_URL`
  - `CRON_SECRET`

---

## 🔄 זרימת בקשה טיפוסית

```
┌─────────────────┐
│   Telegram      │ ──────► משתמש שולח: "תזכיר לי לקנות חלב מחר"
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Vercel/Webhook │ ──────► POST /webhook
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  IDGuard        │ ──────► בדיקת הרשאות (TELEGRAM_USER_ID)
│  Middleware     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Smart Router   │ ──────► LLM מסווג: action_type = "task"
│  (Groq API)     │         payload = {title: "לקנות חלב", due_at: "מחר"}
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Task Handler   │ ──────► שומר ב-Supabase
│  (tasks.py)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Telegram       │ ──────► "✅ משימה נוצרה: לקנות חלב"
│  Response       │
└─────────────────┘
```

---

## ⚙️ משתני סביבה (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_USER_ID=your_telegram_id

# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=your_anon_key

# Groq (LLM)
GROQ_API_KEY=gsk_xxx

# Google OAuth
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx
GOOGLE_REDIRECT_URI=https://super-helper-theta.vercel.app/auth/callback

# Security
M_WEBHOOK_SECRET=random_secret_string
SECRET_KEY=another_random_string

# Vercel
WEBHOOK_URL=https://super-helper-theta.vercel.app/webhook
```

---

## 🚀 הפעלה מקומית

```bash
# 1. התקנת תלויות
python -m venv venv
source venv/bin/activate  # Mac/Linux
pip install -r requirements.txt

# 2. הגדרת משתני סביבה
cp .env.example .env
# ערוך את .env עם הערכים שלך

# 3. הפעלת השרת
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. הפעלת Ngrok (בטרמינל נפרד)
ngrok http 8000
# העתק את ה-URL ועדכן את הWebhook בטלגרם
```

---

## 📦 תלויות (requirements.txt)

| Package | תפקיד |
|---------|-------|
| `fastapi` | Web Framework |
| `uvicorn` | ASGI Server |
| `aiogram` | Telegram Bot Framework |
| `supabase` | Database Client |
| `groq` | LLM API Client |
| `google-auth` | Google OAuth |
| `google-api-python-client` | Google Calendar API |
| `pydantic-settings` | Configuration Management |
| `cryptography` | Token Encryption |

---

## 🎯 פקודות Telegram זמינות

| פקודה | תיאור |
|-------|-------|
| `/start` | התחלת שיחה |
| `/login` | התחברות לחשבון Google |
| `/today` | הצגת האירועים של היום |
| `טקסט חופשי` | הבוט יבין אוטומטית מה לעשות |

---

## 📝 דוגמאות לשימוש

```
>> "תזכיר לי להתקשר לרופא מחר ב-10"
✅ משימה נוצרה: להתקשר לרופא (יעד: מחר 10:00)

>> "הוסף לי פגישה עם דני ביום חמישי ב-14:00"
📅 אירוע נוצר: פגישה עם דני (יום חמישי 14:00)

>> "מה יש לי היום?"
📅 לוח הזמנים שלך:
• 10:00 - ישיבת צוות
• 14:00 - פגישה עם לקוח
✅ משימות פתוחות: 3

>> "תשמור לי את הרעיון: לפתח אפליקציה לניהול זמן"
🧠 הערה נשמרה (תגיות: #רעיונות #פרויקטים)
```

---

## 🔒 אבטחה

1. **Telegram User ID Whitelist** - רק המשתמש המורשה יכול להשתמש בבוט
2. **Webhook Secret** - אימות שהבקשות מגיעות מטלגרם
3. **Token Encryption** - ה-Google Refresh Tokens מוצפנים ב-DB
4. **Environment Variables** - כל הסודות מחוץ לקוד

---

## ☁️ Deployment (Vercel)

הפרויקט מוגדר ל-Auto Deploy מ-GitHub:
1. כל `git push` ל-`main` מפעיל Build חדש
2. Vercel משתמש ב-`api/index.py` כנקודת כניסה
3. Environment Variables צריכים להיות מוגדרים ב-Vercel Dashboard

---

## 📊 מבנה בסיס הנתונים (Supabase)

### טבלת `users`
| Column | Type | Description |
|--------|------|-------------|
| telegram_id | BIGINT (PK) | מזהה Telegram |
| google_refresh_token | TEXT | Token מוצפן |
| timezone | TEXT | אזור זמן |
| created_at | TIMESTAMP | תאריך יצירה |

### טבלת `tasks`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | מזהה |
| user_id | BIGINT | מזהה משתמש |
| title | TEXT | כותרת המשימה |
| due_at | TIMESTAMP | תאריך יעד |
| priority | TEXT | low/medium/high |
| status | TEXT | pending/done |
| created_at | TIMESTAMP | תאריך יצירה |

### טבלת `archive`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID (PK) | מזהה |
| user_id | BIGINT | מזהה משתמש |
| content | TEXT | תוכן ההערה |
| tags | TEXT[] | תגיות |
| created_at | TIMESTAMP | תאריך יצירה |

---

## 🛠️ פתרון בעיות נפוצות

| בעיה | פתרון |
|------|-------|
| הבוט לא מגיב | בדוק Vercel Logs / Webhook status |
| "settings not defined" | וודא שכל הקבצים מייבאים `from app.core.config import settings` |
| 403 Google Error | הפעל Calendar API ב-Google Cloud Console |
| "No refresh token" | בטל הרשאות ב-myaccount.google.com והתחבר מחדש |
| Flood Control | המתן כמה דקות והגדר webhook ידנית |

---

**Created by Shay Feldboy | 2026**
