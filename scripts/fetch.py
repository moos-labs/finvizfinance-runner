#!/usr/bin/env python3
"""Fetch Finviz data for one ticker via the finvizfinance library.

Generic / multi-purpose by design: the ticker is a CLI argument, nothing is
hardcoded beyond the pilot defaults (ticker AAPL, S&P 500 Overview screener).

Two independent parts, each wrapped in its own try/except so a failure in
one never hides the result of the other:

  (a) Fundamentals snapshot for the ticker
      finvizfinance 1.x:  from finvizfinance.quote import finvizfinance
                          finvizfinance(TICKER).ticker_fundament()
      (Older docs name the same class ``Ticker``; a fallback import covers
      that spelling too.)
      Full dict -> results/<ticker>_fundamentals.json

  (b) One Overview screener fetch
      finvizfinance 1.x:  from finvizfinance.screener.overview import Overview
                          Overview().set_filter(filters_dict={'Index': 'S&P 500'})
                          Overview().screener_view()
      (Older docs name the class ``Screener``; a fallback covers that too.)
      Total rows returned is recorded; up to 20 rows ->
      results/screener_overview.csv

Every outcome (success or exact error text) is recorded in
results/summary.json, including a per-part assessment of whether the error
looks like an IP block / HTTP 403 / rate limit, judged only from the actual
error text. The script always exits 0 so the results artifact is uploaded
even when Finviz refuses the request -- a block is a finding, not a crash.
"""

import argparse
import json
import os
import sys
import traceback

RESULTS_DIR = "results"

HEADLINE_CANDIDATES = ["P/E", "Market Cap", "Dividend %", "Dividend Yield",
                       "Price", "EPS (ttm)", "Index", "Sector", "Industry",
                       "Country", "Company"]


def block_assessment(error_text):
    """Judge block/rate-limit likelihood from the actual error text only."""
    if not error_text:
        return "not_applicable_no_error"
    t = error_text.lower()
    if "429" in t or "too many requests" in t or "rate limit" in t or "rate_limit" in t:
        return "looks_like_rate_limit_429"
    if ("403" in t or "forbidden" in t or "captcha" in t or "blocked" in t
            or "access denied" in t or "cloudflare" in t):
        return "looks_like_ip_block_or_403"
    if "401" in t or "unauthorized" in t:
        return "looks_like_401_unauthorized_not_ip_block_evidence"
    return "no_evidence_of_ip_block_403_or_rate_limit_in_error_text"


def load_quote_class():
    """Return the finvizfinance single-ticker class across library versions."""
    try:
        from finvizfinance.quote import finvizfinance as quote_cls
        return quote_cls
    except Exception:
        pass
    try:
        from finvizfinance.quote import Ticker as quote_cls  # older naming
        return quote_cls
    except Exception:
        pass
    import finvizfinance as pkg  # last resort: top-level attribute
    for name in ("Ticker", "finvizfinance"):
        if hasattr(pkg, name):
            return getattr(pkg, name)
    raise ImportError("could not locate finvizfinance quote/Ticker class")


def load_overview_class():
    """Return the finvizfinance Overview screener class across versions."""
    try:
        from finvizfinance.screener.overview import Overview as cls
        return cls
    except Exception:
        pass
    from finvizfinance.screener.overview import Screener as cls  # older naming
    return cls


def fetch_fundamentals(ticker):
    """Part (a): fundamentals snapshot. Returns (info_dict, fundament_or_None)."""
    info = {"status": None, "file": None, "field_count": 0,
            "fields_present": [], "headline_fields": {}, "error": None,
            "block_assessment": "not_applicable_no_error"}
    try:
        quote_cls = load_quote_class()
        stock = quote_cls(ticker)
        fundament = stock.ticker_fundament()
        # Normalise to a plain {str: str} dict whatever the library returns.
        if hasattr(fundament, "to_dict"):
            try:
                raw = fundament.to_dict()
                # DataFrame.to_dict() can nest; flatten one level if needed.
                if raw and all(isinstance(v, dict) for v in raw.values()):
                    first = next(iter(raw.values()))
                    raw = {str(k): v for k, v in first.items()}
                fundament = raw
            except Exception:
                fundament = dict(fundament)
        fundament = {str(k): ("" if v is None else str(v))
                     for k, v in dict(fundament).items()}
        fname = os.path.join(RESULTS_DIR, f"{ticker.lower()}_fundamentals.json")
        with open(fname, "w", encoding="utf-8") as fh:
            json.dump(fundament, fh, indent=2, ensure_ascii=False)
        info.update({
            "status": "ok",
            "file": fname,
            "field_count": len(fundament),
            "fields_present": sorted(fundament.keys()),
            "headline_fields": {k: fundament[k] for k in HEADLINE_CANDIDATES
                                if k in fundament},
        })
        return info, fundament
    except Exception as exc:  # noqa: BLE001 - record exact error, keep going
        err = f"{type(exc).__name__}: {exc}"
        info.update({"status": "error", "error": err,
                     "traceback": traceback.format_exc(),
                     "block_assessment": block_assessment(err)})
        return info, None


def _run_overview(overview_cls, filters):
    """One Overview screener fetch; returns the DataFrame or None.

    finvizfinance's Base.screener_view() returns None (logging
    'No ticker found.') when the response page has no #pageSelect
    element -- it does not raise. Callers must handle None explicitly.
    """
    screener = overview_cls()
    if filters:
        try:
            screener.set_filter(filters_dict=filters)
        except TypeError:
            screener.set_filter(filters)
    try:
        return screener.screener_view(order="Ticker", verbose=0)
    except TypeError:
        return screener.screener_view()


