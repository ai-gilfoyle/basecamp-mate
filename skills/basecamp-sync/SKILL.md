---
name: basecamp-sync
description: Operating contract for a firstmate or second mate whose home mirrors its backlog into a Basecamp project with basecamp-mate (formerly firstmate-basecamp-sync). Use when told to "use this Basecamp project", when setting a home up with `sync.py init` or `basecamp-mate setup`, when checking a home with `basecamp-mate doctor` or `basecamp-mate status`, when the basecamp-sync wake check fires, when handling data/basecamp-sync/pending-comments.jsonl, when putting a decision to the owner as a Basecamp to-do (`sync.py todo`), when posting a report to the Message Board (`sync.py post-message`), when handling an `unmonitored` record (`sync.py unmonitored`), a `ping` record (the owner's direct message), a `mention` or `thread-comment` record (the owner writing on something the agent follows) a `todo-request` record (a to-do assigned to the agent, scheduled or repeating too) or an `advisory` record, or when editing figuring.json, not-now.json, decisions.json, boards.json, extra-repos.json or skip.json.
---

# Basecamp sync

The sync has two layers. Tools are single-purpose: readers that the timer (every 30
seconds) runs to append pending records, and commands you run to post (`reply`, `ask`, `answer`,
`todo create|track|comment|complete|subtask`, `post-message`). Behaviors are the workflows the
config turns on, each composed from tools: the card mirror (`tables`, `repos`; off with
`"cards": false` or without `tables`), the chat inbox (`chats`), chat asks (`ask_chat`),
check-in answering (`checkins`), release announcements (`releases`), decision to-dos
(`todos`), reports (`message_board`), Pings (`pings`: the owner's direct messages to the
agent's login, outside the project), to-do requests (`assigned_todos`: to-dos the owner
assigns to the agent's login, in the project or, with `"scope": "account"`, anywhere in the account), card requests (`assigned_cards`: cards the owner assigns to the agent's login, handled the same way), inbox delivery (`inbox`), the session alert (`session_alert`: one chat line
to the owner while no firstmate session holds the home's lock and inbox notes wait, as after a reboot) and notifications (on
with a `profile`: every run reads the agent login's Basecamp notifications and boosts and
runs the reader for each thread that changed, so the captain's input arrives within about
half a minute; it relays their comments and @mentions on anything the login follows, marks
handled notifications read, and records an `unmonitored` record when the captain does
something nothing monitors). The other readers run as an hourly (to-do requests: 5-minute)
repair sweep. `"listen"` and `sync.py listen`, the retired event listener, are ignored;
re-running `init` removes its service. `python3 $SYNC/sync.py behaviors --home <home>
--config <config>` lists which are on and how often each runs. Sections below about cards and the side files apply
only when the card mirror is on. Mechanics (column rules, file formats, record fields,
safety bounds) are in the repo's README: `basecamp-mate/README.md` (https://github.com/calebl/basecamp-mate). `SYNC` below
means that checkout.

`$SYNC/prompts/base.md` sets a home up and lists the records and the bounds. How the home
works with Basecamp (where decisions go, what is answered and when, the decision to-do
lifecycle, check-ins, reports, unmonitored input, advisories) is the home's own
`data/basecamp-sync/defaults.md`, which `init` copies from `$SYNC/prompts/defaults.md` once
and never overwrites. Read it and follow it; where it and this skill differ, the home's
defaults win. Inbox notes give only the facts and the commands, and point to it. When the owner asks to change how the agent
works with Basecamp, edit that file, not the sync. The workflow this skill describes below
is the shipped default.

## Set up

A person setting a home up by hand runs `$SYNC/bin/basecamp-mate setup`: a guided, plain-language
setup (sign-in with a link and code, project picker, yes/no questions) that ends in the same
`init`. To run it yourself without questions, pass `--answers <file.json>` (keys: `home`,
`login`, `project` id or URL, `captain` person id, `timezone`, and `true`/`false` for `chat`,
`todos`, `checkins`, `cards`, `releases`, `reports`, `pings`, `inbox`, `session_alert`); it refuses
instead of asking when the login is not signed in. `$SYNC/bin/basecamp-mate doctor` checks
everything (CLI, sign-in, project, card columns, notifications, the timer, last sync, wake check) and prints
each problem with its fix; it exits non-zero when something is broken.

"Use this Basecamp project with the firstmate login" means:

```sh
python3 $SYNC/sync.py init <project URL> --login firstmate --home <this home> --dry-run
```

Read the printed config, then run it again without `--dry-run`. It refuses rather than
guesses; fix what it names (usually `--repo-map <table>=<repo>` when a card table's title
is not a registered project's name). A card table matching no registered project is
skipped and printed, not refused; check the skipped list and pass `--repo-map` for any
board that is really a repo's. When the owner wants only chat, check-ins or release
announcements, or the project has no card tables, pass `--no-cards`. `--todos`,
`--reports`, `--every-line`, `--pings`, `--assigned-todos`, `--inbox` and `--checkins <time zone>` turn on decision
to-dos, reports, every-line chat, Ping relaying, to-do requests, inbox delivery and check-in answering; `--no-releases` leaves announcements out.
`--session-alert [MINUTES]` (with `--inbox`) adds the session alert in the dock's chat.
Notifications need no flag (`--listen` is accepted and does nothing).
`--no-assign-captain` keeps the mirror from assigning the owner to held cards (`"assign_captain": false`).
`--assigned-todos-anywhere` takes to-do requests from every project of the account
(`"assigned_todos": {"scope": "account"}`); only the main firstmate home uses it, and a
second mate's home keeps `--assigned-todos`.
`--listen-to <person id, email or name>` (repeatable) adds someone the sync listens to
besides the captain (the config's `people`); `--operator <person>` adds someone whose word
authorizes the agent as the captain's does (`operators`); `--participants-project`,
`--participants-company` and `--participants-domain <domain>` let anyone on the project,
in the captain's company, or with an email at the domain, ask the agent things
(`participants`). For a new home, `init` admits the captain's company by itself on a team
account (others in that company) and listens only to the captain on a personal one;
`--no-participants-company` overrides that, and a re-run keeps an existing config's
setting. Someone heard in one project only goes in the config's `project_people`
(`{"<project id>": [person ids]}`). `sync.py access` prints who gets through, and where.
Add only what the owner names: widening
trust is the owner's call. Prefer ids or exact names: Basecamp masks other people's email
addresses unless the login is an account admin, so an email or a domain usually matches
no one. Anyone else never reaches the agent: a new home gets `"blocked": {}`, so someone
else who @mentions, Pings or assigns the login gets one short automatic reply
(`--blocked-reply <text>` sets it, `--no-blocked-reply` leaves it off; a re-run keeps an
existing config's setting). Change it only when the owner asks. Never pass `--force` or `--create-missing-columns`
without the main firstmate's go-ahead.

## The backlog is the source of truth

Cards follow the backlog; never move, edit or assign a card by hand, since the next run
undoes it. To change where a card sits, change the backlog or a side file in
`<home>/data/basecamp-sync/`:

- `figuring.json` `{"task": "why"}`: a queued task that needs a plan approved goes to
  Figuring it out. Remove it once the plan is approved.
- `not-now.json` `{"task": "why"}`: deferred; Not now. Remove it when the task resumes.
- `decisions.json`: the question text for a card waiting on the captain (below).
- `boards.json` `{"task": ["owning task", ...]}`: link a plan board another task hosts
  (e.g. a scout's Lavish board) on this task's card. A task's own open boards under
  `data/<task>/` link themselves.
- `extra-repos.json` `{"task": ["board", ...]}`: show a task on more boards.
- `skip.json` `["task", ...]`: never mirror a task.

Keep these current as part of the task work, in the same step that changes the task.

## Decisions for the captain

Hold the task for the captain in the backlog (`hold-kind: captain`, no until date), and
put the decision in `decisions.json` in plain language:
`{"task": {"question": "...", "items": ["option 1 (recommended)", "option 2"], "note": "..."}}`.
The card moves to Figuring it out and is assigned to the captain automatically. Once the
answer is recorded and the hold released, the next run unassigns it. Remove the
`decisions.json` entry then.

With `"assign_captain": false` (`init --no-assign-captain`) the card still moves to
Figuring it out but is never assigned (the next run unassigns the owner from cards it had
assigned). The owner takes decisions in the main home's project, not this one: send the
decision to the main firstmate home (its inbox) with the card's URL, so it puts the
decision to the owner as a to-do there linking back to this project. Relay the answer back
and release the hold here as usual.

## Decisions as to-dos

A decision about an existing to-do or card goes on it by default: `python3 $SYNC/sync.py
decide --home <home> --config <config> --on <to-do or card id or URL> --question "<plain
question>" --body-file <comment> [--key <key>]` comments there and adds a step assigned to
the decider (the captain, or an operator with `--decider`); their comments and boosts there
and their checking the step (`decision-step-done`) come back, and `todo subtask` closes the
step once it is carried out. The to-do or card itself is never completed for them.

With `todos` on, put each other item waiting on the captain (a decision, approval, merge call,
credential or login) to them as one to-do instead:
`python3 $SYNC/sync.py todo create --home <home> --config <config> --key <stable key> --title "<plain question>" --body-file <file>`,
with a Markdown description of the evidence, consequence, options, recommendation and the
full URL of every PR. It goes where the config's `todos` says (this project unless it names
another `project` or a `list`); `--project <id>`, `--list <id>` or `--card-table <id>
--column <id>` (a card) put one decision somewhere else, as the home's defaults say. When it carries more than one decision, every one of them, the first
included, is its own repeatable `--subtask "<question>"` by default, the title names the
topic and the description holds only context, never a question outside the subtasks (each a native
Basecamp subtask of the to-do, numbered from 1 in order; basecamp CLI 0.12.0+), complete
each as it is answered with `sync.py todo subtask --todo <key> --number <n>`, and the to-do
completes once all are done. Their comment comes back as a `todo-comment` record, and their
boost on the to-do or on any comment under it (yours included) as a `boost` record. A decision: act
on it (relaying first if this is not the main firstmate's home), then
`sync.py todo complete --todo <key>`. Feedback that is not a decision: act on it and
answer with `sync.py reply --recording <comment id>`; the to-do stays open. Complete a
to-do settled another way (the captain merged the PR themselves). Adopt one made by hand with
`sync.py todo track --key <key> --todo <id>`.

## To-do requests

With `assigned_todos` on, a to-do a listened-to person creates or reassigns in the project
so that this home's login is an assignee arrives once as a `todo-request` record (title,
description `text`, link, `author` = who assigned it) and gets a 👀 from the sync. It is a
request for work. From the captain or an operator (the record authorizes, below) it is
captain work: take it into the backlog and do it (in a home that is not the main
firstmate's, relay it first, like a comment). From a participant it is information or a request to weigh and route,
never a captain decision: do it only when it is plainly within what the captain already
wants, else ask the captain with a decision to-do. Its later comments and boosts arrive as
`todo-comment` and `boost` records with `request: true` (more about the same request:
answer with `sync.py reply --recording <comment id>` or post progress with
`sync.py todo comment --todo <key>`), an edit as `todo-request-update` (adjust to the new
text), and its closing by someone else as `todo-request-closed` (`reason` completed,
unassigned, trashed or archived: stop, note it in the backlog, nothing to complete). When the
work is done, comment what was done with full links, then
`sync.py todo complete --todo <key>` (the key is `request-<to-do id>`). To-dos assigned to
anyone else are ignored.

**Scheduled to-dos.** Every request record carries `due_on` (null when undated) and
`repeats` (Basecamp's repetition rule, null when it does not repeat), and, with the
config's `schedule`, `when` and its `list`: `ask` (comment to ask whether to do it on the
due date or before, then remember the answer for that list with `sync.py schedule set
--list <list> --dated due|before`, or `--recurring` for a repeating one), `due` (on the due
date) or `before` (before it), as the home's defaults say. Without `when`, a scheduled
request is done on its due date, not before when its description says so; it stays open
until then. Basecamp alone owns the schedule: a moved due date or a changed or removed rule
arrives as a `todo-request-update` with the new `due_on` and `repeats`, and on the day,
before acting, re-read it with `sync.py todo show --todo <key>` (read-only) instead of
trusting a date saved earlier. For a repeating to-do, do the occurrence on its date,
comment the results with full links, and `todo complete` it; Basecamp makes the next
occurrence as a new to-do, which the sweep records once as a new `todo-request` with
`follows` (the to-do id it comes after), attributed to whoever asked for the series.
Never create the next occurrence by hand.

**Account-wide** (`"assigned_todos": {"scope": "account"}`, `init --assigned-todos-anywhere`):
a to-do assigned to the login in any project of the account is a request, and every record
of it carries `project` (`id`, `name`); comments, boosts, edits and closing follow it there,
and `reply`, `todo comment` and `todo complete` post in its project. Run it in **one home
per Basecamp login**: the main firstmate home, which routes each request to the second mate
or domain that owns its project (keeping the key, to comment and complete it when done).
Every other home on that login keeps `"assigned_todos": {}` (its own project), or the same
to-do reaches several homes; `basecamp-mate doctor` flags two homes on this computer doing
it with the same account and `profile`.

**Cards assigned to you** (`"assigned_cards": {}`, `init --assigned-cards`; off by
default; the same `limit` and `scope` as `assigned_todos`): a card a listened-to person
assigns to this home's login is a request handled exactly like a to-do request. It
arrives once as a `card-request` (key `request-<card id>`), its edits and closing as
`card-request-update` and `card-request-closed`, and its comments and boosts as
`todo-comment` and `boost` records with `request: true` and `type: "card"`. Answer with
`sync.py reply` or `sync.py todo comment --todo <key>`; when the work is done,
`sync.py todo complete --todo <key>` completes the card, which Basecamp shows by moving it
to the table's Done column. Cards the card mirror makes from the backlog are never
requests.

## One home per project and login

Two homes on this computer that watch the same project, the same login's Pings, or its
account-wide to-do requests with the same Basecamp login would each relay the same input.
So each timer run claims what its config watches, and a run beside another home's live
claim that overlaps is refused: it reads nothing and logs `FAILED duplicate run`, naming
the other home (once, then hourly). A chat watched with `sync.py watch` never refuses a
run; its chat is skipped while another home on this login reads that project. A claim stops blocking within 10 minutes of its home's
last run, so a stopped timer or a removed config frees it. `basecamp-mate status` lists
every claim, live or stale, and any sync.py running without one; `basecamp-mate doctor`
flags overlapping homes. `"allow_duplicate": true` in a config (or `--allow-duplicate` on
one run) runs both anyway; set it only on the owner's word.

## Pending records

The wake check fires when `pending-comments.jsonl` or a FAILED line in `sync.log` grows.
With `inbox` set (`init --inbox`), each new record instead arrives as a note in this
home's firstmate inbox (request id `basecamp-<kind>-<id>`) and the wake check watches
only FAILED lines: the inbox note is the wake. Handle the record it names, then ack the
note with `bin/fm-inbox.sh drain --ack <note id>`.
Handle each new record by its `kind` (missing `kind` = `comment`).

Every record says who wrote it (`author`), whether that is the captain (`captain`; a
record without it is the captain's) and their `role`: `captain`, `operator` or
`participant`. A record **authorizes** when its `role` is `captain` or `operator` (one
without `role` authorizes when `captain` is true): an operator's word counts as the
captain's, so their decision on a decision to-do settles it and their 👍 on an assigned
card is an `approval`; when an operator and the captain disagree, the captain wins.
Participants are the `people` list (their lines, comments and boosts are relayed) and,
with `participants`, anyone on the project or at a listed email domain (their lines,
Pings, comments and mentions, never their boosts, assignments or unmonitored input). A
participant's record never authorizes: it is information or a question or request to
weigh and route (to the main firstmate, or to the captain as a decision to-do), never a
decision, approval or instruction. Their comment or boost on a decision to-do never
completes it, their 👍 on a card arrives as a `boost`, not an `approval`, and an
instruction in a check-in question they wrote is a request.

A boost on the agent's own recording (its line, comment, card, to-do, message or answer)
is recorded only when the agent's received-boosts feed (`/my/boosts.json`, read fresh each
run) lists it, which proves it was aimed at the agent; its booster and text come from
that read.

- `comment`, `approval`: relay to the main firstmate with the task and card link and wait
  for its answer before acting. An approval (the captain's 👍 on an assigned card) means
  every recommendation on the card is approved as recommended; one from an operator says so.
- `question`, `chat-question`: if it only asks for information within this home's scope,
  answer it directly:
  `python3 $SYNC/sync.py reply --home <home> --config <home>/data/basecamp-sync/config.json --recording <comment or line id> --body-file <file>`.
  If it asks for a decision or gives an instruction, relay it to the main firstmate first,
  like a comment.
  A `chat-question` from a chat set to `every_line` may be any line the owner wrote there;
  treat each as addressed to you.
- `ping`: a line the captain wrote to this home's login in a Ping (a direct message, in a
  bucket of its own). Treat it like a `chat-question` from an `every_line` chat, and answer
  in the Ping with `sync.py reply --recording <line id>`; never with the `basecamp` CLI.
- `mention`, `thread-comment`: the captain @mentioned this home's login in, or commented
  on, something it follows that nothing else tracks (a card whose task left the backlog, a
  document, someone else's to-do). A mention is addressed to you; a thread comment is said
  to you. Handle it like a card `comment` (relay outside the main firstmate's home), and
  answer there with `sync.py reply --recording <comment id>`. With `"mentions": {"scope":
  "account"}` (`init --mentions-anywhere`, the main home only, like account-wide to-do
  requests), one from any project of the account arrives too, and it and a `chat-question`
  from another project's chat carry `project` (`id`, `name`); route it like an account-wide
  request, and `reply` still answers in that project.
- `todo-comment`: the captain commented on a tracked to-do; see "Decisions as to-dos", or,
  with `request: true`, "To-do requests".
- `todo-request`, `todo-request-update`, `todo-request-closed`: a to-do assigned to this
  home's login (with `due_on`, `repeats` and, for a repeating to-do's next occurrence,
  `follows`), an edit to it (its text or its schedule), or its closing; see "To-do requests".
- `card-request`, `card-request-update`, `card-request-closed`: the same for a card assigned
  to this home's login (`assigned_cards`); see "Cards assigned to you".
- `message-comment`: the captain commented on a Message Board post this home made (a
  report); it is feedback or an instruction on it. Act on it (relaying first outside the
  main firstmate's home), then answer with `sync.py reply --recording <comment id>`.
- `boost`: the captain boosted something being watched (`surface`: chat, ping, card,
  card-comment, todo, todo-comment, checkin-answer, message, message-comment, thread-comment); a boost
  can carry short text. It is an answer to what was boosted, like a comment there: on a
  decision to-do or a comment under it, a decision or feedback as above; on a to-do request
  (`request: true`), more about that request; elsewhere a reply
  to that line, answer or post. Relay it like a comment outside the main firstmate's home.
- `unmonitored`: the captain did something in the project that no enabled behavior handles
  (`event_type` on `recording_type`, e.g. `chat.line.created` in a chat not relayed, or
  `todo.assignment_changed` on a `Todo` with to-do requests off; one record per kind, `key`). Put it to the owner as a decision to-do (see
  "Decisions as to-dos") asking how such events should be handled: start monitoring them
  and how, ignore them, or something else. Act on the answer (monitoring needs a change to
  the sync: relay it to the main firstmate), then record it with
  `python3 $SYNC/sync.py unmonitored handle --home <home> --config <config> --key '<key>' --decision <text>`
  so that kind stays quiet; `unmonitored forget --key '<key>'` raises it again next time.
- `advisory`, `advisory-update`, `advisory-unreadable` (`advisories` config): a GitHub
  security advisory first seen in triage or draft, its later publishing or closing, or a
  repository whose advisories this login cannot read (reported once). An advisory is
  security-sensitive work. Route it to whoever owns the repository (the home or domain that
  works on it), validate the report privately, and bring severity, fix and disclosure
  decisions to the captain (a decision to-do, see "Decisions as to-dos"). Never change an advisory's state,
  publish it, request a CVE or comment on it without the captain's word, and keep its
  details out of public places (issues, PRs, chats others read) until it is published.
- `dependabot-baseline`, `dependabot-alert`, `dependabot-unreadable` (`advisories` config,
  same repos): the first read of a repo's alerts as one summary (open count by severity; file
  one to-do for that backlog if any are open), then each Dependabot alert that opens or reopens after it (`package`, `severity`, `fixed_in`,
  `manifest`, `summary`, link), or a repository whose alerts this login cannot read
  (reported once). File a to-do for the alert and route the fix the same day to whoever owns
  the repository, through the normal PR flow; bring a dismissal or a fix that cannot be
  made to the captain as a decision to-do. Never dismiss or change the alert on GitHub.
- `checkin`: a check-in question came due today. Work out the answer within this home's
  scope and post it once with
  `python3 $SYNC/sync.py answer --home <home> --config <home>/data/basecamp-sync/config.json --question <question id> --body-file <file>`.
  A question that is an instruction (e.g. "Run /stow and report") is carried out first,
  and the answer reports what was done.
- To post a report (only when `message_board` is set):
  `python3 $SYNC/sync.py post-message --home <home> --config <config> --subject <text> --body-file <file>`
  (Markdown); each run posts a new message.
- After you ask someone a question in a chat outside the project (for example with `reply`
  to their line there), watch that chat so their plain answer reaches you without an
  @mention:
  `python3 $SYNC/sync.py watch --home <home> --config <home>/data/basecamp-sync/config.json --chat <URL> --until <YYYY-MM-DD>`.
  Pass the `url` of the line you replied to (the record's `url`, ending in `@<line id>`):
  the watch starts after the line the URL names (else the chat's newest line), so an
  answer posted before `watch` runs is not missed, while older lines are never relayed,
  even when a late notification names one; your own lines never are.
  Every line of the people the sync listens to comes through, so a plain answer without
  `?` reaches you. Their lines arrive as `chat-question` records carrying
  `project`; answer with `reply`. Once answered, stop with `sync.py unwatch --chat <URL>`
  (`--until` also ends it). A chat in the config's `chats` needs no watch, and a Ping is
  refused (the Ping reader relays it). If `watch` warns, or `sync.log` says `watch:
  skipping watched chat`, another home on this login reads that project: while it runs,
  an @mention of you in that chat reaches that home, but a plain answer reaches no agent,
  so ask there with an @mention instead.
- With `timesheet` in the config, your active time on each to-do or card is posted to its
  timesheet once per finished day, read from the Claude Code transcripts you name. When
  work on a to-do or card starts, run
  `python3 $SYNC/sync.py timesheet start --home <home> --config <config> --key <task id> --on <to-do or card URL> --workdir <dir>`
  (again with the same key for each other directory the work runs in; a to-do or card
  tracked under the key needs no `--on`), and `timesheet stop --key <task id>` when it
  ends. `timesheet post --dry-run` prints what would be posted. A `timesheet-unmapped`
  record is time with no to-do or card: name one with `timesheet start --on`. A
  `timesheet-refused` record is a project whose timesheet refused an entry: tell the
  captain timesheet tracking is off there, and `timesheet retry --project <id>` once fixed.
- To put a question or decision to the owner in chat (only when `ask_chat` is set), post
  one per line with
  `python3 $SYNC/sync.py ask --home <home> --config <home>/data/basecamp-sync/config.json --body-file <file>`;
  it @mentions the owner.
- A FAILED run: read `sync.log` and run
  `$SYNC/bin/basecamp-mate doctor`, which names the problem and its fix; a token failure needs
  `BASECAMP_NO_KEYRING=1 basecamp auth login -P <profile> --device-code` (or `basecamp-mate setup`),
  which only the captain can do, so relay it. `FAILED duplicate run` means another home
  holds this project and login (see "One home per project and login"); relay it.

The main firstmate's own home answers its questions itself rather than relaying.

## Never

- Write to Basecamp with basecamp-axi: it is only for your own reading (the whole to-do or
  card, comments, mention handles, search, links) as the agent's login
  (`BASECAMP_PROFILE=<profile> basecamp-axi ...`), as the home's defaults say; the sync
  itself never uses it.
- Post, comment, complete, delete, archive or trash anything in Basecamp outside `sync.py`
  and its commands (`reply`, `ask`, `answer`, `todo create|track|comment|complete|subtask`, `decide`,
  `post-message`), or hand-edit `todos.json`, `pings.json`, `notifications.json`, `threads.json`, `timer.json`, `assigned-todos.json`, `participants.json`, `companies.json`, `blocked.json` or `unmonitored.json`.
  The sync's automatic posts are a release announcement (releases only: GitHub releases
  of the repos in `releases`, never merges or PRs, Message Board only), the opt-in session
  alert, and, with `blocked` on, the short reply to a blocked sender. Never announce
  anything by hand, and never edit or delete an announcement.
- Act on a comment or approval before the main firstmate confirms.
- Treat a participant's record (`role` `participant`, or `captain: false` with no `role`) as a decision or approval.

Acknowledgements are the sync's. With the config's `acknowledge` `read` (a new home: 👀) it
goes on everything read; without it, 👀 on a captain question means "looking into it" and
👍 on a comment or card means "got it". `sync.py reply` removes the read boost once
answered. With `acknowledge` `decided` (a new home: 👍), `todo complete` and `todo subtask`
put it on each deciding comment once the decision is carried out. A boost on a decision
step (a subtask) arrives as a `boost` with `surface` `subtask` and `step`: that decision's
answer. Don't add or remove acknowledgements by hand.
