# Roomagent — Project Plan

A Telegram-based AI roommate coordinator that tracks shared supplies, chores, rent, and utilities across a household.

---

## 1. Vision

A single Telegram group chat where roommates @-mention or DM a bot to log purchases ("got TP $12"), claim chores ("I'll do dishes"), or check status ("who owes what?"). The bot maintains running ledgers for supplies, chores, and money, sends proactive nudges (low TP, rent due in 3 days, trash night), and produces a monthly settle-up summary.

**Why Telegram first:** zero install friction, bots are free, group chat is where roommates already live. Web dashboard is phase 2.

---

## 2. Core Features (MVP)

**Supply tracking**
- Log purchases with item, cost, buyer ("@bot tp $12 costco")
- Low-stock alerts based on consumption rate or manual "we're out" flag
- Running list of "need to buy" items

**Chore tracking**
- Recurring chores (dishes daily, trash Tue/Fri, bathroom weekly)
- Round-robin or claim-based assignment
- Streak/accountability ("@thomas hasn't done dishes in 4 days")

**Rent & utilities**
- Monthly rent splits (equal or custom shares)
- Utility bills logged by whoever receives them, split automatically
- Venmo/Zelle handle storage, generate "pay $X to @roommate" messages
- Due-date reminders (3 days out, day-of)

**Settle-up**
- Running balance per roommate
- Monthly "who owes whom" summary that minimizes transactions (debt simplification)

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Bot framework | `python-telegram-bot` v21+ | Mature, async, good docs |
| LLM | Claude Sonnet 4.5 via Anthropic API | Natural language parsing of messages |
| DB | SQLite (MVP) → Postgres (Supabase) for v2 | Free, simple, file-based |
| Hosting | Railway or Fly.io | $5/mo, easy deploys, persistent disk for SQLite |
| Scheduling | APScheduler in-process | No external cron needed |
| Payments | Venmo/Zelle deep links (no API) | No PCI, no auth headaches |
| Affiliate | Amazon Associates search links | Monetization without infra |

---

## 4. Architecture

```
Telegram Group Chat
       ↓
  Telegram Bot API (webhook or long-poll)
       ↓
  bot.py (dispatcher + handlers)
       ↓
  ┌────────────────────────────────────┐
  │  Intent Router                     │
  │  - /commands → direct handler      │
  │  - Free text → prefilter → Claude  │
  └────────────────────────────────────┘
       ↓
  Domain modules:
  - supplies.py
  - chores.py
  - bills.py
  - settle.py
       ↓
  SQLite (roomagent.db)
       ↑
  Scheduler (rent reminders, chore rotations, low-stock checks)
```

**Claude's role:** parse free-text like "I grabbed paper towels and some dish soap, like 18 bucks" into structured `{items: [{name, category}], total: 18.00, buyer: <user_id>}`. Use tool calling / structured outputs so the LLM returns JSON you can validate.

**Prefilter:** Don't send every group message to Claude. Only invoke when bot is @-mentioned, replied to, or message matches keyword regex (`$`, dollar amounts, `got`, `bought`, `paid`, `grabbed`, `picked up`). Include an `"ignore"` intent option.

---

## 5. Data Model

```sql
-- Households (one per group chat)
households (id, telegram_chat_id, name, created_at)

-- Roommates
users (id, household_id, telegram_user_id, name, venmo_handle, zelle_email, rent_share_pct)

-- Supply purchases
purchases (id, household_id, buyer_id, item, category, amount_cents, note, purchased_at)

-- Supply inventory (derived from purchases + flags)
supplies (id, household_id, name, last_purchased_at, typical_days_between, low_flag)

-- Recurring bills (rent lives here too, with is_rent=true)
recurring_bills (id, household_id, name, amount_cents, due_day, split_method, is_rent)

-- One-off bills (utilities)
bills (id, household_id, type, amount_cents, due_date, paid_by_user_id, split_method)
bill_shares (id, bill_id, user_id, amount_cents, paid)

-- Chores (Phase 2, schema-ready)
chores (id, household_id, name, recurrence_rule, assignee_strategy)
chore_completions (id, chore_id, assigned_to_user_id, completed_by_user_id, completed_at)

-- Action log for undo
actions (id, household_id, user_id, action_type, payload_json, created_at, reversed_at)
```

Balances are NOT stored — computed on demand from `purchases` and `bill_shares`.

---

## 6. Command Surface

**Slash commands (deterministic):**
```
/start            – onboard household, register chat
/join             – add yourself as a roommate
/bought <text>    – log a purchase (also works as free text)
/need <item>      – flag low stock, returns Amazon affiliate link
/chore <name>     – mark a chore done (Phase 2)
/rent             – status of current month
/owe              – show balances (DMs the caller)
/settle           – generate this month's settle-up
/undo             – reverse caller's last action
/help
```

