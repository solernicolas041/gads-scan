# Sample scan

Synthetic demo data. No real account appears in this repository.

```
# Google Ads scan — 123-456-7890 (LAST_30_DAYS)

Read section 1 before believing sections 2 to 8.

## 1. Conversion integrity — the foundation
| Action | Type | Category | Primary | Counting | Conversions |
|---|---|---|---|---|---|
| Clicks to call | GOOGLE_HOSTED | CONTACT | True | MANY_PER_CLICK (!) | 0.0 |
| Calls from ads | AD_CALL | PHONE_CALL_LEAD | True | MANY_PER_CLICK (!) | 0.0 |
| Booking click | WEBPAGE | OUTBOUND_CLICK | True | ONE_PER_CLICK | 19.0 |
> (!) 2 lead action(s) counting MANY_PER_CLICK. That inflates the conversion count and
> the ROAS built on it. Check these before trusting any number below.

## 2. Bidding strategy and budget
| Campaign | Bid strategy | Target | Budget/day |
|---|---|---|---|
| Search - Core | MAXIMIZE_CONVERSIONS | tCPA $30 | $25.00 |

## 3. Device
| Segment | Spend | Conv | CPA | ROAS |
|---|---|---|---|---|
| MOBILE | $482.00 | 18.0 | $26.78 | 249% |
| DESKTOP | $41.60 | 2.0 | $20.80 | 305% |
| TABLET | $1.03 | 0.0 | — | — |
> One device at twice the CPA of another is a bid adjustment waiting to happen.

## 4a. Day of week
| Segment | Spend | Conv | CPA | ROAS |
|---|---|---|---|---|
| MONDAY | $118.40 | 6.0 | $19.73 | 342% |
| THURSDAY | $63.20 | 0.0 | — | — |

## 5. Wasted spend — keywords with 0 conversions
| Keyword | Campaign | Spend | Clicks | Conv |
|---|---|---|---|---|
| emergency plumber near me | Search - Core | $18.40 | 11 | 0 |
| plumber cost | Search - Core | $12.05 | 9 | 0 |
| 24 hour plumbing | Search - Core | $9.70 | 3 | 0 |
> $40.15 spent on zero conversions in this window. Cut only the ones with enough clicks
> to judge — a keyword with 4 clicks has not failed yet, it has not been tested.

## 7. Assets — missing ones cost CTR, and CTR costs Quality Score
| Campaign | Present | Missing |
|---|---|---|
| Search - Core | CALLOUT, SITELINK | CALL, STRUCTURED_SNIPPET |

## 8. Month over month
| Metric | Last month | This month (MTD) |  |
|---|---|---|---|
| Spend | $1,240.00 | $860.00 | down |
| Conversions | 44.0 | 31.0 | down |
| Conv. value | $3,180.00 | $2,410.00 | down |
> This month is partial. Compare the shape, not the totals.
```
