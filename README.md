# IPTrack India

Track the status of your **Trademark** and **Design** applications on IP India — automatically.

## Features

- **Auth** – Sign up / log in with hashed passwords (Flask-Bcrypt)
- **Track filings** – Add trademark or design application numbers
- **Auto polling** – APScheduler checks status every N hours (default 12h, configurable)
- **Manual check** – "Check Now" button per filing (AJAX, no page reload)
- **Status history** – Full log of every status snapshot
- **In-app notifications** – Bell icon with live badge, dropdown, and notifications page
- **Scraping strategy** – Open-source first (requests + BeautifulSoup); falls back to Playwright headless Chromium; optional anticaptcha.com integration for trademark portal
- **Memory optimised** – SQLAlchemy connection pooling, `pool_pre_ping`, `pool_recycle`, batch polling with `yield_per`, Gunicorn `--max-requests` for leak protection

## Project Structure

```
iptrack/
├── app/
│   ├── __init__.py        # App factory, extensions, scheduler
│   ├── models.py          # User, Filing, StatusHistory, Notification
│   ├── scraper.py         # IP India scraper (requests → Playwright fallback)
│   ├── tasks.py           # APScheduler job
│   ├── routes/
│   │   ├── auth.py        # /login /signup /logout /health
│   │   ├── dashboard.py   # /dashboard /filings/add /history
│   │   └── api.py         # /api/filings/:id/check  /api/notifications/*
│   └── templates/
│       ├── base.html
│       ├── login.html / signup.html
│       ├── dashboard.html / add_filing.html
│       ├── history.html / notifications.html
├── wsgi.py
├── Procfile
├── railway.toml
├── requirements.txt
└── .env.example
```

## Local Development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium       # only needed for CAPTCHA fallback
cp .env.example .env              # fill in values
flask db upgrade                  # or python -c "from app import create_app,db; app=create_app(); app.app_context().push(); db.create_all()"
flask run
```

## Deploy on Railway

1. Push this folder to a GitHub repo.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Add a **PostgreSQL** plugin — Railway auto-sets `DATABASE_URL`.
4. In **Variables**, add:
   - `SECRET_KEY` = a long random string
   - `CHECK_INTERVAL_HOURS` = `12` (or any value)
   - `ANTICAPTCHA_KEY` = *(optional, for TM captcha solving)*
5. Railway detects `Procfile` / `railway.toml` and builds automatically.

## How Status Checking Works

### Design registrations
`https://search.ipindia.gov.in/DesignApplicationStatus/` — accepts a plain POST form with the application number (format `NNNNNN-001`). No CAPTCHA on this endpoint. Pure `requests` scrape.

### Trademark applications
`https://tmrsearch.ipindia.gov.in/eregister/eregister.aspx` — has a CAPTCHA on the status page.

| Method | When used |
|--------|-----------|
| `requests` POST | Tried first; works if CAPTCHA is not triggered |
| Playwright (headless Chromium) | Fallback if requests fails or CAPTCHA detected |
| anticaptcha.com solver | Used inside Playwright if `ANTICAPTCHA_KEY` env var is set |

If neither works (CAPTCHA blocks everything), the status shows `"Manual check required (CAPTCHA)"` — the user can still visit the IP India portal directly.

## Memory Optimisations

- `pool_size=3, max_overflow=1` — keeps DB connections low on Railway free tier
- `pool_pre_ping=True` — drops stale connections before use
- `pool_recycle=300` — recycles connections every 5 min
- Scheduler polling uses `limit/offset` pagination (page_size=20) + `db.session.expire_all()` after each filing to release object memory
- Gunicorn `--max-requests 500 --max-requests-jitter 50` — workers restart after ~500 requests to prevent memory leaks
- `--preload` — loads the app once in the master process (saves RAM per worker)
