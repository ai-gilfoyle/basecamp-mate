# basecamp-mate

> Formerly `firstmate-basecamp-sync`. GitHub redirects the old repository URL; existing
> installs keep working unchanged (the `basecamp-sync` skill, config keys and systemd unit
> names are the same).

Source: <https://github.com/calebl/basecamp-mate>

## Getting started

You need a Linux computer where your firstmate agent runs, and a Basecamp project. Open a
terminal and run:

```sh
git clone https://github.com/calebl/basecamp-mate.git
basecamp-mate/bin/basecamp-mate setup
```

Setup walks you through everything, one question at a time:

1. It checks for what it needs and offers to install the Basecamp command-line tool if it
   is missing. basecamp-mate needs version 0.12.0 or newer (the first with subtasks); setup
   stops on an older one and says how to update it.
2. It asks where your firstmate home is.
3. It signs the agent in to Basecamp: you open a link and type a code. Sign in as the
   agent's **own** Basecamp person (invite one to your project first, with an email you
   control), not as yourself. [More on the agent's account](docs/firstmate-account.md).
4. It lists your projects; pick one by its number. Then pick yourself from the people on it.
5. It asks a few yes/no questions. Press Enter to take the suggested answer: the agent
   reads your chat questions, asks you for decisions as to-dos and answers check-ins. It
   reads its own Basecamp notifications every 30 seconds, so it notices what you write to
   it within about half a minute. Card tables, release announcements, Message Board
   reports and Pings stay off unless you say yes. The agent listens only to you on a
   personal account, and to you and everyone in your company on a team account (only you
   decide); anyone else who @mentions, Pings or assigns it never reaches it, and gets a
   short automatic reply instead. The summary says which; see "People the sync listens to"
   to change either.
6. It turns on what the project needs, starts the background services, runs one test sync
   and tells you what is on.

To change your answers later, run `setup` again. If something stops working, run:

```sh
basecamp-mate/bin/basecamp-mate doctor
```

It checks each piece (the Basecamp tool, the sign-in, the project, the card tables, the
background services, the last sync, the agent's wake-up) and prints each problem in plain
words with the exact fix.

Everything below is the detailed reference.

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

**Tools** ([`tools.py`](tools.py)) are small, explicit, single-purpose operations with no
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
| thread reader | reader | records the owner's new comments on any other thread the agent's login follows or is @mentioned in (a card whatever its backlog state, a document, an upload, someone else's to-do or message) as `mention` when they @mention or Reply to the agent, and as `thread-comment` only when the thread awaits the agent's answer (see "Followed threads" below; it skips the rest), and a mention in a chat nothing relays as `chat-question`; in the project, or, with `"mentions": {"scope": "account"}`, any project of the account |
| to-do request readers | reader | find the open to-dos in the project (or, with `"scope": "account"`, any project of the account) assigned to the agent's login (`/my/assignments.json`, or a to-do event) and record each one a listened-to person assigned as `todo-request`, once; then its edits and closing as `todo-request-update` and `todo-request-closed` |
| message comment reader | reader | records the owner's new comments on the agent's own recent Message Board posts as `message-comment` |
| boost readers | reader | record the owner's new boosts, with their text, on every monitored surface as `boost` (below); a boost on the agent login's own recording only once its received-boosts feed lists it |
| unmonitored recorder | reader | records owner input that no behavior handles as `unmonitored`, once per kind of thing (below) |
| `sync.py reply` | command | answers a recorded comment, chat line, Ping line, to-do comment, thread comment or mention where it was made (with `--boost`, as a boost of 16 characters or fewer on it), then removes the 👀; with `--awaiting`, on a followed thread, the next comment there is relayed |
| `sync.py ask` | command | posts a new chat line @mentioning the owner |
| `sync.py watch`, `sync.py unwatch` | command | starts or stops watching one chat in any project of the account (kept in `chats.json`, not the config): its listened-to lines after a given line are recorded as `chat-question` (see "Chat watches") |
| `sync.py answer` | command | answers a check-in question, once per question per day |
| `sync.py todo create` | command | creates a to-do assigned to the owner, with a description, and tracks it under a key: in the configured decisions project and list, or any project (`--project`), list (`--list`) or card table column (`--card-table --column`, a card) |
| `sync.py todo track` | command | tracks an existing to-do (in any project, `--project`) under a key, without relaying its old comments |
| `sync.py todo comment` | command | comments on a tracked to-do |
| `sync.py todo complete` | command | completes a tracked to-do (a decision to-do, or a to-do request when the work is done) |
| `sync.py todo subtask` | command | completes one native subtask (decision) of a multi-decision to-do, or a decision step; a decision to-do completes once all are done (never a request, or a to-do or card a step was only put on) |
| `sync.py schedule show\|set` | command | prints, or remembers per to-do list (or by default), when dated and repeating requests are done (`ask`, `due`, `before`); local state only |
| `sync.py decide` | command | puts a decision on the to-do or card it is about (any project): a comment there and a step (a native subtask) assigned to the decider, the captain or an operator; their comments, boosts and checking the step (`decision-step-done`) come back; the to-do or card itself is never completed |
| `sync.py todo show` | command | reads a tracked to-do as Basecamp has it now (open or not, due date, repetition rule), read-only |
| `sync.py post-message` | command | posts a message (subject and body) on the configured message board |
| `sync.py status` | command | lists the sync runs on this computer, what each watches and with which login, and any `sync.py` running unrecorded (see "One home per project and login") |
| `sync.py access` | command | prints who gets through to the agent, and where; read-only (see "People the sync listens to") |
| `sync.py timesheet start\|stop\|post` | command | starts or stops counting the agent's active time on a key (a to-do or card, and the Claude Code transcripts the work is in; local state only), or posts the finished days now (`--dry-run` prints them) (see "Timesheets") |
| transcript reader and timesheet entry | reader, primitive | reads the event times of Claude Code transcripts (local files only); reads the agent's timesheet entries on a to-do or card for a day (`basecamp timesheet item`) and logs one (`basecamp timesheet entry create`) |
| `sync.py unmonitored list\|handle\|forget` | command | lists the unmonitored-event keys, marks one handled with the owner's decision, or drops one so it is raised again |
| inbox note | primitive | queues a note in a firstmate home's inbox through its `bin/fm-inbox.sh note --request-id` |
| card, Message Board and boost primitives | primitive | create, update, move, assign and unassign a card; post a board message; add an acknowledgement boost |

A reader polls and appends what it finds to `pending-comments.jsonl`, once each, keeping a
cursor or seen-list beside the config; it never acts on what it reads. A command posts
exactly what the agent hands it, and only when the agent runs it.

**Behaviors** ([`behaviors.py`](behaviors.py)) are opt-in workflows a home turns on in its
config, each composed from tools. A behavior whose keys are absent makes no calls at all.
Most have a timer step; the others are carried out by the agent with the commands. The
agent's side of each is in [`prompts/base.md`](prompts/base.md).

The timer runs `run.sh` every 30 seconds, and each behavior's step runs when it is due
(`timer.json` keeps when each last ran): `notifications` and `inbox-delivery` every run;
`card-mirror`, `release-announcements`, `checkin-answering` and `assigned-todos` every 5
minutes; `security-advisories` and `timesheet` hourly; `backup` daily; and `chat-inbox`, `chat-watch`, `decision-todos`, `reports` and
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

Every piece shares `account`, `project`, `captain` (the owner's person id) and the optional
`profile`. A config without `tables` (or with `"cards": false`) never reads the backlog,
never calls a card or card-table endpoint, and never writes `map.json`; the other
behaviors run the same either way. Set such a home up with `sync.py init --no-cards`.

## People the sync listens to

Who gets through is explicit, and enforced before the agent: **what anyone else writes
never reaches it.** Every reader checks the person first, so someone not on the lists below
makes no pending record, no inbox note and no 👀, whatever they write and wherever. When
they address the agent directly, the block is logged and, with `blocked` on, they get one
short automatic reply (see "Blocked senders" below).

A config with no lists listens only to the captain. For a new home, `init` (and so
`setup`) picks the default from the account: on a **personal account** (no one but the
captain and the agent's login in the captain's Basecamp company) it listens only to the
captain; on a **team account** (others in that company, clients aside) it also writes
`"participants": {"company": true}`, so everyone in the company may ask the agent things,
while only the captain (and operators) decide. It reads the account's people
(`/people.json`, page by page, stopping at the first colleague) to tell, says which it
found after the printed config, and listens only to the captain when that read fails.
`--participants-company` or `--no-participants-company` overrides the default; a re-run
of `init` or `setup` on an existing home keeps its config's setting, so an existing home
never widens on its own. To change it later, edit `participants` in the config, or ask the
agent to.

The agent acts with the captain's authority, so who else reaches it, and whose word
counts, is widened only deliberately, in the config (or with `init`'s flags), in four ways:

- **Operators** (`operators`: person ids, `init --operator <person>`) are named people
  whose word authorizes the agent as the captain's does. They are listened to everywhere
  the captain is.
- **People** (`people`: person ids, `init --listen-to <person>`) are named people the agent
  listens to as **participants**: every reader that relays the captain (chat lines,
  every-line chats, card, to-do, message and thread comments, mentions, boosts on every
  surface, Pings, to-do requests and unmonitored input) relays them too.
- **People in one project** (`project_people`: `{"<project id>": [person ids]}`, by hand)
  are named participants heard only where they write in that project: an outside person
  invited to one project, say. Elsewhere, including Pings (a Ping is a bucket of its own,
  not a project), they are not heard. With account-wide to-do requests (`"scope":
  "account"`), a to-do they assign the agent in their project is a request; in any other
  project it is not. With account-wide mentions (`"mentions": {"scope": "account"}`), the
  same holds for their @mentions and comments.
- **Participants by rule** (`participants`: `{"project": true}`, `init
  --participants-project`, `{"company": true}`, `init --participants-company`, and/or
  `{"domains": ["example.com"]}`, `init --participants-domain <domain>`): anyone on the
  project who is not a client, anyone whose Basecamp profile is in the captain's company
  (`{"company": <id>}` names another company), or anyone whose email address is at a
  listed domain. They may ask the agent things: their chat
  lines (in a relayed chat), Pings, comments and @mentions are relayed as a participant's.
  Their boosts, to-do assignments and unmonitored input are not: a boost or an assignment
  reads as a decision, which a participant cannot make, and a whole project's unrelated
  activity is not something to ask the captain about.

The captain always counts, whether or not a list names them, and the acting login never
does: its own lines only move cursors. "Owner" below means anyone the sync listens to.

**One caveat decides which to use.** Basecamp shows a person's email address only to
themselves and to account admins; everyone else sees it masked (`r••••@•••.•••`). Unless
the agent's login is an account admin, a domain matches no one and an email passed to
`--operator` or `--listen-to` matches no one (`init` says why). Operators and people need
not be on the project: `init` looks a person it cannot find there up among the account's
people, and `sync.py access` lists them like anyone else. `"project": true` and the
named lists are keyed on person ids, which every login sees. `init` warns, and `doctor`
flags, domains that can admit nobody. The project's people are read once a run, only when
someone not named writes, and kept in `participants.json`, whose last list stands in when
that read fails.

`"company"` works for every login: a person's company is on their Basecamp profile. It
comes from the person object when Basecamp includes it, and otherwise from their person
record (`/people/<id>.json`), read at most once a run per person; `companies.json` keeps
the last company read for each person, and that stands in when a read fails. When there is
none to fall back on, the company admits no one it cannot check, and that person gets no
automatic reply either (a person with an unknown company is not known to be blocked).

`sync.py access --home <home> --config <config.json>` prints who gets through, and where:
the captain, operators, people, people per project, the rules, and whether blocked senders
get the automatic reply. It only reads (each person's name).

Every record says who wrote it (`author`: `id`, `name`), whether that is the captain
(`captain`: true or false) and their `role` (`captain`, `operator` or `participant`), and
so does its inbox note ("from the captain", "from <name> (an operator: their word counts
as the captain's)" or "from <name> (not the captain)"). A record **authorizes** when its
`role` is `captain` or `operator`; a participant's never does:

- only the captain's or an operator's 👍 on an assigned card is an `approval`; a
  participant's is a `boost`;
- only the captain's or an operator's comment or boost on a decision to-do is the
  decision; a participant's is input to weigh, and the to-do stays open;
- a check-in question's instruction counts only when the captain or an operator wrote it;
- a to-do request from the captain or an operator is captain work; a participant's is a
  request to weigh and route;
- decision to-dos, assigned cards and `sync.py ask` mentions are still the captain's alone.

A config with only `captain` behaves exactly as before; its records gain only `role`.

Operators are the trusted group for risky actions: name in `operators` the people whose
word may authorize what only the captain could otherwise (a merge, a force-push, a
credential), and keep everyone else a participant.

### Blocked senders

Someone the sync does not listen to **addresses** the agent when they @mention it in a chat
line or a comment, write in a Ping with it (with `pings` on), or assign it a to-do or card
(with `assigned_todos` or `assigned_cards` on, as a notification reports it). What they wrote
is dropped before the agent, with a `blocked <name> (<id>): ... not relayed` line in
`sync.log`. With `"blocked": {}` the sync also posts one short reply where they wrote:
a new line in that chat or Ping, or a comment on that thread, to-do or card:

> Sorry, I can't act on this: I only take requests from the people I work for. Please ask
> one of them to pass it on.

`{"reply": "<text>"}` posts your text instead. The reply goes out at most once per person
and conversation a day, and never for anything written before automatic replies started
(`blocked.json`'s `since`, set on the first run with `blocked` on). The captain, the
acting login and Basecamp's own senders are never answered, and lines and comments that do
not address the agent get no reply. A to-do the assignment sweep finds is never answered:
the sweep only guesses who assigned it (its creator); only a notification names them. Nor is
a participant admitted by rule whose assignment is ignored: they are listened to, just not
for assignments. With
`blocked` on, the Ping reader reads every Ping the login is in, not only those with someone
listened to, so a stranger's Ping can be answered. A failed reply is logged and not retried.
Like every post, nothing is posted without a `profile`, as the owner, or in a dry run.

`init` turns it on for a new home; `--no-blocked-reply` leaves it off and `--blocked-reply
<text>` sets the text. An existing config without `blocked` keeps today's behavior (the
silent drop), and re-running `init` or `setup` keeps whatever the config has. To change it,
edit `blocked` in the config, or ask the agent to.

## Card mirror

The backlog is the source of truth; Basecamp is a one-way view of it. Each run reads
every backlog task, picks its board from the task's repo, picks a column, and then
creates, updates, moves, assigns or unassigns cards.

Column rules:

| Task state | Column |
| --- | --- |
| Queued | Triage |
| Queued, listed in `figuring.json` | Figuring it out |
| Held for the captain (`hold-kind: captain`, no until date) | Figuring it out, **assigned to the captain** (unless `"assign_captain": false`) |
| Parked, has an until date, or listed in `not-now.json` | Not now |
| In flight | In progress |
| In flight with `pr=` in `state/<task>.meta` | Ready for QA |
| Done | Done |

`"assign_captain": false` (`init --no-assign-captain`, or "No" to setup's card-assignment
question) keeps a held card in Figuring it out but never assigns the owner, and the next run
unassigns the owner (only the owner) from every card the mirror had assigned for a hold. Use it
in a home whose project is not where the owner takes decisions: the agent sends each decision
to its main home, which puts it to the owner as a to-do in its own project linking back here.
The 👍 approval reader reads only assigned cards, so it is quiet there too.

If an agent hosts a Lavish review board for a task, the card links it. Each run reads the
live sessions once from plain `lavish-axi` (it has no machine-readable listing, so its
`sessions[N]{file,status,url,...}` table is parsed, with a 15s cap). A session with status
`open` whose file is under `<home>/data/<task>/` belongs to that task, and its URL, exactly
as printed, is listed as **Plan board** near the top of the note. `boards.json` adds boards
owned by other tasks (e.g. a scout's plan). Once a session ends or disappears the link
drops on the next run. If `lavish-axi` is missing, fails or times out, the run logs it and
each card keeps the links it had.

Card notes are HTML blocks (`<div>`, `<ol>`/`<ul>`) with no raw newlines between them. When the captain hold is released the card is unassigned on the next run.

## Notifications

The agent hears the owner through its own Basecamp login's notifications, the same list
as the bell in Basecamp's web app. They are durable: an item stays until it is read, so a
missed run loses nothing, and one call covers every project. With a `profile` (unless
`"notifications": false`), every timer run, every 30 seconds, makes two reads as the
acting login:

- `GET /my/readings.json`: the unread notifications and the first page of read ones.
- `GET /my/boosts.json`: the boosts on the login's own recordings (its chat lines,
  comments, to-dos, cards, messages and check-in answers). This endpoint works but is not
  in Basecamp's API docs.

Without a `profile`, or with one that signs in as the owner, nothing is read (the
notifications would be the owner's own) and the readers that `notifications` takes over
run every 5 minutes, as before.

A notification is a pointer to a whole thread ("this to-do has 2 new comments", "this
chat has a new line"), not to each comment or line. `notifications.json` keeps a cursor
per item (its id and `unread_at`). For each item whose cursor moved, the existing reader
for that thread runs once, records what is new by its own cursor and acknowledges it,
exactly as on the timer, so records, 👀/👍, seen-lists and inbox notes are the readers'
own:

| Notification | Reader run |
| --- | --- |
| a project chat in `chats`, or a chat watched with `sync.py watch` (any project of the account), has a new line | the chat reader, for that chat; an @mention of the agent there, or a Campfire reply to one of its lines (which carries no mention), is recorded even without `?` |
| a Ping with an owner in it (with `pings` on) | the Ping reader, for that Ping |
| a comment or @mention on a mirrored card | that card's comment and 👍 readers, also for a card whose task has left the backlog (the mirror itself leaves such a card as it is) |
| a comment or @mention on an open tracked to-do (a decision to-do or a request) | that to-do's comment reader |
| a comment on one of the agent's own messages (with `reports` on) | that message's reader, whatever its age |
| a comment or @mention on any other thread in the project (a document, an upload, someone else's to-do or message, a card nothing mirrors, a tracked to-do once completed or closed) | the thread reader: `mention` records, and `thread-comment` records only as "Followed threads" below says. A thread's first read starts at the notification's first unread comment (or, when it names none, at when it became unread), and never before the newest comment a to-do, card or message reader already read there; a comment one of them recorded or answered is never recorded again |
| an @mention of the agent in a chat nothing relays | a `chat-question` for that line |
| with `"mentions": {"scope": "account"}`, a comment or @mention on a thread in another project of the account, or an @mention of the agent in a chat there | the thread reader, or the chat mention reader, as in the project (see "Mentions anywhere in the account" below) |
| `Assignment` or `Completion` of a to-do (with `assigned_todos` on; in any project with `"scope": "account"`) | the to-do request reader, or the request's edit and closing check; the notification says who assigned or completed it |
| any other event on a card (an `Event`: assigned, moved, edited, completed), with `assigned_cards` on, unless the card mirror made it | the card request reader, or the request's edit and closing check; an event that leaves no request stays unmonitored |
| a check-in `Reminder` (with `checkins` on) | the check-in reader: a faster signal than the 5-minute schedule check |

The agent's login hears what it is subscribed to: what it created, commented on or was
assigned, plus anything it was @mentioned in, replied to or Pinged in. To have it hear
another thread without mentions (another chat, a check-in's answers), subscribe its
person to it once in Basecamp.

Each new owner boost in `/my/boosts.json` runs the reader whose state holds the boosted
recording (the chat, Ping, mirrored card, tracked to-do, message, check-in answers or
followed thread), which records it as a `boost` (or the captain's 👍 on an assigned card as
an `approval`). A boost on a to-do request, which the owner created, is not listed there;
the `assigned-todos` sweep reads those every 5 minutes.

**Boost confirmation.** `/my/boosts.json` is read once a run, and that one read serves
both the notifications and every boost reader. A boost a reader finds on one of the
agent login's own recordings (its chat or Ping line, comment, card, decision to-do,
message or check-in answer) is recorded, approval included, only when that read lists it
on the same recording by the same booster: Basecamp files a boost there under the person
it was aimed at, so the listing proves both that the boost exists and that it was aimed
at the agent, and the record's booster, text and time are taken from it. A boost the read
does not list is retried while it is under 5 minutes old (it may have landed after the
read; a failed read retries too), then dropped with a line in `sync.log`. A boost on
anyone else's recording (the owner's own to-do request, a colleague's chat line) is not
aimed at the agent and is recorded as read, as before; so is every boost with no
`profile`, or one that signs in as the owner, since there is then no agent feed.

**Mark read.** Once an item is handled with no failed Basecamp call, it is marked read
(`basecamp notifications read <ids>`), after its records are written; `"notifications":
{"mark_read": false}` leaves everything unread. This keeps the unread list under
Basecamp's cap of 100 and the bell honest. The cursor, never the read flag, decides what is
new, so someone marking everything read in a browser loses nothing. An item whose reader
had a failed call keeps its old cursor and is not marked: the next run retries it, and the
readers' own cursors make that harmless. A failed mark is retried next run.

**Followed threads.** Basecamp subscribes the agent's login to every thread it comments on or
is @mentioned in, so most comments there are other people talking to each other. On such a
thread (the thread reader's, above: not a mirrored card, a tracked to-do or the agent's own
message, whose readers relay every comment as before) a new comment by someone listened to
reaches the agent only when one of these holds:

- it @mentions the agent, or it is a Basecamp Reply to one of the agent's comments (Basecamp
  sends both as a mention notification, though a Reply's text has no @mention): a `mention`;
- the thread awaits an answer: the agent's last `sync.py reply --awaiting` there has not
  expired and nobody listened to has commented since. The first comment after that reply,
  from anyone listened to but the agent, is relayed (`thread-comment` with `"why":
  "awaiting"`, or a `mention`) and clears the mark.

Any other comment is skipped: one `skipped comment <id> by <name> on <type> <thread>` line in
`sync.log`, no record and no 👀 or 👍. The thread's cursor still moves past it, so it is
never read again, unless a mention notification later names it (a Reply that arrived
while the thread was being read): then it is recorded as a `mention`, unless it already
was. The rule is deterministic: the text of a comment (a `?`, a
name) never decides it. Pings, chat lines, @mentions, to-do and card requests, completions
and every other reader are unchanged.

`sync.py reply --awaiting` on a thread comment or mention sets the mark for 7 days: `threads.json` keeps, for the thread, `awaiting` with the
agent's comment id (none when it answered with `--boost`, and then a comment dated before
the mark is not the answer), when it was set (`at`) and when it ends (`until`). An expired
mark is dropped at the next read, with a log line. A comment from someone not listened to,
or from the agent itself, leaves the mark as it is. `--awaiting` on any other reply (a card
mirror comment, a chat or Ping line, a tracked to-do, a message) is logged and ignored,
since everything there already reaches the agent.

Boosts on a followed thread follow the same rule: an owner's boost of emoji only (emoji and
spaces, such as 👍, ❤️, 🎉🎉 or 1️⃣) on a comment there is skipped (one `skipped boost` line in
`sync.log`), judged on the boost's text as the agent's received-boosts feed gives it. A
boost does not clear the mark. A boost with words, punctuation or a symbol that is not an
emoji (`yes`, `a`, `?`,
`!!`, `✓`) is relayed as before, and boosts on every other surface are unchanged.

**Scope.** Items in other projects are another home's: they are never read, acted on or
marked read here, except a Ping (with `pings` on), a to-do request with `"scope":
"account"`, a line in a chat watched with `sync.py watch` (see "Chat watches"), and, with `"mentions": {"scope": "account"}`, an @mention of the agent or a
comment on a thread it follows. Basecamp's own items (`BoostReport`, `Bulletin`,
`Onboarding`) are ignored.

**Mentions anywhere in the account.** With `"mentions": {"scope": "account"}` (`init
--mentions-anywhere`), an @mention of the agent's login, or a comment on a thread it follows
(one it created, commented on or was @mentioned in), in *any* project of the account reaches
it as one in the configured project would: the thread reader records a `mention` or
`thread-comment` by the rules in "Followed threads" above, and an @mention in a chat there is a `chat-question`. Each such record
carries `project`: `{"id", "name"}` of the project it is in, and its inbox note names that
project, so the agent can route it. Who is heard is decided exactly as in the project (the
captain, operators, `people`, participants by rule, and `project_people` for that project),
a blocked sender gets the automatic reply there, the 👀 and the owner's boosts on the agent's
replies in that thread are read there, `sync.py reply --recording <id>` answers in that
project, and a notification handled this way is marked read. A chat line in another
project's chat that does not @mention the agent is still another home's and is left alone
(a Campfire reply to the agent's line there, which carries no mention, included), unless
the agent watches that chat (see "Chat watches"). It needs
notifications (a `profile`). The default, `{}` or `"scope": "project"`, or no `mentions` key,
is unchanged: only the configured project's. Like account-wide to-do requests, run it in
**one home per Basecamp login** (the main home); `basecamp-mate doctor` flags two homes on
this computer that run it with the same account and `profile`.

The first run only seeds the cursors and the boosts seen, so no history is replayed; the
repair sweep reads the configured surfaces the same run. Later, an item with no cursor that
became unread more than 5 minutes before the previous read (it dropped out of a read and
came back) is seeded the same way, since its thread was read then. A dry run reads and logs, and
records, boosts, saves and marks nothing.

**The repair sweep.** Notifications cannot see edits, card or to-do moves, or a thread the
login stopped following, so the readers they take over still run on the timer: the chat
(configured and watched), decision to-do, report and Ping readers hourly, and the `assigned-todos` sweep (new
assignments, every open request's comments, boosts, edits and closing) every 5 minutes.

### Unmonitored input

Unless `"notifications": {"unmonitored": false}`, owner input in the project that no
enabled behavior handles is recorded so the agent can ask the owner what to do about it:

| Notification or boost | Key |
| --- | --- |
| a line in a project chat not in `chats` | `chat.line.created/Chat::Lines` |
| a to-do assigned to the agent, or completed, with `assigned_todos` off | `todo.assignment_changed/Todo`, `todo.completed/Todo` |
| an owner boost on one of the agent's recordings nothing monitors | `boost.created/<type>`, e.g. `boost.created/Comment on Document` |
| a kind of notification Basecamp adds later | `<kind>/<recording type>` |

Comments and @mentions are never unmonitored: whatever they are on, a reader records them.
Each key is recorded once as an `unmonitored` record (fields below) and delivered like any
other record; the keys keep the event feed's vocabulary, so decisions recorded before
notifications replaced it still apply. `unmonitored.json` beside the config keeps the keys
seen, the first notification (or boost), a count of later ones and, once the agent runs
`sync.py unmonitored handle --key <key> --decision <text>`, the owner's decision; the key
then stays quiet. `sync.py unmonitored forget --key <key>` drops it, so the next one is
recorded (and put to the owner) again; `sync.py unmonitored list` prints the file. The
notification's own title, excerpt and link fill the record; the recording is refetched only
when its type is unknown.

The timer run and each command hold one shared lock (`sync.lock` beside the config)
throughout, so two of them never read and write the state at once.

### Upgrading from the event listener

Earlier versions polled Basecamp's event feed from a `sync.py listen` service beside the
timer (`"listen"` in the config). Notifications replaced it: they reached the agent in
0.5-11 seconds in the owner's tests, against 31-40 seconds for the feed, and never need
re-entering. A config that still has `"listen"` keeps working: the key is ignored, with one
note in `sync.log` (its `"unmonitored": false` still applies; move it to
`"notifications"`). `sync.py listen` only logs that and exits 0, so a listener service
left running stays down. Re-run `init` (or `basecamp-mate setup`) to remove the service and
drop `"listen"` from the config; `init` does both without `--force`. `basecamp-mate doctor`
flags a listener service still installed. The old `feed.json`, `pings-feed.json` and
`requests-feed.json` are no longer read and can be deleted.

## Pings

A Ping (a direct message) is a chat in a bucket of its own (a "circle"), not in the
project. With `"pings": {}` (`init --pings`), a new line in a Ping makes its notification
change, and the Ping reader runs for that Ping in the same run. The reader's own sweep
(hourly with notifications) reads `GET /my/readings.json` as the
acting login: its `pings` section lists each recently active Ping (read or unread) with
the bucket and chat ids in its `subscription_url`. Only Pings whose participants or
creator include the owner are read, at most `limit` a run (`{"limit": <n>}`, default 10),
the most recently active first; a quiet Ping drops out of the readings and a new line
brings it back. For each, the chat's lines are read
(`/buckets/<circle>/chats/<chat>/lines.json`) and every new owner line is recorded once
as a `ping` record and acknowledged with a 👀, like a chat question; the owner's boosts
on lines there are `boost` records with surface `ping`. `pings.json` keeps `since` (the
first run) and a line cursor per Ping chat: history from before `since` is never relayed,
while a Ping that starts later is relayed from its first line. The agent answers with
`sync.py reply --recording <line>`, which posts a new line in that Ping (the `basecamp
chat` commands refuse circle buckets, so it posts to the lines endpoint) and removes the
👀, under the same refusal rules as every reply. Without a `profile`, or with one that
signs in as the owner, nothing is read: the readings would be the owner's own.

## To-do requests

With `"assigned_todos": {}` (`init --assigned-todos`), a to-do a listened-to person
creates or reassigns in the project so that the agent's login is an assignee is a request
to the agent: it is recorded once as a `todo-request` (title, description, link, assignees
and who assigned it), acknowledged with a 👀 on the to-do, and tracked in `todos.json` as
`request-<to-do id>`. From then on it is read like a tracked to-do: comments by the people
listened to arrive as `todo-comment` records and their boosts as `boost` records, both
with `"request": true` and the to-do's `title`; an edit to its name or description arrives as
`todo-request-update` with the new text (and a moved due date or changed repetition rule, with
the new schedule); and its closing as `todo-request-closed`, with a
`reason`: `completed` (by anyone; who, when Basecamp says), `trashed`, `archived` or
`unassigned` (no longer assigned to the agent). A closed request is no longer read. The
agent answers with `sync.py reply --recording <comment>` or `sync.py todo comment --todo
request-<id>`, and closes the request itself with `sync.py todo complete --todo
request-<id>` when the work is done; those to-do commands work on a request with or without
`todos` in the config. A closed request assigned to the agent again is a new request.
To-dos assigned to anyone else are not requests, and with `assigned_todos` on an
assignment or completion is never unmonitored.

Who assigned it decides how the agent treats it: from the captain it is captain work; from
another listed person it is information or a request to weigh, never a captain decision.

**Cards.** With `"assigned_cards": {}` (`init --assigned-cards`; off by default, the same
`limit` and `scope` as `assigned_todos`), a card a listened-to person assigns to the agent's
login is a request read the same way: a `card-request` record (key `request-<card id>`,
tracked in `todos.json` with `"type": "card"`), then `card-request-update` and
`card-request-closed`, and its comments and boosts as `todo-comment` and `boost` records
with `"request": true` and `"type": "card"`. Basecamp raises an `Event` notification on the
card when it is assigned, which the notification reader takes as the signal to read it; the
5-minute sweep of `/my/assignments.json` is the repair sweep. `sync.py reply`, `todo comment`,
`todo show` and `todo complete` work on it; `todo complete` completes the card, which
Basecamp shows by moving it to its table's Done column. A card the card mirror made from the
backlog (in `map.json`) is never a request, and `assigned_todos` is unchanged by it.

**Scheduled to-dos.** A request with a due date is scheduled: every request record carries
`due_on` (null when undated), and the agent does the work on that date, not before when
the description says so, leaving the to-do open until then. Some scheduled to-dos also
repeat on a schedule Basecamp keeps (set in Basecamp's *Repeat* block): their records carry
`repeats`, Basecamp's `repetition_schedule` without its empty fields (`frequency`, `days`
with 0 for Sunday, `start_date`, `end_date`, intervals), and null for one that does not
repeat. Basecamp alone owns the schedule; the sync and the agent keep no copy of it as
authority. A record shows the due date and rule as read then, and a change in Basecamp
(the due date moved, the rule changed or removed) arrives as a `todo-request-update`
carrying the new `due_on` and `repeats`. Before acting on the day, the agent re-reads the
to-do with `sync.py todo show --todo request-<id>` (read-only: whether it is open and
assigned to the agent, its due date and rule now) rather than trusting a date it saved.

When a repeating to-do is completed (by the agent with `todo complete`, or by anyone),
Basecamp makes the next occurrence itself: a **new to-do** (a new id) in the same list with
the same name, rule and assignees, due on the next date. That making raises no event or
notification, so the 5-minute sweep finds it in `/my/assignments.json`. It is recorded once
as a new `todo-request` with `follows` set to the to-do id of the occurrence it continues,
and attributed to whoever asked for that occurrence: Basecamp's copy does not say who asked,
so its creator does not decide whether it is heard. The match is the completed repeating
request in the same project and list with the same name that no other to-do has followed
yet; one still open here (completed since the last sweep) has its last comments and its
closing read first. A to-do Basecamp reuses (completed, then open again with a new due date)
is its own next occurrence: a new request under the same key, with `follows` its own id and
`reopened` false. The agent comments the results, completes the to-do, and never creates the
next occurrence. Other repeating Basecamp objects (check-ins, schedule events) are not
to-do requests.

**Anywhere in the account.** With `"assigned_todos": {"scope": "account"}` (`init
--assigned-todos-anywhere`), a to-do assigned to the agent's login in *any* project of the
account is a request, not just one in the configured project. Every record of a request
(`todo-request`, `todo-request-update`, `todo-request-closed`, and its `todo-comment` and
`boost` records) carries `project`: `{"id", "name"}` of the project the to-do is in, and
the inbox note names a project other than the configured one, so the agent can route the
work. Everything later read or posted on it (comments, boosts, 👀, `reply`, `todo
comment`, `todo complete`) goes to that project. Notifications act on an assignment or
completion in any project, and on comments on tracked requests there (nothing else in
another project); the timer's sweep reads every project's assignments. The default scope,
`"project"`, is unchanged: only the configured project's to-dos.

Run the account-wide scope in **one home per Basecamp login**: the main home (firstmate's
own), which routes each request to the home or domain that owns its project. Every other
home on that login keeps `"assigned_todos": {}`, or leaves it off; two homes running it
would each relay the same to-do. `basecamp-mate doctor` flags two homes on this computer
that run it with the same account and `profile`; it cannot see homes on other computers.

Notifications see the assignment (`Assignment`), comments and completion (`Completion`)
within about 30 seconds, and say who assigned or completed it. The timer's sweep, every 5
minutes, is the backup and the only reader of a request's edits and of the owner's boosts
on it, which raise no notification: one read of `GET
/my/assignments.json` as the acting login (the agent's open assignments in every project,
filtered to this one unless the scope is `account`) finds a to-do the notifications missed, attributed to its creator, since
Basecamp does not say who assigned it; at most `limit` new to-dos are fetched a sweep
(`{"limit": <n>}`, default 10), and a to-do whose creator is not listened to is fetched
once and remembered in `assigned-todos.json`. Each sweep then reads every open request's
comments, boosts and the to-do itself, for edits and closing. Without a `profile`, or with
one that signs in as the owner, nothing is read: the assignments would be the owner's own.

## Timesheets

With `"timesheet": {}` (`init --timesheet`), the sync posts the agent's active time on each
piece of work to the Basecamp timesheet of its to-do or card: one entry per piece of work
per day, once. Off (the default), nothing about it runs: no calls, no files, no log lines.

The agent names its work. `sync.py timesheet start C --key <key> --on <to-do or card URL>
--workdir <dir>` starts counting from now, in the Claude Code transcripts of sessions run in
that directory (`~/.claude/projects/<the path, each character that is not a letter or digit
as ->`, or `CLAUDE_CONFIG_DIR`).
Use a directory only that work runs in (a worker's worktree), never the home, which every
session there shares: `start` refuses a directory another key is counting now, naming that
key, since its time would be counted for both. Repeat it with the same key for each other
directory the work runs in. Without `--on`, the
key's to-do (`todos.json`: a request or decision to-do) or mirrored card (`map.json`) is
used. `sync.py timesheet stop C --key <key>` stops counting. Nothing is guessed from paths
or task names.

**Active time** is read from the transcript lines' timestamps, between each start and stop:
a gap of at most 5 minutes between two lines of a transcript counts, and so does a whole
wait of at most 60 minutes on a tool call the agent made (a long command, a subagent),
from the call to its result, across the lines between them. A longer gap is idle, a longer
wait adds nothing beyond its short gaps, and a call with no result adds nothing. The spans of all a key's transcripts are merged, so time is never counted twice,
then split by local day (the config `timezone`, default `America/New_York`) and rounded to
the nearest quarter hour.

**Posting.** Hourly, each finished day (an hour past its local midnight, once its last
tool wait is known) with a quarter hour or
more is logged as one entry (`basecamp timesheet entry create`: `date`, `hours`,
`description` "Active agent time: <key>") as the agent's login, never as the owner. `timesheet.json` keeps every entry posted, and
before each post the sync reads the agent's entries on that to-do or card for that day: when
this key's entry (description "Active agent time: <key>") is already there (a post whose
answer was lost, or a run that stopped before saving), it is only added to `timesheet.json`,
so a re-run or a restart finds that day's entry instead of posting a second one. Another
key's entry on the same to-do or card that day does not stop the post: several pieces of
work can share one to-do. A key with no to-do or card is skipped and reported once
(`timesheet-unmapped`); its days post once it has one. A project whose timesheet refuses an
entry (forbidden or invalid) is reported once (`timesheet-refused`, one FAILED
line). From then on it is tried with one entry at most once a local day, logged without
FAILED, until an entry there posts again; then its other entries follow. A key whose to-do
or card is not found is reported once (`timesheet-missing`) and tried the same way, alone, so
the project's other keys keep posting; `timesheet start --on` names another. A stopped key whose
days wait only on such a refusal keeps them in `timesheet.json` as `unposted`, so its
transcripts are not read again (`timesheet start` adds new days to them); they go with that
daily try, and the key settles once they all post. Any other failed entry is a
FAILED line and is tried again on the next run. When one of a key's transcripts
cannot be read, that key posts nothing that run (a FAILED line) and is not settled, so no
day is saved with part of its time; a transcript removed while it is read is skipped and
logged. `sync.py timesheet post C
--dry-run` prints what would be posted and posts nothing. Only days after `start` count:
there is no backfill.

To turn it on in another agent home, give its agent this prompt (`SYNC` is its
basecamp-mate checkout, `HOME` its firstmate home):

> Turn on Basecamp timesheets for this home: add `"timesheet": {}` to
> `HOME/data/basecamp-sync/config.json`, check that `python3 SYNC/sync.py behaviors --home
> HOME --config HOME/data/basecamp-sync/config.json` lists `timesheet` as on, and copy the
> "Timesheets" section of `SYNC/prompts/defaults.md` into
> `HOME/data/basecamp-sync/defaults.md`. From now on follow it: run `sync.py timesheet start`
> with the to-do or card and the work's directory whenever work on a Basecamp to-do or card
> starts (a directory only that work runs in, such as a worker's worktree, never this home),
> and `sync.py timesheet stop` when it ends. Tell me when a project refuses
> timesheet entries, because that project does not have timesheet tracking turned on.

## Safety bounds

- Only the configured account, project, card tables, chats, check-ins, to-do set and
  message board are touched, and the chats the agent watches with `sync.py watch`, and, with `pings` on, the Pings the acting login is in with
  the owner; with `assigned_todos` on, the project's to-dos assigned to the acting login are
  read and acknowledged with a 👀 (with `"scope": "account"`, those in any project of the
  account, and the comments and boosts on them). Notifications and boosts are read for the
  whole account, but only this project's items (and those Pings, requests and watched chats) are acted on;
  in the project (with `"mentions": {"scope": "account"}`, any project of the account), any
  thread the acting login follows or is @mentioned in is read for the owner's comments, and
  `reply` answers there. Their only writes are the readers' 👀/👍
  acknowledgements and marking handled notifications read; never another home's.
- Cards are never deleted, trashed or archived. Cards whose task left the backlog are
  left as they are (the run logs how many).
- The sync never posts as a comment, and never to chat, except the opt-in session alert
  (one line per outage, below) and, with `blocked` on, the short reply to a blocked sender
  where they addressed the agent (see "Blocked senders"). Its other automatic post is a
  release announcement, and only on the Message Board (below). Otherwise the only thing that
  posts is the explicit `sync.py reply` command below, run by the relaying agent to
  answer a captain question where it was asked: a comment on the card, or a line in the chat.
  Two more explicit commands post, both opt-in: `sync.py ask` (a new chat line for the
  owner) and `sync.py answer` (a check-in answer). The sync run never calls either, nor
  the to-do commands or `sync.py post-message` (below).
- Release announcements: for each GitHub repo in the optional `releases` config, each run
  lists recent releases (`gh release list`, then `gh release view --json` for the notes)
  and posts one Message Board message per new one as the acting user, with the subject
  `<Name> <tag> released` (e.g. "Terminal v0.3.0 released") and a body of the release
  notes rendered to Basecamp rich text (escaped; headings bold, lists, links), a link to
  the release page, and the repo's optional `note`. The first run for a repo only seeds
  the watch with the releases that exist then, so no backlog is announced; after that a
  release is new when it was published after the watch started and is not yet recorded.
  Drafts are always skipped, and prereleases unless `"prereleases": true` or
  `--include-prereleases`. Announced tags are kept per repo in `releases.json`, so a
  release is posted once; messages are never edited or deleted. A failed `gh` read or
  post is logged and retried next run and never fails the sync. It posts even with no
  `profile` or a profile signed in as the captain: an announcement is not an
  acknowledgement. Merges and PRs are never announced.
- Security advisories: for each GitHub repository in the optional `advisories` config,
  each hourly run lists its repository security advisories (`gh api
  repos/<owner>/<name>/security-advisories`, the newest 100) and records one `advisory`
  record (repo, GHSA id, summary, severity, state, reporter, created date, link) per
  advisory the first time it is seen in `triage` or `draft`: a report waiting on the
  maintainers. `advisories.json` keeps each advisory's last seen state, so it surfaces once;
  when one later moves to `published` or `closed` an `advisory-update` record follows, once.
  Advisories already published or closed when first seen are not recorded. A repository
  the `gh` login cannot read advisories for (HTTP 403 or 404: reading them needs admin or
  security-manager access) is recorded once as `advisory-unreadable` and stays quiet until
  it is readable again; any other failed read is logged and retried next run. The sync
  never changes, publishes or comments on an advisory. The same run lists each repository's
  open Dependabot alerts (`gh api repos/<owner>/<name>/dependabot/alerts?state=open`, the
  newest 100). The first read of a repository is a baseline: one `dependabot-baseline` record
  (open count by severity, link to its Dependabot page) and the alerts already open are
  marked seen. After that, one `dependabot-alert` record (number, package, ecosystem,
  severity, fixed-in version, manifest, summary, link) is recorded per alert that opens;
  `advisories.json` keeps which alerts are open, so an alert that is fixed or dismissed and
  later reopens is recorded again, marked `reopened`. A repository whose alerts cannot be
  read (HTTP 403 or 404, or a `gh` error asking for a missing token scope: alerts off, or
  the login lacks access) is recorded once as
  `dependabot-unreadable`. Alerts are never dismissed or changed. Code-scanning alerts and
  the global advisory database are not read.
- Backup: with the optional `backup` config, once a day (and now with `sync.py backup`)
  the config, its JSON and JSONL state files and the home's `defaults.md` (never a lock, a `.tmp`, `sync.log`, a hand
  copy such as `config.json.bak-...`, or `backup.json`) are uploaded to that Docs & Files
  folder, which may be in another project. Only a file whose content changed since its last
  upload is sent: as a new version of its upload there (`basecamp files replace`, so the
  folder holds one file per name, nobody is notified, and Basecamp keeps the old versions
  in that upload's history), or as a new upload when the folder has none of that name yet.
  A file holding a credential is never uploaded: any file with a token-shaped value, and
  `config.json` with a key naming one (`token`, `secret`, `password`, `api_key`, ...); it
  is logged and skipped. Nothing in the folder is ever deleted or trashed. A failed upload
  is logged and retried on the next backup. To restore, download the files from the folder
  (in Basecamp, or `basecamp files download <upload id>`) into the config's directory while
  the timer is stopped.
- Timesheet: with the optional `timesheet` config, the only writes are timesheet entries,
  one per key per finished day, on the to-do or card the agent named for that key (or
  tracks under it); the transcripts are only read. See "Timesheets".
- Chat questions: for each chat id in the optional `chats` config list, each run reads the
  chat's lines newer than a cursor kept in `chats.json` (the first run only sets the
  cursor, so older history is not relayed). A captain line that mentions the acting user
  or contains `?` is appended to `pending-comments.jsonl` as a `chat-question`, recorded
  once, and gets the acting user's 👀. Lines from anyone else, the acting user included,
  are never captured. `sync.py reply --recording <line id>` answers it with a new line in
  that chat as the acting user and then removes the 👀, under the same `--again` and
  failure rules as a card reply. A chat configured as `{"chat": <id>, "every_line": true}`
  relays every line the owner posts there, not only mention or `?` lines; each goes
  through the same record, cursor and 👀 rules.
- Chat watches: `sync.py watch --home <home> --config <config.json> --chat <chat URL>
  [--after-line <line id>] [--until YYYY-MM-DD]` watches one chat in any
  project of the account, so a plain answer there reaches the agent without an @mention.
  Use it after the agent asks a question in a chat outside the project. The watch is kept
  in `chats.json` (the chat's `bucket`, its cursor, and `watch`: `after`, `until`,
  `since`), not in the config, so no config edit or restart is needed. The command reads
  the chat's lines once (which proves the login can read it) and starts the cursor at
  `--after-line`, else at the line the URL names (`.../chats/<chat>@<line>`), else at the
  newest line, and keeps that starting line as `watch.after`: no line at or before it is
  ever relayed, even when a late `Mention` notification names it. It also takes the boost
  counts there now and the ids of the boosts already on its lines as the baseline, so no
  boost made before the watch (or while unwatched) is ever relayed.
  From then on the `chat-watch` behavior's sweep and notifications read it with the chat
  reader, in that chat's project: every listened-to line after the start is a
  `chat-question`, a plain answer without `?` included. A record from another project carries `project` (`id`,
  `name`), the 👀 and the owner's boosts on the agent's lines are read there, and `reply
  --recording <line id>` answers there. Who is heard is decided as in the project
  (`project_people` for that project), and a blocked sender's line gets the automatic reply
  only when it @mentions the agent or a notification marks it addressed. The watch ends
  with `sync.py unwatch --chat <chat URL>`, or once its `--until` day (in the people's zone)
  has passed: the next run then drops it and logs that. The lines recorded stay, so `reply`
  still answers them. Watching a chat again replaces its options, cursor and boost baseline; a chat in
  `chats` is refused, since the chat inbox reads it already, as is a Ping (a `circles`
  URL, a chat in a Ping bucket in `pings.json`, or one whose lines are in a `Circle`), since
  the Ping reader relays it and its lines would be recorded twice, a URL in another
  account and an `--until` day already past. Pass the URL of the line being replied to
  (`.../chats/<chat>@<line>`, a record's `url`), so an answer posted before `watch` runs is
  not missed. `--until` must be a real `YYYY-MM-DD` day; a stored `until` that is not one
  (a hand edit) never stops a run: the next run ends that watch and logs why. Basecamp may
  report a Campfire reply to the agent's line as a `Mention` or as a `Chat` notification; no recorded example of either
  shape exists here, so both run the chat reader. A watch never makes a timer run refuse:
  when another home's live run reads the watched chat's project with the same login, each
  run skips that chat and logs `watch: skipping watched chat` once (and `reading the watched
  chats ... again` once that stops); the watch stays. That home relays only an @mention of
  the agent there (or lines of a chat in its own `chats`), so a plain answer reaches no
  agent meanwhile; `watch` warns up front when such a home is live (see "One home
  per project and login"). A home that never runs `watch` reads exactly as before.
- Asking the owner: with `ask_chat` set,
  `sync.py ask --home <home> --config <config.json> --body-file <file>` posts the file's
  Markdown (rendered like a reply) as a new line in that chat as the acting user,
  starting with an @mention of the owner (their person record's `attachable_sgid`, read
  from `/people/<id>.json`). Post one question or decision per line. With no profile, or a
  profile signed in as the owner, it posts nothing; `--dry-run` logs only. The owner's
  answer comes back through the chat relay when the chat is also in `chats`.
- Check-ins: with `checkins` set, each run lists the questions of every configured
  questionnaire and records each one that is due today as a `checkin` in
  `pending-comments.jsonl`, once per question per day. A question is due when it is not
  paused, today's weekday is in its schedule `days` (Basecamp numbering, 0 = Sunday), its
  `start_date` has been reached (and any `end_date` not passed), and its `hour`:`minute`
  has passed in the configured `timezone` (the machine's local time when unset). One the
  acting user already answered today is not recorded. The run never answers;
  `sync.py answer --home <home> --config <config.json> --question <id> --body-file <file>` (Markdown,
  rendered like a reply) does, through `basecamp checkins answer create <id> <content> --date <today>`, at most
  once per question per day (checked in `checkins.json` and in the question's answers),
  and under the same refusal rules as `ask`.
- Decision to-dos: with `todos` set,
  `sync.py todo create --home <home> --config <config.json> --key <key> --title <text> --body-file <file>`
  creates a to-do assigned to the owner, loose on the configured to-do set
  (`"todos": {"todoset": <id>}`, or the project's only one with `"todos": {}`), or in a
  to-do list (`--list <id>`, or `"todos": {"list": <id>}`), with an optional `--due`. The
  body file is the description in Markdown, rendered by the CLI (bare URLs become links).
  A to-do with more than one decision passes every one, the first included, as its own
  repeatable `--subtask "<question>"` (a lone subtask is refused), leaving the description
  only context: each becomes a native Basecamp subtask of the to-do (`basecamp subtasks
  create`, CLI 0.12.0+), assigned to the owner and kept in `todos.json` in order;
  `sync.py todo subtask --todo <key or id> --number <n>` completes one (`basecamp subtasks
  complete`) and completes the to-do once the last is done.
  The to-do is tracked in `todos.json` under the key; a key already tracked is refused, so
  a re-run never creates a duplicate. Each run reads the comments (and boosts) of every tracked to-do
  that is not completed and records each owner comment newer than that to-do's cursor as
  a `todo-comment`, once, with the same acknowledgement as a card comment (👀 when it
  contains `?`, 👍 otherwise); other people's comments, the acting user's included, only
  move the cursor. `sync.py reply --recording <comment id>` answers one with a comment on
  the to-do (Markdown) and removes the 👀; `sync.py todo comment --todo <key or id>
  --body-file <file>` posts any other comment; `sync.py todo complete --todo <key or id>`
  completes it, after which its comments are no longer read. `sync.py todo show --todo <key
  or id>` prints it as Basecamp has it now (open, assigned to the acting login, due date,
  repetition rule), reading only. `sync.py todo track --key
  <key> --todo <id>` adopts a to-do made some other way: its existing comments are skipped
  and only later ones are recorded. Only tracked to-dos can be commented on or completed;
  to-dos are never deleted, trashed or archived. All of these post nothing with no
  profile or a profile signed in as the owner, and `--dry-run` only logs.
- Reports: with `message_board` set,
  `sync.py post-message --home <home> --config <config.json> --subject <text> --body-file <file>`
  posts one message on that board (Markdown body, rendered by the CLI) as the acting user,
  under the same refusal rules as `reply`. Each run of the command posts a new message;
  messages are never edited or deleted. Each run reads the board's newest messages and,
  for each one the acting user posted in the last 14 days that has comments, records
  every owner comment newer than that message's cursor as a `message-comment`, once,
  acknowledged like a card comment (👀 with `?`, 👍 otherwise). A message has no cursor
  until its first read, so feedback already on a recent post is relayed. `sync.py reply
  --recording <comment id>` answers one with a comment on the message (Markdown) and
  removes the 👀.
- Boosts are answers: a boost can carry short text, and the owner's boost on anything a
  behavior monitors is recorded once as a `boost` with its text, the boosted recording and
  the surface. The reads stay bounded: a recording's boosts are read only when its
  `boosts_count` changes, except the few read every run. The surfaces:
  `chat` (every line in the newest page of each relayed chat, the agent's own lines included);
  `card` (an assigned card, read every run as for approvals; a 👍 is still an `approval`, now with its `text`);
  `card-comment` (every comment on a mirrored card);
  `todo` (a tracked open to-do, read every run) and `todo-comment` (its comments);
  `checkin-answer` (the agent's own check-in answers from the last 7 days);
  `message` and `message-comment` (the agent's own posts on `message_board` from the last
  14 days, and their comments). The first time a surface's state has no boost counts,
  they are seeded without recording, so turning this on (or upgrading) never replays old
  boosts. The acting user's own boosts are never recorded, and a boost on the acting
  user's own recording only once its received-boosts feed lists it (see "Boost
  confirmation" under Notifications).
- Inbox delivery: with `inbox` set (`{}` for the `--home`, or `{"fm_home": "<home>"}`),
  each run ends by delivering every record appended to `pending-comments.jsonl` since the
  last delivery as a note through `<fm_home>/bin/fm-inbox.sh note --request-id
  basecamp-<kind>-<id> --json -` (FM_HOME set to that home). The note body is short and
  plain: what it is and who wrote it, the text, the Basecamp link, the facts that matter
  (whether their word authorizes, a due date Basecamp owns, a repeat Basecamp makes, a
  closed request) and the commands that act on it. It never says how to handle it: that is
  the home's `defaults.md` (see "The base prompt and the agent skill"), which the note
  points to.
  The request id (the comment, line or boost id; question and date for a check-in) makes a
  replay return the original note rather than a new one. Records go in order from a
  line cursor in `inbox.json`; the first run starts after the records that existed
  before it. A failed note (any non-zero exit, including 3, "saved but not woken") stops
  that run's delivery, is logged as `FAILED inbox note <id>` once, and is retried next
  run; it never fails the sync. `pending-comments.jsonl` stays the record either way,
  and a home without `inbox` keeps the file-plus-wake-check flow unchanged. With
  `init --inbox` the wake check watches only FAILED lines, since each note is its own wake.
- Session alert: inbox notes wake only a running firstmate session, so after a reboot
  (or a crash) nothing handles them. With `session_alert` set (and `inbox` on), each minute
  the run reads the inbox delivery's firstmate home, read-only: line 1 of
  `<fm_home>/state/.lock` (the primary session's pid) and the pending notes in
  `<fm_home>/state/inbox/`. The session is down when no live process holds the lock; a lock
  written before this boot counts as down even when its pid now names another process.
  Once it is down and the oldest pending note has waited `after_minutes` (default 15), the
  run posts one line in the `chat`, @mentioning the owner: the session is down and N
  messages have waited since when. `session-alert.json` records it, so later runs stay quiet;
  a session holding the lock again clears it (logged), and the next outage alerts again. A
  failed post is logged `FAILED session alert` and retried next run; a profile that is the
  owner never posts. The alert does not restart the session: relaunching it after boot is
  left to the operator for now.
- Acknowledgement boost: when the run records a new captain comment or approval, the
  acting user boosts it once (the comment itself, or the card for an approval). A comment
  whose text contains `?` is recorded with `"kind": "question"` and gets 👀 ("looking into
  it"); other comments and approvals get 👍 ("got it"). The sync never removes a 👀. Once the relaying agent
  has the answer it runs
  `sync.py reply --home <home> --config <config.json> --recording <question comment id> --body-file <file>`,
  which posts the file's Markdown (rendered to Basecamp rich text: bold, italic, links,
  lists, quotes and code blocks, everything else HTML-escaped) as a comment on that card as the acting user, then removes the acting user's 👀 from the question; no
  👍 is added. The question id is recorded in the card's `replied` list in `map.json` and a
  second reply is refused unless `--again` is passed. A failed post removes nothing, so the
  👀 stays. `--boost "<text>"` in place of `--body-file` answers with a boost of that text
  on the question itself instead of a comment, for an answer that is only an
  acknowledgement, a thanks, "done" or a plain yes. It works the same way for every record
  `reply` takes (card, chat, Ping, to-do, message and thread comments and lines): it is
  recorded in `replied`, a second reply needs `--again`, and the 👀 comes off (unless that
  boost is the read acknowledgement itself, which then stays as the answer). On a thread
  comment or mention, `--awaiting` also marks the thread as awaiting an
  answer for 7 days (see "Followed threads"). Text over
  16 characters (Basecamp's limit for a boost), or empty text, is refused before anything
  is read or posted, and the command exits 1. With no profile, or a profile signed in as the captain, it posts nothing;
  `--dry-run` logs the plan only. The acting user's own replies are never relayed, since
  only the captain's comments are. Boosts are posted via `POST /buckets/<project>/recordings/<id>/boosts.json`. The
  acting identity is read from `/my/profile.json` once per run; with no `profile`, or a
  profile that signs in as the captain, nothing is boosted, so the captain never appears
  to boost his own items. A recording that already has the acting user's 👍 is not
  boosted again, and boosted ids are kept in `map.json` (`ack` queued, `acked` done). A
  failed boost is logged and retried next run; it never blocks the pending record or
  fails the run. The acting user's boost is never read as an approval, since only the
  captain's boosts are.
- New comments by the captain are appended to `pending-comments.jsonl` for firstmate to
  relay; they are never acted on.
- On cards assigned to the captain (and only those), the run reads the card's boosts. A 👍
  (any skin tone) by the captain is appended to `pending-comments.jsonl` as an approval
  record. It means **approve every recommendation on the card as recommended**; a comment
  is how to change one. The script never releases, answers, unassigns or moves anything
  because of a boost: firstmate records the decision in the backlog, and the next normal
  run unassigns the card. Other emoji, other people's boosts and boosts on unassigned
  cards are ignored; each boost id is recorded in `map.json` so it is emitted once.
- `map.json` maps `task|board` to a card id, so re-runs update the same card instead of
  creating a duplicate.
- `--dry-run` makes no Basecamp writes (it only reads boosts on assigned cards and the
  acting identity, and logs any new captain approval and the acknowledgement boosts it
  would add) and leaves `map.json` and the pending file alone; it logs, per card,
  whether it would be created, updated, moved, assigned or unassigned, and a total.

## One home per project and login

Two homes whose configs watch the same project with the same Basecamp login would each
read the same notifications, record the same input, acknowledge it twice and wake two
agents. The shared state lock cannot stop that, since it is per config, so every timer run
first claims what its config watches ([`runs.py`](runs.py)): its project, and,
account-wide, its login's Pings (with `pings`), the to-dos assigned to it anywhere
(with `"assigned_todos": {"scope": "account"}`) and its @mentions and followed threads
anywhere (with `"mentions": {"scope": "account"}`). Two claims overlap when they share one of
those with the same login: the same Basecamp person when both runs know it, else the same
`profile`. A run beside another config's live claim that overlaps is refused: it reads
nothing, exits 1 and logs `FAILED duplicate run`, naming the other home, its config and
login, when it last ran or its pid if it is running now (once, then hourly, so the wake
check and `doctor` see it without flooding the log). When the other run stops, the next
run says `duplicate run cleared` and carries on. A chat its home watches with `sync.py
watch` is never claimed and never refuses a run: when another home's live run on the same
login claims that chat's project, the run skips that chat (logged once when that starts
and once when it ends), checked every run, so a home that starts after the watch is
covered too. The watches are read under the shared state lock the whole run holds, so a
watch started meanwhile waits for the next run. Two runs of the same config are never
duplicates: they share one claim and the state lock serializes them, and the explicit
commands (`reply`, `todo ...`) never claim.

Each config's claim is a file in `$BASECAMP_MATE_RUNS` (default
`~/.local/state/basecamp-mate/runs/`): the config and home, the login, what it watches, the
run's pid and process start time, and when it last ran. A claim is live while its run is
going (the pid, checked against its start time since pids are recycled) or for 10 minutes
after it last ran while its config still exists. The timer runs every 30 seconds, so a
killed run, a stopped timer or a removed config stops blocking within 10 minutes, and a
claim whose config is gone is removed. Checking and claiming are one step under a lock, so
two homes starting together cannot both claim. A dry run checks and claims nothing.

`basecamp-mate status` (`sync.py status`) lists every claim, live or stale, what it
watches and with which login, the live claims it overlaps, and any `sync.py` process
running (from `/proc`) whose config holds no claim. To run two homes on the same thing on
purpose, set `"allow_duplicate": true` in the config (or pass `--allow-duplicate` to one
run); the run then notes the overlap once and goes ahead. `doctor` flags homes on this
computer whose configs overlap, unless all of them allow it.

## Set up a home

```sh
python3 sync.py init https://app.basecamp.com/<account>/projects/<project> --login firstmate --home <home> --dry-run
```

`init` reads the project as the `--login` profile and discovers every id below: the card
tables in the project's dock, each matched to a backlog repo by its title
(case-insensitive, surrounding spaces ignored) against the names in the home's
`data/projects.md`; each table's columns by their exact names; the login's own identity
(`/my/profile.json`); the captain; the chats in the dock; and, when the dock has one
Message Board, `releases`: its id and the GitHub repo behind each mapped repo's
`<home>/projects/<repo>` origin (renames followed through `gh repo view`), named by its
board. A repo's `note` is added by hand. `--captain <person id or
email>` names the captain; by default it is the project's one account owner other than the
login. `--listen-to <person>` (repeatable: a person id, email or exact name on the
project) adds someone the sync listens to besides the captain, written as `people` (the
captain first). Someone not on the project (a colleague who is not on the agent's project,
say) is looked up among the account's people (`/people.json`, every page, read once a run)
and written all the same, and `init` prints which accepted people are not members of the
project; a name or email matching no one on the project or in the account, several people
on the project (or, when none is on it, in the account), or the login itself, is refused,
and so is a person not on the project when the account's people cannot be read.
`--operator <person>` (repeatable, the same forms) names an operator, written
as `operators`. A re-run on a home that already has a config keeps the `people` and
`operators` it names besides the ones the command line names (and says which it kept), as
it keeps `participants` and `blocked`, so re-running `init` or `setup` never drops a hand-added
person; `--replace-people` writes exactly the `--listen-to` and `--operator` given instead; `--participants-project` and `--participants-domain <domain>` (repeatable)
admit participants, written as `participants` (see "People the sync listens to"; an email
the login sees masked matches no one, and `init` says so, and warns when a domain can admit
nobody). `--repo-map <table>=<repo>` (repeatable) maps a table whose title is not a
registered project's name. A table whose title matches no registered project and that
`--repo-map` does not name (a general "Ideas" board, say) is skipped: `init` prints a line
for it, leaves it out of the config, never reads its columns, and the sync never touches
it. At least one table must be included. Any other missing or ambiguous table, column,
repo or identity is refused with a message, and nothing is guessed or written. `--create-missing-columns`
creates a missing Figuring it out, In progress or Ready for QA column (the only Basecamp
write `init` makes); Triage, Not now and Done are built in and never created.

Without `--dry-run` it then:

- writes `<home>/data/basecamp-sync/config.json` (refusing if one exists and differs,
  unless `--force`) and creates the empty hand-kept side files that are missing;
- installs and enables the systemd user units `basecamp-sync-<home path>.service` and
  `.timer` (every 30 seconds, 240s cap), running this checkout's `run.sh`;
- stops, disables and removes `basecamp-sync-<home path>-listen.service`, the retired
  event listener, if an earlier version installed it;
- writes `<home>/state/basecamp-sync.check.sh` and registers it with the home's own
  `bin/fm-check-register.sh`, so the home wakes on new pending records and failed runs.

Four opt-in flags turn on more behaviors from the project's dock, each refusing when the
dock lacks what it needs: `--todos` (`todos` with the one enabled to-do set),
`--reports` (`message_board`, the one enabled message board), `--every-line` (every
discovered chat as `{"chat": <id>, "every_line": true}`) and `--checkins <IANA time zone>`
(`checkins` with the one enabled Automatic Check-ins questionnaire). Without them the
config is the same as before they existed, so re-running `init` on an older home changes
nothing. `--no-releases` leaves `releases` out. `--advisories <owner/name>` (repeatable)
adds `"advisories": {"repos": [...]}`. `--inbox` adds `"inbox": {}` and installs
the failures-only wake check. `--session-alert [MINUTES]` (with `--inbox`) adds
`"session_alert": {"chat": <the dock's first chat>, "after_minutes": MINUTES}` (default 15),
refusing when the dock has no chat. `--listen` is still accepted and does nothing (notifications
replaced the event listener); a config that differs from the discovered one only in a
`"listen"` key is rewritten without it, no `--force` needed. `--pings` adds
`"pings": {}`: the owner's Pings to the `--login` are relayed (see Pings).
`--assigned-todos` adds `"assigned_todos": {}`: a to-do the owner assigns to the
`--login` is a request (see To-do requests). `--assigned-todos-anywhere` adds
`"assigned_todos": {"scope": "account"}` instead: one in any project of the account is (only
in the main home; see To-do requests). `--assigned-cards` adds `"assigned_cards": {}`: a
card the owner assigns to the `--login` in the project is a request (see To-do requests).
`--mentions-anywhere` adds `"mentions": {"scope": "account"}`: the owner's @mentions of the
`--login`, and comments on threads it follows, in any project of the account are relayed
(only in the main home; see Notifications).

`--no-cards` sets a home up without the card mirror: it reads no card tables (the project
need not have any), writes a config without `tables` or `repos`, creates only
`pending-comments.jsonl`, and takes every registered repo with a GitHub origin as a
`releases` source, named after the repo. It cannot be combined with `--repo-map` or
`--create-missing-columns`. Chats, the Message Board, the timer and the wake check are set
up as usual.

`--no-chats` leaves the dock's chats out (no chat inbox). `--no-keyring` (on by default when
`BASECAMP_NO_KEYRING` is set in the environment `init` runs in) adds
`Environment=BASECAMP_NO_KEYRING=1` to the units, so every `basecamp` call they make uses
the CLI's file credential store and never waits on a locked system keyring.

Re-running with the same inputs changes nothing. `--dry-run` prints the discovered config
and what it would write, install or refuse, and writes nothing.

### Guided setup and doctor

`bin/basecamp-mate setup` ([`setup_home.py`](setup_home.py), also `sync.py setup`) is the
guided front end to `init` for people: it checks Python, the `basecamp` CLI (offering its
installer; 0.12.0 or newer) and systemd user services; asks for the home; signs the login in with
`basecamp profile create <login> --device-code` (or `auth login -P <login> --device-code`)
under `BASECAMP_NO_KEYRING=1`; lists the login's accounts and projects to pick from and the
project's people to pick the captain from; asks the yes/no questions (chat inbox, decision
to-dos, check-ins on; card mirror, releases, reports, Pings off by default) and the
check-in time zone, and, when inbox notes are chosen, whether to turn on the session alert
(default yes, 15 minutes, in the dock's chat); enables or adds the dock tool each chosen behavior needs (to-do set,
questionnaire, chat, message board) and, for the card mirror, a card table per registered
repo that has none; then runs `init` itself (with `--create-missing-columns` only for the card
mirror, `--force` after asking when the home already has a config, and `--no-keyring`), runs
`run.sh` once as a test sync, and prints what is on and how to change it.
`--answers <file.json>` answers by key (`home`, `login`, `account`, `project` (id or URL),
`captain` (person id), `timezone`, `replace`, `install_cli`, and booleans `chat`, `todos`,
`checkins`, `cards`, `releases`, `reports`, `pings`, `inbox`, `session_alert`) and `--yes` takes the
defaults; without a terminal, anything that needs a person (a sign-in, an unanswered pick)
refuses with what to do instead of asking.

`bin/basecamp-mate doctor` ([`doctor.py`](doctor.py), also `sync.py doctor`) checks Python
3.11+, the `basecamp` CLI and its version (0.12.0 or newer), systemd user services, then per home (every home
with installed `basecamp-sync-*` units, or `--home`): the firstmate home, the config, the
login (`auth status` under a 20-second timeout and `BASECAMP_NO_KEYRING=1`, renewing an
expired token as `run.sh` would), the project and the dock tools the config names, each card
table's columns by id and title, the agent's notifications (`/my/readings.json` readable,
and fewer than Basecamp's 100 unread), the timer units (installed, pointing at files that
exist, enabled and active) and a leftover listener service, the last `sync.log` line
(FAILED, or older than 20 minutes; a `FAILED duplicate run` names `basecamp-mate status`),
the wake check's registration, and, with `participants` domains, that the login sees anyone's
email address; then, across the homes checked, that no two watch the same project, Pings,
account-wide to-do requests or account-wide mentions with the same login (unless all set `"allow_duplicate"`). Each
problem prints with its fix; it exits 1 when anything is broken.

### The base prompt and the agent skill

[`prompts/base.md`](prompts/base.md) is a base prompt that, given a fresh firstmate home,
this checkout and a Basecamp project URL, sets the home up and re-establishes the
connection: which behaviors to turn on, what each pending record is and where it is
answered, who gets through, and the safety bounds. Hand it to the agent once, with `SYNC`,
`HOME` and the project URL filled in.

[`prompts/defaults.md`](prompts/defaults.md) is the workflow basecamp-mate ships as a
default, not a rule: where decisions go (a to-do assigned to the owner in the project),
what gets answered and when (every chat and Ping line), the decision to-do lifecycle,
check-in answers, reports on the Message Board, unmonitored input, advisories and
Dependabot alerts, failures. `init` copies it to `<home>/data/basecamp-sync/defaults.md`
when the home has none and never overwrites that copy; the home owns it, the agent follows
it over the skill and the inbox notes' hints, and the owner changes it by asking the agent
("put my decisions in the Ops project"). `diff prompts/defaults.md
<home>/data/basecamp-sync/defaults.md` shows how a home's copy differs from what ships now.
The backup keeps it; `doctor` flags a home without one. A home set up before this file
existed gets it on its next `init` or `setup`, or by copying it there; until then the
agent falls back to the shipped file, which is the same workflow the base prompt used to
fix.

[`skills/basecamp-sync/SKILL.md`](skills/basecamp-sync/SKILL.md) is the operating contract
a firstmate or second mate follows in a home that uses the sync, and points to the base
prompt. Install it once for Claude, Codex and Pi from this checkout:

```sh
for d in ~/.claude/skills ~/.codex/skills ~/.pi/agent/skills; do mkdir -p "$d" && ln -sfn "$PWD/skills/basecamp-sync" "$d/basecamp-sync"; done
```

## Config and state

One config per home, normally `<home>/data/basecamp-sync/config.json`, written by `init`
or by hand from
[`examples/config.example.json`](examples/config.example.json):

- `account`, `project`: Basecamp ids.
- `captain`: the captain's Basecamp person id: the assignee, and the only person whose
  word is a decision or approval.
- `operators` (optional): Basecamp person ids whose word authorizes the agent as the
  captain's does (approvals, decisions); they are listened to like the captain. Default:
  none. See "People the sync listens to".
- `people` (optional): the Basecamp person ids whose lines, comments and boosts are
  relayed, as participants; the captain is always included. Default: just the captain.
  Neither list needs its people on the project; a re-run of `init` keeps both unless
  `--replace-people`.
- `project_people` (optional): `{"<project id>": [person ids]}`: named participants heard
  only in that project. Default: none.
- `participants` (optional): `{"project": true}`, `{"company": true | <company id>}` and/or
  `{"domains": ["<domain>", ...]}`: also relay what anyone on the project (not a client), in
  the captain's (or that) company, or with an email at a domain, writes to the agent, as a
  participant's. Domains need a login that sees email addresses (an account admin).
- `blocked` (optional): `{}` or `{"reply": "<text>"}`: someone the sync does not listen to who
  @mentions, Pings or assigns the agent gets one short automatic reply (the default text, or
  yours). Absent or `false`: no reply. Either way nothing they write reaches the agent. `init`
  writes `{}` for a new home and keeps an existing home's setting. See "Blocked senders".
- `allow_duplicate` (optional): `true` lets a timer run go ahead beside another home's live
  run that watches the same thing with the same login. See "One home per project and
  login".
- `profile` (optional): the `basecamp` CLI login every call runs as (`-P <profile>`),
  including `run.sh`'s token refresh. Absent means the CLI's default login. It changes
  only who acts; `captain` stays the assignee and the only person whose 👍 approves, and
  the acting user's own comments and boosts are never relayed.
- `todos` (optional): `{}`, `{"todoset": "<to-do set id>"}` or `{"list": "<to-do list id>"}`,
  with `"project": <id>` when they are in another project of the account (default: the
  configured project): where `sync.py todo create` puts the owner's to-dos by default
  (loose on the set, or in the list); turns on the to-do comment reader. One decision can
  go elsewhere with `todo create --project`, `--list` or `--card-table --column` (a card);
  its comments, boosts, replies and completion then happen in that project, and its
  notifications are this home's.
- `message_board` (optional): the message board id `sync.py post-message` posts on; the
  owner's boosts on the agent's messages there are relayed.
- `timezone` (optional): an IANA zone name (like `America/Chicago`) for the times the sync
  writes for people: records' `at_local`, inbox notes and the session alert. Unset, the
  captain's Basecamp time zone (their profile's `time_zone`) is used, and UTC when that
  can't be read.
- `inbox` (optional): `{}` or `{"fm_home": "<firstmate home>"}`: deliver each new pending
  record as a note in that home's inbox (default: the `--home`).
- `session_alert` (optional, needs `inbox`): `{"chat": <chat id>, "after_minutes": 15}`: post
  one line to the owner in that chat once no firstmate session holds that home's lock while
  an inbox note has waited `after_minutes`.
- `notifications` (optional): on whenever `profile` is set. `false` turns it off;
  `{"mark_read": false}` leaves handled notifications unread; `{"unmonitored": false}`
  turns off `unmonitored` records. See Notifications.
- `mentions` (optional): `{}` or `{"scope": "project" | "account"}`: `"account"` relays the
  owner's @mentions of the acting login, and comments on threads it follows, from every
  project of the account (one home per login); the default `"project"` only from this one.
  Needs notifications. See Notifications (Mentions anywhere in the account).
- `listen` (retired): ignored, with one note in `sync.log`; its `"unmonitored": false` still
  applies. See "Upgrading from the event listener".
- `pings` (optional): `{}` or `{"limit": <n>}`: relay the owner's Pings (direct messages)
  to the acting login, reading at most `n` Pings a run (default 10).
- `assigned_todos` (optional): `{}` or `{"limit": <n>, "scope": "project" | "account"}`:
  treat a to-do a listened-to person assigns to the acting login as a request, fetching at
  most `n` newly assigned to-dos a sweep (default 10); `"scope": "account"` takes them from
  every project of the account (one home per login), the default `"project"` only from this
  one. See To-do requests.
- `assigned_cards` (optional): `{}` or `{"limit": <n>, "scope": "project" | "account"}`: the
  same for cards assigned to the acting login, never one the card mirror made. See To-do
  requests (Cards).
- `chats` (optional): chat (Campfire) ids whose owner questions are relayed.
  An entry may be `{"chat": <id>, "every_line": true}` to relay every owner line in that chat.
- `ask_chat` (optional): the chat id `sync.py ask` posts in.
- `checkins` (optional): `{"questionnaires": ["<questionnaire id>", ...], "timezone":
  "<IANA zone, e.g. America/Chicago>"}`. Use the account's time zone so the schedule's
  hour and minute match Basecamp's.
- `releases` (optional): `{"board": "<message board id>", "repos": {"owner/name": "<name>"
  or {"name": "<name>", "note": "<text>"}}, "prereleases": false}`. The name is the
  subject's first word (capitalized; `init` uses the mapped board name) and the note is
  appended to the body, e.g. `` "note": "Run `ta upgrade` to install." ``.
- `advisories` (optional): `{"repos": ["owner/name", ...]}`: record each new security
  advisory and each new or reopened open Dependabot alert of these GitHub repositories
  once. See Security advisories.
- `acknowledge` (optional): `{"read": "<emoji>", "decided": "<emoji>" | false}`: the boost
  put on everything read (`read`; `reply` takes it off once it answers), and on each deciding
  comment (the captain's or an operator's on a decision to-do or a to-do or card a decision
  step is on) once `todo complete` or `todo subtask` carries the decision out (`decided`,
  which also takes the read boost off). Absent: 👀 on a question and 👍 on anything else
  when read, nothing after deciding, as before. `init` writes `{"read": "👀", "decided":
  "👍"}` for a new home and keeps an existing home's.
- `schedule` (optional): `{"dated": "ask" | "due" | "before", "recurring": "ask" | "due" |
  "before"}`: when a scheduled request is done by default, carried on its records as `when`
  (with its `list`): `ask` the owner (a comment on it), on its `due` date, or `before` it.
  `"recurring"` applies to a repeating request, `"dated"` to one with only a due date.
  `sync.py schedule set --list <id> --dated ...` remembers an answer for one to-do list (or
  card column), and without `--list` for every list without its own, in `schedule.json`,
  which wins over the config. `init` writes `{"dated": "ask", "recurring": "due"}` for a new
  home with requests on and keeps an existing home's; without it, records carry no `when`
  and the home's defaults file's wording stands.
- `backup` (optional): `{"project": <bucket id>, "vault": <Docs & Files folder id>}`: keep
  one latest copy of the config and each state file in that folder, uploaded daily when
  changed. See Backup under Safety bounds.
- `timesheet` (optional): `{}`: post the
  agent's active time per to-do or card per finished day to its timesheet. See "Timesheets".
- `cards` (optional): the card mirror's switch. Absent means on when `tables` is set and
  off otherwise; `false` turns it off even with `tables` present.
- `repos` (card mirror): backlog repo name -> board name.
- `tables` (card mirror): per board, the card table id (`table`) and a column id for each of
  `Triage`, `Not now`, `Figuring it out`, `In progress`, `Ready for QA`, `Done`.

Everything else lives beside the config, never in this repo (except each config's run
claim, in `~/.local/state/basecamp-mate/runs/`; see "One home per project and login"):

| File | Kept by | Purpose |
| --- | --- | --- |
| `map.json` | the script (card mirror) | task/board -> card id, column, assignment, content digest, linked boards, seen comments and boosts, acknowledgement boosts queued and done, questions replied to |
| `releases.json` | the script | GitHub repo -> when the watch started (`since`), the tags seeded then, and the tags announced (tag -> message id) |
| `chats.json` | the script (and `sync.py watch`) | chat id -> line cursor, captured question lines, acknowledgement boosts queued and done, questions replied to (a chat nothing relays holds only the mentions recorded there); for a chat in another project, its `bucket` and `project_name`; for a watched chat, `watch` (`after`, `until`, `since`) |
| `checkins.json` | the script | check-in question id -> dates recorded as due (`recorded`) and dates answered (`answered`) |
| `inbox.json` | the script | the line cursor of `pending-comments.jsonl` delivered to the inbox, and request ids whose failure was logged |
| `session-alert.json` | the script | the session alert posted for the current outage (when, its line id, how many notes waited); empty while a session holds the lock |
| `messages.json` | the script | per agent message: comment cursor, owner comments recorded, acknowledgement boosts queued and done, comments replied to; boost counts and boosts seen on the messages and their comments |
| `notifications.json` | the script | per notification id, its cursor (`unread_at` when last handled, empty while its first handling keeps failing); when it last read `/my/readings.json`; the boost ids seen in `/my/boosts.json`; ids whose mark-read failed, to retry |
| `timer.json` | the script | when each behavior's timer step last ran (`ran`), for its cadence; while refused as a duplicate run, the homes it overlaps and when that was last logged (`duplicate`) |
| `threads.json` | the script | per followed thread (`<bucket>:<recording>`): its type and title, comment cursor, owner comments recorded, acknowledgement boosts queued and done, boost counts and boosts seen, comments replied to, and, after `reply --awaiting`, the answer awaited (`awaiting`: the agent's comment, `at`, `until`) |
| `pings.json` | the script | `since` (the Ping reader's first run) and, per Ping chat id, its bucket, title, URL, line cursor, owner lines recorded, acknowledgement boosts queued and done, boost counts and boosts seen, lines replied to |
| `unmonitored.json` | the script | unmonitored key -> the first notification (or boost) and recording recorded, when, how many were seen, and the owner's decision once handled |
| `backup.json` | the script | per backed-up file: its upload id in the backup folder, the SHA-256 of the content last uploaded, and when |
| `timesheet.json` | `sync.py timesheet` and the script | per key (`items`): its to-do or card (`on`) and the transcript sources counted, each with `from` and `to`, and `settled` once it stopped and every day is posted; the days of a stopped key that wait on a refusal (`unposted`: day -> hours, posted from there; its sources then marked `counted`, not read again); a settled key is not read again; per key and day, each entry posted (`posted`: hours, entry id, URL, when); keys reported as unmapped (`unmapped`); projects reported as refusing entries (`refused`: the error, and `probed`, the local day of its last try); keys whose to-do or card was not found (`missing`: the same) |
| `schedule.json` | `sync.py schedule set` | when scheduled requests are done: `default` and per to-do list or card column (`lists`), each `{"dated", "recurring"}`, as the owner answered |
| `blocked.json` | the script | `since` (when automatic replies to blocked senders started) and, per person and conversation (`<person>:<bucket>:<thread>`), when the last automatic reply was posted (kept two days) |
| `sync.lock` | the script | the shared state lock held by a timer run or a command |
| `participants.json` | the script | the project's non-client person ids, as last read for `"participants": {"project": true}` |
| `companies.json` | the script | person id -> their company id, as last read for `"participants": {"company"}` |
| `todos.json` | the script | to-do key -> to-do id, title, URL, created and completed times, comment cursor, owner comments recorded, acknowledgement boosts queued and done, comments replied to; for a to-do request (`request-<id>`), also who assigned it (`request`), how many times it was assigned (`requests`), a digest of its name and description, the project it is in (`bucket`, `project_name`), and why it closed (`closed`) |
| `assigned-todos.json` | the script | `ignored`: to-dos assigned to the acting login whose creator is not listened to, so the sweep fetches each once |
| `sync.log` | the script | one line per action, plus a counts line per run |
| `pending-comments.jsonl` | the script | captain comments and approvals waiting to be relayed, one JSON record per line (see below) |
| `extra-repos.json` | hand | `{"task": ["board", ...]}`: extra boards for a task |
| `figuring.json` | hand | `{"task": "why"}`: queued tasks that need a plan approved |
| `not-now.json` | hand | `{"task": "why"}`: tasks deferred |
| `skip.json` | hand | `["task", ...]`: tasks never mirrored |
| `boards.json` | hand | `{"task": ["board-owning task", ...]}`: extra tasks whose open Lavish boards are linked on this task's card |
| `decisions.json` | hand | `{"task": {"question": "...", "items": ["..."], "note": "..."}}`: decision text for a card waiting on the captain, rendered as the question, a numbered list and a note in place of the raw hold reason |
| `defaults.md` | the agent (copied once by `init`) | the home's workflow defaults (see "The base prompt and the agent skill"); the owner changes them by asking the agent |

## Reading the backlog

The backlog is read through `tasks-axi`, addressed the way firstmate's
`bin/fm-tasks-axi.sh` does it: run from the parent of `<home>/data`, with
`TASKS_AXI_FILE=<home>/data/backlog.md` for a markdown backend (per `.tasks.toml`).
`tasks-axi list` gives the task ids; `tasks-axi show <id> --full` gives state, repo,
hold reason/kind/until, `deps` (for "After"), and `links`.

`tasks-axi` has no JSON output for reads, so its plain `key: value` output is parsed.
One field falls back to text parsing: the **title**. `tasks-axi` keeps PR URLs inside
the title (they are cut out and merged with `links`), and on a row that repeats its
`(field: ...)` groups it keeps the leading ones in the title, so the title is cut at the
first `(repo:`/`(kind:`/`(priority:`/... the way the original markdown parser did.
`deps` is used rather than `blocked_by`, because `blocked_by` drops dependencies that
are already done while the cards list them all.

## Run it once

Needs Python 3.11+, `tasks-axi`, and an authenticated `basecamp` CLI.

```sh
python3 sync.py --home ~/path/to/home --config ~/path/to/home/data/basecamp-sync/config.json --dry-run
```

A run from the command line runs only the steps that are due, like the timer; add `--all`
to run every step now. Drop `--dry-run` to apply. `run.sh <home> <config> [--dry-run]` is
the scheduled entry point: it runs `sync.py renew`, then `sync.py`, all within a 240-second
cap (the renewal gets at most 80 seconds, each `basecamp auth` call 30).

`sync.py renew` ([`login.py`](login.py)) renews the OAuth token when it expires within 15
minutes (by the token's own `expires_at`) and guards the login the `basecamp` CLI keeps in
`credentials.json` (its file store, used with `BASECAMP_NO_KEYRING=1`):

- Before a renewal it copies that file to `credentials.json.basecamp-mate-backup` beside it
  (mode 0600), but only while the file still holds the profile. After a renewal that worked
  it copies the file again, so the copy never holds a refresh token the renewal spent. Every
  home and login shares the copy, so it is merged rather than replaced: an entry the file has
  already lost stays in the copy.
- It guards only the file store: `BASECAMP_NO_KEYRING` is set, `auth status` reports
  `"storage": "file"`, or status names no store and the backup holds the login. A login kept
  in the system keyring is only renewed, even when an old file backup holds it.
- Each entry is restored at most once per copy, by its own mark, so an emptied file never
  brings back another profile's login that was already restored and refused.
- If the profile is missing after the renewal, it puts the missing entry back from the copy
  (every entry when the whole file was emptied) and logs one `login: restored` line. It
  does this once per copy: if the restored login is refused again, it logs one
  `FAILED token refresh` line naming the `basecamp auth login` to run and stops renewing,
  since Basecamp blocks an address after a handful of refused renewals.
- After a renewal it flushes the file to disk, so a power cut cannot bring back the
  refresh token the renewal just spent.
- A run that cannot reach Basecamp (the minute after boot, before the network is up)
  leaves the stored login alone and skips the sync. This is not counted as a login failure;
  one `network:` line marks when the outage starts and one when it ends.

A failed renewal logs its `FAILED token refresh` line once per cause and then hourly, not
every run. A run first claims what its config watches and refuses to
run beside another home's live run on the same thing with the same login;
`--allow-duplicate` overrides that for one run (see "One home per project and login").

### A dedicated Basecamp user

To act as its own user instead of the captain, invite that user to the project, sign it
in as a separate CLI login, verify it, and set `"profile"` in the config:

```sh
basecamp auth login -P firstmate --account <account id>
basecamp api get /my/profile.json -P firstmate
```

Its token is short-lived; `run.sh` renews it with `basecamp auth refresh -P <profile>`
(through `sync.py renew`, which guards the stored login; see "Run it once").
Full setup steps, and what changes for the captain: [docs/firstmate-account.md](docs/firstmate-account.md).

## A systemd timer by hand

`init` installs a 30-second timer; to set one up by hand instead,
`~/.config/systemd/user/basecamp-sync.service` (also in [`examples/`](examples)):

```ini
[Unit]
Description=Mirror a firstmate backlog into its Basecamp project

[Service]
Type=oneshot
ExecStart=%h/src/basecamp-mate/run.sh %h/path/to/firstmate-home %h/path/to/firstmate-home/data/basecamp-sync/config.json
```

`~/.config/systemd/user/basecamp-sync.timer`:

```ini
[Unit]
Description=Basecamp sync for a firstmate backlog, every 30 seconds

[Timer]
OnBootSec=2min
OnUnitActiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now basecamp-sync.timer
```

## Tests

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_sync.py` covers the existing behaviors and is unchanged by the layer split;
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
`/my/readings.json`, the reply in a Ping, and a Ping's notification and boosts;
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
`tests/test_login.py` covers the login guard (`sync.py renew`) with a stubbed `basecamp auth`
and a temporary `credentials.json`: a renewal that empties the file restored from the
backup, a network-down run leaving the file untouched, only the lost profile put back beside
another login's newer entry, a restored login lost again not restored twice, the backup
retaken after a good renewal and keeping another profile's entry the file already lost, the
restore marks read and written under the CLI's locks, an emptied file not bringing back
another profile already restored once, a lost file-store login restored without
`BASECAMP_NO_KEYRING`, a keyring login renewed without the guard or a false FAILED line, no renewal far from expiry, and one FAILED line per cause;
`tests/test_assigned_todos.py` covers to-do requests with a stubbed `/my/assignments.json`:
the sweep, who assigned it, comments, boosts, edits and closing, reassignment, the agent
completing it, the `Assignment` and `Completion` notifications, and their notes, scheduled
and repeating to-dos (`due_on` and `repeats` on the records, a moved due date or changed rule
as an update, Basecamp's next occurrence recorded once as a request that `follows` the
completed one, a reused to-do, `todo show`), plus the account-wide
scope (a request in another project carrying its project, its comments, boosts, replies,
edits and closing in that project) and the unchanged project default;
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

## Pending records

Each line of `pending-comments.jsonl` is one JSON object with a `kind`. Records written
before `kind` existed have none; treat a missing `kind` as `"comment"`. Every record but
`checkin` is something a listened-to person did, and carries `author` (`id`, `name`),
`captain` (true when the captain wrote it) and `role` (`captain`, `operator` or
`participant`; null for a check-in question's writer nobody listens to); a `checkin`
carries the question's writer the same way. A record written before the `people` list has
none of them and is the captain's; one written before operators has no `role`. Only a
record whose `role` is `captain` or `operator` (without `role`: `captain` true) can be a
decision or an approval.

Times stay as Basecamp or GitHub wrote them (`at`, `created_at`); a record with a readable
time also carries it in the people's local time as `at_local` or `created_at_local`, like
`2026-10-21 09:14 CDT`. Quote the local one when telling a person a time. The zone is the
config `timezone`, else the captain's Basecamp time zone, else UTC. Logs and state files
stay in UTC.

- `comment`: `task`, `repo`, `card`, `comment` (id), `at`, `text`.
- `question`: the same fields as `comment`, for a comment containing `?`.
- `chat-question`: `chat` (id), `line` (id), `url`, `text`, `at`; `mention: true` when it
  @mentions the agent (or replies to its line) in a chat nothing else relays; `project`
  (`id`, `name`) when that chat is in another project (`"mentions": {"scope": "account"}`,
  or a chat watched with `sync.py watch`).
- `thread-comment`: `bucket`, `parent` (the thread's id), `parent_type` (e.g. `Document`,
  `Kanban::Card`, `Todo`), `title`, `comment` (id), `question`, `url`, `text`, `at`, `why`.
  A listened-to person commented on a thread the agent follows that no other reader tracks
  (a card nothing mirrors, a document, someone else's to-do), and it is the first comment
  after the agent's `reply --awaiting` there (`"why": "awaiting"`; see "Followed threads").
  Answer with `sync.py reply
  --recording <comment>`, which comments on the thread.
- `mention`: the same fields but `why`, for a comment that @mentions the agent or is a
  Basecamp Reply to its comment: addressed to it.
  Either carries `project` (`id`, `name`) when the thread is in another project of the
  account (`"mentions": {"scope": "account"}`).
- `ping`: `bucket` (the Ping's circle), `chat`, `line` (ids), `title` (the Ping's name),
  `url`, `text`, `at`. The owner wrote to the agent's login in a Ping; answer there with
  `sync.py reply --recording <line>`.
- `checkin`: `questionnaire`, `question` (ids), `date` (local, `YYYY-MM-DD`), `title`,
  `url`, `at`. A check-in question came due today; answer it with `sync.py answer`. An
  instruction in it is the captain's only when `captain` is true.
- `approval`: `task`, `repo`, `card`, `url` (card URL), `boost` (id), `text`, `at`. The captain
  (or an operator) gave the card a 👍: approve every recommendation on it as recommended.
- `todo-comment`: `key`, `todo` (id), `comment` (id), `question` (true when it contains
  `?`), `url` (the comment), `text`, `at`. The owner commented on a tracked to-do; answer
  with `sync.py reply --recording <comment>`.
- `todo-request`: `key` (`request-<to-do id>`), `todo` (id), `n` (how many times it was
  assigned; `reopened` when more than once), `title`, `text` (the description), `assignees`
  (names), `url`, `at`, `project` (`{"id", "name"}`, the project it is in), `due_on` (its due
  date, or null), `repeats` (Basecamp's repetition rule, or null) and `follows` (the to-do id
  of the repeating to-do's occurrence this one is next after, or null); `author` is who
  assigned it (for a next occurrence, whoever asked for the one before). A to-do assigned to the agent's login is
  a request: captain work when `captain` is true, a request to weigh otherwise. Complete it
  with `sync.py todo complete --todo <key>` when the work is done. Its later comments and
  boosts are `todo-comment` and `boost` records with `request: true`, `title` and `project`.
  A scheduled one is done on its due date as Basecamp shows it then (`sync.py todo show`);
  a repeating one is completed when done, and Basecamp makes the next occurrence.
- `todo-request-update`: `key`, `todo`, `n`, `title`, `project`, `text` (the new description), `url`,
  `at`, `due_on`, `repeats`; `author` is null (Basecamp does not say who edited it). The request's
  name, description, due date or repetition rule changed.
- `todo-request-closed`: `key`, `todo`, `n`, `title`, `project`, `reason` (`completed`, `trashed`,
  `archived` or `unassigned`), `url`, `at`, `due_on`, `repeats`; `author` is who completed it,
  when known, else null. The request is closed and no longer read; nothing is left to
  complete. When it repeats, Basecamp's next occurrence arrives as a new `todo-request`.
- `card-request`, `card-request-update`, `card-request-closed`: the same fields, for a card
  assigned to the agent's login (`assigned_cards`); `todo` is the card id, `title` the card's
  title and `text` its content. Handled like the to-do request records.
- `message-comment`: `message` (id), `subject`, `comment` (id), `question`, `url` (the
  comment), `text`, `at`. The owner commented on a post the agent made, usually feedback
  or an instruction on a report; act on it and answer with `sync.py reply --recording <comment>`.
- `boost`: `surface` (`chat`, `ping`, `card`, `card-comment`, `todo`, `todo-comment`,
  `checkin-answer`, `message`, `message-comment`, `thread-comment`), the surface's ids (`chat`; `bucket`, `chat`; `task`,
  `repo`, `card`; `key`, `todo`; `question`; `message`, `subject`; `bucket`, `parent`, `title`), `recording` (the
  boosted recording), `boost` (id), `text` (the boost's text), `url`, `at`. The owner's
  boost is an answer to what was boosted, like a comment (on a `thread-comment` surface an
  emoji-only boost is skipped); a 👍 on an assigned card by
  anyone but the captain is a `boost`, not an `approval`.
- `unmonitored`: `key` (`<what happened>/<recording type>`), `event_type`, `recording_type`,
  `notification` (the notification's or boost's id; `event` in records from the retired
  listener), `recording` (id), `title`, `text` (an excerpt), `creator` (`id`, `name`), `url`,
  `at`. The owner did something no enabled behavior handles; put it to them as a decision to-do asking how events like it should be
  handled, act on the answer, then `sync.py unmonitored handle --key <key> --decision <text>`.
- `timesheet-unmapped` (`timesheet` on): `key`, `days` (day -> hours not posted), `hours`
  (their total), `at`. The key has no to-do or card; name one with `sync.py timesheet start
  --key <key> --on <URL>`. Recorded once per key.
- `timesheet-refused` (`timesheet` on): `bucket` (the project), `key`, `date`, `hours`, `url`
  (the to-do or card), `error`, `code` (the CLI's), `at`. That project's timesheet refused
  the entry: timesheet tracking may be off there. It is tried again with one entry at most
  once a day. Recorded once per project until an entry there posts again.
- `timesheet-missing` (`timesheet` on): `key`, `bucket`, `date`, `hours`, `url`, `error`,
  `code`, `at`. The key's to-do or card was not found (deleted, or not visible to the login).
  Only that key is tried again, once a day; name another with `sync.py timesheet start --key
  <key> --on <URL>`. Recorded once per key until an entry for it posts again.

## License

MIT. See [LICENSE](LICENSE).
