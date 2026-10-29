# Setup module `7edcba53`

Consumes the platform-provided results of **SMA Crossover Range Hunter** (`6ceeece5`); it never calls, copies, or modifies the screener. Returns exactly one informational `alert` for every entry in `signals.new`, in supplied order. It ignores `matches`, `still`, and `dropped` for decisions and does not re-rank, cap, or filter historical matches from the screener's ten-session scan.

The fixed setup is notifications-only (`trade_legs: false`): no entries, exits, or modifications. The baseline's paper/live and trade-leg support is retained for a future upgrade; these return an alert plus an entry, but never re-enter an existing open/pending/partially closed position.

## Alert text

Default message:

`TCS · Date: 2026-10-28 · High: 105 · Low: 100 · Range: 5 · Candle time: 2026-10-28T09:15:00 · New SMA crossover match.`

Accepts display labels (`Symbol`, `Date`, `High`, `Low`, `range`, `Candle time`) and raw screener keys (`stock`, `scan_date`, `high`, `low`, `range`, `date`). The action symbol comes from `signal.stock`. The candle time is the matching row's time, not the current run's time; missing values are shown as `unknown`, and zero values are preserved. Text is plain text, under 220 characters.

## Platform responsibilities

The fixed configuration remains authoritative: the platform runs every five minutes on trading days, within 09:30–15:15 IST (15 minutes after the market opens), inherits the screener's NIFTY 50 universe, applies all-match scope, once-per-stock-per-day deduplication and guardrails, and delivers through `inapp` and `email`. Those controls are not duplicated in this stateless module. In particular, it does not add another 15-minute delay to 09:30 or change the screener's scan parameters or presentation limit.

There are no additional tunable parameters or custom panels: `/manifest` returns `{"parameters": [], "panels": []}` and unknown panel IDs return 404. Evaluation is pure, requires no network, and returns no screener overrides.

## Run and test

From this folder:

```sh
pip install -r requirements.txt
pytest -q
uvicorn main:app --host 0.0.0.0 --port 8000
```

Endpoints: `GET /health`, `GET /manifest`, `POST /evaluate`, `POST /panels/{panel_id}`.

For standalone deployment, run `docker build -t setup-7edcba53 .` then `docker run --rm -p 8000:8000 setup-7edcba53`.
