#!/usr/bin/env python3
"""Connect an agent home to one Basecamp project, in two layers.

tools.py is the tools layer: small, single-purpose operations with no policy. The
readers poll Basecamp and append pending records; the commands post exactly what
the agent hands them (`reply`, `ask`, `answer`, `todo ...`, `post-message`).
behaviors.py is the behaviors layer: opt-in workflows a home turns on in its config
and that compose the tools: the card mirror ("tables", "repos"; off with "cards":
false), the chat inbox ("chats"), chat watches (`sync.py watch`: one chat in any project of
the account, kept in chats.json), chat asks ("ask_chat"), check-in answering
("checkins"), release announcements ("releases"), decision to-dos ("todos"),
reports ("message_board"), Pings ("pings": the owner's direct messages to the agent's
login), to-do requests ("assigned_todos": to-dos the owner assigns to the agent's login),
inbox delivery ("inbox"), the session alert ("session_alert"), the state backup ("backup"), timesheets ("timesheet":
the agent's active time per to-do or card per day, `sync.py timesheet`) and notifications (on with a "profile": every run reads the agent
login's notifications and boosts and runs the reader for each thread that changed, and
records the owner's unmonitored input; `sync.py unmonitored` keeps their keys).
prompts/base.md is the agent's side of each behavior.

The timer runs every 30 seconds. Each behavior's step runs when it is due (timer.json
keeps when each last ran): notifications and inbox delivery every run, the card mirror,
release announcements and check-ins every 5 minutes, and the other readers hourly as the
repair sweep (every 5 minutes without notifications). `--all` runs every step.

Deterministic, no model calls. Safety bounds, enforced here:
  - only the configured account, project, card tables, chats, check-ins, to-do set
    and message board are touched, and the chats the agent watches with `sync.py
    watch`, and, with "pings" on, the Pings the agent's login is in with the owner;
    with "assigned_todos" on, the project's to-dos assigned to the agent's login (with
    "scope": "account", any project's) are read and acknowledged with a 👀; the agent
    login's notifications and boosts are read across the account, but only this
    project's (and those Pings, requests and watched chats) are acted on, and an
    owner's @mention of the agent anywhere in the project is relayed (with
    "mentions": {"scope": "account"}, anywhere in the account, and comments on threads
    it follows there); with "backup" on, the config and state files are uploaded to
    its Docs & Files folder (a changed file as a new version of its upload there);
    with "timesheet" on, timesheet entries go only on the to-dos and cards the agent
    names with `sync.py timesheet start` (or tracks under the same key);
  - nothing is ever deleted, trashed or archived, except the acting user's own 👀
    boost once a question is answered; the timer run posts no comment, and its only
    writes besides the card mirror are 👀/👍 acknowledgement boosts, marking the agent
    login's handled notifications read, a Message Board announcement per new GitHub
    release, and, with "session_alert", one chat line to the owner while the firstmate
    session is down and inbox notes wait (once per outage), and, with "timesheet", one
    timesheet entry per piece of work per finished day (timesheet.json keeps each one posted);
  - only the explicit commands post, run by the agent, and each refuses to when no
    profile is set or the profile signs in as the owner;
  - id maps (map.json, todos.json, ...) make re-runs update or skip instead of
    duplicating.
All runtime state lives in the directory that holds the config file, except each config's
run claim (runs.py): a timer run refuses to run beside another home's live run that watches
the same project (or Pings, account-wide to-do requests or account-wide mentions) with the same login, unless
"allow_duplicate" or --allow-duplicate; `sync.py status` lists the runs.

Usage: sync.py --home <home> --config <config.json> [--all] [--dry-run] [--include-prereleases] [--allow-duplicate]
       sync.py status      (the runs recorded on this computer; also bin/basecamp-mate status)
       sync.py reply --home <home> --config <config.json> --recording <comment, chat line or Ping line id> (--body-file <file> | --boost <text>) [--again] [--dry-run]
       sync.py watch --home <home> --config <config.json> --chat <chat URL> [--after-line <line id>] [--until YYYY-MM-DD] [--dry-run]
       sync.py unwatch --home <home> --config <config.json> --chat <chat URL> [--dry-run]
       sync.py ask --home <home> --config <config.json> --body-file <file> [--dry-run]
       sync.py answer --home <home> --config <config.json> --question <check-in question id> --body-file <file> [--dry-run]
       sync.py todo create --home <home> --config <config.json> --key <key> --title <text> [--body-file <file>] [--project <id>] [--list <id>] [--due YYYY-MM-DD] [--subtask <question>]... [--card-table <id> --column <id>] [--dry-run]
       sync.py todo subtask --home <home> --config <config.json> --todo <key or id> --number <n> [--dry-run]
       sync.py todo track --home <home> --config <config.json> --key <key> --todo <id> [--project <id>] [--dry-run]
       sync.py todo comment --home <home> --config <config.json> --todo <key or id> --body-file <file> [--dry-run]
       sync.py todo complete --home <home> --config <config.json> --todo <key or id> [--dry-run]
       sync.py todo show --home <home> --config <config.json> --todo <key or id>
       sync.py schedule show|set --home <home> --config <config.json> [--list <id>] [--dated ask|due|before] [--recurring ask|due|before] [--dry-run]
       sync.py decide --home <home> --config <config.json> --on <to-do or card id or URL> --question <text> --body-file <file> [--key <key>] [--project <id>] [--decider <person id>] [--dry-run]
       sync.py post-message --home <home> --config <config.json> --subject <text> --body-file <file> [--dry-run]
       sync.py unmonitored list|handle|forget --home <home> --config <config.json> [--key <key>] [--decision <text>] [--dry-run]
       sync.py backup --home <home> --config <config.json> [--dry-run]
       sync.py timesheet start --home <home> --config <config.json> --key <key> [--on <to-do or card URL>] --workdir <dir>... [--dry-run]
       sync.py timesheet stop|post --home <home> --config <config.json> [--key <key>] [--dry-run]
       sync.py behaviors --home <home> --config <config.json>
       sync.py access --home <home> --config <config.json>      (who gets through to the agent, and where)
       sync.py setup [--answers <file.json>] [--yes]      (guided; also bin/basecamp-mate setup)
       sync.py doctor [--home <home>] [--config <config.json>]
       sync.py init <project URL> --login <profile> --home <home> [--captain <id or email>] [--listen-to <person>]... [--repo-map TABLE=REPO] [--dry-run]
"""
import argparse, json, subprocess, sys
from datetime import datetime, timezone

