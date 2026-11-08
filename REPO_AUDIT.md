What this repo does: It displays an Interactive Brokers portfolio, calculates portfolio statistics, adds market information and coaching, and exports PDFs. The assumed load is one owner, one Python process, a few browser tabs, and tens of holdings.

# Repository audit — 8 November 2026

**Audited checkout:** branch `fix-FX-and-beta`, commit `00b7365`. This is a review of the local source, not a claim about what is currently deployed or published upstream.

**Result:** 15 original findings are resolved within their stated scope; 4 are partially resolved. There are **15 confirmed open findings** below, including remaining gaps in the partial fixes. The previous blanket statement that all 19 findings were fixed was too broad.

Only this report was changed during this audit. No application fixes were applied. The older report remains available in Git history. See [the developer map](docs/ARCHITECTURE.md) for the current backend/UI layout.

## Disposition of the previous 19 findings

“Resolved” means the original source defect is corrected and the relevant local checks pass; it does not mean live TWS, Windows, or Docker behavior has been certified. **Old IDs** in this table refer to the October audit. The numbered findings in the next section are the current action list. “Partial” means the original mechanism improved, but the intended behavior still has a confirmed gap in the same feature; the remaining case is stated explicitly.

| Old ID | Original issue | Current status and evidence |
|---|---|---|
| 1 | Aggregate since-purchase FX disagrees with trade lots | **Partial.** The aggregate estimator is deleted and a missed task deadline stays unavailable. Quantity matching still accepts incomplete lot history, and an undated old close can become “current” FX: current findings **4–5**. |
| 2 | Mixed-currency weights and missing-weight displays | **Resolved for weights.** `positions.py` requires finite base values; the live adapter constructs them from verified quote/FX inputs. Allocation, exposure, and Coach weight-dependent answers handle unavailable weights. Mixed-currency *return* calculations are a separate remaining issue: finding **6**. |
| 3 | Sorted table selects the wrong stock | **Resolved in the callback.** Selection reads `derived_viewport_data`; the sorted-row regression passes. Full browser pagination was not exercised. |
| 4 | ETF sector exposure divided by 100 twice | **Resolved.** UI, PDF, and Coach use `sector_geo_breakdown`; the 50% technology regression passes. Missing ETF coverage is assigned to Unknown. |
| 5 | Genuine zero daily P&L gets replaced | **Partial.** Broker output and UI/PDF selection now preserve zero, with regression checks. The Coach bypasses that selection and can contradict a broker zero: finding **9**. The nonzero fallback also has a currency error: finding **8**. |
| 6 | Global ticker guard leaves intelligence blank/stale | **Resolved for that guard.** It is removed, each tab gets a result through the timed cache, and exposure rendering listens to portfolio updates. A separate failed-symbol cache remains: finding **14**. |
| 7 | Interrupted or overlapping trade saves lose history | **Partial.** Temporary sibling files, flush/fsync, atomic replacement, and a write lock fix interrupted writes and concurrent merges. Read failures and fill deduplication can still discard trades: findings **1–2**. |
| 8 | Project configuration breaks the lint gate | **Resolved.** Ruff policy is restored and the current tree passes Ruff. Runtime and CI use Python 3.12. |
| 9 | Docker context includes private runtime data | **Resolved for the documented default locations.** `.dockerignore` excludes `data/`, local environments, local metadata, and generated graphs. No image was built or inspected; this is not a guarantee about custom data directories or old published images. |
| 10 | Compose publishes the dashboard on every interface | **Resolved in configuration.** Port publication binds `127.0.0.1`; README Docker commands also specify loopback. Actual container networking was not exercised. |
| 11 | Performance chart never refreshes for unchanged holdings | **Resolved.** Its render callback has the existing refresh interval as an input. An open detail/Coach panel has a different refresh gap: finding **12**. |
| 12 | Dividend-adjusted prices described as dividends excluded | **Resolved.** UI/PDF describe dividend-adjusted backcasts and their exclusions. This remains a reconstruction of today's holdings, not account history. |
| 13 | Aggregate beta ignores short positions | **Resolved conservatively.** A short-containing portfolio returns unavailable aggregate beta; the numerical regression passes. |
| 14 | Coach mislabels account currency and omits cash | **Partial.** AI context uses explicit currency/unit fields and `cash_base`; rules-based amounts no longer hardcode EUR. Rules still judge unverified aggregate returns and use the wrong daily-P&L source: findings **6, 9**. |
| 15 | Container recreation discards uploaded history | **Resolved in configuration.** Compose mounts a named data volume; cache and trades share `config.DATA_DIR`. Dockerfile prepares the writable directory. Runtime permissions and an actual upgrade remain unverified; the developer guide includes backup instructions. |
| 16 | CI never runs pytest | **Resolved.** `.github/workflows/checks.yml` runs pytest after smoke checks. The suite currently passes 123 tests. |
| 17 | Buffett calculation treats index points as USD billions | **Resolved by replacing the calculation.** It uses matched annual World Bank market-cap/GDP observations in USD, with a distinct cache key and an annual observation label. The matched-year fixture passes; live responses were not checked. |
| 18 | Broker trades omitted from the central store | **Resolved.** `portfolio_service.py` forwards trades and the payload schema includes them; the forwarding regression passes. |
| 19 | PDF truncates fractional quantities | **Resolved for PDF.** It no longer casts quantity to an integer or imposes a fixed decimal cutoff. Position-detail formatting still rounds quantities: finding **10**. |

