# Tests

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_sync.py` covers the existing behaviors, including a chat reply's @names as mentions (only people on its project, never in a quote) and `render_reply`, and is unchanged by the layer split;
`tests/test_tools.py` covers the to-do and message tools, the to-do comment reader, the
boost readers on every surface, inbox delivery (with `fm-inbox.sh` stubbed), the
new commands and the layer boundary; `tests/test_notifications.py` covers notifications
with stubbed `/my/readings.json` and `/my/boosts.json`: the first run seeding without
replaying, each kind of notification running only its thread's reader (chats, mentions and
Campfire replies, tracked to-dos, mirrored cards, the agent's messages, check-in Reminders),
followed threads and mentions with their replies, a comment on a card whose task has left
the backlog, boosts, marking read only after the record is written and not after a failed
read, a failed mark retried, other projects left alone, unmonitored input once per kind
(old listener keys still applying), the timer's cadences and the upgrade from a `"listen"`
config; `tests/test_people.py` covers the `people` list: relaying
several people with who wrote each record, captain-only approvals and decisions, an
unchanged single-captain config and `init --listen-to` and `--operator` (someone only in
the account accepted and named, a miss or a tie in the account and a failed account read
refused, a re-run keeping the people and operators it does not name, `--replace-people`); `tests/test_pings.py` covers the Ping reader with a stubbed
`/my/readings.json`, the reply in a Ping (its @names as mentions of its own participants only, or as text when the people cannot be read), and a Ping's notification and boosts;
`tests/test_trust.py` covers operators and participants: roles, an operator's line,
decision, approval and to-do request authorizing, project members and email domains heard
as participants (clients, strangers and masked emails not), never through boosts,
assignments or unmonitored input, the project's people read once a run with its last list
standing in, a participant's Ping, `init`'s flags and `doctor`'s domain check;
`tests/test_acknowledge.py` covers acknowledgements: 👀/👍 by question without
`acknowledge`, its `read` boost on everything read and taken off by `reply`, its `decided`
boost on the captain's deciding comment (never a participant's) once `todo complete` or
`todo subtask` carries it out with the read boost taken off, once, a boost on a decision
step relayed as its answer, and `init`'s new-home default;
`tests/test_relay_ledger.py` covers the relay ledger: a `relayed` line per record with its
note's request id, kind, why, recording, text and agent, none on a dry run, a `skipped` line
beside the filter's log line, `answered` from `reply` and `--boost`, `dismiss` (the read boost
taken off and the outcome recorded, by the CLI, refused for an id nothing relayed, a no-op on
an item answered by `reply` or by a to-do command on its key, a dry run, an item from before the ledger, a `not_found` recording), the
note's dismiss line, the ledger kept out of the backup, and `sync.py stats`: counts per week
and in total, the noise rate, the top noise source first, `--since` and `--until`, answered
winning over dismissed and a to-do key answering only earlier items, and tokens from
transcripts (session total with each message once, subagents included, per-item spans
shared evenly, a request named by its `--todo` key, an unanswered span not attributed, a
missing directory, `--json`), and an outcome never reaching an item relayed after it;
`tests/test_schedule.py` covers when scheduled requests are done: `when` and `list` on a
dated request (`ask` until its list has an answer), the list's remembered answer winning
over the remembered default, the recurring default, nothing on an undated request or
without `schedule`, an edit and `todo show` carrying it, `sync.py schedule set|show` and
`init`'s new-home default;
`tests/test_decision_steps.py` covers `sync.py decide`: a comment and a step assigned to the
captain, tracked from then on, the captain's comment there relayed with `on`, their checking
the step recorded once (the agent's own never), the to-do or card itself never completed, a
step joining a tracked request without completing it, only the captain or an operator
deciding, a URL naming the project, cards, refusals, the dry run and the CLI;
`tests/test_decisions_anywhere.py` covers decisions anywhere: `todo create` in another
project (its own to-do set, or the config's `todos` `project` and `list`), its subtasks,
comments and completion there, a decision card in any column completed by moving it to
Done, `todo track --project`, the CLI flags, and notifications in another project relayed
or handled for the decisions put there, a decision card's own event never unmonitored;
`tests/test_defaults.py` covers the workflow defaults: the workflow moved from the base
prompt into `prompts/defaults.md`, `init` copying it once and never overwriting a home's
copy (a dry run only planning it), the backup keeping it, `doctor` flagging a home
without it, inbox notes giving facts and commands but never the handling, and basecamp-axi
reported as optional by `doctor` and `setup` and described as read-only in the vision,
defaults and bounds;
`tests/test_access_lists.py` covers `project_people` (heard in their project only, never in
a Ping), `"participants": {"company"}` (a colleague heard and an outsider not, one read per
person a run, the last company known standing in, an unknown company admitting no one and
getting no automatic reply, a company by id, a colleague's assignment neither a request nor
answered), `sync.py access` (people `init` found only in the account listed as operators
and people), and `init`'s account-aware default (a personal account the
captain only, the agent's own person and a client not making a team, a team account the
company, a failed read the captain only, an existing config kept, the flags overriding);
`tests/test_blocked.py` covers blocked senders: a stranger's @mention in a relayed or
unrelayed chat, a comment mention, a Ping and a notified assignment answered once in place
and never relayed, once per person and conversation a day, never the captain, the agent or
history, custom text, dry run, a failed reply, off by default for an existing config, and
`init`'s new-home default and flags;
`tests/test_received_boosts.py` covers boost confirmation: a listed boost recorded with
the feed's booster and text from one feed read a run, an unlisted one retried while recent
and then dropped, a feed entry by another booster or on another recording, a failed feed
read, a boost on someone else's recording, an assigned card's approval, and no feed without
a profile; `tests/test_runs.py` covers the run guard: claiming, refusing and naming another
home's live overlapping run (project, login by person or profile, Pings), logging it once
then hourly, stale, removed and recycled-pid claims, the override, dry runs, commands,
`sync.py status`, and `doctor`'s overlap check;
`tests/test_login_guard.py` covers the agent check: a run signed in as someone other than
the agent reads only `/my/profile.json` and writes no state (an established home and a new
one), a matching sign-in runs as before, a sign-in that changes while the run waits for the
state lock or for its run claim, an unreadable profile skipped (in a dry run too, writing
nothing), a config without `agent` refused with or without a profile, a dry run that only
prints, a profile read that times out, a lasting refusal logged once and then hourly (a new
cause at once, and one line when the agent signs in again), `backup`, `timesheet post`,
`watch` and every posting command (`reply`, `dismiss`, `todo`, `decide`, `ask`, `answer`,
`post-message`, `schedule set`, `unmonitored handle`) refused as someone else or without
`agent`, `refused()` comparing the login with the agent, an `agent` that is
not a number refused, and `init` refusing a new home without `--agent`, recording only the
`--agent` given or the config's, adding it to an older config without `--force` (never
over an old `"listen": {"unmonitored": false}`), and refusing a login that signs in as
someone else, and the sign-in command naming `BASECAMP_NO_KEYRING=1` only when the run had it;
`tests/test_login.py` covers the login guard (`sync.py renew`) with a stubbed `basecamp auth`
and a temporary `credentials.json`: a renewal that empties the file restored from the
backup (only the agent's own login), an operator signed in to the profile never backed up
or restored, a confirmed login backed up again without another profile read, a login that
cannot be confirmed (or a config without `agent`) not backed up, the default login (no
`profile`) copying and marking only its own entry, never a named profile's, an older copy never
confirmed as this config's agent not restored, a network-down run leaving the file untouched, only the lost profile put back beside
another login's newer entry, a restored login lost again not restored twice, the backup
retaken after a good renewal and keeping another profile's entry the file already lost, the
restore marks read and written under the CLI's locks, an emptied file not bringing back
another profile already restored once, a lost file-store login restored without
`BASECAMP_NO_KEYRING`, a keyring login renewed without the guard or a false FAILED line, no renewal far from expiry, and one FAILED line per cause;
`tests/test_assigned_todos.py` covers to-do requests with a stubbed `/my/assignments.json`:
the sweep, who assigned it, comments, boosts, edits and closing, a to-do not found (a
request closed once as `not_found`, an assignment's notification marked read, never retried,
while any other failed read is retried), reassignment, the agent
completing it, the `Assignment` and `Completion` notifications, and their notes, scheduled
and repeating to-dos (`due_on` and `repeats` on the records, a moved due date or changed rule
as an update, Basecamp's next occurrence recorded once as a request that `follows` the
completed one, a reused to-do, `todo show`), plus the account-wide
scope (a request in another project carrying its project, its comments, boosts, replies,
edits and closing in that project) and the unchanged project default;
`tests/test_assigned_cards.py` also covers a card listed only for the agent's step never being
fetched nor counted against `limit`, and a card not found (its notification handled once, its
request closed as `not_found`); `tests/test_assigned_steps.py` covers card step requests with fixtures shaped like Basecamp's
(a step nested in its card's `/my/assignments.json` entry, the step record, its notifications
subscribed to the step): discovery once and acknowledged, the card itself not a request,
someone else's or a stranger's step, the account scope and its claim, the card's comments
(old ones never, only an @mention, a Reply or the first after the agent's own), `todo
comment` and `reply` on the card, `todo complete` checking the step off once and counted
answered by `stats`, an edit, unassignment and reassignment, the owner's completion, a card
another reader has (and one whose reader is off), the assignment event as the comment
baseline and the assigner, two steps on one card relaying each comment once, a late Reply
keeping the wait on a newer comment, a stranger's assignment answered on the card, the `Assignment`, `Event` and `Completion` notifications (once, marked
read), unmonitored and never read as a to-do or card when off, a decision step's
completion, and the notes;
`tests/test_mentions_anywhere.py` covers `"mentions": {"scope": "account"}`: a mention on a
document and comments on a followed card in another project recorded with their project,
acknowledged, marked read, noted and answered there, the owner's boost on the agent's reply
there, a chat mention there answered in that chat and a chat line without one ignored, the
access list (a blocked stranger answered there, `people` heard everywhere and
`project_people` only in their project), off by default and with the project scope, a
malformed setting refused, the configured project's records unchanged, the run claim and
`doctor`'s check for two homes running it with one login, and the inbox note naming the
project; `tests/test_chat_watch.py` covers `sync.py watch` and `unwatch`: a `Chat`
notification for a watched chat in another project recorded as a `chat-question` carrying
its project, acknowledged, marked read and noted, and the same line in an unwatched chat
ignored, a watch read by the sweep without mentions anywhere, the first read starting after
`--after-line` (or the line the URL names, else the newest line), a watch relaying a plain
line without `?`, a delayed `Mention` of a line at or before the start never relayed (in
another project and in this one), watching again taking the boosts there now as its
baseline (counts and boost ids), a watched chat in a project another home on this login
reads skipped (logged once, the watch kept, never a refused run, read again once that home
stops) and a notification for it left alone, another login or a stale claim not skipping
it, `watch` warning up front when such a home is live (not for another login or a stale
claim), a malformed `--until` refused and a bad stored one ending the watch without stopping
the run, a watch started during the claim waiting for the run, a Ping refused, a reply
that comes as a `Mention` recorded once, the owner's boost on the agent's line there, `reply`
answering in that chat, `unwatch` stopping reads, a watch past its `--until` day reading
nothing and ending, refusals (a past day, another account, a configured chat, a URL that is
no chat), a dry run, a blocked sender answered only when they address the agent, and the
behavior's cadence; `tests/test_timesheet.py` covers timesheets: active time (short gaps
and tool waits counted, a tool wait lasting to its result across the lines between, a longer wait or one with no
result adding nothing, idle
gaps and an over-long tool wait not,
several transcripts merged and a symlinked copy counted once, only between start and
stop, a day cut at midnight in the config zone, New York by default, rounding to quarters), off
by default (no call, no file, no log line, the commands refused), the dry run posting
nothing, one entry per day and the ledger stopping a second, two keys on one to-do and day each posting, an entry already in Basecamp
only added to the ledger, a day waiting an hour past its end, an entry post tried once, a
settled key not read again, unmapped work
skipped and reported once and posted once it has a to-do, a mirrored card as the target, a
refused project reported once and then tried with one entry a day without FAILED lines, a
stopped key there keeping its days unposted, posting them with the daily try and then settling, and `start` keeping
them, a missing to-do tried daily alone while the project posts, an unreadable directory deferring the key, other failures retried each
run, no post as the owner, `timesheet start` and `stop`, `start` refusing a directory another
key is counting, and `init --timesheet`. The `basecamp`, `gh` and `lavish-axi` CLIs are stubbed, and `init`'s systemd and check
registration sit behind a fake; tests make no network calls and touch no real home.
`tests/test_init.py` also covers removing the retired listener service and `"listen"` key.
`tests/test_setup.py` covers `setup` (defaults, a project URL, interactive picks, the card
mirror creating tables and columns, the device-code sign-in, refusals, re-runs and a failed
test sync) and `doctor` (a healthy home and each failure: no config, a locked keyring, an
expired login, a missing column, a stopped timer, a leftover listener, the unread cap, a
failed or stale sync, an unregistered wake check, a missing home, two homes taking to-dos
account-wide with one login) with the CLIs, `systemctl` and `run.sh` stubbed.
