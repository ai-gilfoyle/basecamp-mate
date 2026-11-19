# Config and state

One config per home, normally `<home>/data/basecamp-sync/config.json`, written by `init`
or by hand from
[`examples/config.example.json`](../examples/config.example.json):

- `account`, `project`: Basecamp ids.
- `captain`: the captain's Basecamp person id: the assignee, and the only person whose
  word is a decision or approval.
- `operators` (optional): Basecamp person ids whose word authorizes the agent as the
  captain's does (approvals, decisions); they are listened to like the captain. Default:
  none. See [People the sync listens to](people.md).
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
  writes `{}` for a new home and keeps an existing home's setting. See [Blocked senders](people.md#blocked-senders).
- `allow_duplicate` (optional): `true` lets a timer run go ahead beside another home's live
  run that watches the same thing with the same login. See [One home per project and
  login](multiple-homes.md).
- `profile` (optional): the `basecamp` CLI login every call runs as (`-P <profile>`),
  including `run.sh`'s token refresh. Absent means the CLI's default login. It changes
  only who acts; `captain` stays the assignee and the only person whose 👍 approves, and
  the acting user's own comments and boosts are never relayed.
- `agent` (required): the agent's own Basecamp person id, a number: the person the login
  (`profile`, or the CLI's default login) must sign in as. `init --agent <id>` (or
  `setup`, which asks for it) writes it; nothing takes it from the login. A timer run,
  `backup`, `timesheet post`, `watch` and the commands that post signed in as anyone else
  do nothing, and without `agent` they do nothing either; the login backup keeps only a
  login confirmed as `agent` (see [The agent check](running.md#the-agent-check)).
- `todos` (optional): `{}`, `{"todoset": "<to-do set id>"}` or `{"list": "<to-do list id>"}`,
  with `"project": <id>` when they are in another project of the account (default: the
  configured project): where `sync.py todo create` puts the owner's to-dos by default
  (loose on the set, or in the list); turns on the to-do comment reader. One decision can
  go elsewhere with `todo create --project`, `--list` or `--card-table --column` (a card);
  its comments, boosts, replies and completion then happen in that project, and its
  notifications are this home's.
- `decision_lists` (optional): a list of to-do list ids, e.g. `[10525772928]`, in any
  project of the account: the lists that hold the agent's decision to-dos made outside
  `todo create`. `sync.py complete --todo <bucket>/<to-do id>` completes only a to-do in one
  of them, and never one that `todos.json` tracks (those use `todo complete`). Absent or
  empty, `complete` refuses every to-do.
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
  turns off `unmonitored` records. See [Notifications](notifications.md).
- `mentions` (optional): `{}` or `{"scope": "project" | "account"}`: `"account"` relays the
  owner's @mentions of the acting login, and comments on threads it follows, from every
  project of the account (one home per login); the default `"project"` only from this one.
  Needs notifications. See [Notifications](notifications.md) (Mentions anywhere in the account).
- `listen` (retired): ignored, with one note in `sync.log`; its `"unmonitored": false` still
  applies. See [Upgrading from the event listener](notifications.md#upgrading-from-the-event-listener).
- `pings` (optional): `{}` or `{"limit": <n>}`: relay the owner's Pings (direct messages)
  to the acting login, reading at most `n` Pings a run (default 10).
- `assigned_todos` (optional): `{}` or `{"limit": <n>, "scope": "project" | "account"}`:
  treat a to-do a listened-to person assigns to the acting login as a request, fetching at
  most `n` newly assigned to-dos a sweep (default 10); `"scope": "account"` takes them from
  every project of the account (one home per login), the default `"project"` only from this
  one. See [To-do requests](todo-requests.md).
- `assigned_cards` (optional): `{}` or `{"limit": <n>, "scope": "project" | "account"}`: the
  same for cards assigned to the acting login, never one the card mirror made. See [To-do
  requests](todo-requests.md) (Cards).
- `assigned_steps` (optional): `{}` or `{"limit": <n>, "scope": "project" | "account"}`: the
  same for card steps assigned to the acting login. See [To-do requests](todo-requests.md)
  (Card steps).
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
  once. See Security advisories in [safety-bounds.md](safety-bounds.md).
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
  changed. See Backup in [safety-bounds.md](safety-bounds.md).
- `timesheet` (optional): `{}`: post the
  agent's active time per to-do or card per finished day to its timesheet. See [Timesheets](timesheets.md).
- `cards` (optional): the card mirror's switch. Absent means on when `tables` is set and
  off otherwise; `false` turns it off even with `tables` present.
- `repos` (card mirror): backlog repo name -> board name.
- `tables` (card mirror): per board, the card table id (`table`) and a column id for each of
  `Triage`, `Not now`, `Figuring it out`, `In progress`, `Ready for QA`, `Done`.

Everything else lives beside the config, never in this repo (except each config's run
claim, in `~/.local/state/basecamp-mate/runs/`; see [One home per project and login](multiple-homes.md)):

| File | Kept by | Purpose |
| --- | --- | --- |
| `map.json` | the script (card mirror) | task/board -> card id, column, assignment, content digest, linked boards, seen comments and boosts, acknowledgement boosts queued and done, questions replied to and the boost that answered each (`answers`) |
| `releases.json` | the script | GitHub repo -> when the watch started (`since`), the tags seeded then, and the tags announced (tag -> message id) |
| `chats.json` | the script (and `sync.py watch`) | chat id -> line cursor, captured question lines, acknowledgement boosts queued and done, questions replied to and the boost that answered each (`answers`) (a chat nothing relays holds only the mentions recorded there); for a chat in another project, its `bucket` and `project_name`; for a watched chat, `watch` (`after`, `until`, `since`) |
| `checkins.json` | the script | check-in question id -> dates recorded as due (`recorded`) and dates answered (`answered`) |
| `inbox.json` | the script | the line cursor of `pending-comments.jsonl` delivered to the inbox, and request ids whose failure was logged |
| `session-alert.json` | the script | the session alert posted for the current outage (when, its line id, how many notes waited); empty while a session holds the lock |
| `messages.json` | the script | per agent message: comment cursor, owner comments recorded, acknowledgement boosts queued and done, comments replied to and the boost that answered each (`answers`); boost counts and boosts seen on the messages and their comments |
| `notifications.json` | the script | per notification id, its cursor (`unread_at` when last handled, empty while its first handling keeps failing); when it last read `/my/readings.json`; the boost ids seen in `/my/boosts.json`; ids whose mark-read failed, to retry |
| `timer.json` | the script | when each behavior's timer step last ran (`ran`), for its cadence; while refused as a duplicate run, the homes it overlaps and when that was last logged (`duplicate`) |
| `threads.json` | the script | per followed thread (`<bucket>:<recording>`): its type and title, comment cursor, owner comments recorded, acknowledgement boosts queued and done, boost counts and boosts seen, comments replied to and the boost that answered each (`answers`), attachment-only comments folded into a relayed note (`folded`), and, after `reply --awaiting`, the answer awaited (`awaiting`: the agent's comment, `at`, `until`) |
| `pings.json` | the script | `since` (the Ping reader's first run) and, per Ping chat id, its bucket, title, URL, line cursor, owner lines recorded, acknowledgement boosts queued and done, boost counts and boosts seen, lines replied to and the boost that answered each (`answers`), and `people`: the ids of the others in it as the reader last saw them, the only people a reply there may @mention |
| `unmonitored.json` | the script | unmonitored key -> the first notification (or boost) and recording recorded, when, how many were seen, and the owner's decision once handled |
| `backup.json` | the script | per backed-up file: its upload id in the backup folder, the SHA-256 of the content last uploaded, and when |
| `timesheet.json` | `sync.py timesheet` and the script | per key (`items`): its to-do or card (`on`) and the transcript sources counted, each with `from` and `to`, and `settled` once it stopped and every day is posted; the days of a stopped key that wait on a refusal (`unposted`: day -> hours, posted from there; its sources then marked `counted`, not read again); a settled key is not read again; per key and day, each entry posted (`posted`: hours, entry id, URL, when); keys reported as unmapped (`unmapped`); projects reported as refusing entries (`refused`: the error, and `probed`, the local day of its last try); keys whose to-do or card was not found (`missing`: the same) |
| `schedule.json` | `sync.py schedule set` | when scheduled requests are done: `default` and per to-do list or card column (`lists`), each `{"dated", "recurring"}`, as the owner answered |
| `blocked.json` | the script | `since` (when automatic replies to blocked senders started) and, per person and conversation (`<person>:<bucket>:<thread>`), when the last automatic reply was posted (kept two days) |
| `sync.lock` | the script | the shared state lock held by a timer run or a command |
| `participants.json` | the script | the project's non-client person ids, as last read for `"participants": {"project": true}` |
| `companies.json` | the script | person id -> their company id, as last read for `"participants": {"company"}` |
| `todos.json` | the script | to-do key -> to-do id, title, URL, created and completed times, comment cursor, owner comments recorded, acknowledgement boosts queued and done, comments replied to and the boost that answered each (`answers`); for a to-do request (`request-<id>`), also who assigned it (`request`), how many times it was assigned (`requests`), a digest of its name and description, the project it is in (`bucket`, `project_name`), and why it closed (`closed`) |
| `pending-completions.json` | `sync.py complete`, the script | `<bucket>/<to-do id>` -> a decision to-do `complete` has not yet seen completed (kept before its first read): its title, URL, the last error and whether it was the read (`lookup`), how many times in a row it was not found while its project read (`missing`), since when, when it was last tried (`tried`), and when the agent was told (`noted`) or the note failed (`note_failed`); each timer run retries the one tried least recently and drops it once Basecamp shows it completed, it is refused, or it was not found twice |
| `assigned-todos.json` | the script | `ignored`: to-dos assigned to the acting login whose creator is not listened to, so the sweep fetches each once |
| `sync.log` | the script | one line per action, plus a counts line per run |
| `pending-comments.jsonl` | the script | captain comments and approvals waiting to be relayed, one JSON record per line (see [Pending records](records.md)) |
| `relay.jsonl` | the script, `reply`, `dismiss` and the to-do commands | the relay ledger: one line per item relayed, per item the relay filter skipped, and per outcome (answered, dismissed); never backed up (see [Relay ledger and stats](relay-ledger.md)) |
| `extra-repos.json` | hand | `{"task": ["board", ...]}`: extra boards for a task |
| `figuring.json` | hand | `{"task": "why"}`: queued tasks that need a plan approved |
| `not-now.json` | hand | `{"task": "why"}`: tasks deferred |
| `skip.json` | hand | `["task", ...]`: tasks never mirrored |
| `boards.json` | hand | `{"task": ["board-owning task", ...]}`: extra tasks whose open Lavish boards are linked on this task's card |
| `decisions.json` | hand | `{"task": {"question": "...", "items": ["..."], "note": "..."}}`: decision text for a card waiting on the captain, rendered as the question, a numbered list and a note in place of the raw hold reason |
| `defaults.md` | the agent (copied once by `init`) | the home's workflow defaults (see [The base prompt and the agent skill](setup.md#the-base-prompt-and-the-agent-skill)); the owner changes them by asking the agent |
