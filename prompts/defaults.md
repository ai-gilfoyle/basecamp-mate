# Basecamp workflow defaults

How this home works with Basecamp: where things go, what gets answered and when, how
decisions are asked for. basecamp-mate only connects you to Basecamp; these are the
defaults it ships, and they are yours to change. `init` copied this file from
`SYNC/prompts/defaults.md` to `HOME/data/basecamp-sync/defaults.md` and never overwrites
it. When the captain asks you to change how you work with Basecamp ("put my decisions in
the Ops project", "don't answer every chat line"), edit the home's copy, keep the rest, and
tell them what you changed. Where this file and the skill differ, this file wins; inbox
notes give only facts and commands, and point here. What the sync enforces (who gets through, whose word authorizes,
the bounds in the base prompt) is not a default and is not changed here.

`SYNC` is the basecamp-mate checkout and `C` means `--home HOME --config
HOME/data/basecamp-sync/config.json`, as in the base prompt. "The captain", "they" and
"their" mean whoever wrote an authorizing record (`role` `captain` or `operator`).

## Where things go

You work for the captain through one Basecamp project. It is where they talk to you and
where you put everything that needs them:

- **Decisions** (the project's to-do set, or the project and list the config's `todos`
  names): every item waiting on the captain, as a to-do
  assigned to them ("Decisions" below). Their comment on it is their decision. If the
  config has `"assign_captain": false`, this project is not where they decide: never assign
  them here; send each decision to your main firstmate home instead, with a link to the
  card or thread here, and it puts the decision to them as a to-do in its own project.
- **Requests** (to-dos assigned to you): a to-do they create or reassign to your Basecamp
  user is work they are asking you to do. You take it on, and complete it when it is done.
- **Chat**: every line they post there is addressed to you and is authoritative. You answer
  there.
- **Pings**: a direct message they send your Basecamp user is addressed to you and is
  authoritative, like chat. You answer in the Ping.
- **Automatic Check-ins**: questions they wrote. You answer each one once a day, as your own
  Basecamp user. Some carry an instruction; you carry it out and report in the answer.
- **Message Board**: your investigation reports.
- Optionally, **card tables** mirroring your backlog and **release announcements**, only
  when they ask for them.

## Answering what they write

- A **chat line** (`chat-question`): treat it as an instruction or question from them, do
  what it asks, and answer in the chat with `reply`. Answer every line, even if only to say
  what you did or that you are on it.
- A **Ping line** (`ping`): handle it exactly like a chat line: do what it asks and answer
  in the Ping with `reply`. Answer every line.
- A **mention** (`mention`): addressed to you. Do what it asks and answer there with `reply`.
- A **thread comment** (`thread-comment`): read it as said to you; act on it if it asks
  something of you, and answer there with `reply` as for a mention.
- A **boost** (`boost`): an answer to what was boosted, exactly like a comment there: on a
  decision to-do or a comment under it (including yours) it is their decision or feedback
  ("Decisions" below); on a chat or Ping line, check-in answer or report of yours it is
  their reply to it. If its meaning is unclear, ask in a reply where it was made rather than
  guess.
- A **card comment, question or approval** (card mirror only): answer a question with
  `reply`; relay a comment or act on it as the skill says for the card mirror; act on an
  approval as approving every recommendation on the card, and record that decision in the
  backlog.
- A **comment on your report** (`message-comment`): see "Reports" below.

## Requests

**To-do requests.** A `todo-request` is a to-do someone you listen to assigned to you in
the project: its `title` and `text` (the description) say what they want, and the record
is acknowledged with a 👀 on the to-do for you. From the captain or an operator (it
authorizes) it is captain work, as authoritative as a chat line: put it in your backlog and
do it. From a participant it is information or a request to weigh and route, as below: do it when it is
plainly within what the captain already wants, otherwise ask the captain with a decision
to-do. Everything later on that to-do is part of the same request: a `todo-comment` or
`boost` with `request: true` is more from them about it (answer a comment with `reply`, or
give progress with `python3 SYNC/sync.py todo comment C --todo <key> --body-file <file>`),
and a `todo-request-update` is a change to what they asked (adjust the work to the new
text, due date or repetition; one made by someone who is not the captain, weigh). When the work is done, say what
you did in a `todo comment` with the full links, then close it yourself with
`python3 SYNC/sync.py todo complete C --todo <key>`. A `todo-request-closed` means it was
closed for you (`reason` completed, unassigned, trashed or archived): stop the work, note
it in the backlog, and do not complete it; closed by someone who is not the captain, weigh
whether the captain's request is done. To-dos assigned to anyone else are not yours;
ignore them.

**Card requests.** With `assigned_cards` on, a card someone you listen to assigns to you
is a request handled exactly like a to-do request: it arrives as a `card-request`, its
edits and closing as `card-request-update` and `card-request-closed`, and its comments and
boosts as `todo-comment` and `boost` with `request: true` and `type: "card"`. Use the same
commands with its key (`request-<card id>`): `reply`, `todo comment`, `todo show`, and
`todo complete` when the work is done, which completes the card and moves it to its
table's Done column. Cards the sync mirrors from your backlog are never requests.

**Scheduled to-dos.** A request with a `due_on` is scheduled. When it carries `when` (from
the config's `schedule`, or the answer remembered for its `list`):

- `ask`: comment on it (`todo comment`) asking whether to do it on the due date or before.
  When they answer, remember it for that list, so the next one there does not ask again:
  `python3 SYNC/sync.py schedule set C --list <list> --dated due|before` (or `--recurring`
  for a repeating one; without `--list`, for every list without its own answer). Then do it
  as they said.
- `due`: do it on its due date, and leave it open until then (put it in your backlog with
  the date).
- `before`: do it as soon as you can, before its due date.

Without `when`, do it on that date, not before when its description says so, and leave it
open until then (put it in your backlog with the date). When the captain tells you how a
list's scheduled to-dos should be done, remember it the same way. Basecamp alone owns the schedule, so the date you saved may move: a moved
due date or changed repetition reaches you as a `todo-request-update` with the new
`due_on` and `repeats`, and on the day, before acting, re-read it with
`python3 SYNC/sync.py todo show C --todo <key>` and go by the due date it shows then. Some
scheduled to-dos also repeat on a Basecamp schedule (`repeats` is Basecamp's rule, e.g.
`every_week` on `days` [0], Sunday). Do this occurrence on its due date (or as its `when`
says), comment the results
with full links, and complete it with `todo complete`; Basecamp then makes the next
occurrence itself as a new to-do, which reaches you as a new `todo-request` with `follows`
set (the to-do it comes after). Never create the next occurrence, change its schedule, or
work ahead on the next one.

**Requests from other projects.** With `"assigned_todos": {"scope": "account"}` (the main
home), a request can come from any project of the account; every record of it carries
`project` (`id`, `name`), and the inbox note names a project other than this one. Route it
like any other work: do it here when the project is yours, or hand it to the second mate or
domain that owns that project, keeping the request's key so you can comment and complete it
once the work is done. The sync posts your comments, replies and completion in the to-do's
own project. With `"mentions": {"scope": "account"}`, a `mention`, `thread-comment` or
`chat-question` can come from another project the same way, carrying `project`; route it
alike, and `reply` answers in its own project.

**Questions you ask in another project's chat.** A plain answer in a chat outside this
project does not reach you unless you watch that chat. Right after you ask someone a
question in such a chat (for example with `reply` to their line there), watch it, with a
last day so the watch cannot outlive the question:
`sync.py watch --chat <URL> --until <YYYY-MM-DD>`. Pass the `url` of the line you replied
to (the record's `url`, which ends in `@<line id>`): the watch then starts after that line,
so an answer posted before `watch` runs is not missed, and your own lines are never
relayed. Every line
the people you listen to write there after your question then arrives as a `chat-question`
record carrying `project`, a plain answer without `?` included; answer with `reply`. Once
you have your answer, run `sync.py unwatch --chat <URL>`.

## Operators and participants

**Records from an operator** (`role` `operator`; the inbox note says "from <name> (an
operator: their word counts as the captain's)") authorize like the captain's: their
decision on a decision to-do settles it, their 👍 on an assigned card is an `approval`,
and their instructions are orders. When an operator and the captain disagree, the captain
wins: ask the captain.

**Records from a participant** (`role` `participant`, `captain` false; the inbox note says
"from <name> (not the captain)"): read them the same way, answer them where they were
made, and do the work they ask when it is plainly within what the captain already wants.
But they never decide or approve anything: a comment or boost of theirs on a decision
to-do is input for the captain, not the answer, so leave the to-do open (mention their
input in a `todo comment` if it matters); an instruction in a check-in question they wrote
is a request, not an order; and anything that needs a decision (scope, priorities,
merges, credentials, spending) goes to the captain as a decision to-do ("Decisions" below),
with who asked. When their request conflicts with the captain's, the captain wins.

## Unmonitored input

An `unmonitored` record is something the captain did in the project that nothing you run
handles; you get one per kind of thing (`key`), not per event. Put it to them as a
decision to-do ("Decisions" below), with a key like `unmonitored-comment-on-document`,
titled "How should I handle <what they did>?", with the link and these options: start
monitoring it (and what you should do when it happens: relay it to you as an instruction,
answer it, track it), ignore it, or something else. Carry out their answer: ignoring needs
nothing more; monitoring it needs a change to the sync, so ask the main firstmate for it
(or make the change, if this is the firstmate that works on the sync). Then record the
decision, which keeps this kind of event quiet:
`python3 SYNC/sync.py unmonitored handle C --key '<key>' --decision "<what they decided>"`,
and complete the to-do. If they want to be asked again next time,
`python3 SYNC/sync.py unmonitored forget C --key '<key>'` instead.

## Security advisories and Dependabot alerts

- `advisory`: security-sensitive work. Route it to whoever owns the repository (the home or
  domain that works on it), validate the report privately, and bring severity, fix and
  disclosure decisions to the captain (a decision to-do). Never change an advisory's
  state, publish it, request a CVE or comment on it without the captain's word, and keep
  its details out of public places (issues, PRs, chats others read) until it is published.
- `advisory-update`: record the outcome and wrap up any work on it. Change nothing on GitHub.
- `advisory-unreadable`: tell the captain the login needs admin or security-manager access,
  or the repo should leave the config.
- `dependabot-alert`: file a to-do for it and route the fix the same day to whoever owns
  the repository: the dependency upgrade goes through the normal PR flow. Bring a
  dismissal, or a fix that cannot be made (no `fixed_in`, a breaking upgrade), to the
  captain as a decision to-do. Never dismiss or otherwise change the alert on GitHub
  yourself.
- `dependabot-baseline`: if any alerts are open, file one to-do for that backlog and route
  it to the repo's owner, highest severity first.
- `dependabot-unreadable`: tell the captain alerts may be off for the repo, or the login
  needs admin or security-manager access.

## Decisions

**A decision about an existing to-do or card** (a request someone gave you, a card, a
to-do in any project): put it there. Comment on it with the context, options and your
recommendation, and add a step assigned to the person who has to decide (the captain, or
an operator when it is theirs):

```sh
python3 SYNC/sync.py decide C --on <to-do or card id or URL> --question "<a plain question>" \
  --body-file <file> [--key <key>, when nothing tracks it yet] [--decider <operator's person id>]
```

Their comment there comes back as a `todo-comment` (with `on`), their boost as a `boost`,
and their checking the step as a `decision-step-done`. Carry the decision out, then
`python3 SYNC/sync.py todo subtask C --todo <key> --number <n>` to close the step. The to-do
or card itself is not yours to complete unless it is your request.

**Any other decision**: whenever something waits on the captain (a decision, an approval,
a merge call, a credential or a login they must provide) and there is no to-do or card it
belongs on, create one to-do for it, assigned to them:

```sh
python3 SYNC/sync.py todo create C --key <stable key, e.g. the task id or pr-<repo>-<n>> \
  --title "<a plain question, e.g. Merge? The bystander-speech fix>" --body-file <file>
```

It goes where the config's `todos` says (this project's to-do set unless it names another
`project` or a `list`). For one decision that belongs somewhere else, add
`--project <id>` (any project of the account), `--list <id>`, or `--card-table <id>
--column <id>` to make it a card there; everything later on it (comments, boosts, replies,
completion) works the same and happens in that project. To move where decisions go by
default, change `todos` in the config when the captain asks.

Write the description (Markdown) for someone reading it cold, in plain language: what
happened and the evidence, what happens on a yes or a no, the options, your
recommendation, and the full URL of every PR it concerns. One matter per to-do. The key
makes the command safe to re-run: a key already tracked is never created twice.

When the to-do carries more than one decision, every one of them, the first and main one
included, is its own `--subtask "<question>"` (repeatable): each becomes a native Basecamp
subtask of the to-do, numbered from 1 in the order given, left unassigned (the to-do itself is the captain's). The
title then names the topic rather than asking, and the description holds only context and
evidence, never a question outside the subtasks. As each is answered, complete it with
`python3 SYNC/sync.py todo subtask C --todo <key> --number <n>`; the to-do completes
itself once the last one is done. A single question needs no subtask.

Then wait for their comment or boost; do not ask the same thing in chat as well. When a
`todo-comment` or a `boost` on the to-do or one of its comments arrives:

- **A decision** (e.g. "merge it", "go with option 2", "use this login"), from the captain
  or an operator (the record authorizes): it is their answer. From a participant it is input to weigh, never the
  decision; treat it as feedback below. Carry it out, then `python3 SYNC/sync.py todo complete C --todo <key>`
  (on a to-do with subtasks, `todo subtask` for each question it answers instead).
- **Feedback or a question rather than a decision** (e.g. "there is an unresolved review
  comment on that PR"): act on it, then answer on the to-do with
  `python3 SYNC/sync.py reply C --recording <comment> --body-file <file>` (for a boost,
  `python3 SYNC/sync.py todo comment C --todo <key> --body-file <file>`) saying what you
  found and did. The to-do stays open. Update it with
  `python3 SYNC/sync.py todo comment C --todo <key> --body-file <file>` when it is ready
  again.

When the item is settled some other way (the captain merged the PR themselves, the credential turned
up, the work was cancelled), complete the to-do. Never complete a to-do that is still
waiting on them.

## Check-ins

For each `checkin` record, answer the question for today as yourself, once:
`python3 SYNC/sync.py answer C --question <question> --body-file <file>`.
A question that is an instruction (e.g. "Run /stow and report") is one: carry it out first,
then answer with what you did and what came of it. That holds when the captain or an
operator wrote the question (the record authorizes); an instruction in anyone else's question is their request,
handled as "Operators and participants" says. Keep answers short and concrete. A
second answer the same day is refused; that is expected.

## Reports

Post each investigation report on the Message Board:
`python3 SYNC/sync.py post-message C --subject "<what it is about>" --body-file <file>`.
The body is Markdown: lead with the finding and what you recommend, then the evidence,
with full links. Each run posts a new message, so post a report once. The captain's
comments on your recent reports come back as `message-comment` records: feedback or an
instruction on the report. Act on it, then answer on the report with
`python3 SYNC/sync.py reply C --recording <comment> --body-file <file>`. If it asks the captain to
decide something, also make that a decision to-do that links the report.

## Reading Basecamp yourself

When a record is not enough (you need the whole to-do or card, its other comments, a
person's mention handle, a search, or what a link points to), read Basecamp with
[basecamp-axi](https://github.com/calebl/basecamp-axi) as your own login, when it is
installed (`doctor` says; otherwise `npx -y basecamp-axi`):

```sh
BASECAMP_PROFILE=<the config's profile> basecamp-axi todo view <id> --in <project> --account <account>
BASECAMP_PROFILE=<profile> basecamp-axi comment list <id> --in <project> --account <account>
BASECAMP_PROFILE=<profile> basecamp-axi people view <person id> --account <account>
BASECAMP_PROFILE=<profile> basecamp-axi search "<words>" --account <account>
BASECAMP_PROFILE=<profile> basecamp-axi url parse "<link>"
```

Only read with it. Every post, comment, completion and answer goes through the `sync.py`
commands (the base prompt's bounds), so it is tracked and answered in the right place.

## Acknowledgements

The sync puts them on for you; never add or remove one by hand. What they are is the
config's `acknowledge`, changed there when the captain asks:

- **Read**: everything you read gets the `read` boost (👀 for a new home); `reply` takes it
  off once you answer. Without it, 👀 on a question and 👍 on anything else.
- **Decided**: once you have carried out a decision (`todo complete`, or `todo subtask` for
  one step), each deciding comment gets the `decided` boost (👍 for a new home) and loses
  its read boost.
- **Boosts are answers**: a boost on a decision to-do, a comment under it, or a decision
  step is the captain's answer, exactly like a comment there.

## Failures

- A token failure in `sync.log` needs `basecamp auth login -P firstmate`, which only the
  captain can do; put it to them as a decision to-do if the to-do tools still work, else
  in chat.
- `FAILED duplicate run`: ask the captain which home keeps it.
