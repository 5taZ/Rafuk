# Account limits

| Active plan | AI requests in 5 hours | AI requests in 7 days | Running trackers |
|---|---:|---:|---:|
| Free | 0 | 0 | 3 |
| Pro | 20 | 100 | 10 |
| Max | 60 | 350 | 10 |
| Ultra | 150 | 1000 | 10 |

## Pro trial

A new account gets Pro for 3 days once it has accepted the current user
agreement and Telegram has confirmed its `REQUIRED_CHANNEL` subscription
(an unanswered membership check grants nothing; admins never get it). The
Mini App announces the trial once (`status.is_trial` in `/profile/me`).
The trial is granted once per Telegram account: `pro_trial_claims` keeps an
HMAC of the Telegram ID (key derived from `BOT_TOKEN`) that account erasure
does not delete, so a deleted and re-created account gets no second trial.
Accounts that existed before the trial never get it. Rotating `BOT_TOKEN`
would make the recorded claims unrecognisable.

## Defaults

These are defaults. The admin status editor configures the two AI limits
and tracker capacity separately for each active role. Free cannot enable AI.
Both AI limits must be positive, or both zero; the weekly limit must be
at least the five-hour limit.

AI analysis, negotiation, price advice and the seller assistant share one
request budget. Each delivered result costs one request in both windows.
Cache hits and joins to an analysis already in progress are free. Provider
failures, cancelled work and requests that deliver no result are refunded.
Delivered market fallbacks retain the existing charging policy.

The windows roll continuously. A request at 12:00 stops counting against
the short window at 17:00 and against the weekly window seven days later
at 12:00. There is no midnight/Monday reset. The profile shows remaining
requests and the next recovery time in Europe/Minsk. Changing plans does
not clear usage. If both windows are exhausted, the user must wait for
capacity in both.

Redis reserves both windows in one Lua operation. A request identifier
prevents duplicate reservations and refunds. An unavailable quota store
blocks paid AI work temporarily; the profile marks usage as unavailable.
Consent revoke preserves usage, while full account erasure removes it.

Tracker capacity counts active, unpaused trackers. Pause/delete releases a
slot; create/resume checks capacity. Excess trackers are paused after a
role change or subscription expiry. They remain saved and can be resumed
when capacity is available.

The stable role codes remain bare_search (Free), scout (Pro), flipper
(Max) and market_maker (Ultra). Former shark subscribers move to Ultra
with their expiry and note intact. The retired shark row is archived,
and old role-code requests resolve to Ultra. Existing tracker settings
are preserved; merging the top roles keeps their larger tracker limit.
