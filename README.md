# gads-scan

**One command. Every leak in a Google Ads account, in 30 seconds.**

```bash
python gads_scan.py --customer-id 123-456-7890 --days 30
```

No AI, no SaaS, no account access for anyone but you. Read-only by default.

---

## Why this exists

Every audit checklist starts with keywords and ad copy. That's the wrong end.

If the conversion actions are set up wrong, every number below them is wrong — the CPA,
the ROAS, the "winning" campaign, and every bid Smart Bidding has ever placed. Most
accounts I open have at least one lead action counting `MANY_PER_CLICK`, which inflates
the conversion count and quietly teaches the bidding algorithm to chase the wrong people.

`gads-scan` checks that **first**, then walks down the account looking for money that is
leaving without coming back.

---

## What it prints

Sample output. Figures below are synthetic demo data, not a real account.

```
# 🔍 SCAN — 123-456-7890 (LAST_30_DAYS)

## 1. Conversion integrity (the foundation — if this is wrong, the audit is wrong)
| Action                    | Type          | Category       | Primary | Counting            | Conv 30d |
|---------------------------|---------------|----------------|---------|---------------------|----------|
| Clicks to call            | GOOGLE_HOSTED | CONTACT        | True    | MANY_PER_CLICK ⚠️   | 0.0      |
| Calls from ads            | AD_CALL       | PHONE_CALL_LEAD| True    | MANY_PER_CLICK ⚠️   | 0.0      |
| Booking click             | WEBPAGE       | OUTBOUND_CLICK | True    | ONE_PER_CLICK       | 19.0     |
> ⚠️ = MANY_PER_CLICK on a lead action inflates the count. Verify before trusting the ROAS.

## 2. Bidding strategy + budget
| Campaign        | Bid strategy          | Target   | Budget/day |
|-----------------|-----------------------|----------|------------|
| Main Search     | MAXIMIZE_CONVERSIONS  | tCPA $30 | $25.00     |

## 3. Device
| Segment | Spend   | Conv | CPA | ROAS |
|---------|---------|------|-----|------|
| MOBILE  | $482.00 | 18.0 | $27 | 249% |
| DESKTOP | $41.60  | 2.0  | $21 | 305% |

## 4. Day of week / hour of day
| MONDAY   | $118.40 | 6.0 | $20 | 342% |
| THURSDAY | $63.20  | 0.0 | $0  | 0%   |

## 5. Wasted spend — keywords with 0 conversions
| Keyword                | Spend  | Conv |
|------------------------|--------|------|
| emergency plumber near me   | $18.40 | 0    |
| plumber cost                | $12.05 | 0    |
```

Each section is queried independently: one broken query never takes the whole scan down.

---

## What to do with each section

| Section | The question it answers | Act when |
|---|---|---|
| 1. Conversion integrity | Are my numbers real? | Any lead action on `MANY_PER_CLICK`, or a primary action at 0 conversions |
| 2. Bidding + budget | Is the strategy matched to the data? | tCPA set on an account with < 30 conv/month |
| 3. Device | Where does the money actually convert? | One device eats spend at 2× the CPA of another |
| 4. Day / hour | When is the budget burning for nothing? | A day or hour with real spend and 0 conversions, repeated over 90 days |
| 5. Wasted keywords | What can I cut today? | Spend above your CPA target with 0 conversions and enough clicks to judge |

**Read section 1 before believing sections 2–5.** That is the whole point of the ordering.

---

## Install

```bash
git clone https://github.com/solernicolas041/gads-scan.git
cd gads-scan
pip install -r requirements.txt      # google-ads only
```

You need three things from Google:

1. A **developer token** — Google Ads UI → Tools → API Center (Basic access is enough).
2. An **OAuth client** — Google Cloud Console → Credentials → OAuth client ID (Desktop).
3. A **refresh token** — `python generate_refresh_token.py` walks you through the consent screen.

Put them in `google-ads.yaml` at the repo root:

```yaml
developer_token: YOUR_DEV_TOKEN
client_id: YOUR_CLIENT_ID
client_secret: YOUR_CLIENT_SECRET
refresh_token: YOUR_REFRESH_TOKEN
login_customer_id: 1234567890     # your MCC, no dashes
use_proto_plus: true
```

`google-ads.yaml` is git-ignored. Nothing leaves your machine — the tool talks to Google
and prints to your terminal.

---

## Usage

