# Suspicion

Telegram bot that risk-checks a forwarded job or internship offer and returns **signals, sources and next steps** — never a verdict.

Forward a recruiter message, an offer PDF, or a screenshot. You get back a short case file: what stands out, what was actually looked up, and what to do before paying or sharing documents.

> Suspicion reports risk signals, not legitimacy. "High risk" means the message matches known fraud patterns; "Low risk" is not a clean bill of health.

- Bot: [@suspicion_check_bot](https://t.me/suspicion_check_bot)
- API: FastAPI, same process as the bot
- Storage: SQLite (redacted evidence, 90-day TTL)

---

## What a check looks like

```
HIGH RISK
4 issues · identity brand record found

WHAT STANDS OUT

1. Payment to a personal UPI handle
“Pay ₹1500 registration fee via UPI to amazon.hiring@okaxis.”
→ A personal UPI handle is not a company account. Do not transfer.

2. Aadhaar requested
“Send Aadhaar to redacted@gmail.com.”
→ Hold Aadhaar/PAN until a signed offer. Verify who owns the form.

3. Sender uses gmail.com
→ Confirm this email on the official careers page; call the HR number listed there.

4. Scammers use this brand's name
“Major Job Scam in Noida Using Amazon Name: Fake Interviews and Offer Letters Exposed”
→ A real brand is being copied. Check the offer only on its official site.

CHECKS RUN
• Payment handle: amazon.hiring@okaxis
• Company ID: none in message
• Company record: Amazon · multinational technology company · founded 1994 · official site amazon.com
• Public reports: 3 result(s) for “Amazon” + fraud

DO NOW
1. Do not pay or share Aadhaar/PAN.
2. Find this role on the employer's own careers page.
3. Ask for the CIN and check it on MCA.

Risk signals, not a verdict. @suspicion_check_bot
```

Telegram messages are sent as HTML: bold section headers, one quoted line per finding, and clickable source links. If HTML is ever rejected, the bot logs it and falls back to plain text instead of failing silently.

A PNG share card with the same content is sent after the message.

---

## How it works

```
forward (text | PDF | screenshot)
        │
        ├─ PDF text extraction (pypdf) / OCR (Tesseract)
        │
   redact  ── phone, Aadhaar, PAN, account numbers, email/UPI local parts
        │      domains are kept because the checks need them
        │
   extract ── LLM, strict JSON schema (company, domains, fees, channels,
        │      doc requests, urgency, referral, salary). No scoring.
        │      Falls back to regex when the LLM is unavailable.
        │
   evaluate ── deterministic rules + live lookups
        │
   findings() ── group rule hits into ≤4 issues (one finding per issue)
   checks()   ── one row per lookup, with its source link
        │
   band ── Low | Elevated | High
        │
   Telegram HTML + share card  ·  SQLite (redacted)  ·  FastAPI JSON
```

The LLM only reads text and fills fields. Every band and every finding is produced by code you can test offline.

---

## Checks

### Message rules (no network)

| Code | What it flags | Severity |
|---|---|---|
| `fee_demand` | registration / training / security / refundable fee | heuristic; `hard` only with verified AICTE NIP provenance |
| `payment_channel` | payment via UPI, gift card, crypto | heuristic |
| `personal_vpa` | payee is a consumer UPI handle (`@okaxis`, `@ybl`, `@paytm`, …), not a company account | heuristic |
| `data_request` | Aadhaar, PAN, passport, bank statement, off-platform form | heuristic |
| `offsite_form` | documents collected on Google Forms / Typeform / Jotform, not the employer's domain | heuristic |
| `mlm_pattern` | fee + referral income + no product described | heuristic |
| `pressure` | 24/48-hour deadlines, "limited seats", no interview | heuristic |
| `terms_review` | unpaid training, bond, salary deduction | **info** — contract review, not fraud |
| `registry_identifier` | CIN/GSTIN present in the message | info |
| `identity_unverified` | no registry identifier supplied | info |

### Domain and DNS (live)

| Code | Lookup | Source |
|---|---|---|
| `domain_missing` | domain is not registered | RDAP via `rdap.org` (HTTP 404) |
| `fresh_domain` | domain registered < 90 days ago | RDAP registration event |
| `rdap_unavailable` | RDAP lookup failed (network, not evidence) | RDAP attempt |
| `no_mx` | domain cannot receive mail | Google DNS-over-HTTPS, `MX` |
| `no_dmarc` | no DMARC policy | Google DoH, `TXT _dmarc.<domain>` |
| `no_site` | domain serves no website | direct `https` → `http` fetch |
| `site_title_mismatch` | homepage title does not name the claimed company | page `<title>` |
| `lookalike_domain` | sender domain resembles the claimed brand but is not it | string similarity (weak, informational) |
| `free_mail` | sender uses gmail / yahoo / outlook / rediffmail | sender address |

`no_dmarc` and `lookalike_domain` are deliberately weak and never decide a band alone. `rdap_unavailable` is reported as a failed lookup, never as a negative result.

### Company and public reports (live)

| Code | Lookup | Source |
|---|---|---|
| `company_record` | public record: legal name, description, founding year, official site | Wikidata API |
| `domain_mismatch` | sender domain is not any official site on the record | Wikidata `P856` |
| `company_unknown` | no entity matched the claimed name | Wikidata search |
| `public_reports` | indexed coverage that names this company **and** fraud | Google News RSS |
| `brand_impersonation` | a real brand is being copied (reports describe fake interviews/offers using its name) | Google News RSS + company record |
| `no_reports` | nothing indexed for name + fraud wording — reported as "none found", not as "safe" | Google News RSS |

False-accusation guards on public reports:

- the fraud word must sit within 30 characters of the company name, so "Zoho founder reacts to AI voice scam" is not treated as an accusation of Zoho;
- denial wording (`denies`, `allegations against`, `victim`, `acquitted`, `defamation`) drops the hit;
- results always keep their link, and the wording is "coverage mentions", never "this company is a fraud".

### Registry

CIN/GSTIN format detection plus official deep-links (MCA for India, Companies House, SEC EDGAR, ABN, ACRA, Corporations Canada). No paid registry API is called. A missing record is never presented as evidence of fraud.

---

## Band logic

```
hard rule hit                     → High
2+ distinct heuristic codes       → High
1 heuristic code                  → Elevated
none                              → Low
```

- False positives are treated as ~10× worse than false negatives, so one weak signal never reaches High.
- Bands are ordinal, not probabilities. There is no calibrated score in v0, and the README will say so until there are enough labels.
- Presentation groups hits by issue; a fifth rule hit does not create a fifth accusation.

---

## Privacy

| Data | Handling |
|---|---|
| Raw forwarded message | never persisted |
| Phone, Aadhaar, PAN, account numbers | replaced before extraction (`[PHONE]`, `[ID]`) |
| Email / UPI local part | replaced (`redacted@domain.com`); domain kept because checks need it |
| Verdict (band, signals, redacted quote) | SQLite, deleted after 90 days on startup |
| Feedback ("this is legit") | stored as an id + timestamp |

The LLM only ever sees redacted text. Set a real `SUSPICION_HASH_SALT` in production if you enable sender hashing (`hash_id` is available for that, currently unused by the bot).

---

## Setup

Requires Python 3.11+ (tested on 3.14).

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# edit .env: SUSPICION_TELEGRAM_TOKEN from @BotFather
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### macOS / Linux

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Get the token from [@BotFather](https://t.me/BotFather) (`/newbot`). Without a token the HTTP API still works; the Telegram poller simply does not start.

### Optional: PDF and screenshot support

- PDF: `pypdf` is in `requirements.txt`.
- Screenshots: `Pillow` + `pytesseract` are installed via pip, but OCR also needs the **Tesseract binary** on `PATH`.
  - Debian/Ubuntu: `sudo apt install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: UB Mannheim installer, then confirm `tesseract --version` works in a new shell.

If the binary is missing, the bot replies with a readable note instead of crashing.

### Configuration

Environment variables (`.env` is loaded if `python-dotenv` is installed):

| Variable | Default | Notes |
|---|---|---|
| `SUSPICION_TELEGRAM_TOKEN` | — | BotFather token. Empty = API only |
| `SUSPICION_BOT_HANDLE` | `@suspicion_check_bot` | shown in messages and cards |
| `SUSPICION_LLM_API_KEY` | — | or `GROQ_API_KEY` |
| `SUSPICION_LLM_BASE_URL` | `https://api.groq.com/openai/v1` | any OpenAI-compatible endpoint |
| `SUSPICION_LLM_MODEL` | `qwen/qwen3.8-27b` | must support JSON schema or JSON mode |
| `SUSPICION_DB_PATH` | `suspicion.db` | relative to the working directory; put it on a volume when hosting |
| `SUSPICION_HASH_SALT` | `change-me` | change in production |
| `SUSPICION_PUBLIC_URL` | — | public base URL; enables the self-ping that stops a free instance idling |
| `RENDER_EXTERNAL_URL` | — | read automatically on Render; same effect as above |
| `PORT` | `8000` | used by the Docker entrypoint |

Groq free tier (30 RPM / 8K TPM / 1K requests per day) is enough for early testing: one forwarded message = one extraction call. Company and news lookups are cached in memory for 6 hours; RDAP and DNS run per check and need no keys.

---

## API

No authentication is built in. Bind `127.0.0.1` and put a proxy with auth in front if you expose it.

```bash
curl -s http://127.0.0.1:8000/check \
  -H 'content-type: application/json' \
  -d '{"text":"Company: Acme. Pay 2500 registration fee via UPI. Send Aadhaar.","country":"IN","online":true}'
```

`POST /check` accepts:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `text` | string | required | message or extracted document text |
| `country` | string | `IN` | picks the registry deep-link |
| `online` | bool | `true` | run RDAP / DNS / website / company / news lookups |

Response fields:

| Field | Meaning |
|---|---|
| `band` | `Low` \| `Elevated` \| `High` |
| `signals` | every rule hit with code, category, severity, evidence, source, URL, date |
| `findings` | ≤4 grouped issues used in the message and card |
| `checks` | one row per lookup: label, value, source URL |
| `identity` | `not verified` \| `brand record found` \| `registry ID given` |
| `offer` | extracted fields (company, domains, fees, channels, …) |
| `quote` | redacted excerpt of the input |
| `markdown` | the exact Telegram HTML body |
| `plain` | plain-text fallback |
| `id` | verdict id |

Other endpoints:

- `GET /verdict/{id}` — stored redacted verdict
- `GET /share/{id}` — PNG card (SVG if Pillow is missing)
- `POST /feedback/{id}` — record "this is legit"
- `GET /health` — liveness

---

## Deploy

The bot uses **long polling**, so it needs no public domain, TLS certificate, or webhook. It needs one long-running process with outbound internet — that is the whole requirement.

### Rules that matter

1. **Run exactly one instance.** Two pollers fight over `getUpdates` and messages get split or lost.
2. **Persist the SQLite file.** `SUSPICION_DB_PATH` holds the 90-day evidence *and* the Telegram offset that stops a restart from replaying old forwards.
3. **Keep the API private.** There is no auth. Bind `127.0.0.1` unless you add a proxy.
4. **Set a real `SUSPICION_HASH_SALT` and keep `.env` out of the image/repo.**
5. Outbound access to `api.telegram.org`, `rdap.org`, `dns.google`, `www.wikidata.org`, `news.google.com`, and your LLM endpoint is required.

### Free hosting: what is actually free

| Host | 24/7 | Card required | Catch |
|---|---|---|---|
| **Render free web service** | no | **no** | sleeps after 15 min without traffic; disk is ephemeral |
| **This PC** (Task Scheduler) | while the PC is on | no | offline when the machine is off |
| Koyeb free | no | no | scale-to-zero, small monthly allowance |
| Oracle Cloud Always Free | yes | yes (verification) | A1 capacity often unavailable |
| Google Cloud e2-micro | yes | yes (billing account) | one free VM per account, region-limited |
| Railway / Fly.io | yes | yes | free credits expire |

No-card options all sleep. Sleeping breaks long polling, so the fix is the same in every case: something must hit the instance so it never idles.

### Option A — Render free (no card)

Fastest path: the repo already contains `render.yaml`. In Render pick **New → Blueprint**, select the repo, and Render reads the plan, runtime, health check and env var names from it. You only fill in the two secrets it marks `sync: false`:

- `SUSPICION_TELEGRAM_TOKEN`
- `SUSPICION_LLM_API_KEY`

`SUSPICION_HASH_SALT` is generated for you. Manual setup, if you prefer the UI:

1. Push the repo to GitHub. `.dockerignore` already keeps `.env` and the database out.
2. Render → **New → Web Service** → connect the repo → **Runtime: Docker**.
3. Instance type **Free**. Region closest to your users (Singapore for India).
4. Environment variables:
   - `SUSPICION_TELEGRAM_TOKEN`
   - `SUSPICION_LLM_API_KEY`
   - `SUSPICION_HASH_SALT` (random string, e.g. `openssl rand -hex 24`)
   - `SUSPICION_DB_PATH` = `/tmp/suspicion.db` (Render free has no disk; any writable path works)
5. Health check path: `/health`.
6. **Disk:** persistent disks are paid-only on Render, so the free plan loses `suspicion.db` on every restart and redeploy. The bot handles that: with no stored offset it replays only messages from the last 15 minutes and drops anything older, so a restart does not re-answer yesterday's forwards. Evidence history and the "This is legit" feedback do not survive, though. If that matters, use Option C and a real disk.
7. Deploy.

Render sets `RENDER_EXTERNAL_URL` automatically; the app reads it and pings its own `/health` every 10 minutes so the free instance does not spin down. Nothing external is needed. If you host elsewhere, set `SUSPICION_PUBLIC_URL=https://your-host` to switch the same behaviour on.

Verify:

```bash
curl https://<your-service>.onrender.com/health
# {"ok":true,"product":"Suspicion"}
```

Then forward a message to the bot in Telegram. If nothing comes back, open **Logs** in Render — the poller logs every failure and every rejected HTML send.

### Option B — this PC, start on boot (zero cost, no account)

Windows, in an elevated PowerShell once:

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\Users\<you>\Desktop\Suspicion\.venv\Scripts\python.exe" `
           -Argument "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" `
           -WorkingDirectory "C:\Users\<you>\Desktop\Suspicion"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "Suspicion bot" -Action $action -Trigger $trigger `
  -Description "Suspicion Telegram bot" -RunLevel Limited
Start-ScheduledTask -TaskName "Suspicion bot"
```

`-AtLogOn` (not `-AtStartup`) matters: the task needs your user's `.env` and network context. The bot is offline while the PC is asleep, and Telegram queues updates for 24 hours, so messages sent overnight are answered on wake.

### Option C — small VPS + systemd (paid, but 24/7 and stable)

A 1 vCPU / 1 GB box is enough.

```bash
sudo useradd -r -m -d /opt/suspicion suspicion
sudo -u suspicion git clone <your-repo> /opt/suspicion/app-src   # or rsync the folder
cd /opt/suspicion/app-src
sudo -u suspicion python3 -m venv .venv
sudo -u suspicion .venv/bin/pip install -r requirements.txt
sudo -u suspicion cp .env.example .env      # then edit values
sudo apt install tesseract-ocr              # optional, for screenshots
```

`/etc/systemd/system/suspicion.service`:

```ini
[Unit]
Description=Suspicion Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=suspicion
WorkingDirectory=/opt/suspicion/app-src
EnvironmentFile=/opt/suspicion/app-src/.env
Environment=SUSPICION_DB_PATH=/opt/suspicion/data/suspicion.db
ExecStart=/opt/suspicion/app-src/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /opt/suspicion/data && sudo chown suspicion: /opt/suspicion/data
sudo systemctl daemon-reload
sudo systemctl enable --now suspicion
journalctl -u suspicion -f        # logs, including Telegram send failures
```

### Option D — Docker anywhere

```bash
docker build -t suspicion .
docker run -d --name suspicion --restart unless-stopped \
  --env-file .env \
  -e SUSPICION_DB_PATH=/data/suspicion.db \
  -v suspicion-data:/data \
  suspicion
docker logs -f suspicion
```

The image installs Tesseract, so screenshots work out of the box. The entrypoint honours `$PORT` (Render, Heroku, Koyeb) and defaults to 8000. Add `-p 127.0.0.1:8000:8000` only if you want the API on the host.

### Updating

```bash
# Render: push to the connected branch, it redeploys.
# VPS:
sudo -u suspicion git -C /opt/suspicion/app-src pull
sudo -u suspicion /opt/suspicion/app-src/.venv/bin/pip install -r /opt/suspicion/app-src/requirements.txt
sudo systemctl restart suspicion
```

Both paths keep the stored offset, so a redeploy does not re-answer messages that were already handled. A brand-new database is the only case that skips the backlog, by design.

---

## Tests

Stdlib `unittest`, no network, LLM disabled by the test file:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Covers redaction, the NIP hard-rule provenance, band asymmetry, info-vs-risk severity, MLM gating, finding de-duplication, HTML escaping, the fraud-accusation filter, the UPI-suffix-is-not-a-domain guard, and restart behaviour (stored offset vs. replay grace window).

---

## Honest status

v0 is **rules + live lookups**. There is no calibrated model, no precision/recall claim, and no benchmark yet.

- Plan: 150–200 consented, redacted, human-annotated real messages plus public corpora and a minority of synthetic samples.
- A logistic model is only worth training after ~300 real labels; until then, bands are ordinal and clearly labelled.
- Wikidata covers notable organisations only. Private limited companies and LLPs will show `Company ID: none in message` plus an MCA link — that is a gap, not a conclusion.
- Public-reports coverage is a news index, not a scam database. Absence of results is reported as absence, never as safety.

## Skipped, add when

- Paid MCA / third-party registry API → when users ask for verified company details.
- Logistic model + calibration → when 300 real labels exist.
- WhatsApp Business → after Telegram validates the loop.
- Per-user rate limiting and API auth → before exposing `/check` publicly.
- Postgres → when more than one process needs the same store (SQLite is single-writer by design).
- A real scam-report corpus with consent tracking → when the benchmark collection starts.

## Layout

```
app/
  main.py       FastAPI app, Telegram handler, HTML/plain rendering, keepalive
  checks.py     deterministic rules, grouping, band inputs
  lookup.py     Wikidata + Google News lookups and accusation filter
  extract.py    redaction-aware extraction, regex baseline + LLM enrichment
  redact.py     PII stripping
  scoring.py    band decision
  sharecard.py  PNG/SVG card
  store.py      SQLite verdicts + feedback, 90-day TTL, update offset
  telegram.py   transport, polling start point, send fallback
  media.py      optional PDF/OCR ingest
tests/
  test_suspicion.py
Dockerfile      Render/any-host image, honours $PORT
render.yaml     one-click Render blueprint (free plan)
```