## Must fix

1. **A failed history read can cause the next upload to erase existing trades** — [trades.py:L134](backend/storage/trades.py#L134), [trades.py:L146](backend/storage/trades.py#L146)
   - **What this is:** Loads the saved JSON before merging a new upload.
   - **Problem:** The loader turns read/parse failures into an empty list, which the saver treats as a successfully empty history. With a valid saved `OLD` trade and a simulated read error, saving `NEW` replaced the file with only `NEW`.
   - **Fix:** Return an empty list only when the file is absent. Abort a save when existing history cannot be read or validated; the display may catch that error separately.
   - **If we skip it:** Atomic replacement can safely write the wrong, incomplete history over the previous file.

2. **Two genuine identical-looking fills collapse into one trade** — [trades.py:L124](backend/storage/trades.py#L124), [trades.py:L152](backend/storage/trades.py#L152)
   - **What this is:** Removes duplicate trades during CSV persistence.
   - **Problem:** The key is ticker, side, rounded quantity/price, and timestamp; it has no execution identity. Two separate one-share fills with those same fields produced one stored share in the synthetic save check.
   - **Fix:** Preserve execution IDs when provided. Otherwise retain the number of matching fills within an upload while making a repeat upload idempotent, rather than using a set that loses multiplicity.
   - **If we skip it:** Valid fills disappear and the saved history can no longer reconstruct the holding.

3. **Ticker text is used as an identity even when contracts differ** — [positions.py:L108](backend/portfolio/positions.py#L108), [fx_history.py:L55](backend/providers/fx_history.py#L55), [history.py:L85](backend/portfolio/history.py#L85)
   - **What this is:** Matches quote enrichment, FX results, historical series, and selected positions.
   - **Problem:** Two contracts can share a symbol; a EUR and USD `ABC` both received the USD trade-FX result of 60, even though the EUR position must have zero currency conversion. Historical valuation also overwrote one `ABC` position: quantities 1 and 2 at prices 10/11 produced 20/22 instead of 30/33.
   - **Fix:** Carry contract identity and currency through these mappings; keep ticker text for display. Match uploaded rows to a specific contract or reject ambiguous matches, and select detail rows by the same stable identity.
   - **If we skip it:** A supported multi-currency holding can borrow another contract's values, disappear from a chart, or open the wrong details.

4. **Matching the current quantity does not establish complete purchase history** — [fx.py:L106](backend/portfolio/fx.py#L106), [fx.py:L159](backend/portfolio/fx.py#L159)
   - **What this is:** Reconstructs open FIFO lots, meaning the earliest purchases are treated as sold first.
   - **Problem:** Buy one share in January, buy one in February, then sell one: the remaining lot is February's. Supplying only January's purchase still matches the one-share position and returns **60 with no warning**, versus **30** using the full history in the synthetic example.
   - **Fix:** Require an explicit coverage/reconciliation basis for the open lots; quantity alone is insufficient. Until that exists, mark unreconciled results as unverified or unavailable rather than claiming complete history.
   - **If we skip it:** An incomplete CSV can display a plausible but wrong since-purchase FX amount.

5. **An old historical close can silently become the current FX rate** — [fx_history.py:L45](backend/providers/fx_history.py#L45), [fx_history.py:L80](backend/providers/fx_history.py#L80)
   - **What this is:** Chooses an FX rate when the live rate is missing.
   - **Problem:** `_rate_lookup` returns the latest number without its observation date, and `compute_trade_fx` uses it as `rate_now`. A mocked series ending in February 2020 was accepted against a current position and returned 60 rather than unavailable.
   - **Fix:** Retain and check the observation date. Require a current verified rate, or expose a clearly dated end-of-day estimate with a defined age limit; reject older data.
   - **If we skip it:** Old data is presented as the currency effect on the current holding without disclosing its age.

6. **Overall return still adds amounts with unverified currency units** — [positions.py:L165](backend/portfolio/positions.py#L165), [scenarios.py:L51](backend/coaching/scenarios.py#L51)
   - **What this is:** Calculates total P&L percentage and feeds summary cards, PDF performance, and Coach judgments.
   - **Problem:** A EUR holding bought for 100 and now worth 50, plus a USD holding bought for 100 and now worth 200, reports **+25%** from the raw sums. At a constant 0.10 EUR/USD conversion the common-currency result is **−36.36%**, yet the Coach says “Good shape — up overall.”
   - **Fix:** Suppress aggregate return percentages and related judgments while cost/P&L units are unverified. Calculate them only from costs and P&L expressed consistently in one currency.
   - **If we skip it:** Correct allocation weights coexist with an incorrect overall profit/loss judgment, potentially even the wrong sign.

7. **Dividend totals assume all payments are USD** — [summary.py:L371](ui/components/summary.py#L371), [pdf.py:L161](ui/reports/pdf.py#L161)
   - **What this is:** Shows projected dividend income and portfolio yield.
   - **Problem:** A EUR-denominated holding paying 10 EUR annually rendered **$10.00 / €9.26** at EURUSD 1.08. PDF code similarly calls the unconverted sum `annual_usd` and converts it as though every payment were USD.
   - **Fix:** Carry an explicit payment currency with dividend data and convert each verified amount before aggregation. Keep unknown-currency payments unavailable and use the same calculation for UI and PDF.
   - **If we skip it:** Income amounts and portfolio yields are wrong for non-USD or mixed-currency dividends.

8. **The missing-broker daily-P&L fallback can label USD as EUR** — [positions.py:L175](backend/portfolio/positions.py#L175), [positions.py:L211](backend/portfolio/positions.py#L211)
   - **What this is:** Uses calculated price changes when broker daily P&L is unavailable.
   - **Problem:** One USD share moving from 100 to 110 produces a fallback of 10. In an EUR-base payload with USD worth 0.50 EUR, the correct price-change contribution is 5 EUR, but the shared selector accepts 10 as a base-currency amount merely because the account's value-unit flag is `base`.
   - **Fix:** Calculate each fallback contribution from verified price units and convert it to the account currency before summing. Return unavailable when those inputs are missing; the market-value unit flag does not verify daily-change units.
   - **If we skip it:** A missing broker result can introduce a wrong daily amount into both the page and PDF.

9. **The Coach bypasses the corrected broker daily-P&L selection** — [scenarios.py:L60](backend/coaching/scenarios.py#L60), [positions.py:L211](backend/portfolio/positions.py#L211)
   - **What this is:** Produces the preset performance answer's “Today's move” line.
   - **Problem:** It reads `summary.total_daily_pnl` directly. With broker daily P&L **0** and calculated summary P&L **50**, the Coach reported **50 EUR** while the corrected UI/PDF selector preserves zero.
   - **Fix:** Reuse the shared daily-P&L selector and its currency rules. Do not maintain a separate choice of financial inputs inside the scenario.
   - **If we skip it:** The Coach contradicts the broker and the dashboard on the same refresh.

10. **Position details still round fractional shares and hardcode dollars** — [detail.py:L124](ui/callbacks/detail.py#L124), [detail.py:L147](ui/callbacks/detail.py#L147)
    - **What this is:** Displays the expanded holding's quantity, price, value, cost, and P&L.
    - **Problem:** The renderer showed a synthetic **0.4-share EUR holding** as **Quantity 0**, with **$150.00** price and **$60.00** value. The table/PDF fixes did not reach this separate formatting path.
    - **Fix:** Preserve fractional quantity and distinguish verified native prices, verified base values, and unverified TWS units. Apply the matching currency label rather than a literal dollar sign.
    - **If we skip it:** Opening a correctly listed holding misstates its shares and the units of its money amounts.

11. **A failed macro fetch breaks an otherwise valid Coach risk answer** — [scenarios.py:L103](backend/coaching/scenarios.py#L103), [scenarios.py:L104](backend/coaching/scenarios.py#L104)
    - **What this is:** Adds optional CAPE/Buffett context to the largest-risk scenario.
    - **Problem:** The real provider contract permits `None` for an unavailable metric, but `{'cape': None, 'buffett': None}` raises `AttributeError` on `.get`. Even an 80% single-holding concentration becomes a generic error card because macro access happens before that answer is returned.
    - **Fix:** Normalize each optional nested value with `or {}` before reading it. Add a test asserting the actual concentration answer when macro fetches fail.
    - **If we skip it:** A normal provider outage disables a risk answer that already has sufficient portfolio data.

12. **Open detail and preset-Coach panels do not follow refreshed data** — [detail.py:L105](ui/callbacks/detail.py#L105), [coach.py:L657](ui/callbacks/coach.py#L657)
    - **What this is:** Renders selected-position details and the preset Coach panel.
    - **Problem:** Their portfolio data is a Dash `State`, which is read only when another input triggers the callback. A portfolio refresh alone leaves an open detail panel unchanged; arriving portfolio/intelligence/valuation data also does not refresh an already open preset answer.
    - **Fix:** Make the relevant render callbacks depend on their data stores as inputs. Keep paid AI request submission user-triggered.
    - **If we skip it:** Different parts of the page display different snapshots, and an initially “not ready” answer can remain that way until another control changes.

13. **The earnings-reaction calculation skips the price gap after the release** — [market_data.py:L363](backend/providers/market_data.py#L363)
    - **What this is:** Estimates historical one-day price moves around earnings.
    - **Problem:** It selects the first two daily bars whose midnight timestamps are after the release timestamp. For a Monday 16:05 release and Monday/Tuesday/Wednesday closes of **100/110/110**, it reported **0%**, missing the **10%** Monday-to-Tuesday reaction.
    - **Fix:** Select the pre-release and post-release trading-session closes using the release time and exchange timezone. Test both before-open and after-close releases rather than comparing the first two later bars.
    - **If we skip it:** The earnings calendar can substantially understate the observed reaction to earnings.

## Should fix

14. **A transient Yahoo lookup failure is cached for the process's lifetime** — [market_data.py:L128](backend/providers/market_data.py#L128)
    - **What this is:** Maps a broker symbol to a Yahoo symbol such as `SPPE.DE`.
    - **Problem:** After every lookup fails, the function permanently records the unresolved plain symbol in `_YF_SYM_CACHE`. The synthetic sequence returned `SPPE` again after `SPPE.DE` became available, without retrying resolution.
    - **Fix:** Delete the final assignment that caches an unsuccessful resolution. Retain successful mappings; ordinary timed provider caching can still limit retries.
    - **If we skip it:** Some earnings, news, and fundamentals requests stay pointed at the wrong symbol until the application restarts.

15. **Docker helper scripts report success after Docker fails** — [update.sh:L10](scripts/update.sh#L10), [stop.sh:L10](scripts/stop.sh#L10), [update.bat:L11](scripts/update.bat#L11)
    - **What this is:** Stops or updates the optional Docker deployment.
    - **Problem:** With a fake `docker` command exiting **7**, `scripts/update.sh` exited **0** and printed “Done. Dashboard restarted.” Windows update and installer-generated helper paths also lack the necessary failure checks.
    - **Fix:** Propagate each failed Docker command's exit status and print success only after successful completion. Update both checked-in launchers and the templates emitted by the installers.
    - **If we skip it:** Users are told an update or shutdown succeeded when it did not.

## Validation and limits

- **Baseline:** `python -m pytest -q -p no:cacheprovider --disable-warnings` passed **123 tests**; smoke checks and Ruff passed. There are **38 deprecation warnings**, primarily pandas/NumPy and Dash DataTable warnings.
- **Additional checks:** Synthetic inputs and mocked provider responses reproduced the financial, save, lookup, formatting, and earnings failures above. Storage checks used temporary directories; script checks replaced Docker with a harmless executable returning a chosen exit code.
- **Callback checks:** Registered inputs/states were inspected for both open-panel refresh paths. This verifies the dependency wiring; it is not a full browser interaction test.
- **Coverage:** Reviewed the entry point, configuration/deployment files, dependencies, broker normalization, portfolio/FX/history calculations, trade persistence, provider/cache behavior, coaching, relevant UI/PDF consumers, scripts, and tests. Visual styling and long fundamentals/narrative renderers were sampled rather than exhaustively reviewed.
- **Why green tests are insufficient:** Existing cases cover quantity-mismatched incomplete history but not equal-quantity wrong lots; write failures but not read failures; and zero P&L in UI/PDF but not Coach. Some Coach tests accept any nonempty card, so an error card can pass.
- **Reproducibility:** Each current finding includes its concrete input or failure condition. The extra diagnostic scripts were run outside the repository; no application code or persistent user trade history was modified.

**Verdict:** Keep the backend/UI separation. Fix trade-history data loss and contract identity first, then reconcile currency units and FX-history validity; address the remaining display/provider failures individually.

**Lean:** −1 line, −0 dependencies possible immediately by deleting the unsuccessful symbol-cache assignment in finding 14. The other fixes need correctness checks, not new frameworks.

**Not checked:** Live TWS/account behavior; live market, macro, or AI responses; an actual Docker build/container upgrade; Windows execution; full browser interaction; remote-only Git state, published installer URLs/images, or secret history. This audit does not establish multi-process safety or the financial validity of the investment-scoring thresholds.
