What this repo does: It displays an Interactive Brokers portfolio, calculates portfolio statistics, adds market information and coaching, and exports PDFs. The assumed load is one owner, one Python process, a few browser tabs, and tens of holdings.

# Repository audit — 8 November 2026

**Audited checkout:** branch `fix-FX-and-beta`, commit `00b7365`. This is a review of the local source, not a claim about what is currently deployed or published upstream.

**Original 8 November result:** 15 original findings are resolved within their stated scope; 4 are partially resolved. There were **15 confirmed open findings** below, including remaining gaps in the partial fixes. Follow-up finding **16** is retained below as resolved locally. The previous blanket statement that all 19 findings were fixed was too broad.

**9 November implementation:** The approved valuation, shared beta, short exposure and automatic history plan is implemented locally on `fix-FX-and-beta`, after commit `7f758a8`. **211 tests**, smoke checks and Ruff pass. Current findings **1, 2, 4, 5, 6, 9, 10 and 14** are resolved within the scope stated in the follow-up below. **7 findings remain open or partial: 3, 7, 8, 11, 12, 13 and 15.** Findings **16–17** are resolved locally. The original findings and old-ID disposition table retain the 8 November evidence; the implementation status below supersedes them. This is a scoped follow-up, not a new full-repository audit.

Only this report was changed during the original 8 November audit. The 9 November follow-up includes the application fixes, documentation and tests described below. The older report remains available in Git history. See [the developer map](docs/ARCHITECTURE.md) for the current backend/UI layout.

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

## Must fix — original findings, with current status below

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

## Resolved locally — 9 November 2026 follow-up

