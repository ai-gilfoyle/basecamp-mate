# Notifications

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
| an `Assignment`, `Event` (a step added already assigned) or `Completion` of a card step, which subscribes to the step and links to its card (`/card_tables/cards/<card>#__recording_<step>`), with `assigned_steps` on (in any project with `"scope": "account"`) | the card step request reader, or the request's edit and closing check; a decision step `sync.py decide` put there runs its record's reader instead; with `assigned_steps` off, an owner's assignment or completion of a step is unmonitored, never read as a to-do or card |
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
- the thread awaits an answer: a `sync.py reply --awaiting` there set a mark that has not
  expired, and nobody listened to has answered since. The first comment after the reply that
  set it, from anyone listened to but the agent, is relayed (`thread-comment` with `"why":
  "awaiting"`, or a `mention`) and clears the mark; so does a boost on that reply (below);
- the agent made the thread itself (its login is the creator of the card, document, upload
  or other recording; read once a run): `thread-comment` with `"why": "yours"`.

Any other comment is skipped: one `skipped comment <id> by <name> on <type> <thread>: <why>`
line in `sync.log` (no answer awaited, or posted before the reply that awaits one, or after
the mark ended), no record and no 👀 or 👍. The thread's cursor still moves past it, so it is
never read again, unless a mention notification later names it (a Reply that arrived
while the thread was being read): then it is recorded as a `mention`, unless it already
was or was folded into a note (below). The rule is deterministic: the text of a comment (a `?`, a name) never decides it, so
an ask that only names the agent, with no @mention or Reply, is skipped.
A comment of attachments only (an image, a file) right after a relayed comment by the same
person, in the same read, is folded into that comment's record (`attachments`: `filename`
and `url` each) instead of being judged on its own. Chat lines, @mentions, to-do and card
requests, completions and every other reader are unchanged; group Pings are read as below.

`sync.py reply --awaiting` on a thread comment or mention sets the mark for 7 days: `threads.json` keeps, for the thread, `awaiting` with the
agent's comment id (none when it answered with `--boost`, and then a comment dated before
the mark is not the answer), when it was set (`at`) and when it ends (`until`). A second
`--awaiting` reply there while the mark is unanswered and unexpired keeps its comment and
`at` and only moves `until` 7 days on, so an answer to the first question, posted before
the second reply, still counts. A comment or boost counts only when posted by `until`,
whenever it is read, and the earliest answer wins: a boost on the agent's comment made before
the first answering comment is the answer, and that comment is then judged like any other.
While the boosts on the agent's comment cannot all be read, that comment and every later one
wait for the next run, and the notification that led to the read counts as a failed read, so
it is read again then. A
mark whose `until` passed is dropped after the read, with a log line, unless a read of the
boosts on its comment failed (it is read again next run). A comment from someone not listened to,
or from the agent itself, leaves the mark as it is. `--awaiting` on any other reply (a card
mirror comment, a chat or Ping line, a tracked to-do, a message) is logged and ignored,
since everything there already reaches the agent.

**Boosts on other people's things.** A boost counts only on the agent's own recording (its
comment, line, card, message or answer): one on someone else's comment or line, even in a
chat or Ping the agent reads, is skipped with a `skipped boost <id> by <name> on <recording>:
not on the agent's own recording` line in `sync.log`. The exception is a decision to-do (or a
to-do or card a decision step is on), its comments and its decision steps, where any boost is
decision feedback, whoever made what was boosted, and a to-do request and its comments, where
a worded boost (not emoji only, as below) counts whoever made what was boosted.

On a followed thread, any boost on the agent's comment that holds the awaiting mark, made
by its `until`, is the awaited answer, whatever its text (a 👍 or ✅ is a yes): it is a
`boost` record with `"why": "awaiting"` and clears the mark. Otherwise an owner's boost of
emoji only (emoji and spaces, such as 👍, ❤️, 🎉🎉 or 1️⃣) on one of the agent's comments
there is skipped (one `skipped boost` line in `sync.log`), judged on the boost's text as the
agent's received-boosts feed gives it. A boost with words, punctuation or a symbol that is
not an emoji (`yes`, `a`, `?`, `!!`, `✓`) is relayed as before; elsewhere the emoji test
applies only to a boost on someone else's recording on a to-do request.

**Group Pings.** A Ping with three or more people (two or more besides the agent among its
participants; `pings.json` keeps `"group": true`) is read like a chat: a line is relayed
only when it @mentions the agent or directly follows one of
the agent's own lines there. Any other line is skipped (`skipped line <id> by <name> in
group Ping <chat>`), and someone not listened to who does not address the agent gets no
automatic reply. A Ping with one other person relays every line of theirs, as before. In
any Ping a line that is only uploads, right after a relayed line by the same person in the
same read, is folded into that line's record (`attachments`); one that follows nothing
relayed is its own `ping` record with its `attachments` and empty `text`.

**Scope.** Items in other projects are another home's: they are never read, acted on or
marked read here, except a Ping (with `pings` on), a to-do request with `"scope":
"account"`, a line in a chat watched with `sync.py watch` (see "Chat watches" in [safety-bounds.md](safety-bounds.md)), and, with `"mentions": {"scope": "account"}`, an @mention of the agent or a
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
the agent watches that chat (see "Chat watches" in [safety-bounds.md](safety-bounds.md)). It needs
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

## Unmonitored input

Unless `"notifications": {"unmonitored": false}`, owner input in the project that no
enabled behavior handles is recorded so the agent can ask the owner what to do about it:

| Notification or boost | Key |
| --- | --- |
| a line in a project chat not in `chats` | `chat.line.created/Chat::Lines` |
| a to-do assigned to the agent, or completed, with `assigned_todos` off | `todo.assignment_changed/Todo`, `todo.completed/Todo` |
| a card step assigned to the agent, or completed, with `assigned_steps` off | `step.assignment_changed/Kanban::Step`, `step.completed/Kanban::Step` |
| an owner boost on one of the agent's recordings nothing monitors | `boost.created/<type>`, e.g. `boost.created/Comment on Document` |
| a kind of notification Basecamp adds later | `<kind>/<recording type>` |

Comments and @mentions are never unmonitored: whatever they are on, a reader records them.
Each key is recorded once as an `unmonitored` record (fields in [records.md](records.md)) and delivered like any
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

## Upgrading from the event listener

Earlier versions polled Basecamp's event feed from a `sync.py listen` service beside the
timer (`"listen"` in the config). Notifications replaced it: they reached the agent in
0.5-11 seconds in the owner's tests, against 31-40 seconds for the feed, and never need
re-entering. A config that still has `"listen"` keeps working: the key is ignored, with one
note in `sync.log` (its `"unmonitored": false` still applies; move it to
`"notifications"`). `sync.py listen` only logs that and exits 0, so a listener service
left running stays down. Re-run `init` (or `basecamp-mate setup`) to remove the service and
drop `"listen"` from the config; `init` does both without `--force`, except over a
`"listen"` that still holds `"unmonitored": false`. `basecamp-mate doctor`
flags a listener service still installed. The old `feed.json`, `pings-feed.json` and
`requests-feed.json` are no longer read and can be deleted.
