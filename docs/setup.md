# Setup, doctor and the agent skill

`bin/basecamp-mate setup` (see "Getting started" in the [README](../README.md)) is the easy
way in. This page covers what it does underneath: `sync.py init`, the guided setup and
`doctor`, and the prompt and skill the agent follows.

## Set up a home

```sh
python3 sync.py init https://app.basecamp.com/<account>/projects/<project> --login firstmate --agent <agent person id> --home <home> --dry-run
```

`init` reads the project as the `--login` profile and discovers every id below: the card
tables in the project's dock, each matched to a backlog repo by its title
(case-insensitive, surrounding spaces ignored) against the names in the home's
`data/projects.md`; each table's columns by their exact names; the login's own identity
(`/my/profile.json`), which must be the `--agent <person id>` given (or, on a re-run
without it, the existing config's `agent`), recorded as `agent` for
[the agent check](running.md#the-agent-check); a new home without `--agent` is refused,
and an existing config without `agent` is left without it, so the sync does nothing until
it is set; the captain; the chats in the dock; and, when the dock has one
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
admit participants, written as `participants` (see [People the sync listens to](people.md); an email
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
`"listen"` key is rewritten without it, no `--force` needed, unless that key still holds
`"unmonitored": false`. `--pings` adds
`"pings": {}`: the owner's Pings to the `--login` are relayed (see [Pings](pings.md)).
`--assigned-todos` adds `"assigned_todos": {}`: a to-do the owner assigns to the
`--login` is a request (see [To-do requests](todo-requests.md)). `--assigned-todos-anywhere` adds
`"assigned_todos": {"scope": "account"}` instead: one in any project of the account is (only
in the main home; see [To-do requests](todo-requests.md)). `--assigned-cards` adds `"assigned_cards": {}`: a
card the owner assigns to the `--login` in the project is a request (see [To-do requests](todo-requests.md)).
`--assigned-steps` adds `"assigned_steps": {}`: a card step the owner assigns to the
`--login` in the project is a request (see [To-do requests](todo-requests.md)).
`--mentions-anywhere` adds `"mentions": {"scope": "account"}`: the owner's @mentions of the
`--login`, and comments on threads it follows, in any project of the account are relayed
(only in the main home; see [Notifications](notifications.md)).

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

## Guided setup and doctor

`bin/basecamp-mate setup` ([`setup_home.py`](../setup_home.py), also `sync.py setup`) is the
guided front end to `init` for people: it checks Python, the `basecamp` CLI (offering its
installer; 0.12.0 or newer) and systemd user services; asks for the home; signs the login in with
`basecamp profile create <login> --device-code` (or `auth login -P <login> --device-code`)
under `BASECAMP_NO_KEYRING=1`; lists the login's accounts and projects to pick from and the
project's people to pick the captain from; asks for the agent's person id (shown beside the
login's own, but typed, never taken from the login; `init` refuses when they differ); asks the yes/no questions (chat inbox, decision
to-dos, check-ins on; card mirror, releases, reports, Pings off by default) and the
check-in time zone, and, when inbox notes are chosen, whether to turn on the session alert
(default yes, 15 minutes, in the dock's chat); enables or adds the dock tool each chosen behavior needs (to-do set,
questionnaire, chat, message board) and, for the card mirror, a card table per registered
repo that has none; then runs `init` itself (with `--create-missing-columns` only for the card
mirror, `--force` after asking when the home already has a config, and `--no-keyring`), runs
`run.sh` once as a test sync, and prints what is on and how to change it.
`--answers <file.json>` answers by key (`home`, `login`, `account`, `project` (id or URL),
`captain` (person id), `agent` (person id), `timezone`, `replace`, `install_cli`, and booleans `chat`, `todos`,
`checkins`, `cards`, `releases`, `reports`, `pings`, `inbox`, `session_alert`) and `--yes` takes the
defaults; without a terminal, anything that needs a person (a sign-in, an unanswered pick)
refuses with what to do instead of asking.

`bin/basecamp-mate doctor` ([`doctor.py`](../doctor.py), also `sync.py doctor`) checks Python
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

## The base prompt and the agent skill

[`prompts/base.md`](../prompts/base.md) is a base prompt that, given a fresh firstmate home,
this checkout and a Basecamp project URL, sets the home up and re-establishes the
connection: which behaviors to turn on, what each pending record is and where it is
answered, who gets through, and the safety bounds. Hand it to the agent once, with `SYNC`,
`HOME` and the project URL filled in.

[`prompts/defaults.md`](../prompts/defaults.md) is the workflow basecamp-mate ships as a
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

[`skills/basecamp-sync/SKILL.md`](../skills/basecamp-sync/SKILL.md) is the operating contract
a firstmate or second mate follows in a home that uses the sync, and points to the base
prompt. Install it once for Claude, Codex and Pi from this checkout:

```sh
for d in ~/.claude/skills ~/.codex/skills ~/.pi/agent/skills; do mkdir -p "$d" && ln -sfn "$PWD/skills/basecamp-sync" "$d/basecamp-sync"; done
```