import behaviors, runs
from behaviors import COLUMNS, render_decision, render_release, inline_md  # noqa: F401
from tools import (Tools, THUMBS, EYES, BOOST_MAX, is_due, render_reply, is_emoji, is_thumbs_up,  # noqa: F401
                   parse_lavish, parse_show, task_item, none, parse_chat_url)


class Sync(Tools):
    """The tools for one home, with its behaviors configured; `main` is one timer run."""

    def __init__(self, home, config_path, dry=False, runner=subprocess.run, prereleases=False):
        super().__init__(home, config_path, dry=dry, runner=runner)
        self.behaviors = behaviors.configure(self, self.cfg, prereleases=prereleases)

    @property
    def card_mirror(self):
        return self.behaviors["card-mirror"]

    @property
    def cards(self):
        return self.card_mirror.on

    @property
    def tables(self):
        return self.card_mirror.tables

    @property
    def repo_map(self):
        return self.card_mirror.repo_map

    def column_for(self, it, notnow):
        return self.card_mirror.column_for(it, notnow)

    def body_for(self, *args, **kw):
        return self.card_mirror.body_for(*args, **kw)

    def main(self, items=None, due=False):
        """One run: each behavior that is on, in order; a behavior left out of the config makes no calls.

        With `due` (the timer's run), only the behaviors whose cadence has passed since
        they last ran (timer.json); otherwise all of them. The card mirror's slot logs
        "cards off" when it is off, so sync.log shows the timer alive every 5 minutes. It
        holds the shared state lock throughout, so a command waits for it.
        """
        plan = None
        with self.locked("sync"):
            timer = self.load("timer.json", {})
            ran = timer.setdefault("ran", {})
            if self.cfg.get("listen") not in (None, False) and not timer.get("listen_noted"):
                self.log('notifications replace the event listener: "listen" in the config is ignored'
                         + ('; its "unmonitored": false still applies (move it to "notifications")'
                            if isinstance(self.cfg["listen"], dict) and self.cfg["listen"].get("unmonitored") is False else "")
                         + "; re-run init (or basecamp-mate setup) to remove the listener service")
                timer["listen_noted"] = True
            now = datetime.now(timezone.utc)
            for b in self.behaviors.values():
                if not b.timer or (not b.on and b is not self.card_mirror):
                    continue
                last = ran.get(b.name)
                if due and b.every and last and (now - datetime.fromisoformat(last)).total_seconds() < b.every - SLACK:
                    continue
                if b.on:
                    out = b.run(items)
                    plan = out if b is self.card_mirror else plan
                else:
                    self.log("cards off" + (" (dry run)" if self.dry else ""))
                ran[b.name] = now.isoformat(timespec="seconds")
                if not self.dry:
                    self.save_json("timer.json", timer)
            if not self.dry:
                self.save_json("timer.json", timer)
        return plan

    def watch(self, url, after_line=None, until=None):
        """Tools.watch, then a warning when another home's live run on this login already reads the chat's project:
        while it does, the timer skips the chat, so only an @mention there reaches an agent (that home's)."""
        done = super().watch(url, after_line, until)
        bucket, chat, _ = parse_chat_url(url, self.account)
        if bucket == str(self.project):
            return done
        me = self.acting_id()
        homes = sorted(c.get("home") or "?" for c in runs.Registry().holders(
            self.config_path, self.cfg, me if isinstance(me, int) else None, bucket))
        if homes:
            say = f"watch: warning: another home's live run reads project {bucket} with this login ({', '.join(homes)}), " \
                  f"so chat {chat} is skipped while it runs: an @mention of the agent there reaches that home, " \
                  "but a plain answer reaches no agent"
            self.log(say)
            print(say)
        return done

    def claim(self, allow=False):
        """Claim what this config watches for this run (runs.py): False when another home's live run overlaps.

        Refused, it logs a FAILED line naming the other run, when the overlap is new and then
        at most hourly, and the run reads nothing. "allow_duplicate": true in the config (or
        `allow`) runs anyway, noting the overlap once. A dry run checks and claims nothing,
        and only prints.

        A chat watched with `sync.py watch` never refuses the run: when another live home on
        this login reads the chat's project as its own, this run skips that chat
        (`watch_held`), logged when that starts and when it ends, so that home reads it
        alone; this is checked every run, so it also covers a home that starts after the watch.
        """
        allow = allow or self.cfg.get("allow_duplicate") is True
        me = self.acting_id()
        reg = runs.Registry()
        self.watch_held = set()
        self.end_watches()  # a watch past its day, or with an "until" that is not a date, ends here, logged once
        buckets = {str(r.get("bucket") or self.project) for r in self.watches().values()}
        others = reg.claim(self.config_path, self.home, self.cfg, me if isinstance(me, int) else None, allow=allow, dry=self.dry,
                           buckets=buckets)
        self.watch_held = set(reg.held)
        now, say = reg.now(), print if self.dry else self.log
        homes = sorted(c.get("home") or "?" for c, _ in others)
        with self.locked("sync"):
            timer = self.load("timer.json", {})
            held = {b: sorted(c.get("home") or "?" for c in cs) for b, cs in sorted(reg.held.items())}
            if held != (timer.get("watch_held") or {}):
                for b, hs in held.items():
                    if (timer.get("watch_held") or {}).get(b) != hs:
                        chats = sorted(c for c, r in self.watches(held=True).items() if str(r.get("bucket") or self.project) == b)
                        say(f"watch: skipping watched chat {', '.join(chats)} in project {b}: another home's live run "
                            f"reads that project with this login ({', '.join(hs)}); an @mention of the agent there "
                            "reaches that home, but a plain answer reaches no agent until it stops")
                for b in sorted(set(timer.get("watch_held") or {}) - set(held)):
                    say(f"watch: reading the watched chats in project {b} again: no other home's live run reads it now")
                if held:
                    timer["watch_held"] = held
                else:
                    timer.pop("watch_held", None)
                if not self.dry:
                    self.save_json("timer.json", timer)
            dup = timer.get("duplicate") or {}
            hourly = not allow and now - dup.get("noted", 0) >= DUPLICATE_NOTE
            if others and (dup.get("with") != homes or dup.get("allowed") != allow or hourly):
                said = "; ".join(f"{runs.describe(c, now)}, also watching {', '.join(shared)}" for c, shared in others)
                say(f"running beside another home's live run (allow_duplicate): {said}" if allow else
                    f"FAILED duplicate run: another home's live run watches the same Basecamp with this login: {said}. "
                    "Stop that home's timer (or drop the overlapping setting from one config), or set "
                    '"allow_duplicate": true to run both on purpose; `basecamp-mate status` lists the runs')
                timer["duplicate"] = {"with": homes, "allowed": allow, "noted": now}
            elif not others and dup:
                say("duplicate run cleared: no other home's live run overlaps this one now")
                timer.pop("duplicate")
            else:
                return allow or not others
            if not self.dry:
                self.save_json("timer.json", timer)
        return allow or not others


