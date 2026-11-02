# Repository audit — 29 October 2026

What this repo does: It displays an Interactive Brokers portfolio, adds market information and coaching, and exports reports. The assumed load is one owner, one Python process, a few browser tabs, and tens of holdings.

**Recommendation: make targeted fixes while preserving the existing architecture. No source code was changed during this audit.**

## Baseline and provenance

At the time of the audit, GitHub matched `origin/main` at **`e6ea293`**. The labels below describe where the faulty logic originated, even when its file also contains newer changes:

| Label | Origin |
|---|---|
| **Remote** | Already present in the other author's code |
| **Local** | Introduced in uncommitted changes |

The three local commits add branding (`82c8ab9`), daily FX attribution (`bb14298`), and the splash screen (`f573c67`). No separate confirmed defect was found attributable solely to those commits within the checks performed.

Line references below describe the working tree at the time of the audit and can drift as fixes are made.

## Must fix

1. **Remote — Market intelligence can remain blank or stale indefinitely** — [intel.py:99](dashboard_core/intel.py#L99)

   - **What this is:** Avoids fetching information when ticker symbols have not changed.
   - **Problem:** The guard belongs to the server, while the data store belongs to each browser tab. A second tab receives `no_update`; unchanged holdings also prevent expired data from being fetched again.
   - **Fix:** Remove the global ticker guard and use the existing timed cache. Make allocation rendering respond to portfolio updates too.
   - **If we skip it:** Another tab can stay empty, and earnings or exposure information can remain outdated.

2. **Remote — An interrupted trade-history save destroys the previous file** — [trade_history.py:141](trade_history.py#L141)

   - **What this is:** Saves uploaded transactions.
   - **Problem:** Opening the destination with `'w'` truncates it before the replacement is complete. A simulated write failure left invalid JSON, which subsequently loaded as an empty history.
   - **Fix:** Write a temporary file beside the destination, then replace it atomically. Protect the read–merge–write operation against overlapping uploads.
   - **If we skip it:** A failed save or concurrent upload can lose recorded trades.

3. **Local — The project configuration breaks the existing lint gate** — [pyproject.toml:1](pyproject.toml#L1)

   - **What this is:** Configures dependencies, Python compatibility, and code checks.
   - **Problem:** The replacement removes the author's Ruff configuration. Even with the original rules restored temporarily, the current tree has **three lint errors**, while remote passes.
   - **Fix:** Preserve the existing Ruff sections and resolve the two import-order errors and unused import. Treat the Python-version change separately.
   - **If we skip it:** The PR introduces avoidable CI failures and changes checks for otherwise untouched code.

4. **Remote — Building Docker locally can package private trade history** — [Dockerfile:27](Dockerfile#L27), [.dockerignore](.dockerignore)

   - **What this is:** Copies the working directory into the image.
   - **Problem:** `.dockerignore` excludes neither `data/` nor `.venv/`. Building after uploading trades can put those records inside the resulting image.
   - **Fix:** Exclude runtime data and local environments from the build context.
   - **If we skip it:** Sharing an image built from a used checkout can share private records. This review did not establish that any published image already contains them.

5. **Remote — Docker exposes the unauthenticated dashboard beyond localhost** — [docker-compose.yml:9](docker-compose.yml#L9)

    - **What this is:** Publishes the dashboard's HTTP port.
    - **Problem:** The default mapping binds all host interfaces, while the application has no login. Other machines that can reach that port can access the dashboard.
    - **Fix:** Bind the published port to `127.0.0.1` for the documented private, local deployment.
    - **If we skip it:** Portfolio access depends on the host's network and firewall configuration.

## Should fix

6. **Local — The performance chart stops refreshing when holdings stay unchanged** — [performance.py:284](dashboard_core/performance.py#L284)

    - **What this is:** Refreshes historical performance through a holdings store.
    - **Problem:** That store excludes prices and dates, and the chart has no interval input. It can remain unchanged across trading days until the user changes a control.
    - **Fix:** Connect chart refresh to the existing interval; retain the shared price cache.
    - **If we skip it:** A refreshed dashboard can display an old performance chart.

7. **Local — “Dividends excluded” conflicts with the downloaded prices** — [portfolio_history.py:176](portfolio_history.py#L176)

    - **What this is:** Builds the historical holdings chart and PDF comparison.
    - **Problem:** The inherited download helper uses dividend-adjusted prices. A stock dropping from $100 to $90 while paying a $10 dividend can therefore appear flat, although the new description says dividends are excluded.
    - **Fix:** Choose one interpretation consistently: use prices without dividend adjustment, or label the adjusted series and its absolute values accurately.
    - **If we skip it:** Users cannot reliably interpret the reported value and return.

8. **Local — Portfolio beta silently drops short positions** — [data_processor.py:120](data_processor.py#L120)

    - **What this is:** Combines individual holding sensitivities into portfolio beta.
    - **Problem:** Negative positions are excluded from the numerator but remain in the coverage denominator. A €100 long at beta 1 and €50 short at beta 2 returns **beta 1 with 200% coverage**, rather than the signed exposure's beta 0.
    - **Fix:** Either calculate signed exposure consistently or explicitly show aggregate beta as unavailable for short-containing portfolios.
    - **If we skip it:** A hedge can appear to contribute no risk reduction.

9. **Remote — Coach inputs label non-EUR amounts as EUR and omit cash** — [ai_provider.py:314](ai_provider.py#L314)

    - **What this is:** Builds the portfolio information sent to the AI.
    - **Problem:** USD-account values become `total_value_eur` and `net_liq_eur`. Cash is read from `cash_eur`, while the producer supplies `cash_base`; the rules-based coach also hardcodes euros.
    - **Fix:** Carry explicit currency and unit fields, read `cash_base`, and format amounts using those fields.
    - **If we skip it:** The coach receives incorrect currency information and missing cash.

10. **Remote — Container updates discard uploaded history** — [docker-compose.yml:4](docker-compose.yml#L4)

    - **What this is:** Stores uploaded trades inside the container's writable filesystem.
    - **Problem:** There is no persistent data volume. Recreating the container during an update loses those uploads.
    - **Fix:** Mount a persistent, writable data directory and point `IBKRDASH_DATA_DIR` to it.
    - **If we skip it:** History survives a restart but can disappear during a normal update.

11. **Remote — Pull-request checks never run the test suite** — [checks.yml:21](.github/workflows/checks.yml#L21)

    - **What this is:** Runs lint and the smoke script in CI.
    - **Problem:** The 93 pytest tests are never executed. Numerical regressions can pass the smoke script because it mainly checks that functions return something.
    - **Fix:** Add `python -m pytest -q` after the smoke test.
    - **If we skip it:** The existing regression tests provide no automatic protection when merging.

12. **Remote — The Buffett estimate assumes index points equal dollar billions** — [market_valuation.py:117](market_valuation.py#L117)

    - **What this is:** Estimates total market capitalization divided by GDP.
    - **Problem:** It directly treats the Wilshire index level as capitalization in billions. Wilshire documents that the conversion changes with its divisor; it was already approximately $1.05 billion per point in 2020. [Wilshire methodology, §2.2.1](https://assets-global.website-files.com/60f8038183eb84c40e8c14e9/612f894c20ad970151859d9d_FT%20Wilshire%205000%20Index%20Series.pdf)
    - **Fix:** Use capitalization data with explicit dollar units, or a verified applicable divisor. Do not hardcode the historical 1.05 figure.
    - **If we skip it:** The displayed ratio and valuation classification contain an uncontrolled approximation.

13. **Remote — Fetched live trades never reach their display consumers** — [data_callbacks.py:189](dashboard_core/data_callbacks.py#L189)

    - **What this is:** Passes fetched broker data into the central portfolio store.
    - **Problem:** The producer fetches live trades, but this callback omits them. Position details subsequently read `data['trades']` and receive nothing.
    - **Fix:** Forward `raw.get('trades', [])` and update the store schema.
    - **If we skip it:** Recent broker trades remain absent from the detail chart.

14. **Remote — PDF export truncates fractional quantities** — [export.py:364](dashboard_core/export.py#L364)

    - **What this is:** Prints each holding's quantity.
    - **Problem:** `int(quantity)` turns 0.75 shares into 0 and 1.75 into 1.
    - **Fix:** Format the existing quantity without converting it to an integer.
    - **If we skip it:** The exported report misstates fractional holdings.

## Pull-request approach

Fix the **Local** findings first and submit inherited bug fixes as separate, narrowly described commits or a separate PR. Preserve the author's structure. Also remove accidental line-ending changes before submission: they inflate the tracked diff from roughly **1,502 substantive additions to 4,333 displayed additions**.

## Validation and limits

**Validation:** 93 tests passed, the smoke checks passed, and in-process layout/assets requests returned HTTP 200. Targeted synthetic checks reproduced the calculation, state, selection, and save failures described above.

**Verdict:** Keep the architecture; fix local FX correctness and CI first, then address inherited privacy and numerical bugs individually.

**Lean:** Removing the broken global ticker guard deletes seven executable lines; no dependency changes are needed.

**Not checked:** Live TWS behavior, live external-provider responses, an actual Docker build, full browser interaction, or secret history. Static UI presentation code was sampled.