```bash
# full scan, last 30 days
python gads_scan.py --customer-id 123-456-7890 --days 30

# a single section
python gads_scan.py --customer-id 123-456-7890 --section conversions
python gads_scan.py --customer-id 123-456-7890 --section waste

# exact calendar month (what a client report must use)
python gads_scan.py --customer-id 123-456-7890 --month 2026-07

# every account under your MCC, one file each
python gads_scan.py --all --out ./scans/

# markdown to a file instead of stdout
python gads_scan.py --customer-id 123-456-7890 > audit.md

# list the accounts under your MCC
python gads_scan.py --list-accounts
```

Sections, in the order they print: `conversions`, `bidding`, `device`, `dayofweek`,
`hour`, `waste`, `harvest`, `assets`, `trend`. Each is queried independently — a section
that fails prints `DEGRADED` and the scan carries on.

Output is markdown on purpose: paste it into Notion, a client doc, or a PR.

---

## Use it from an AI agent

The output is markdown on stdout and nothing else. That makes it a clean tool for any
coding agent or LLM runner — Claude Code, Codex CLI, Cursor, Aider, a DeepSeek or GPT
script, an n8n node. There is no SDK to learn and no model inside: the agent runs the
command and reads the report.

```bash
# Claude Code / Codex CLI — hand the scan to the model for interpretation
python gads_scan.py --customer-id 123-456-7890 > /tmp/scan.md
claude -p "Read /tmp/scan.md. List the three changes that save the most money this week,
           and say explicitly which ones you would NOT make and why."
```

```python
# any LLM API — the scan is just context
scan = subprocess.run(["python", "gads_scan.py", "--customer-id", CID],
                      capture_output=True, text=True).stdout
messages = [{"role": "user", "content": f"{scan}\n\nWhich sections warrant action?"}]
```

Two rules worth keeping if you do this:

- **Let the model read, not write.** This repo is read-only by design; keep the mutations
  behind a human confirmation, whatever your agent framework.
- **Give it the whole scan, not one section.** Section 1 is what tells the model whether
  sections 2–5 can be trusted at all.

---

## Run it on a schedule

```cron
# Monday 7am — scan every account, keep a dated copy
0 7 * * 1  cd /path/to/gads-scan && python gads_scan.py --all --out ./scans/$(date +\%F)/
```

```yaml
# GitHub Actions — weekly scan committed to the repo (credentials in secrets)
on:
  schedule: [{cron: "0 7 * * 1"}]
```

Diffing this week's scan against last week's is where it gets useful: a keyword that
crossed into waste, a conversion action that flipped to primary, a day that went to zero.

---

## When people actually run it

- **New client onboarding.** First command you run on an account you've just been given
  access to, before you promise anything.
- **Pre-pitch audit.** A prospect grants read access for 20 minutes; you leave with a
  findings list instead of a hunch.
- **Monthly close.** `--month 2026-07` gives an exact calendar month, which is what a
  client report needs — rolling 30 days quietly overlaps two months.
- **After somebody else touched the account.** Section 1 catches a conversion action that
  changed status without anyone announcing it.
- **Before trusting a ROAS.** Especially on lead-gen, where `MANY_PER_CLICK` is the
  default nobody revisits.

---

## Safety

- **Read-only.** This tool never writes to an account. No pause, no budget change, no
  keyword edit — those live in a separate repo behind an explicit confirmation.
- **No data collection.** No telemetry, no phoning home, no third-party service.
- **Your credentials stay local.** `google-ads.yaml` is git-ignored and never read by
  anything but the Google client library.

---

## Tests

```bash
python3 tests/test_gads_scan.py
```

30 tests, no credentials and no network: date-range building, the invalid-`DURING`
guard, customer-id parsing, section isolation, the `MANY_PER_CLICK` flag on lead vs
purchase actions, and a check that no mutation verb exists anywhere in the source.

---

## Requirements

- Python 3.9+
- `google-ads` (only dependency)
- Google Ads API access at Basic level or above

---

## FAQ

**Does it work on a single account without an MCC?**
Yes — set `login_customer_id` to the account itself.

**Does it need an LLM / API key?**
No. It is plain Python and Google's own API. Nothing is generated, everything is queried.

**Why is the conversion table first?**
Because a CPA computed on a miscounted conversion action is not a CPA. Every other number
in the scan inherits that error.

**Can I use it on a client account I don't own?**
Only with their granted access, same as the Google Ads UI. The tool has no special powers.

---

## License

MIT.
