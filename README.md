# basecamp-mate

**Run your AI agent from Basecamp.**

basecamp-mate connects an AI agent on your own computer to a Basecamp project. You work
with the agent the way you work with a colleague there: chat with it, assign it a to-do,
@mention it on a document. It comes back to you the same way, with to-dos assigned to you,
chat replies and Message Board posts. There is no new app to learn, and it all works from
the Basecamp app on your phone.

It is built for [firstmate](https://github.com/kunchenguid/firstmate): one agent you talk to,
which runs a crew of other agents on your Linux computer.

- **Decisions arrive as to-dos you answer from your phone.** When the agent needs you (a
  choice between options, an approval, a login only you can give) it assigns you a to-do
  with the context, the options and what it recommends. Your comment or boost on that
  to-do is your answer, and it reaches the agent in about half a minute.
- **Chat with your agent.** Ask it something in the project's chat and it answers there.
  Turn on Pings and to-do requests, and you can also message it directly or assign it a
  to-do: it takes the work on, comments as it goes and completes the to-do when it is done.
- **Check-ins become recurring reports.** Write a Basecamp Automatic Check-in question
  ("What did you get done today?") and the agent answers it on schedule, under its own
  name. A question can also be an instruction ("Check our dependencies and report"): it
  does the work, then answers with what came of it.
- **Agent work shows up where your team already looks.** Turn on what you want: the
  agent's reports on the Message Board, release announcements, its backlog as cards on a
  card table, and its working time on each to-do's timesheet.
- **You decide who it listens to.** By default that is only you on a personal account, and
  everyone in your company on a team account. Anyone else who writes to it gets a short
  automatic reply, and what they wrote never reaches the agent.

## What it looks like

<!-- Screenshot slot 1: replace this block with docs/images/decision-todo.png -->
> **[Screenshot to come] A decision, as a to-do assigned to you.** The agent asks which of
> two options to take, with its recommendation, and your one-line comment is the answer.

<!-- Screenshot slot 2: replace this block with docs/images/chat.png -->
> **[Screenshot to come] A chat with the agent.** You ask a question in the project chat;
> the agent marks it 👀 while it works and answers in the chat.

<!-- Screenshot slot 3: replace this block with docs/images/checkin.png -->
> **[Screenshot to come] A check-in answer.** The agent's answer to a recurring check-in
> question, beside the people's answers.

## A day with it

- **8:05** On the train, you open Basecamp. The agent has assigned you a to-do: "Merge?
  The invoice export fix", with what changed and its recommendation. You comment "yes".
- **8:06** The agent has your answer. It merges and comments the link on the to-do.
- **9:00** Your check-in "What's on today?" comes due, and the agent answers with its plan.
- **11:30** Between meetings, you Ping it: "Is the staging site back up?" It checks and
  answers in the Ping.
- **16:30** It posts an investigation report on the Message Board. You comment "go with
  option 2", and it starts on that.

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
   short automatic reply instead. The summary says which; see
   [People the sync listens to](docs/people.md) to change either.
6. It turns on what the project needs, starts the background services, runs one test sync
   and tells you what is on.

To change your answers later, run `setup` again. If something stops working, run:

```sh
basecamp-mate/bin/basecamp-mate doctor
```

It checks each piece (the Basecamp tool, the sign-in, the project, the card tables, the
background services, the last sync, the agent's wake-up) and prints each problem in plain
words with the exact fix.

## How it works

A background timer runs the sync every 30 seconds. It reads the agent's own Basecamp
notifications, picks out what someone it listens to wrote for the agent, and hands each one
to the agent as a short note. The agent answers with the sync's commands (`sync.py reply`,
`sync.py todo create`, `sync.py answer` and the rest), which post where the question was
asked. The sync makes no model calls and decides nothing on the agent's behalf: how the
agent works with Basecamp is a defaults file in its home that you change by asking it.
[How it works](docs/how-it-works.md) has the full list of tools and behaviors.

basecamp-mate connects a firstmate home: a directory on your computer where a firstmate
agent keeps its backlog, its state and an inbox of notes. By default the sync appends what it
reads to `pending-comments.jsonl` in the home, and a wake check wakes the agent when that
file grows. Inbox notes are opt-in (setup asks, and suggests no; `init --inbox`): with them
on, the sync also delivers each new record as a note in the firstmate inbox, and the wake
check then watches only for failed runs. The agent learns how to use the sync
from [`skills/basecamp-sync/SKILL.md`](skills/basecamp-sync/SKILL.md) and the
[base prompt](prompts/base.md).

## Reference

- [How it works](docs/how-it-works.md): the two layers, every tool and command, and every
  behavior with its config key, setup flag and timer.
- [People the sync listens to](docs/people.md): the captain, operators, participants, and
  the automatic reply to blocked senders.
- [Notifications](docs/notifications.md): how the agent hears you within half a minute,
  followed threads, boosts, unmonitored input and mentions anywhere in the account.
- [Pings](docs/pings.md): direct messages to the agent.
- [To-do requests](docs/todo-requests.md): to-dos, cards and card steps assigned to the agent,
  scheduled and repeating to-dos.
- [Card mirror](docs/card-mirror.md): the backlog as cards, and how the backlog is read.
- [Relay ledger and stats](docs/relay-ledger.md): what was relayed, what came of it, and
  `sync.py stats`.
- [Timesheets](docs/timesheets.md): the agent's active time on each to-do or card.
- [Safety bounds](docs/safety-bounds.md): exactly what the sync reads and writes, and the
  rules behind chat, check-ins, decision to-dos, reports, releases, advisories and backups.
- [One home per project and login](docs/multiple-homes.md): how two homes are kept from
  relaying the same thing twice, and `basecamp-mate status`.
- [Setup, doctor and the agent skill](docs/setup.md): `sync.py init` and its flags,
  guided setup, `doctor`, the base prompt and the workflow defaults.
- [Config and state](docs/config.md): every config key and every state file.
- [Running the sync](docs/running.md): a run by hand, the login renewal, a dedicated
  Basecamp user and a systemd timer by hand.
- [Pending records](docs/records.md): the fields of every record handed to the agent.
- [A Basecamp account for the agent](docs/firstmate-account.md): giving the agent its own
  Basecamp user.
- [Tests](docs/tests.md): what each test file covers.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests stub every command-line tool and make no network calls; [Tests](docs/tests.md)
lists what each file covers.

## License

MIT. See [LICENSE](LICENSE).

Formerly `firstmate-basecamp-sync`; the old URL redirects, and existing installs keep working unchanged (same skill, config keys and systemd unit names).
