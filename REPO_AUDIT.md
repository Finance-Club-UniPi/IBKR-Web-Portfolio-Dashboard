What this repo does: A private dashboard that reads an IBKR portfolio, calculates risk and currency effects, adds market information and coaching, and exports PDFs. Assumed load: one owner, one Python process, a few browser tabs and tens of stock/ETF holdings, including shorts.

# Repository audit — 9 November 2026

Checked `fix-FX-and-beta` at `b0d559d`. This report lists current problems only; previous reports remain in Git.
**Recent** means introduced or exposed by the latest portfolio changes. **Earlier** means the problem existed before those changes. Finding numbers replace the old audit IDs.

**Update, 11 November 2026 (`fix/audit-batch-a`):** findings 1–4 and 17 are fixed and removed from the list below (see `git log c757037..fix/audit-batch-a`); 16, 18, 19 and 20 are new; other numbers are unchanged. 236 tests pass; Ruff and smoke checks pass.

## Must fix

5. **Dividend totals assume every payment is USD** — Earlier; [summary.py:447](ui/components/summary.py#L447), [pdf.py:162](ui/reports/pdf.py#L162)
   - **What this is:** Projected income, payment amounts and portfolio dividend yield.
   - **Problem:** A holding paying 10 EUR renders as $10.00 / €9.26 at EURUSD 1.08. The PDF also sums payments as USD before conversion.
   - **Fix:** Carry each payment's currency, convert before summing, and share the calculation between page and PDF. Leave unknown currencies unavailable.
   - **If we skip it:** Non-USD income amounts and mixed-currency yields are wrong.

6. **Different contracts still share ticker-keyed quotes** — Earlier; [positions.py:58](backend/portfolio/positions.py#L58)
   - **What this is:** Previous close, daily change, spreads and optional market information.
   - **Problem:** Valuation and risk now distinguish contracts, but quote enrichment still uses ticker text. Two “SAME” contracts reuse one previous close: a EUR price of 200 with its actual previous close 190 reports +110 when the other contract supplies 90.
   - **Fix:** Key broker quote data and its consumers by account/contract ID. Reject ambiguous ticker-only enrichment.
   - **If we skip it:** Correct valuations can sit beside another contract's daily change or market information.

7. **Known opening FX still depends on Yahoo being online** — Recent; [fx_history.py:77](backend/providers/fx_history.py#L77)
   - **What this is:** Since-purchase currency attribution from reconciled open lots.
   - **Problem:** Yahoo history is fetched before checking whether every lot already has broker opening FX. Two shares at price 100, with opening FX 0.80 and current FX 0.90, should produce 20; a mocked Yahoo outage makes the result unavailable.
   - **Fix:** Fetch historical FX only for lots missing an opening rate. Test the all-broker-data case with Yahoo unavailable.
   - **If we skip it:** A provider outage hides a value that the broker data already lets us calculate.

8. **Fallback daily P&L can use the wrong currency** — Earlier; [positions.py:152](backend/portfolio/positions.py#L152), [positions.py:188](backend/portfolio/positions.py#L188)
   - **What this is:** Calculated daily profit/loss when the broker supplies no result.
   - **Problem:** One USD share rising by $10 produces a fallback of 10, accepted as EUR when the account's value-unit flag is `base`. At 0.50 EUR/USD, that price-change contribution is €5. The live adapter's `unverified` flag currently blocks this fallback; the underlying calculation remains wrong.
   - **Fix:** Convert each verified native-currency contribution before summing. A market-value flag alone must not verify daily-change units.
   - **If we skip it:** Base-labelled payloads can put a wrong daily amount into the page, Coach and PDF.

9. **Missing macro data breaks a valid Coach answer** — Earlier; [scenarios.py:104](backend/coaching/scenarios.py#L104)
   - **What this is:** The preset “biggest risk” answer, with optional market-valuation context.
   - **Problem:** `{'cape': None, 'buffett': None}` raises `AttributeError`, even when one holding is 80% of the portfolio. The optional lookup fails before the concentration answer is returned.
   - **Fix:** Read each optional nested value through `or {}`. Test the answer's actual concentration text when macro providers fail.
   - **If we skip it:** A routine provider outage disables advice based on portfolio data that is already available.

10. **Earnings reactions miss the release-day price gap** — Earlier; [market_data.py:412](backend/providers/market_data.py#L412)
    - **What this is:** Historical one-day price moves around earnings releases.
    - **Problem:** For a Monday 16:05 release and Monday/Tuesday/Wednesday closes of 100/110/110, the code compares Tuesday with Wednesday and reports 0%, missing the 10% reaction.
    - **Fix:** Select the closes immediately before and after the release using exchange trading hours. Test releases before opening and after closing.
    - **If we skip it:** The earnings calendar can materially understate observed reactions.

11. **Completed background jobs keep stale results indefinitely** — Recent; [portfolio_service.py:26](backend/portfolio_service.py#L26)
    - **What this is:** Background enrichment for individual holding betas and dividend data.
    - **Problem:** An unchanged input key reuses its completed job forever. After simulating five hours, the provider's timed cache refreshes beta from 1 to 2, but the dashboard job still returns 1.
    - **Fix:** Expire completed jobs with the provider's cache lifetime and retain the last result while refreshing.
    - **If we skip it:** Unchanged holdings can display stale enrichment until inputs change or the app restarts.

## Should fix

12. **Linked accounts can create duplicate subscriptions** — Recent; [ibkr.py:378](backend/providers/ibkr.py#L378)
    - **What this is:** Selecting one account with `IBKR_ACCOUNT`.
    - **Problem:** Positions are filtered by account, but request contracts are filtered only by contract ID. When two accounts own the same contract, the reproduced result has one selected holding but two generic-data subscriptions.
    - **Fix:** Build the request-contract list from the selected account's positions, then remove duplicate contract IDs.
    - **If we skip it:** Duplicate requests and missing-request cancellation errors can return for linked accounts.

13. **Open preset-Coach answers do not follow refreshed data** — Earlier; [coach.py:657](ui/callbacks/coach.py#L657)
    - **What this is:** The currently open rules-based Coach panel.
    - **Problem:** Portfolio, intelligence and valuation stores are callback `State`: their changes do not trigger rendering. A refreshed portfolio or recovered provider leaves the old answer visible.
    - **Fix:** Make those stores rendering inputs. Keep paid AI submissions triggered only by the user.
    - **If we skip it:** The Coach can describe an older snapshot than the rest of the dashboard.

14. **Docker helpers print success after failure** — Earlier; [update.sh:10](scripts/update.sh#L10), [stop.sh:10](scripts/stop.sh#L10)
    - **What this is:** Optional stop/update scripts and installer-generated copies.
    - **Problem:** With a harmless fake Docker command exiting 7, both shell scripts exit 0 and print “Done.” Windows scripts and installer templates also lack the required failure handling.
    - **Fix:** Propagate failed Docker exit codes and print success only after completion. Apply the same checks to the installer templates.
    - **If we skip it:** Users are told a shutdown or update worked when it failed.

15. **Image and release publishing bypass the test gate** — Earlier; [docker-publish.yml:8](.github/workflows/docker-publish.yml#L8), [release.yml:18](.github/workflows/release.yml#L18)
    - **What this is:** Workflows that publish Docker images and release packages.
    - **Problem:** Publishing neither runs nor requires the separate checks workflow. A main-branch push or version tag containing failing tests can still publish an image; tag releases do not trigger the current checks workflow at all.
    - **Fix:** Require the existing lint, smoke and regression checks to succeed before publishing the same commit.
    - **If we skip it:** A broken release can reach users despite having a test suite.

16. **PDF benchmark is in base currency; the page's is in USD** — Recent; [report_data.py:89](backend/portfolio/report_data.py#L89), [performance.py:143](ui/callbacks/performance.py#L143)
    - **What this is:** The S&P 500 comparison rows in the PDF and on the Performance page.
    - **Problem:** The PDF now takes the benchmark from `account_history`, converted to the account's base currency; the page still compares against `^GSPC` in USD. With the S&P flat in USD and USD down 10% against EUR, the PDF shows −10.0% and the page 0.0%. Neither label names the currency. A failed USD FX fetch also drops the PDF's S&P rows.
    - **Fix:** Decide on one convention (base currency is the like-for-like comparison for an account-equity return), apply it to both, and label the currency.
    - **If we skip it:** The PDF's "Difference (pp)" contradicts the page.

18. **A live fill can restore an execution that Flex voided or corrected** — Earlier; [flex.py:74](backend/providers/flex.py#L74)
    - **What this is:** The cancellation and correction checks in the broker-history merge.
    - **Problem:** The checks key on `transaction_id` / `correction_of`, which live fills never carry. Flex execution E (transaction 123), then a Flex correction with its own execution ID and `correction_of=123` removes E; a later live fill of E adds it back beside the correction. A cancellation row with its own execution ID behaves the same. Reproduced with synthetic data; whether real IBKR corrections reuse or replace execution IDs has not been checked.
    - **Fix:** First confirm IBKR's execution-ID scheme for corrections and cancellations. If they get new IDs, remember voided execution IDs and skip live fills for them.
    - **If we skip it:** A corrected trade can be counted twice in lots and currency attribution.

19. **Total Return is never available** — Recent (exposed by fixing the old finding 3); [positions.py:176](backend/portfolio/positions.py#L176)
    - **What this is:** The portfolio's Total Return on the page and in the PDF.
    - **Problem:** The backend always sets `total_pnl_pct=None`, and the PDF no longer rebuilds it from mixed-currency P&L, so it always shows "—".
    - **Fix:** Compute it in the backend from verified base-currency cost and value; leave it unavailable when any holding lacks them.
    - **If we skip it:** The headline return figure is permanently blank.

20. **Broker-history write errors show a file path** — Earlier; [portfolio_service.py:88](backend/portfolio_service.py#L88)
    - **What this is:** The status note under the FX total card when saving broker history fails.
    - **Problem:** `OSError` text is shown as-is; with a read-only data directory it contains the full data path. The uploaded-trades handlers already show a fixed message instead.
    - **Fix:** Show a fixed message for `OSError` and log the detail.
    - **If we skip it:** The local data path appears in the dashboard.

## Validation and limits

- **Checks (at `b0d559d`):** 211 tests pass, with 2 existing Dash deprecation warnings; Ruff and smoke checks pass.
- **Evidence:** Temporary assertion-based diagnostics reproduced the data-loss, PDF, currency, lookup, refresh and shell-script cases above. They used synthetic data, temporary history files and mocked providers; no user trade history was changed. Workflow publishing was checked from configuration.
- **Coverage:** Entry point, configuration, broker/valuation/risk flows, Flex and CSV persistence, background jobs, AI request/key handling, relevant UI/PDF consumers, build context, launchers, CI and tests. Long fundamentals/narrative renderers and styling were sampled.
- **Known limits:** Analysis uses current exposures and cash, not actual historical trading. Newer listings can shorten 3Y/5Y to the same shared history; that is documented behavior, not a duplicated beta calculation.

**Verdict:** Fix history metadata loss and misleading deletion first, then the two incorrect PDF return paths. Keep the current backend/UI separation.

**Not checked:** Fresh live broker/Flex/AI calls, full browser interaction, real Docker upgrades, Windows execution, published installers/images, remote-only Git state or secret history. No application code was changed by this audit.