SLACK = 20  # seconds: a step comes due this much early, so timer jitter never pushes it a whole run later
DUPLICATE_NOTE = 3600  # seconds between the FAILED lines of a run refused as a duplicate


def listen(a, runner):
    """`sync.py listen`, retired: notifications on the timer replaced the event listener. Logs that and exits 0,
    so a listener service left from before stays down (it restarts only on failure) until init removes it."""
    Sync(a.home, a.config, runner=runner).log(
        "listen: the event listener was removed; notifications on the timer read the owner's input now. "
        "Re-run init (or basecamp-mate setup) to remove this service.")
    return 0


def common(prog, description):
    ap = argparse.ArgumentParser(prog=prog, description=description)
    ap.add_argument("--home", required=True)
    ap.add_argument("--config", required=True)
    return ap


def read(path):
    with open(path) as f:
        return f.read()


def command(a, what, call, runner):
    s = Sync(a.home, a.config, dry=getattr(a, "dry_run", False), runner=runner)
    try:
        with s.locked("sync"):
            call(s)
    except Exception as e:
        s.log(f"FAILED {what} {type(e).__name__}: {e}")
        return 1
    return 0


def todo_cli(argv, runner):
    sub = argv[:1][0] if argv else None
    descs = {"create": "Create a to-do assigned to the owner and track it under a key.",
             "track": "Track an existing to-do under a key; its comments so far are not relayed.",
             "comment": "Comment on a tracked to-do.",
             "complete": "Mark a tracked to-do complete.",
             "subtask": "Complete one subtask of a tracked to-do, answered; the to-do completes once all are done.",
             "show": "Print a tracked to-do as Basecamp has it now: open or not, due date, repetition rule."}
    if sub not in descs:
        print("usage: sync.py todo {create,track,comment,complete,subtask,show} --home <home> --config <config.json> ...", file=sys.stderr)
        return 2
    ap = common(f"sync.py todo {sub}", descs[sub])
    if sub in ("create", "track"):
        ap.add_argument("--key", required=True, help="a stable key for this to-do, e.g. the task id")
        ap.add_argument("--project", help="the project it is in, any of the account's (default: the config's "
                                          '"todos" "project", else the configured project)')
    if sub == "create":
        ap.add_argument("--title", required=True)
        ap.add_argument("--body-file", help="the description, Markdown")
        ap.add_argument("--list", help="a to-do list id (default: the config's, else loose on the to-do set)")
        ap.add_argument("--due", help="YYYY-MM-DD")
        ap.add_argument("--subtask", action="append", default=[],
                        help="one decision, repeatable, every one when there are several: each becomes a native Basecamp subtask")
        ap.add_argument("--card-table", help="make it a card on this card table instead (with --column)")
        ap.add_argument("--column", help="the card table column the card goes in")
    elif sub == "track":
        ap.add_argument("--todo", required=True, type=int, help="the to-do's id")
    else:
        ap.add_argument("--todo", required=True, help="the tracked to-do's key or id")
    if sub == "subtask":
        ap.add_argument("--number", required=True, type=int, help="the subtask's number, from 1")
    if sub == "comment":
        ap.add_argument("--body-file", required=True, help="the comment, Markdown")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv[1:])
    if sub == "create" and bool(a.card_table) != bool(a.column):
        ap.error("--card-table and --column go together")
    card = (a.card_table, a.column) if sub == "create" and a.card_table else None
    calls = {"create": lambda s: s.todo_create(a.key, a.title, read(a.body_file) if a.body_file else "", a.list, a.due, a.subtask,
                                               a.project, card),
             "track": lambda s: s.todo_track(a.key, a.todo, a.project),
             "comment": lambda s: s.todo_comment(a.todo, read(a.body_file)),
             "complete": lambda s: s.todo_complete(a.todo),
             "subtask": lambda s: s.todo_subtask(a.todo, a.number),
             "show": lambda s: s.todo_show(a.todo)}
    return command(a, f"todo {sub}", calls[sub], runner)


