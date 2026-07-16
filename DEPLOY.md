# Deploying ClipFind

The app is tested and working locally (Flask serves the page, `/api/demo`
and `/api/analyze` both return correct data). It just needs to live
somewhere with a public URL and normal outbound internet access, since
it has to reach YouTube's servers to fetch transcripts.

## Fastest path: Render.com (free tier)

1. Put these files in a GitHub repo: `app.py`, `clipfind.py`,
   `requirements.txt`, `sample_transcript.txt`.
2. Go to [render.com](https://render.com) → New → Web Service → connect
   the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Deploy. Render gives you a public URL like `clipfind.onrender.com`
   you can send straight to the people who said they'd buy it.

## Alternative: Railway.app

Same idea — connect the repo, Railway auto-detects the Flask app and
`requirements.txt`, and gives you a public URL. Slightly more generous
free tier than Render as of mid-2026, worth comparing pricing before
you commit.

## Why not host it from here

This session runs in a sandboxed environment with restricted outbound
network access — I confirmed both `yt-dlp` and `youtube-transcript-api`
get blocked at the network layer when trying to reach youtube.com from
here. That's a property of this sandbox, not the code: `youtube-transcript-api`
is a standard, widely-used library that works normally on Render,
Railway, or any regular server/laptop. Test the `/api/analyze` route
with a real video URL right after you deploy, before sending the link
to anyone — YouTube does occasionally rate-limit or change caption
delivery, and you want to catch that before a buyer does.

## Fixing "RequestBlocked" / "IpBlocked" errors

If `/api/analyze` fails with an error mentioning YouTube blocking requests
from your IP, this is expected on cloud hosts (Render, AWS, GCP, Azure,
etc. — YouTube blocks most of their IP ranges outright to stop scraping).
It's not a bug and retrying won't fix it. The fix:

1. Create a [Webshare](https://www.webshare.io) account and buy a
   **"Residential"** proxy package (not "Proxy Server" or "Static
   Residential" — those still get blocked). Pricing scales with usage;
   check current plans before committing.
2. In Webshare's dashboard → Proxy Settings, copy your **Proxy Username**
   and **Proxy Password**.
3. In Render → your service → **Environment** tab → add two variables:
   `WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD`, paste the
   values in.
4. Save — Render will automatically redeploy with the new variables.
   `clipfind.py` already checks for these and routes transcript requests
   through the proxy automatically when they're present.
5. Test `/api/analyze` again with a real video URL.

## Switching to Docker (needed for real video cutting)

`/api/cut` downloads video and trims it with `ffmpeg`, which is a system
binary — Render's plain "Python 3" native runtime doesn't include it.
There's now a `Dockerfile` in this repo that installs ffmpeg + everything
else. To switch your existing Render service over:

1. Push the new files to your GitHub repo: `Dockerfile`, the updated
   `app.py` and `clipfind.py`, and the updated `requirements.txt`
   (now includes `yt-dlp`).
2. In Render → your service → **Settings** → find **Runtime** (or
   Environment/Language) → change it from "Python 3" to **Docker**.
   Render will auto-detect the `Dockerfile` at the repo root.
3. The Build/Start Command fields become irrelevant once on Docker — the
   Dockerfile's own `CMD` handles starting gunicorn. You can leave them
   blank.
4. Your `WEBSHARE_PROXY_USERNAME`/`PASSWORD` environment variables carry
   over automatically — no need to re-add them. The same proxy is reused
   for video downloads (via yt-dlp's `--proxy`), not just transcripts.
5. Redeploy, then test `/api/cut` with a real clip from the running app —
   this is the one piece that couldn't be verified from this sandbox
   (network here can't reach YouTube or Webshare at all, confirmed while
   building this), so treat the first live test as the real check.

If you'd rather not deal with Docker, Railway and Fly.io both also
support Dockerfile-based deploys with a similar setup flow.

## Setting up the paywall (accounts, database, Stripe)

The app now has real user accounts, a 3-clips/day free limit, and a paid
tier via Stripe. Three things need setting up before this works live:

### 1. A real database

Locally this falls back to a SQLite file with zero setup. In production
you need Postgres — Render's free Postgres add-on works:

1. In Render → **New** → **PostgreSQL**. Free tier is fine to start.
2. Once created, copy its **Internal Database URL**.
3. On your `clipfind-v2` web service → **Environment** → add
   `DATABASE_URL` set to that value. `app.py` already detects and uses it
   automatically (falls back to SQLite only if this isn't set).
4. Also add `SECRET_KEY` — any random long string (this signs login
   session cookies; without a real value set, sessions won't persist
   properly across restarts). Generate one with, e.g., `python3 -c
   "import secrets; print(secrets.token_hex(32))"` and paste the output in.

### 2. A Stripe account

1. Sign up at [stripe.com](https://stripe.com) — no bank details needed
   to start in **test mode**, which is enough to verify the whole flow
   before going live.
2. In the Stripe dashboard, create a **Product** (e.g. "ClipFind Clipper
   Plan") with a **recurring price** — $29/mo to match the pricing you
   already showed people, or whatever you land on. Copy the **Price ID**
   (starts with `price_`).
3. Get your **Secret key** from Developers → API keys (starts with `sk_`
   — use the test-mode one first).
4. Add to Render's Environment tab: `STRIPE_SECRET_KEY` and
   `STRIPE_PRICE_ID` with those two values.

### 3. The Stripe webhook

This is how Stripe tells your app "this person just paid" so their
account gets marked unlimited.

1. In Stripe dashboard → Developers → **Webhooks** → **Add endpoint**.
2. Endpoint URL: `https://clipfind-v2.onrender.com/webhook/stripe`
   (your actual live URL + `/webhook/stripe`).
3. Select events: `checkout.session.completed`,
   `customer.subscription.updated`, `customer.subscription.deleted`.
4. After creating it, Stripe shows a **Signing secret** (starts with
   `whsec_`). Add that to Render as `STRIPE_WEBHOOK_SECRET`.

### Testing it

Stripe test mode gives you fake card numbers (`4242 4242 4242 4242`, any
future expiry, any CVC) that trigger real webhook events without moving
real money. Sign up on the live app, click Upgrade, pay with the test
card, and confirm the account bar flips to "Unlimited clips." Only switch
`STRIPE_SECRET_KEY`/`STRIPE_PRICE_ID`/webhook to their live-mode versions
(and add real payout/bank details in Stripe) once that full loop works.

## Setting up the LLM-powered scorer

`/api/analyze` now tries a real Claude call first (via `llm_scorer.py`) —
the model reads the transcript and writes actual reasoning for each clip,
instead of the old regex/keyword scoring. It automatically falls back to
the free heuristic scorer if anything goes wrong (no key set, API error,
bad response), so this is safe to leave unconfigured — you just won't
get the AI reasoning until it's set up.

1. Go to [console.anthropic.com](https://console.anthropic.com), sign up,
   and add billing (this is metered/pay-as-you-go, not a subscription —
   see the cost section below).
2. Create an API key under Settings → API Keys.
3. In Render → `clipfind-v2` → Environment, add:
   `ANTHROPIC_API_KEY` set to that key.
4. Optional: add `ANTHROPIC_MODEL` to pick a specific model — defaults to
   the cheap/fast Haiku model if not set, which is the right choice for
   free-tier volume. Only override this if you want to experiment with a
   higher-quality (and pricier) model for testing.
5. Redeploy, then test `/api/analyze` on a real video — the response's
   `scoring_method` field will say `"llm"` if it worked, `"heuristic"` if
   it silently fell back (check Render's logs for the warning message
   explaining why, if so).

This runs on **every** analyze call, free tier included — factor that
into the per-user cost math from earlier before deciding whether to keep
it available to free users or gate it to paid only.

## Setting up the Discover tab

The Discover tab (`discover.py`) uses YouTube's **official** Data API v3
— a real, sanctioned, key-based API, not the unofficial scraping
transcript/video fetching relies on. It won't hit IP-blocking issues the
way those did.

1. Go to [console.cloud.google.com](https://console.cloud.google.com),
   create a project (or use an existing one).
2. In the API Library, search for and enable **"YouTube Data API v3"**.
3. Go to **Credentials** → **Create Credentials** → **API key**. Copy it.
4. (Recommended) Click into the new key and restrict it to just the
   YouTube Data API v3 — reduces damage if it ever leaks.
5. In Render → `clipfind-v2` → Environment, add `YOUTUBE_API_KEY` set to
   that key.
6. Redeploy, then open the **Discover** tab in the app. First load after
   a deploy will be slow (15-30s) since it has to build the feed fresh;
   after that it's cached for 3 hours and loads instantly for everyone.

Free quota is 10,000 units/day; each refresh costs roughly 400-500 units
(4 niche searches at 100 units each, plus cheap video/channel lookups),
so this comfortably supports several refreshes a day without paying for
quota. Note this also runs the same clip scorer as `/api/analyze` on the
top candidates each refresh — factor a handful of extra LLM calls per
refresh into the Anthropic cost math from earlier.

## Setting up the email digest

Same Discover feed, delivered straight to people's inboxes instead of
requiring them to remember to check the app — the goal from the original
"make them as lazy as possible" idea. Everyone's opted in by default;
each email has an unsubscribe link that flips them out of it.

### 1. A Resend account (sends the emails)

1. Sign up at [resend.com](https://resend.com) — free tier is 3,000
   emails/month, 100/day, which is plenty at this stage.
2. Get your **API key** from the dashboard (Settings → API Keys).
3. In Render → `clipfind-v2` → Environment, add `RESEND_API_KEY` set to
   that key.

**Important limitation until clipfind.com is bought and verified**:
without a verified sending domain, Resend only lets you deliver to the
email address you signed up with — so digest emails will only actually
reach you, not other users, until you (1) buy the domain, (2) add and
verify it in Resend's dashboard (a DNS record or two), and (3) update the
`DIGEST_FROM_EMAIL` env var to something like
`ClipFind <digest@clipfind.com>`. Fine to leave as-is for now while
you're the only tester.

### 2. The cron secret (so only your scheduler can trigger sends)

`/api/cron/send-digest` sends real email to every opted-in user when
hit — it's protected by a shared secret so a stranger can't spam your
whole user base by finding the URL. Generate one the same way as before:
`python3 -c "import secrets; print(secrets.token_hex(24))"`, then add it
to Render as `CRON_SECRET`.

### 3. An external scheduler to actually trigger it

Render's free/starter web service doesn't include a built-in cron —
Render does offer a separate "Cron Job" resource type, but that's another
paid line item. Simpler zero-cost path: a free external pinger.

1. Sign up at [cron-job.org](https://cron-job.org) (free).
2. Create a new cron job with the URL:
   `https://clipfind-v2.onrender.com/api/cron/send-digest?secret=YOUR_CRON_SECRET`
   (your actual live URL + that query string).
3. Set the schedule — e.g. once a day, morning in your users' timezone.
4. Save. Test it once manually via cron-job.org's "Run now" button and
   check your own inbox (see the domain-verification limitation above —
   for now it'll only land in the email you signed up to Resend with).

The response is JSON (`{"sent": N, "failed": N}`) so cron-job.org's
execution history doubles as a lightweight send log.

## Cost and scope to know about before charging anyone

- **Bandwidth**: cutting actual video uses far more Webshare proxy
  bandwidth than transcript text did. The 1 GB/month Rotating Residential
  plan will not go far once people are downloading and trimming full
  video sections — watch usage in Webshare's dashboard and upgrade the
  plan before it silently starts failing mid-launch.
- **Clip length cap**: currently capped at 3 minutes server-side
  (`cut_youtube_clip` in `app.py`) to keep download/encode time and
  bandwidth bounded. Adjust if that's too restrictive, but longer clips
  cost more per request.
- **Storage**: cut clips are written to `clips_output/` and never
  deleted. Fine for early testing, but add a cleanup job (delete files
  older than a day, e.g.) before real traffic, or disk will fill up.
- **Legal/ToS**: downloading and serving YouTube video content (not just
  reading captions) sits in a real copyright/ToS gray area — YouTube's
  terms don't permit downloading video without permission, and this is
  a materially bigger exposure than the transcript-only version. Common
  practice among clipping tools, but worth being eyes-open about before
  scaling this to paying customers, especially at volume.
- Add a rate limit (Flask-Limiter) so one user can't hammer YouTube and
  get your server's IP blocked, or run up a huge proxy bill.
- The scoring engine is still heuristic (regex-based), not LLM-based —
  fine for a paid beta, but flag that in your pitch so early buyers know
  it'll improve, not that it's the finished product.