**Free text → Claude:**
- "got TP and paper towels at Costco, $34" → logs purchase
- "I'll take trash tonight" → claims chore
- "who's up for dishes" → returns current rotation state
- "paid the electric bill, $87" → logs utility, splits, posts owed amounts
- "we're out of dish soap" → flags low stock + Amazon link

---

## 7. Folder Structure

```
roomagent/
├── README.md
├── PROJECT.md
├── DEVLOG.md
├── .env.example
├── pyproject.toml
├── src/
│   ├── bot.py              # entrypoint, dispatcher
│   ├── config.py
│   ├── db.py               # SQLite connection + migrations
│   ├── llm.py              # Claude API wrapper, intent parsing
│   ├── handlers/
│   │   ├── supplies.py
│   │   ├── chores.py
│   │   ├── bills.py
│   │   ├── settle.py
│   │   └── onboarding.py
│   ├── scheduler.py        # APScheduler jobs
│   └── utils/
│       ├── splits.py       # debt simplification algorithm
│       ├── amazon.py       # affiliate link helper
│       ├── prefilter.py    # regex prefilter for LLM gating
│       └── parsing.py
├── migrations/
│   └── 001_init.sql
└── tests/
    ├── test_intent_parsing.py
    ├── test_splits.py
    └── test_prefilter.py
```

---

## 8. Build Phases

**Phase 0 — Setup (1 evening)**
- Register bot with @BotFather, grab token
- Scaffold repo, install `python-telegram-bot`, `anthropic`, `apscheduler`
- Get a "hello world" echo bot running in test group

**Phase 1 — Onboarding & supplies (week 1)**
- `/start` registers household to chat_id
- `/join` adds users, stores name + venmo handle
- `/bought` and free-text purchase logging (Claude parses)
- `/need` flag with Amazon affiliate link
- Action log + `/undo`

**Phase 2 — Bills & balances (week 2)** *(swapped earlier than chores — money is the validating feature)*
- Rent setup at household creation (total, due day, splits) via `recurring_bills`
- Utility logging with auto-split
- Balance calculation, `/owe` command (DMs)
- Reminders: 3 days before rent, day-of utilities

**Phase 3 — Chores (week 3)**
- Recurring chore definitions
- Hybrid assignment: auto-assign at 6pm if unclaimed
- Daily scheduler posts assignments
- `/chore done dishes` marks complete, updates streaks

**Phase 4 — Settle-up & polish (week 4)**
- Monthly settle-up with debt minimization
- Venmo deep links: `venmo://paycharge?txn=pay&recipients=X&amount=Y&note=...`
- Error handling, retry on Telegram API failures
- Deploy to Railway with persistent volume
- Nightly SQLite backup to S3 or Telegram channel

**Phase 5 — Stretch**
- Web dashboard (Next.js)
- Receipt photo upload → Claude vision extracts items + total
- Spotify-style monthly "wrapped" recap
- Multi-household support
- Three modes: family, trip, long-term roommate

---

## 9. Key Implementation Notes

**Intent parsing prompt (sketch):**
```
You parse roommate chat messages into structured actions. Return JSON only.

Schema:
{
  "intent": "purchase" | "chore_claim" | "chore_done" | "bill_log" | "query_balance" | "low_stock" | "ignore" | "unknown",
  "data": { ...intent-specific fields }
}

Use "ignore" liberally for chitchat, reactions, off-topic messages.

Examples:
"got TP $12 costco" →
{"intent":"purchase","data":{"items":["toilet paper"],"category":"bathroom","amount_cents":1200,"note":"costco"}}

"I'll do trash tonight" →
{"intent":"chore_claim","data":{"chore":"trash","when":"tonight"}}

"lol same" →
{"intent":"ignore","data":{}}
```

Validate with Pydantic before touching the DB.

**Debt simplification:** classic algorithm — net out each pair, then run a greedy min-cash-flow. ~40 lines.

**Privacy:**
- Don't log message content beyond what's needed
- Store Venmo handles, not credentials
- `/owe` and balance queries DM the user; group only sees "settle-up posted, check DMs"
- Nightly SQLite backup

**Costs at small scale:**
- Telegram: free
- Claude API: ~$1–3/mo for a 3–5 person household
- Hosting: $5/mo Railway hobby
- Revenue: Amazon Associates ~1–4% on household purchases via affiliate links

---

## 10. Open Decisions (closed during planning)

- **Chores assignment:** hybrid — claim-first, auto-assign at 6pm if unclaimed
- **Default split:** equal (custom shares configurable)
- **Balance privacy:** DM individuals, not in-group
- **Low-stock alerts:** manual flag only for MVP; consumption-rate inference deferred

---

## 11. First Commit Checklist

- [ ] Create GitHub repo `roomagent`
- [ ] BotFather token → `.env`
- [ ] Anthropic API key → `.env`
- [ ] Amazon Associate tag → `.env`
- [ ] Echo bot responds in test group
- [ ] SQLite DB initialized with `001_init.sql`
- [ ] `/start` and `/join` working
- [ ] First purchase logged via free text
- [ ] `/undo` reverses last action
