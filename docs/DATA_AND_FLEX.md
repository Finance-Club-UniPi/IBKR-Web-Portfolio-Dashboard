# Portfolio data, beta, and automatic IBKR history

For why Flex is used and where it appears in the code, see
[FLEX_INTEGRATION.md](FLEX_INTEGRATION.md).

Current valuation needs a native price, quantity, currency, and a conversion
rate to the account currency. It does **not** need yesterday's FX or purchase
history. IBKR account snapshots and `ExchangeRate` are the primary source.
Compatible last successful values and Yahoo daily prices/FX can be used for up
to five calendar days, with a source/date shown in the holdings tooltip/badge.
An account reset (`AccountReady=false`) cannot establish a new valuation.
Broker exchange hints also guide Yahoo metadata, dividend and individual-beta
lookups. Stock subscriptions wait for qualified contracts; display and attribution
share one FX stream. Delayed broker feeds may omit generic dividend/range ticks.

## One beta and support for shorts

Portfolio and Risk display the same historical account-equity beta. The Risk
period control selects their shared 1Y/3Y/5Y window. Analysis runs in the
background; the existing four-second poll collects completed results. The
ordinary portfolio view does not wait for Yahoo history or Flex downloads.
For unchanged holdings, analysis reuses a dated snapshot for up to four hours;
the valuation time is shown. Quantity, cash-balance, account, currency or period
changes invalidate it. Failed refreshes may retain a compatible successful
analysis for up to five days, while expired underlying valuations are rejected.

Each daily stock/ETF return is weighted by its **signed current value divided
by net liquidation value**. Short exposure is negative; leverage is retained.
Foreign-currency cash contributes its FX return. Base-currency cash contributes
zero. These are scenarios at fixed current weights, not a reconstruction of
actual account trading. Borrow fees, financing and transaction costs are not
included. At least 30 common daily returns and positive account equity are
required. The performance chart uses the same account-equity scenario method.
All windows use dates shared by every holding and the benchmark. A newer
listing can therefore shorten both 3Y and 5Y to the same available history;
the actual dates are shown. Predecessor listings are not automatically joined.

The allocation and sector/geography pies use **absolute position values** and
show gross position composition. The holdings exposure column is signed and
measured against account equity. A zero-net or all-short position book is not
itself a reason to suppress analysis. Inputs that imply a daily loss of all
account equity cannot produce meaningful ordinary percentage risk statistics.

## Configure automatic Flex history once

In IBKR Client Portal, go to **Performance & Reports → Flex Queries** and
create an **Activity Flex Query**, with XML output. Include:

- **Account Information:** account ID, base currency, Date Opened.
- **Trades**, detail **Executions**: all identity/date/quantity/price fields,
  including Conid, currency, buy/sell, IB Execution ID, transaction/original
  trade IDs, FX Rate to Base, commission and commission currency.
- **Open Positions**, detail **Lots**: Conid, currency, side, quantity,
  Open Date Time, originating transaction ID, report date and cost fields.

For the **Since inception** performance period (the account's actual
time-weighted return, matching IBKR), also include:

- **Net Asset Value (NAV) in Base** (the daily by-report-date rows with `Total`): daily NAV.
- **Cash Transactions**, level **Detail**, with the *Deposits & Withdrawals* type
  enabled, plus Currency, FX Rate to Base, Report Date and Transaction ID.
  Dividends, interest and fees stay inside the return; only deposits/withdrawals
  are removed as external flows.

Without these two sections the rest of the dashboard keeps working and the
Since inception view explains what is missing. Existing stored history is
re-read for the latest window once so NAV can be filled in.

Use `yyyyMMdd` dates and `HHmmss` times. Enable Flex Web Service and generate a
reporting token. Set an appropriate token validity and renew it when it expires.
Configure these **locally**, without posting the token in chat:

```text
IBKR_FLEX_TOKEN=<your reporting token>
IBKR_FLEX_QUERY_ID=<your saved Activity Flex Query ID>
IBKR_REPORT_TIMEZONE=<the IANA timezone used by the report and TWS executions>
```

For example, use `Europe/Athens` only if the report/TWS timestamps use that
zone. Never infer a timezone from a naive timestamp. Restrict the query to the
account being displayed. If TWS exposes several accounts, also configure
`IBKR_ACCOUNT` to select one. Automatic account aggregation is not supported.

Docker Compose passes these values from the local `.env`. For a native launch,
export them in the process/service environment before starting the app; Python
does not automatically load `.env`. Keep that local file private and outside Git.
No broker username/password, trading permission or new dependency is required.

The importer downloads the latest statement first, then backfills the provider's
available history in windows of at most 365 days. It runs at startup/first
portfolio refresh and every six hours, with bounded generation polling and a
five-minute retry after failure. TWS execution updates fill the recent window;
they are not treated as lifetime history. The actual imported coverage is stored.

## Lots, persistence, and limits

Authoritative Flex lot snapshots establish remaining long/short lots. Later
opening executions can extend them. A closing execution, transfer, corporate
action, missing origin, or coverage gap which cannot be reconciled requires a
newer report; merely matching the current share quantity is insufficient.

Since-purchase FX uses signed lot quantities and opening-trade FX when available,
otherwise purchase-day daily FX closes as an estimate. An unknown lot basis
suppresses **that estimate only**. It does not suppress valuation or beta.

Broker history is stored under `IBKRDASH_DATA_DIR` (default `data/`) in
`broker_history.json`. Existing CSV history remains supported; its first
successful migration creates `uploaded_trades.json.v1-backup`. Malformed saved
history aborts writes. Saves remain atomic; report failures retain prior data.
Execution IDs preserve distinct partial fills and allow correction replacement.
Cancelled executions remain tombstones so an older backfill cannot restore them.
Ambiguous ticker-only CSV rows cannot establish contract-specific lot coverage.

Both history files and runtime caches are private runtime data, not source files.
Flex report failures are shown as history status, without logging token-bearing
URLs. Order statuses are not used as executions: a cancelled or partially filled
order is not an executed purchase/lot.

Official references:

- [Account fields and ExchangeRate](https://www.interactivebrokers.com/docs/tws-api/doc/accounts/updates)
- [Execution details and limited TWS/Gateway retention](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details)
- [Flex Open Positions/Lots](https://www.ibkrguides.com/reportingreference/reportguide/open%20positionsfq.htm)
- [Flex request parameters](https://www.interactivebrokers.com/docs/web-api/api-reference/send-request)
- [Flex report retrieval](https://www.interactivebrokers.com/docs/web-api/api-reference/get-statement)
- [IBKR execution correction/cancellation codes](https://www.ibkrguides.com/reportingreference/reportguide/codestradeconfirm.htm)
