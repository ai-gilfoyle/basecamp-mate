# Safety bounds

- A timer run, `backup`, `timesheet post`, `watch` or any command that posts (`reply`,
  `dismiss`, `todo` but `show`, `decide`, `ask`, `answer`, `post-message`, `schedule set`,
  `unmonitored handle`) whose login signs in as anyone but the agent (`agent` in the
  config), or whose config has no `agent`, does nothing: it reads only `/my/profile.json`,
  writes no state and logs a FAILED line, once per cause and then at most hourly. The
  login backup (`sync.py renew`) copies, and so restores, only a login confirmed to sign in
  as the agent. See [The agent check](running.md#the-agent-check).
- Only the configured account, project, card tables, chats, check-ins, to-do set and
  message board are touched, and the chats the agent watches with `sync.py watch`, and, with `pings` on, the Pings the acting login is in with
  the owner; with `assigned_todos` on, the project's to-dos assigned to the acting login are
  read and acknowledged with a 👀 (with `"scope": "account"`, those in any project of the
  account, and the comments and boosts on them). Notifications and boosts are read for the
  whole account, but only this project's items (and those Pings, requests and watched chats) are acted on;
  in the project (with `"mentions": {"scope": "account"}`, any project of the account), any
  thread the acting login follows or is @mentioned in is read for the owner's comments, and
  `reply` answers there. Their only writes are the readers' 👀/👍
  acknowledgements and marking handled notifications read; never another home's. The
  agent's `reply` and `dismiss` take its own read boost off again.
- Cards are never deleted, trashed or archived. Cards whose task left the backlog are
  left as they are (the run logs how many).
- The sync never posts as a comment, and never to chat, except the opt-in session alert
  (one line per outage, below) and, with `blocked` on, the short reply to a blocked sender
  where they addressed the agent (see [Blocked senders](people.md#blocked-senders)), and, with
  `decision_autocomplete` on, one "Answered in mate" comment on a decision to-do it then
  completes (below). Its other automatic post is a
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
  tracks under it); the transcripts are only read. See [Timesheets](timesheets.md).
- Chat questions: for each chat id in the optional `chats` config list, each run reads the
  chat's lines newer than a cursor kept in `chats.json` (the first run only sets the
  cursor, so older history is not relayed). A captain line that mentions the acting user
  or contains `?` is appended to `pending-comments.jsonl` as a `chat-question`, recorded
  once, and gets the acting user's 👀. Lines from anyone else, the acting user included,
  are never captured. `sync.py reply --recording <line id>` answers it with a new line in
  that chat as the acting user and then removes the 👀, under the same `--again` and
  failure rules as a card reply. In that line, and in a reply in a Ping, each
  `@First.Last` (the person's whole name, its spaces as dots) or `@First` (a first name or
  one-word name) becomes a Basecamp mention when it names exactly one person the acting
  user can @mention (`basecamp people pingable`) who is also on that chat's project
  (`/projects/<id>/people.json`) or, in a Ping, one of its participants as the Ping reader
  last saw them (`pings.json` keeps them as `people`). Each read is made once a run and
  only when the reply has an @name. An @name that matches no one or more than one person,
  or comes when a read fails or the Ping's people are not known, stays plain text and is
  logged; an @ in code, a link, an email address or a quote (a `>` line, or text inside a
  `<blockquote>` written in the reply) is never touched. A reply on a to-do, message or followed thread goes through `basecamp
  comments create`, which resolves @names itself; a reply on a card comment posts the
  rendered text unchanged. A chat configured as `{"chat": <id>, "every_line": true}`
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
  agent meanwhile; `watch` warns up front when such a home is live (see [One home
  per project and login](multiple-homes.md)). A home that never runs `watch` reads exactly as before.
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
- Decision autocomplete: with `"decision_autocomplete": {"source": "<mate base URL>"}`
  (and `todos`) set, every 5 minutes the run looks at each open to-do (or card) that
  `todo create` made and `todos.json` tracks, never a request, a to-do `decide` only put
  a decision on, or one adopted with `todo track` (marked `adopted`; one adopted earlier
  keeps Basecamp's own `created_at`, which `todo create`'s whole-second UTC stamp never
  matches), and never while one of its subtasks is unanswered. It reads each one's body from Basecamp once and keeps its one decision
  link, `…/#/decision/<home>/<task>`, as `decision` in `todos.json`; a to-do with no link
  (or links to two decisions) is never touched. When any to-do has a link, it reads
  `GET <source>/api/decisions` with `curl`, read-only. A decision whose home mate read
  (`homes[].ok`) and that is no longer in `cards` is closed: the to-do gets one comment,
  "Answered in mate: <answer>" (the newest answer in mate's `history` for it; "Answered in
  mate." when there is none), and is completed as `todo complete` does. A decision still
  listed, a home mate did not read and a source that cannot be read (curl fails, not JSON,
  no `homes` and `cards` lists) touch nothing that run; the last of these is logged once
  until it clears. A to-do that Basecamp already shows completed (or no longer finds) is
  left as it is and marked `decision_settled`. A completion that fails is retried next
  run without a second comment (`decision_comment` keeps that it was posted). `--dry-run`
  reads and only logs.
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
  confirmation" in [notifications.md](notifications.md)).
- Inbox delivery: with `inbox` set (`{}` for the `--home`, or `{"fm_home": "<home>"}`),
  each run ends by delivering every record appended to `pending-comments.jsonl` since the
  last delivery as a note through `<fm_home>/bin/fm-inbox.sh note --request-id
  basecamp-<kind>-<id> --json -` (FM_HOME set to that home). The note body is short and
  plain: what it is and who wrote it, the text, the Basecamp link, the facts that matter
  (whether their word authorizes, a due date Basecamp owns, a repeat Basecamp makes, a
  closed request) and the commands that act on it, with, for a comment, line or boost, how
  to dismiss it when it is not for the agent (`sync.py dismiss`). It never says how to handle it: that is
  the home's `defaults.md` (see [The base prompt and the agent skill](setup.md#the-base-prompt-and-the-agent-skill)), which the note
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
  answer for 7 days (see "Followed threads" in [notifications.md](notifications.md)). Text over
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
