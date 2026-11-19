# How it works

## What it is

Connects an agent home to one Basecamp project through the `basecamp` CLI, in two
layers. No model calls.

The sync makes its own direct calls to the `basecamp` CLI (JSON in, JSON out) and never
uses [basecamp-axi](https://github.com/calebl/basecamp-axi). basecamp-axi is an optional
companion for the agent: when it is installed, the shipped defaults have the agent read
Basecamp with it as its own login (`BASECAMP_PROFILE=<profile> basecamp-axi ...`) when a
record is not enough, while every write still goes through the `sync.py` commands.
`setup` and `doctor` say whether it is installed; nothing requires it.

## Two layers: tools and behaviors

**Tools** ([`tools.py`](../tools.py)) are small, explicit, single-purpose operations with no
policy. Each does one thing to the configured account and project:

| Tool | Kind | What it does |
| --- | --- | --- |
| card comment and 👍 readers | reader | record the captain's new card comments (`comment`, `question`) and their 👍 on an assigned card (`approval`) |
| chat reader | reader | records the captain's chat lines (questions and mentions, or every line) as `chat-question` |
| check-in reader | reader | records each check-in question due today as `checkin` |
| to-do comment reader | reader | records the owner's new comments on tracked open to-dos as `todo-comment` |
| notification reader | reader | reads the agent login's notifications (`/my/readings.json`: every project, one call) and hands each one that changed to a behavior; records nothing itself |
| boost reader | reader | reads the boosts on the agent login's own recordings (`/my/boosts.json`, one call) and hands each new one to a behavior; records nothing itself |
| mark read | primitive | marks the agent login's notifications read (`basecamp notifications read`, i.e. `PUT /my/unreads.json`) |
| Ping reader | reader | finds the Pings (direct messages) the agent's login is in with the owner through `/my/readings.json` and records each new line the owner writes there as `ping` |
| thread reader | reader | records the owner's new comments on any other thread the agent's login follows or is @mentioned in (a card whatever its backlog state, a document, an upload, someone else's to-do or message) as `mention` when they @mention or Reply to the agent, and as `thread-comment` only when the thread awaits the agent's answer or the agent made it (see "Followed threads" in [notifications.md](notifications.md); it skips the rest), and a mention in a chat nothing relays as `chat-question`; in the project, or, with `"mentions": {"scope": "account"}`, any project of the account |
| to-do request readers | reader | find the open to-dos in the project (or, with `"scope": "account"`, any project of the account) assigned to the agent's login (`/my/assignments.json`, or a to-do event) and record each one a listened-to person assigned as `todo-request`, once; then its edits and closing as `todo-request-update` and `todo-request-closed`; the same for cards (`card-request`) and card steps (`step-request`, read from the `children` of their card's entry there) |
| message comment reader | reader | records the owner's new comments on the agent's own recent Message Board posts as `message-comment` |
| boost readers | reader | record the owner's new boosts, with their text, on every monitored surface as `boost` ("Boosts are answers" in [safety-bounds.md](safety-bounds.md)); a boost on the agent login's own recording only once its received-boosts feed lists it |
| unmonitored recorder | reader | records owner input that no behavior handles as `unmonitored`, once per kind of thing (see [Unmonitored input](notifications.md#unmonitored-input)) |
| `sync.py reply` | command | answers a recorded comment, chat line, Ping line, to-do comment, thread comment or mention where it was made (with `--boost`, as a boost of 16 characters or fewer on it), then removes the 👀; in a chat or Ping line, an `@First.Last` or `@First` that names one person the agent's login can @mention on that chat's project or in that Ping, outside a quote, becomes a real mention; with `--awaiting`, on a followed thread, the next comment there is relayed |
| `sync.py dismiss` | command | says a relayed item was not for the agent: takes the 👀 (the read boost) off it, as `reply` would, and records it dismissed in the relay ledger (see [Relay ledger and stats](relay-ledger.md)) |
| `sync.py stats` | command | reports the relay ledger: per day, week or month, the items relayed, answered, dismissed, still open and skipped, the noise rate, its sources by kind and why, and the tokens spent; local files only, read-only (see [Relay ledger and stats](relay-ledger.md)) |
| relay ledger | primitive | appends one line per item recorded for the agent, per item the relay filter skips, and per outcome (answered, dismissed) to `relay.jsonl`; local only |
| `sync.py ask` | command | posts a new chat line @mentioning the owner |
| `sync.py watch`, `sync.py unwatch` | command | starts or stops watching one chat in any project of the account (kept in `chats.json`, not the config): its listened-to lines after a given line are recorded as `chat-question` (see "Chat watches" in [safety-bounds.md](safety-bounds.md)) |
| `sync.py answer` | command | answers a check-in question, once per question per day |
| `sync.py todo create` | command | creates a to-do assigned to the owner, with a description, and tracks it under a key: in the configured decisions project and list, or any project (`--project`), list (`--list`) or card table column (`--card-table --column`, a card) |
| `sync.py todo track` | command | tracks an existing to-do (in any project, `--project`) under a key, without relaying its old comments |
| `sync.py todo comment` | command | comments on a tracked to-do |
| `sync.py todo complete` | command | completes a tracked to-do (a decision to-do, or a to-do request when the work is done), then reads it back and fails unless Basecamp shows it completed (for a decision `decide` put on a to-do or card, only its decision steps are completed and read back; that to-do or card stays open); the agent's own to-do is refused while Basecamp lists one of its subtasks open; a decision to-do made outside `todo create` is adopted with `todo track` first |
| `sync.py todo subtask` | command | completes one native subtask (decision) of a multi-decision to-do, or a decision step, and reads it back; a decision to-do completes once all are done (never a request, or a to-do or card a step was only put on), and running it again on the last one completes a to-do still open |
| `sync.py schedule show\|set` | command | prints, or remembers per to-do list (or by default), when dated and repeating requests are done (`ask`, `due`, `before`); local state only |
| `sync.py decide` | command | puts a decision on the to-do or card it is about (any project): a comment there and a step (a native subtask) assigned to the decider, the captain or an operator; their comments, boosts and checking the step (`decision-step-done`) come back; the to-do or card itself is never completed |
| `sync.py todo show` | command | reads a tracked to-do as Basecamp has it now (open or not, due date, repetition rule), read-only |
| `sync.py post-message` | command | posts a message (subject and body) on the configured message board |
| `sync.py status` | command | lists the sync runs on this computer, what each watches and with which login, and any `sync.py` running unrecorded (see [One home per project and login](multiple-homes.md)) |
| `sync.py access` | command | prints who gets through to the agent, and where; read-only (see [People the sync listens to](people.md)) |
| `sync.py timesheet start\|stop\|post` | command | starts or stops counting the agent's active time on a key (a to-do or card, and the Claude Code transcripts the work is in; local state only), or posts the finished days now (`--dry-run` prints them) (see [Timesheets](timesheets.md)) |
| transcript reader and timesheet entry | reader, primitive | reads the event times of Claude Code transcripts (local files only); reads the agent's timesheet entries on a to-do or card for a day (`basecamp timesheet item`) and logs one (`basecamp timesheet entry create`) |
| `sync.py unmonitored list\|handle\|forget` | command | lists the unmonitored-event keys, marks one handled with the owner's decision, or drops one so it is raised again |
| inbox note | primitive | queues a note in a firstmate home's inbox through its `bin/fm-inbox.sh note --request-id` |
| card, Message Board and boost primitives | primitive | create, update, move, assign and unassign a card; post a board message; add an acknowledgement boost |

A reader polls and appends what it finds to `pending-comments.jsonl`, once each, keeping a
cursor or seen-list beside the config; it never acts on what it reads. A command posts
exactly what the agent hands it, and only when the agent runs it.

**Behaviors** ([`behaviors.py`](../behaviors.py)) are opt-in workflows a home turns on in its
config, each composed from tools. A behavior whose keys are absent makes no calls at all.
Most have a timer step; the others are carried out by the agent with the commands. The
agent's side of each is in [`prompts/base.md`](../prompts/base.md).

The timer runs `run.sh` every 30 seconds, and each behavior's step runs when it is due
(`timer.json` keeps when each last ran): `notifications` and `inbox-delivery` every run;
`card-mirror`, `release-announcements`, `checkin-answering`, `assigned-todos`, `assigned-cards` and
`assigned-steps` every 5 minutes; `security-advisories` and `timesheet` hourly; `backup` daily; and `chat-inbox`, `chat-watch`, `decision-todos`, `reports` and
`pings` hourly, as the repair sweep behind `notifications`, which reads their surfaces as soon as something changes.
Without `notifications` (no `profile`, or `"notifications": false`) those five run every
5 minutes instead. `sync.py --all` runs every step at once.

| Behavior | Config keys | `init` flag | Timer step | Agent side |
| --- | --- | --- | --- | --- |
| `notifications` | `profile` (off with `"notifications": false`), `mentions` | default (`--mentions-anywhere`: @mentions and followed threads account-wide) | every run: the notification and boost readers, then the reader for each thread that changed; mark read | records arrive within about 30 seconds; an `unmonitored` record becomes a decision to-do |
| `card-mirror` | `tables`, `repos` (off with `"cards": false`), `assign_captain` | default (`--no-cards` to leave off; `--no-assign-captain` never assigns the owner) | mirror the backlog; card comment and 👍 readers (5 min) | relay comments and approvals; `reply`; with `"assign_captain": false`, send each decision to the main home instead |
| `chat-inbox` | `chats` | default; `--every-line` | chat reader (hourly sweep) | `reply` to each line |
| `chat-watch` | none: the watches in `chats.json` (`sync.py watch`) | none | chat reader for each watched chat, in its own project (hourly sweep); each run first ends a watch whose `--until` day has passed or is not a date | `watch` after asking a question in a chat outside the project; `reply` to each line; `unwatch` once answered |
| `chat-asks` | `ask_chat` | by hand | none | `ask` |
| `release-announcements` | `releases` | default, when the dock has one message board | post one message per new GitHub release | none |
| `security-advisories` | `advisories` | `--advisories <owner/name>` (repeatable) | record each new GitHub security advisory and each new or reopened open Dependabot alert once (hourly) | route an advisory to the repo's owner, with severity, fix and disclosure decisions going to the captain; file a to-do for a Dependabot alert and route its fix the same day |
| `checkin-answering` | `checkins` | `--checkins <time zone>` | check-in reader (5 min) | `answer` |
| `decision-todos` | `todos` | `--todos` | to-do comment reader (hourly sweep) | `todo create`, `reply`/`todo comment`, `todo complete` |
| `assigned-todos` | `assigned_todos` | `--assigned-todos` (`--assigned-todos-anywhere`: account-wide) | to-do request readers, then the to-do comment reader for the requests (5 min) | take each request on; `todo comment`/`reply`; `todo complete` when done |
| `assigned-cards` | `assigned_cards` | `--assigned-cards` | the to-do request readers for cards assigned to the agent, then the to-do comment reader for those requests (5 min) | as `assigned-todos`; `todo complete` moves the card to Done |
| `assigned-steps` | `assigned_steps` | `--assigned-steps` | the to-do request readers for card steps assigned to the agent, then the comments on their cards that concern the agent (5 min) | as `assigned-todos`; `todo comment` comments on the card; `todo complete` checks the step off |
| `reports` | `message_board` | `--reports` | comment and boost readers on the agent's messages (hourly sweep) | `post-message`; `reply` to feedback |
| `pings` | `pings` | `--pings` | Ping reader (hourly sweep) | `reply` to each line, in the Ping |
| `inbox-delivery` | `inbox` | `--inbox` | deliver each new pending record as a firstmate inbox note | handle the note, then `fm-inbox.sh drain --ack` |
| `session-alert` | `session_alert` | `--session-alert [MINUTES]` (with `--inbox`; setup asks) | tell the owner once in chat when no firstmate session holds the lock while inbox notes wait (each minute) | start the session again |
| `backup` | `backup` | `--backup <Docs & Files folder URL>` (setup keeps an existing one) | upload the config, state files and `defaults.md` that changed to the folder, one latest copy of each (daily) | `sync.py backup` to back up now |
| `timesheet` | `timesheet` | `--timesheet` (a re-run keeps an existing setting) | post the agent's active time per to-do or card per finished day to its timesheet, once (hourly) | `timesheet start` when work starts, `timesheet stop` when it ends; tell the captain about a refused project |
| `blocked-replies` | `blocked` | default for a new home (`--blocked-reply <text>`; `--no-blocked-reply` to leave off; a re-run keeps an existing setting) | none of its own: the readers post the automatic reply to a blocked sender; the timer step only starts its clock | none: nothing a blocked sender writes reaches the agent |

The boundary: behaviors never run a CLI themselves (every Basecamp, GitHub, backlog or
Lavish call goes through a tool), and tools never decide whether to run or what to do
with what they read. `sync.py behaviors --home <home> --config <config.json>` lists which
behaviors a config turns on and how often each runs.

Every piece shares `account`, `project`, `captain` (the owner's person id), `agent` (the
agent's person id, which the login must sign in as) and the optional `profile`. A config
without `tables` (or with `"cards": false`) never reads the backlog, never calls a card or
card-table endpoint, and never writes `map.json`; the other
behaviors run the same either way. Set such a home up with `sync.py init --no-cards`.