def unmonitored_cli(argv, runner):
    sub = argv[:1][0] if argv else None
    descs = {"list": "List the unmonitored-event keys recorded, with each one's decision once handled.",
             "handle": "Mark an unmonitored-event key handled with the owner's decision; it stays quiet.",
             "forget": "Drop an unmonitored-event key, so the next such event is recorded again."}
    if sub not in descs:
        print("usage: sync.py unmonitored {list,handle,forget} --home <home> --config <config.json> ...", file=sys.stderr)
        return 2
    ap = common(f"sync.py unmonitored {sub}", descs[sub])
    if sub != "list":
        ap.add_argument("--key", required=True, help='e.g. "comment.created/Document", as the record names it')
        ap.add_argument("--dry-run", action="store_true")
    if sub == "handle":
        ap.add_argument("--decision", required=True, help="what the owner decided, e.g. ignore them")
    a = ap.parse_args(argv[1:])
    if sub == "list":
        print(json.dumps(Sync(a.home, a.config, runner=runner).load("unmonitored.json", {}), indent=1, sort_keys=True))
        return 0
    return command(a, f"unmonitored {sub}", lambda s: s.unmonitored_handle(a.key, a.decision) if sub == "handle"
                   else s.unmonitored_forget(a.key), runner)


def timesheet_cli(argv, runner):
    sub = argv[:1][0] if argv else None
    descs = {"start": "Count the agent's active time on a piece of work from now, for the timesheet of its to-do or card.",
             "stop": "Stop counting the agent's active time on a piece of work.",
             "post": "Post the finished days' active time that is not posted yet now (--dry-run prints what it would post)."}
    if sub not in descs:
        print("usage: sync.py timesheet {start,stop,post} --home <home> --config <config.json> ...", file=sys.stderr)
        return 2
    ap = common(f"sync.py timesheet {sub}", descs[sub])
    if sub in ("start", "stop"):
        ap.add_argument("--key", required=True, help="a stable key for the work, e.g. the task id")
    if sub == "start":
        ap.add_argument("--on", help="the to-do's or card's URL its entries go on (default: the to-do tracked "
                                     "under --key, else its mirrored card)")
        ap.add_argument("--workdir", action="append", default=[],
                        help="the directory the work's Claude Code session runs in (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv[1:])
    s = Sync(a.home, a.config, runner=runner)
    if not s.behaviors["timesheet"].on:
        print('sync.py timesheet: no "timesheet" in the config: add "timesheet": {} (or init --timesheet)', file=sys.stderr)
        return 2
    calls = {"start": lambda s: s.timesheet_start(a.key, a.on, a.workdir),
             "stop": lambda s: s.timesheet_stop(a.key),
             "post": lambda s: s.behaviors["timesheet"].run()}
    return command(a, f"timesheet {sub}", calls[sub], runner)


def cli(argv=None, runner=subprocess.run):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["init"]:
        import init_home
        return init_home.cli(argv[1:])
    if argv[:1] == ["setup"]:
        import setup_home
        return setup_home.cli(argv[1:])
    if argv[:1] == ["doctor"]:
        import doctor
        return doctor.cli(argv[1:])
    if argv[:1] == ["todo"]:
        return todo_cli(argv[1:], runner)
    if argv[:1] == ["unmonitored"]:
        return unmonitored_cli(argv[1:], runner)
    if argv[:1] == ["timesheet"]:
        return timesheet_cli(argv[1:], runner)
    if argv[:1] == ["reply"]:
        ap = common("sync.py reply", "Answer a captain question where it was asked, with a comment, line or boost, then remove the 👀.")
        ap.add_argument("--recording", required=True, type=int, help="the question comment's, chat line's or Ping line's id")
        how = ap.add_mutually_exclusive_group(required=True)
        how.add_argument("--body-file", help="plain-text answer (Markdown for a to-do comment)")
        how.add_argument("--boost", help=f"answer with a boost of this text ({BOOST_MAX} characters or fewer) on the "
                                          "recording instead of a comment, e.g. a thanks, \"done\" or a plain yes")
        ap.add_argument("--again", action="store_true", help="post even though this question was already answered")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        return command(a, f"reply {a.recording}", lambda s: s.reply(
            a.recording, None if a.boost is not None else read(a.body_file), again=a.again, boost=a.boost), runner)
    if argv[:1] in (["watch"], ["unwatch"]):
        what = argv[0]
        ap = common(f"sync.py {what}", "Watch one chat in any project of the account: relay the listened-to people's lines "
                    "there from a given line on." if what == "watch" else "Stop watching a chat.")
        ap.add_argument("--chat", required=True, help="the chat's Basecamp URL (a line's URL, chats/<chat>@<line>, also works)")
        if what == "watch":
            ap.add_argument("--after-line", type=int, help="relay only lines after this one (default: the line the URL "
                                                           "names, else the chat's newest line now)")
            ap.add_argument("--until", help="stop watching once this day (YYYY-MM-DD) has passed")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        return command(a, what, lambda s: s.watch(a.chat, a.after_line, a.until) if what == "watch"
                       else s.unwatch(a.chat), runner)
    if argv[:1] in (["ask"], ["answer"]):
        what = argv[0]
        ap = common(f"sync.py {what}", "Post a new line in the configured ask_chat, @mentioning the owner." if what == "ask"
                    else "Answer a recorded check-in question for today.")
        if what == "answer":
            ap.add_argument("--question", required=True, type=int, help="the check-in question's id")
        ap.add_argument("--body-file", required=True, help="plain text")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        return command(a, what, lambda s: s.ask(read(a.body_file)) if what == "ask"
                       else s.answer(a.question, read(a.body_file)), runner)
    if argv[:1] == ["schedule"]:
        sub = argv[1:2][0] if argv[1:] else None
        if sub not in ("show", "set"):
            print("usage: sync.py schedule {show,set} --home <home> --config <config.json> ...", file=sys.stderr)
            return 2
        ap = common(f"sync.py schedule {sub}", "Print when scheduled requests are done: the config's default and the "
                    "answers remembered per list." if sub == "show" else
                    "Remember when dated or repeating requests are done, for one to-do list (or card column) or by default.")
        if sub == "set":
            ap.add_argument("--list", help="the to-do list (or card column) id; without it, every list without its own")
            ap.add_argument("--dated", choices=("ask", "due", "before"), help="requests with a due date")
            ap.add_argument("--recurring", choices=("ask", "due", "before"), help="repeating requests")
            ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[2:])
        if sub == "show":
            s = Sync(a.home, a.config, runner=runner)
            print(json.dumps({"config": s.schedule, **s.load("schedule.json", {})}, indent=1, sort_keys=True))
            return 0
        if not (a.dated or a.recurring):
            ap.error("give --dated and/or --recurring")
        return command(a, "schedule set", lambda s: [s.schedule_set(w, v, a.list) for w, v in
                                                     (("dated", a.dated), ("recurring", a.recurring)) if v], runner)
    if argv[:1] == ["decide"]:
        ap = common("sync.py decide", "Put a decision on the to-do or card it is about: a comment there and a step "
                                      "(subtask) assigned to the person who has to decide.")
        ap.add_argument("--on", required=True, help="the to-do's or card's id or Basecamp URL")
        ap.add_argument("--question", required=True, help="the step's text: the decision, as a plain question")
        ap.add_argument("--body-file", required=True, help="the comment: context, options and recommendation, Markdown")
        ap.add_argument("--key", help="a stable key, when nothing tracks the to-do or card yet")
        ap.add_argument("--project", help="its project (default: the URL's, else the configured project)")
        ap.add_argument("--decider", help="who decides: the captain (default) or an operator, by person id")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        return command(a, "decide", lambda s: s.decide(a.on, a.question, read(a.body_file), a.key, a.project, a.decider),
                       runner)
    if argv[:1] == ["post-message"]:
        ap = common("sync.py post-message", "Post a message on the configured message_board.")
        ap.add_argument("--subject", required=True)
        ap.add_argument("--body-file", required=True, help="the message, Markdown")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        return command(a, "post-message", lambda s: s.post_message(a.subject, read(a.body_file)), runner)
    if argv[:1] == ["backup"]:
        ap = common("sync.py backup", "Upload the config and state files that changed to the configured backup folder now.")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        b = Sync(a.home, a.config, runner=runner).behaviors["backup"]
        if not b.on:
            print('sync.py backup: no "backup" in the config: add {"project": <bucket id>, "vault": <Docs & Files folder id>}',
                  file=sys.stderr)
            return 2
        return command(a, "backup", lambda s: s.behaviors["backup"].run(), runner)
    if argv[:1] == ["listen"]:
        ap = common("sync.py listen", "Retired: notifications on the timer replaced the event listener; logs that and exits.")
        ap.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
        ap.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
        return listen(ap.parse_args(argv[1:]), runner)
    if argv[:1] == ["status"]:
        argparse.ArgumentParser(prog="sync.py status", description="List the sync runs recorded on this computer (live or "
                                "stale, what each watches and with which login) and any sync.py running unrecorded.").parse_args(argv[1:])
        return runs.status()
    if argv[:1] == ["access"]:
        a = common("sync.py access", "List who gets through to the agent, and where; everyone else is blocked before it."
                   ).parse_args(argv[1:])
        Sync(a.home, a.config, runner=runner).access()
        return 0
    if argv[:1] == ["behaviors"]:
        a = common("sync.py behaviors", "List the behaviors and whether this config turns each on.").parse_args(argv[1:])
        s = Sync(a.home, a.config, runner=runner)
        for b in s.behaviors.values():
            print(f"{b.name:22} {'on ' if b.on else 'off'}  {b.runs:8}  ({', '.join(b.keys)})")
        return 0
    ap = argparse.ArgumentParser(description="One timer run: every behavior the config turns on that is due.")
    ap.add_argument("--home", required=True, help="firstmate home (holds data/backlog.md and state/)")
    ap.add_argument("--config", required=True, help="config.json; its directory holds all runtime state")
    ap.add_argument("--all", action="store_true", help="run every behavior's step now, whether or not it is due")
    ap.add_argument("--dry-run", action="store_true", help="plan only: no Basecamp writes, map.json untouched")
    ap.add_argument("--include-prereleases", action="store_true", help="also announce GitHub prereleases")
    ap.add_argument("--allow-duplicate", action="store_true",
                    help="run even beside another home's live run that watches the same Basecamp with this login")
    a = ap.parse_args(argv)
    s = Sync(a.home, a.config, dry=a.dry_run, runner=runner, prereleases=a.include_prereleases)
    try:
        with s.locked("sync"):
            if not s.claim(allow=a.allow_duplicate):
                return 1
            s.main(due=not a.all)
    except Exception as e:
        s.log(f"FAILED {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
