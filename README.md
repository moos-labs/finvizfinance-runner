# finvizfinance-runner

Runs **finvizfinance** (a Python scraper for [Finviz](https://finviz.com))
via GitHub Actions — part of a one-public-repo-per-tool pattern: each tool
gets its own repo, the commands to execute live in a repository variable,
runs are triggered manually, and outputs are collected as artifacts.

**Coverage note:** Finviz covers **US-listed securities only**. There is no
EGX (or other non-US exchange) data on Finviz, so this runner is for US
tickers.

## How to run an analysis

1. **Edit the commands**: repo **Settings → Secrets and variables → Actions →
   Variables** → set `COMMANDS` to the shell commands you want executed, e.g.
   ```
   mkdir -p results
   python scripts/fetch.py 2>&1 | tee results/run.log
   ```
   Pass a different US ticker as the script argument, e.g.
   `python scripts/fetch.py MSFT`.
2. **Trigger manually**: **Actions** tab → *Run analysis* → *Run workflow*
   (or dispatch it through the GitHub API).
3. **Collect results**: download the `results` artifact from the finished run.

## What the script does

`scripts/fetch.py` (ticker as CLI arg, default `AAPL`) runs two independent
parts, each recording its exact outcome in `results/summary.json`:

- **(a) Fundamentals snapshot** —
  `finvizfinance(ticker).ticker_fundament()` (class imported from
  `finvizfinance.quote`; older docs call the same class `Ticker`).
  The full returned dict is saved to `results/<ticker>_fundamentals.json`,
  and `summary.json` lists the fields actually present plus headline fields
  (e.g. P/E, Market Cap, Dividend %) when Finviz returns them.
- **(b) Overview screener** —
  `Overview()` from `finvizfinance.screener.overview` (older docs call the
  class `Screener`), filtered to `Index = S&P 500`. `summary.json` records
  how many rows the fetch returned; up to 20 rows are saved to
  `results/screener_overview.csv`.

If Finviz refuses a request (for example an HTTP 403 / datacenter-IP block
or a rate limit), the affected part records the **exact error text** in
`summary.json` together with a `block_assessment` field judging — from that
error text only — whether it looks like an IP block / 403 / rate limit.
The script still exits 0 so the artifact (and the finding) is uploaded.

## Design notes

- **Generic.** Ticker and screener filter are parameters, not baked-in
  analysis; the workflow itself contains no ticker at all — commands live
  in the `COMMANDS` repository variable.
- **Scraper, not an API.** finvizfinance parses Finviz web pages, so field
  names and availability can change when Finviz changes its HTML, and
  datacenter IPs may be blocked. Treat a block as data, not a bug to evade.