16. **Rejected FX contracts could stall the whole refresh — resolved locally** — [ibkr.py:L59](backend/providers/ibkr.py#L59), [ibkr.py:L482](backend/providers/ibkr.py#L482)
    - **What this is:** Acquires fresh spot and previous daily FX rates for verified base-currency holding values and daily FX attribution. Previously, a USD holding in an EUR account first requested `USDEUR`, then intended to try `EURUSD` and invert its rates.
    - **Original problem:** The user's log at **2026-11-09 12:22:20** shows Error **200**, “No security definition has been found,” followed by “Unknown contract” for `USDEUR`. Installed `ib_async 2.1.0` returns **`[None]`**, not `[]`, when qualification has no matches. The old truthiness check treated that as success and requested a quote for the rejected contract. Qualification and snapshot waits had no local deadline; a stalled FX task could hold the gathered portfolio result until the **60-second** outer fetch timeout.
    - **Evidence:** A temporary, assertion-based diagnostic extracted the actual `_fetch_daily_fx` function and used the installed library's real `IB.qualifyContractsAsync` with mocked contract details. It reproduced `[None]`, observed a snapshot request for rejected `USDEUR`, and verified that no `EURUSD` qualification occurred while the gathered dashboard result remained pending despite the holdings task finishing. The pending snapshot was deliberately simulated; the supplied log establishes the contract rejection but does **not** establish the precise live delay or which subsequent request stalled. No live broker request was made by this diagnostic.
    - **Provenance:** This request order and qualification check already exist in pre-Risk-port commit `2c14b70`; the Risk page did not introduce them. The separate EUR/USD display-rate fetch also ignored its qualification result; it now uses the same validated, bounded helper.
    - **Resolution:** Both FX paths now share a quote helper that rejects empty, `[None]`, and nonpositive-`conId` qualification results. It uses a cancellable `reqMktData(..., snapshot=True)` request, waits at most **3 seconds** for qualification plus a valid price, and cancels the market-data request in `finally`, including on caller cancellation. EUR/USD conversion requests only `EURUSD` and normalizes both rates with the existing inversion helper. Other currency pairs retain direct/inverse fallback. Each currency has a **5-second total budget**: qualification/quotes use the lesser of 3 seconds and the remaining budget; historical bars use the remaining budget through the library's timeout/cancellation path. Failed currencies stay unavailable while successful ones and the rest of the portfolio survive. Existing live/freshness checks remain, and the configured display fallback never becomes verified attribution FX. IBKR's [FX contract documentation](https://www.interactivebrokers.com/docs/tws-api/contracts/fx-pairs) describes the currency/IDEALPRO contract fields.
    - **Regression checks:** [test_ibkr_fx.py](tests/test_ibkr_fx.py) covers real installed-library `[None]` qualification behavior, missing contract IDs, EUR/USD inversion in both account currencies, non-EUR/USD inverse fallback, missing/stale/delayed quotes, qualification/quote/history timeouts, cancellation cleanup, and preserving successful currencies alongside a stalled one. The original FX-only follow-up passed **172 tests**; the complete implementation now passes **203 tests**, smoke checks and Ruff locally.
    - **Subsequent verification:** During this implementation a read-only TWS check valued all **3 of 3** live stock/ETF positions and returned the core snapshot in **0.106 seconds**. Optional broker chains now run as persistent background tasks and do not hold that snapshot until completion. This is a measured after value, not a controlled before/after benchmark. TWS refused connections when the final retest was attempted; see the validation limits below.

## Approved data and beta implementation — 9 November 2026

- **Current valuation:** Uses account-specific native stock/ETF price, signed quantity and `ExchangeRate`; `AccountReady=false` cannot establish new values. If the explicit BaseCurrency tag is absent, a unique NetLiquidation currency establishes the base. Compatible cached values and verified Yahoo listing prices/current FX are allowed for at most **5 calendar days**, with source/date. Yesterday's FX, historical beta inputs and purchase lots are independent background enrichments. Stable `(account, conId)` keys reach valuation, FX attribution, current-exposure history and detail selection.
- **One beta:** Portfolio and Risk cards share `backend/portfolio/analysis.py` and the same 1Y/3Y/5Y selector; PDF consumes that result and rejects it when the book changed. Daily stock/ETF returns are weighted by signed current base value / positive account net liquidation, including foreign cash FX returns. At least **30 common daily returns** are required. Common benchmark price dates are selected before calculating returns, so multi-day intervals match. Shorts, leverage, all-short and zero-net books have known-answer tests. A compatible dated exposure snapshot is reused for up to four hours, with its valuation time shown. Quote changes and display sorting do not restart in-flight jobs; account, quantity, cash balance, currency and period changes invalidate them. Compatible provider-failure fallback is bounded to five days, including underlying valuation age.
- **Meaning of performance:** The chart/PDF use fixed-current-exposure account-equity scenarios, not reconstructed past account balances. Historical trading, borrow fees, financing and transaction costs are excluded. Pies use gross absolute position values; holdings expose signed account-equity percentages. Short P&L signs and the Coach's drop scenario follow the position direction.
- **Automatic history:** Flex v3 downloads the latest authoritative lots first, then backfills retained history in windows of at most 365 days. Reports run off the dashboard request path at first refresh and every six hours. Stored coverage is advanced only after all required windows succeed, including gaps after a long offline period. Opening executions can extend a lot snapshot; closing fills, transfers, missing origins and coverage gaps require reconciliation/new reports rather than quantity-only acceptance. Unknown lots suppress since-purchase FX only. Setup and official references are in [DATA_AND_FLEX.md](docs/DATA_AND_FLEX.md).

| Current finding | Implementation status |
|---|---|
| **1 — history read loss** | **Resolved.** Missing file alone means empty history. Unreadable/invalid saved history aborts writes, while the dashboard may show the separate error. Incoming records are validated before replacement. |
| **2 — identical fills** | **Resolved within available identity.** Execution IDs deduplicate actual repeats; anonymous CSV fills retain multiplicity within an upload, and repeated uploads are idempotent. Legacy JSON migrates to schema v2 with a backup. Flex revisions replace earlier executions; cancellations remain tombstones against old backfills. Without IDs, identical fills in separate partial uploads remain indistinguishable. |
| **3 — ticker identity** | **Partial.** Valuation, risk/performance, lot FX, selected details and detail-trade filtering use account/contract identity. Some optional fundamentals/intelligence/dividend mappings and the legacy holdings-only history helper still use ticker text. |
| **4 — quantity-only lot acceptance** | **Resolved conservatively.** Authoritative lots plus explicit execution coverage are required. Unreconciled CSV alone cannot claim complete open lots. Coverage gaps, closing fills and unexplained origins produce a reason, not a numeric estimate. |
| **5 — undated current FX** | **Resolved.** No latest-undated historical rate becomes current FX. Current valuation and purchase-day close validity are checked separately; opening execution FX takes priority when available. |
| **6 — unverified overall return** | **Resolved conservatively for the reported defect.** Aggregate cost-return percentage and related Coach judgments stay unavailable while base-currency cost/P&L is unverified. This does not establish verified aggregate acquisition costs or convert every legacy raw P&L amount. |
| **9 — Coach broker zero** | **Resolved.** Coach uses the shared selector; a dedicated regression verifies that broker zero wins over summary 50. |
| **10 — fractional/native details** | **Resolved for stock/ETF details.** The contract-selected detail preserves a 0.4-share holding, labels native EUR price/P&L and verified base market value, and refreshes when portfolio data changes. |
| **12 — stale open panels** | **Partial.** Position detail now takes portfolio-data as an Input. Preset Coach refresh wiring remains open. |
| **7, 8, 11, 13–15** | **Remain open.** Dividend currency assumptions, fallback daily-P&L currency verification, optional macro failures, earnings-session selection, unsuccessful ticker caching and Docker launcher exit handling were not included in this plan. |

**Validation evidence:** The final local suite passes **203 tests** with **38 existing deprecation warnings**; Ruff and smoke checks pass. New cases cover signed/equity beta, foreign cash, benchmark calendars, matched/mismatched analysis consumers, currency-aware ETF history, independent prior-day FX, dated valuation expiry/compatibility, duplicate contracts, fractional native details, atomic/read-failure saves, v1 migration, execution revisions/cancellations and Flex coverage/backfill. Callback serialization is exercised through Dash HTTP requests.

**Live evidence and remaining limits:** Read-only TWS inspection found one account, three STK holdings, no BaseCurrency tag, positive EUR NetLiquidation, account ExchangeRate fields and native per-share snapshot prices. The updated core fetch valued **3/3 positions in 0.106 seconds**. An intermediate live 1Y analysis produced a beta from **251 observations** after fixing Yahoo chart-listing currency validation (ETF fund currency was unsuitable). The final benchmark-calendar alignment is covered by deterministic tests; a final live rerun failed at connection because TWS port 7497 was closed. Flex credentials/timezone are not configured, so automatic real report download and the user's actual open lots remain unverified. Live short positions were absent; short behavior is tested with synthetic long/short, all-short, leveraged and market-neutral inputs. No orders were placed or changed, and account IDs, balances and holding names were not printed in the diagnostic results. Full browser, Windows and Docker execution were not performed. Existing unrelated `pyproject.toml` edits were preserved; no commit or push was made.

## VUAA listing and dependency follow-up — 9 November 2026, after 16:08

- **Reported symptom:** Yahoo logged `VUAA: possibly delisted; no price data found (period=1y)`, alongside repeated generic NumPy timedelta deprecations.
- **Cause and provenance:** The new contract-aware history lookup recognized IBKR `LSE` but omitted **`LSEETF`**. Read-only live contract details confirm VUAA is a USD ETF on LSEETF. That omission made the lookup probe the invalid plain Yahoo symbol before trying exchange suffixes. This was introduced in the current local implementation; it does not establish that VUAA was delisted or that the eventual suffix fallback failed.
- **Fix:** Map `LSEETF` directly to Yahoo's `.L` suffix, retaining chart quote-currency validation. The regression verifies the sole request is **`VUAA.L`**, with no plain-symbol or wrong-listing probes. This shared helper supplies historical risk, account performance, valuation fallback and the holding sparkline.
- **Warning:** With installed pandas **2.3.3** and NumPy **2.5.4**, even `pd.Timedelta('1d')` reproduced the reported warning without Yahoo or broker access. NumPy's [2.5 release notes](https://numpy.org/doc/stable/release/2.5.0-notes.html) describe the underlying generic-timedelta deprecation. Constrain NumPy to **`>=1.26,<2.5`** in both dependency manifests while retaining pandas 2.x; the local lock/environment now use **2.4.6**. No global warning suppression or vendor-code patch was added. The pre-existing Ruff dependency edit remains intact; the ignored local lock is not staged.
- **Live verification:** A fresh, cache-bypassed `VUAA.L` request returned **253 USD price bars through 9 November**, with **zero Yahoo errors and zero generic-timedelta warnings**. Read-only core fetch valued **3/3 positions in 0.106 seconds**. The final shared 1Y analysis was **ready**, with **244 common daily returns through 6 November**; portfolio and Risk beta card values matched. This supersedes the earlier closed-port limit for broker/analysis verification. No orders were placed or changed.
- **Checks and limits:** **204 tests pass**, Ruff and smoke checks pass, and the local lock matches project metadata. Only **2 Dash DataTable deprecation warnings** remain in the suite. Restart the running dashboard to load the code and dependency changes. Full browser interaction and automatic real Flex downloads remain unverified; Flex configuration is still absent. The earlier eight open/partial audit findings retain their status.

## Subscription cleanup and Yahoo metadata follow-up — 9 November 2026, after 16:22

17. **Error 300 cancellation chatter and a separate plain-VUAA metadata lookup — resolved locally**
    - **Reproduction:** A read-only refresh traced each market-data request and cancellation until all optional chains completed. Stock generic subscriptions were sent before qualification filled the cached contracts' empty exchange fields. TWS rejected them with **321, “Please enter exchange”**; cancelling those rejected requests then produced **300**. The installed library also shares a market-data request slot per contract: concurrent display/attribution EURUSD requests overwrote that slot, leaving one cancellation without a request ID.
    - **Resolution:** Both stock consumers await one shared contract qualification before requesting data. Dividend subscriptions use generic tick **456**, while **59** is the received dividend tick type; see [IBKR's request reference](https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClient.html). Cleanup runs in `finally`, including task cancellation. Display/attribution share one bounded FX stream and one cleanup, also across overlapping holdings snapshots. A fresh tick is required rather than accepting pre-existing cached ticker fields. Core holdings still return without waiting for the optional chains.
    - **Yahoo resolution:** The earlier VUAA change reached historical prices, but legacy metadata and dividend fetchers still queried the plain ticker. The portfolio pipeline now supplies unambiguous broker-listing hints before enrichment. Metadata, individual betas, dividends, fundamentals and existing resolver consumers reuse those hints; price history uses the same exchange suffix helper. No VUAA-specific production exception was added. Failed metadata and unsuccessful symbol resolution are no longer cached as success: **current finding 14 is resolved**. Ticker-only ambiguity elsewhere remains within partial finding 3.
    - **Provenance:** The independent stock qualification/dividend chains and `59,165` subscription exist in checked-in `HEAD`. The current local bounded FX helper exposed the shared-slot cancellation conflict, and the previous local VUAA fix covered price history only. These are distinct causes; the broker requests in this diagnostic concern market data, not orders.
    - **Live evidence:** A complete corrected refresh produced **no 300/321 errors and no missing-request cancellation log**. A subsequent read-only refresh valued **4/4 current holdings in 0.114 seconds**; one EURUSD stream served both consumers. A fresh VUAA metadata/beta/dividend check made exactly one **VUAA.L** metadata request and returned ETF metadata with **zero Yahoo errors**. The broker did emit **2187** for one delayed-data holding: IBKR documents this as unavailable generic ticks on delayed fallback. The Yahoo dividend fallback remains enabled; missing generic range/dividend data does not invalidate valuation. See [IBKR error meanings](https://www.interactivebrokers.com/docs/tws-api/doc/error/codes).
    - **Validation/limits:** **211 tests**, Ruff and smoke checks pass; the suite has **2 existing Dash deprecation warnings**. Regressions cover shared FX subscriptions, holdings changes during a pending FX request, missing-exchange qualification, correct dividend request IDs, cancellation cleanup, shared Yahoo listing hints, ambiguous hints and retry after lookup failure. No orders were placed or changed. Restart the dashboard to load the changes; full browser interaction and automatic Flex downloads remain unverified.

## Original audit validation and limits

- **Baseline:** `python -m pytest -q -p no:cacheprovider --disable-warnings` passed **123 tests**; smoke checks and Ruff passed. There are **38 deprecation warnings**, primarily pandas/NumPy and Dash DataTable warnings.
- **Additional checks:** Synthetic inputs and mocked provider responses reproduced the financial, save, lookup, formatting, and earnings failures above. Storage checks used temporary directories; script checks replaced Docker with a harmless executable returning a chosen exit code.
- **Callback checks:** Registered inputs/states were inspected for both open-panel refresh paths. This verifies the dependency wiring; it is not a full browser interaction test.
- **Coverage:** Reviewed the entry point, configuration/deployment files, dependencies, broker normalization, portfolio/FX/history calculations, trade persistence, provider/cache behavior, coaching, relevant UI/PDF consumers, scripts, and tests. Visual styling and long fundamentals/narrative renderers were sampled rather than exhaustively reviewed.
- **Why green tests are insufficient:** Existing cases cover quantity-mismatched incomplete history but not equal-quantity wrong lots; write failures but not read failures; and zero P&L in UI/PDF but not Coach. Some Coach tests accept any nonempty card, so an error card can pass.
- **Reproducibility:** Each finding includes its concrete input or failure condition. The original extra diagnostic scripts were run outside the repository without modifying application code or persistent user trade history; the subsequent finding-16 fix adds checked-in regressions.
- **Original FX-only follow-up:** The offline diagnostic reproduced the rejected-contract path, and that fix passed **172 tests** with **38 deprecation warnings**. The subsequent **203-test** implementation and scoped live measurements supersede this earlier verification; see above.

**Current verdict:** Keep the backend/UI separation. The approved valuation/beta/history plan is implemented locally; configure Flex and restart the dashboard to enable automatic lot imports. Track the remaining seven open/partial findings individually.

**Lean:** The unsuccessful symbol-cache assignment in finding 14 is now deleted. No new dependency was added for these follow-ups.

**Original audit not checked (superseded only by the scoped live evidence above):** Live TWS/account behavior; live market, macro, or AI responses; an actual Docker build/container upgrade; Windows execution; full browser interaction; remote-only Git state, published installer URLs/images, or secret history. This audit does not establish multi-process safety or the financial validity of the investment-scoring thresholds.
