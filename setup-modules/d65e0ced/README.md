# Setup module `d65e0ced`

This stateless module acts on `signals.new` from screener `6ceeece5` and does not call or modify the screener. For each new signal it returns an informational alert with the configured text `{stock}, {date}, {high}, {low}, {range}, {time}`. It accepts both the screener API keys (`stock`, `scan_date`, `high`, `low`, `range`, `date`) and the displayed column names (`Symbol`, `Date`, `High`, `Low`, `range`, `Candle time`).

The fixed setup defines trade legs, so each new match also returns an `enter` action using the configured long limit entry, ATR stop, and two range-fib targets. In alert mode the platform records the hypothetical entry without placing a live trade. The platform applies the 5-minute schedule, market window, one-alert-per-stock-per-day policy, capital and position sizing, max positions, trade limit, cooldown, and other guardrails; this module does not duplicate those controls.

There are no additional tunable parameters or custom panels. `GET /manifest` returns empty parameter and panel lists.