def _diagnose_screener_page():
    """Raw-page diagnostics for the filtered screener URL.

    Uses finvizfinance's own public fetch() so the request goes through
    the same transport (session, headers, wall detection) as the library.
    Records only markers, never the full HTML.
    """
    diag = {"attempted": True}
    try:
        import re
        from finvizfinance.util import fetch
        resp = fetch("https://finviz.com/screener.ashx",
                     params={"v": 111, "f": "idx_sp500", "o": "ticker"})
        text = resp.text or ""
        title = re.search(r"<title>(.*?)</title>", text, re.S)
        diag.update({
            "status_code": resp.status_code,
            "final_url": str(resp.url),
            "html_bytes": len(text),
            "title": (title.group(1).strip()[:200] if title else None),
            "has_pageSelect_element": 'id="pageSelect"' in text,
            "has_screener_table": "screener_table" in text,
            "has_just_a_moment": "Just a moment" in text,
            "has_cf_challenge": ("cf-chl" in text
                                 or "challenge-platform" in text),
        })
    except Exception as exc:  # noqa: BLE001
        diag["error"] = f"{type(exc).__name__}: {exc}"
    return diag


def fetch_screener():
    """Part (b): one Overview screener fetch (S&P 500, default fallback)."""
    info = {"status": None, "filter": {"Index": "S&P 500"},
            "rows_returned": 0, "rows_saved": 0, "file": None,
            "columns": [], "error": None, "attempts": [],
            "block_assessment": "not_applicable_no_error"}
    try:
        overview_cls = load_overview_class()
        df = None
        used_filter = {"Index": "S&P 500"}
        try:
            df = _run_overview(overview_cls, used_filter)
            info["attempts"].append(
                {"filter": used_filter,
                 "result": "dataframe" if df is not None else "returned_None"})
        except Exception as exc:  # noqa: BLE001
            info["attempts"].append(
                {"filter": used_filter,
                 "result": f"raised {type(exc).__name__}: {exc}"})
            raise
        if df is None:
            # Task-sanctioned alternative: a default Overview screener fetch.
            df = _run_overview(overview_cls, None)
            info["attempts"].append(
                {"filter": None,
                 "result": "dataframe" if df is not None else "returned_None"})
            if df is not None:
                used_filter = None
        if df is None:
            diag = _diagnose_screener_page()
            info["diagnostics"] = diag
            err = ("screener_view returned None for both the filtered "
                   "(Index='S&P 500') and the default Overview fetch. "
                   "finvizfinance returns None when the response page has "
                   "no #pageSelect element (library logs 'No ticker found.'). "
                   f"Raw-page diagnostics: {json.dumps(diag)[:600]}")
            info["error"] = err
            evidence = err + " " + json.dumps(diag)
            if (diag.get("has_just_a_moment") or diag.get("has_cf_challenge")
                    or diag.get("status_code") in (403, 429, 503)):
                info["block_assessment"] = block_assessment(evidence)
                if info["block_assessment"] == \
                        "no_evidence_of_ip_block_403_or_rate_limit_in_error_text":
                    info["block_assessment"] = "looks_like_ip_block_or_403"
            elif diag.get("has_screener_table"):
                info["block_assessment"] = (
                    "no_evidence_of_ip_block_403_or_rate_limit_in_error_text"
                    " (screener table WAS present in raw HTML; the library's"
                    " #pageSelect pagination check failed instead)")
            else:
                info["block_assessment"] = block_assessment(evidence)
            info["status"] = "error"
            return info
        total = int(df.shape[0])
        fname = os.path.join(RESULTS_DIR, "screener_overview.csv")
        df.head(20).to_csv(fname, index=False)
        info.update({"status": "ok", "filter": used_filter,
                     "rows_returned": total,
                     "rows_saved": int(min(20, total)), "file": fname,
                     "columns": [str(c) for c in df.columns]})
        return info
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        info.update({"status": "error", "error": err,
                     "traceback": traceback.format_exc(),
                     "block_assessment": block_assessment(err)})
        return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", nargs="?", default="AAPL",
                        help="US ticker symbol (default: AAPL)")
    parser.add_argument("--ticker", dest="ticker_opt", default=None,
                        help="same as the positional ticker")
    args = parser.parse_args()
    ticker = (args.ticker_opt or args.ticker).upper()

    os.makedirs(RESULTS_DIR, exist_ok=True)

    try:
        import finvizfinance  # noqa: F401
        import importlib.metadata as md
        lib_version = md.version("finvizfinance")
    except Exception:
        lib_version = "unknown"

    print(f"finvizfinance version: {lib_version}")
    print(f"ticker: {ticker}")

    fundamentals_info, _ = fetch_fundamentals(ticker)
    print(f"fundamentals: {fundamentals_info['status']} "
          f"fields={fundamentals_info['field_count']} "
          f"error={fundamentals_info['error']}")

    screener_info = fetch_screener()
    print(f"screener: {screener_info['status']} "
          f"rows={screener_info['rows_returned']} "
          f"error={screener_info['error']}")

    summary = {
        "tool": "finvizfinance",
        "finvizfinance_version": lib_version,
        "ticker": ticker,
        "coverage_note": "Finviz covers US-listed securities only.",
        "fundamentals": fundamentals_info,
        "screener_overview": screener_info,
    }
    out = os.path.join(RESULTS_DIR, "summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
