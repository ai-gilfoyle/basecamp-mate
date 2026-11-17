# Running the sync

## Run it once

Needs Python 3.11+, `tasks-axi`, and an authenticated `basecamp` CLI.

```sh
python3 sync.py --home ~/path/to/home --config ~/path/to/home/data/basecamp-sync/config.json --dry-run
```

A run from the command line runs only the steps that are due, like the timer; add `--all`
to run every step now. Drop `--dry-run` to apply. `run.sh <home> <config> [--dry-run]` is
the scheduled entry point: it runs `sync.py renew`, then `sync.py`, all within a 240-second
cap (the renewal gets at most 80 seconds, each `basecamp auth` call 30).

`sync.py renew` ([`login.py`](../login.py)) renews the OAuth token when it expires within 15
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
every run. A run then makes [the agent check](#the-agent-check), and next claims what its config watches and refuses to
run beside another home's live run on the same thing with the same login;
`--allow-duplicate` overrides that for one run (see [One home per project and login](multiple-homes.md)).

## The agent check

A sign-in can be given to the wrong person: after a login outage, someone approves
`basecamp auth login -P <profile>` in a browser signed in as themselves. Before anything
else, each timer run reads `/my/profile.json` and compares its id with the config's `agent`
(see [Config and state](config.md)), reads it again once it holds the state lock, and
once more just before it writes its run claim. If the login signs in as anyone else, the run does nothing more: it reads no notifications,
Pings or threads, boosts nothing, marks nothing read, writes no record, inbox note, state
file or run claim, and exits 1. It logs a line that names both people and the command to
sign in again:

```
FAILED wrong login: the sign-in firstmate is Jay Park (50519852), not the agent (52942343), so nothing ran and no state changed; sign in as the agent: BASECAMP_NO_KEYRING=1 basecamp auth login -P firstmate --device-code
```

A run that cannot read `/my/profile.json` at all is skipped the same way, with a
`FAILED login check` line, and so is a config without `agent`, with a `FAILED login check`
line that says to add it by hand or to re-run `init` with `--agent <id>`, which adds it
to a config that lacks it without `--force`. A dry run prints the line and writes nothing.
When the right person signs in again, the next run carries on from the state the last good
run left. `backup`, `timesheet post` and `watch` make the same check, before and inside the
state lock. The check is the same with or without `profile`.

## A dedicated Basecamp user

To act as its own user instead of the captain, invite that user to the project, sign it
in as a separate CLI login, verify it, and set `"profile"` and `"agent"` (its person id) in
the config:

```sh
basecamp auth login -P firstmate --account <account id>
basecamp api get /my/profile.json -P firstmate
```

Its token is short-lived; `run.sh` renews it with `basecamp auth refresh -P <profile>`
(through `sync.py renew`, which guards the stored login; see [Run it once](#run-it-once)).
Full setup steps, and what changes for the captain: [firstmate-account.md](firstmate-account.md).

## A systemd timer by hand

`init` installs a 30-second timer; to set one up by hand instead,
`~/.config/systemd/user/basecamp-sync.service` (also in [`examples/`](../examples)):

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
