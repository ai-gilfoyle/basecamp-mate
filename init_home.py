"""`sync.py init`: set a firstmate home up to mirror its backlog into one Basecamp project.

Reads the project through the `basecamp` CLI login and discovers every id the sync
needs: account and project from the URL, each card table in the dock (matched to a
backlog repo by its title, case-insensitively, against the home's data/projects.md, or
by --repo-map), each table's columns by their exact names, the login's own identity,
the captain, the chats in the dock, and, when the dock has one message board, the
GitHub repos behind the mapped repos' <home>/projects/<repo> origins, whose releases
the sync announces there. Any missing or ambiguous table, column or
identity is refused with a message; nothing is guessed. A card table whose title
matches no registered repo, and that --repo-map does not name, is skipped: it is left
out of the config and its columns are never read.

It then writes <home>/data/basecamp-sync/config.json and the empty hand-kept side files,
installs and enables a per-home systemd user timer (every 30 seconds, 240s cap; each
behavior's step runs at its own cadence inside it), removes the retired event-listener
service if an earlier version installed one, and registers the comment wake check
through the home's own bin/fm-check-register.sh.
Re-running with the same inputs changes nothing; --dry-run prints the discovered config
and the planned installs and writes nothing, locally or in Basecamp.

With --no-cards no card table is read and the config has no "tables" or "repos"; every
registered repo with a GitHub origin becomes a release source.

Four opt-in flags turn on more behaviors from the dock, each refusing when the dock
has none or several of what it needs: --todos (decision to-dos on the one to-do set),
--reports (posting to the one message board), --every-line (relay every owner line
in the chats) and --checkins <time zone> (the one Automatic Check-ins questionnaire).
--no-releases leaves release announcements out. --inbox delivers each pending record
as a firstmate inbox note, and the wake check then watches only failures;
--session-alert [MINUTES] (with --inbox) adds the session alert: one line in the dock's
chat, @mentioning the owner, once no firstmate session holds this home's lock while an
inbox note has waited that long (default 15), so a reboot cannot strand messages unseen. --pings
relays the owner's Pings (direct messages) to the --login, which live outside the
project. --assigned-todos treats a to-do the owner assigns to the --login in the
project as a request: relayed, acknowledged and tracked until done;
--assigned-todos-anywhere does so in every project of the account (only one home per
login should: the main one, which routes each request to the right domain). --mentions-anywhere
relays the owner's @mentions of the --login, and comments on threads it follows, in every
project of the account, not just this one (the same one home per login). --assigned-cards
does the same for a card the owner assigns to the --login in the project (never a card the
sync mirrors from the backlog). --advisories
<owner/name> (repeatable) records each new GitHub security advisory of that repository once. --backup
<Docs & Files folder URL> keeps a daily snapshot of the config and state files in that folder. --timesheet
posts the agent's active time on each to-do or card it names with sync.py timesheet start to that
to-do's or card's Basecamp timesheet, once per day (a re-run without it keeps an existing setting). --listen-to <person> (repeatable: a person id, email or exact name) adds
people the sync listens to besides the captain, as "people"; their input is relayed
with who wrote it, but only the captain decides or approves. --operator <person> (the same
forms) names someone whose word authorizes the agent as the captain's does, as
"operators". A person not on the project is looked up among the account's people and
written all the same, with a line saying so; a re-run keeps the people and operators an
existing config names besides these, unless --replace-people; --participants-project (anyone on the project who is not a client),
--participants-company (anyone in the captain's company in Basecamp: the default for a new
home on a team account, where others are in that company; --no-participants-company for
the captain only; a re-run without either keeps an existing config's setting) and
--participants-domain <domain> (anyone whose email is at it; Basecamp shows other people's
emails only to account admins, so for most logins it admits nobody) admit participants,
who may ask the agent things but whose word never authorizes it. Anyone else never reaches
the agent; a new home also gets "blocked": {}, so someone else who @mentions, Pings or
assigns the --login gets one short automatic reply (--blocked-reply <text> sets its text,
--no-blocked-reply leaves it off, and a re-run without either keeps an existing config's
setting). --listen is accepted for
old scripts and does nothing: notifications on the timer replaced the event listener,
and an existing config's "listen" key is dropped without --force. --no-chats leaves the
dock's chats out. --no-keyring (on by default when BASECAMP_NO_KEYRING is set) gives the
units BASECAMP_NO_KEYRING=1, so every basecamp call they make uses the file credential
store and never waits on a locked system keyring.

The only Basecamp write is creating a missing regular column, and only with
--create-missing-columns.
"""
import argparse, hashlib, json, os, re, subprocess, sys
from zoneinfo import ZoneInfo

from behaviors import COLUMNS

HERE = os.path.dirname(os.path.abspath(__file__))
CREATABLE = ("Figuring it out", "In progress", "Ready for QA")  # Triage, Not now and Done are built in
CHECK_ID = "basecamp-sync"
TIMER_SECONDS = 30  # the timer's period; each behavior's step runs at its own cadence inside it (behaviors.py)
SIDE_FILES = {"extra-repos.json": {}, "figuring.json": {}, "not-now.json": {}, "skip.json": [],
              "boards.json": {}, "decisions.json": {}, "pending-comments.jsonl": None}
# The workflow defaults basecamp-mate ships; init copies them beside the config once, and the home owns that copy.
DEFAULTS = os.path.join(HERE, "prompts", "defaults.md")

CHECK = """#!/usr/bin/env bash
# Wakes this home's firstmate when the Basecamp sync records a new captain comment,
# question or approval (data/basecamp-sync/pending-comments.jsonl) or a failed run,
# once per change. Installed by firstmate-basecamp-sync `sync.py init`.
set -u
STATE_DIR="$(cd "$(dirname "$0")" && pwd)"
D="$STATE_DIR/../data/basecamp-sync"
MARK="$STATE_DIR/.basecamp-sync.seen"
n=$(cat "$D/pending-comments.jsonl" 2>/dev/null | wc -l)
f=$(grep -c "FAILED" "$D/sync.log" 2>/dev/null); f=${f:-0}
cur="$n $f"
[ "$(cat "$MARK" 2>/dev/null)" = "$cur" ] && exit 0
printf '%s\\n' "$cur" > "$MARK"
[ "$n" -gt 0 ] || [ "$f" -gt 0 ] && echo "basecamp sync: $n pending record(s), $f failed run(s) - follow the basecamp-sync skill: read data/basecamp-sync/pending-comments.jsonl and sync.log; relay comments and approvals to the main firstmate and wait for its answer before acting; answer informational questions in this home's scope with sync.py reply"
exit 0
"""

# With inbox delivery on, each pending record already arrives as a firstmate inbox note
# (its own wake), so the check watches only for failed runs and failed notes.
CHECK_INBOX = """#!/usr/bin/env bash
# Wakes this home's firstmate when a Basecamp sync run or inbox note fails
# (a FAILED line in data/basecamp-sync/sync.log), once per change. Pending records
# arrive as inbox notes. Installed by firstmate-basecamp-sync `sync.py init --inbox`.
set -u
STATE_DIR="$(cd "$(dirname "$0")" && pwd)"
D="$STATE_DIR/../data/basecamp-sync"
MARK="$STATE_DIR/.basecamp-sync.seen"
f=$(grep -c "FAILED" "$D/sync.log" 2>/dev/null); f=${f:-0}
[ "$(cat "$MARK" 2>/dev/null)" = "$f" ] && exit 0
printf '%s\\n' "$f" > "$MARK"
[ "$f" -gt 0 ] && echo "basecamp sync: $f failed run(s) or inbox note(s) - follow the basecamp-sync skill: read data/basecamp-sync/sync.log"
exit 0
"""


class Refuse(Exception):
    pass


def parse_url(url):
    m = re.search(r"basecamp(?:api)?\.com/(\d+)/(?:projects|buckets)/(\d+)", url)
    if not m:
        raise Refuse(f"not a Basecamp project URL: {url} (expected https://app.basecamp.com/<account>/projects/<project>)")
    return m.group(1), m.group(2)


def parse_folder(url):
    """A Docs & Files folder URL (…/<account>/buckets/<project>/vaults/<folder>) as the "backup" config."""
    m = re.search(r"/buckets/(\d+)/vaults/(\d+)", url)
    if not m:
        raise Refuse(f"--backup takes a Docs & Files folder URL, got {url} "
                     "(expected https://3.basecamp.com/<account>/buckets/<project>/vaults/<folder>)")
    return {"project": int(m.group(1)), "vault": int(m.group(2))}


def registered_projects(home):
    path = os.path.join(home, "data", "projects.md")
    if not os.path.exists(path):
        return []
    return [m.group(1) for m in (re.match(r"- ([A-Za-z0-9._-]+)", ln) for ln in open(path)) if m]


def unit_name(home):
    """basecamp-sync-<home path under ~, as [a-z0-9-]>: one unit pair per home."""
    rel = os.path.relpath(home, os.path.expanduser("~"))
    if rel.startswith(".."):
        rel = home
    return "basecamp-sync-" + (re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-") or "home")


def sd_quote(s):
    return f'"{s}"' if re.search(r"\s", s) else s


def diff_fields(old, new):
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


class System:
    """The local installs, behind a seam so tests never touch systemd or a real home.

    Each method returns what it changed (or, with dry, would change); [] means nothing.
    """

    def __init__(self, runner=subprocess.run, unit_dir=None):
        self.run = runner
        self.unit_dir = unit_dir or os.path.expanduser("~/.config/systemd/user")

    def preflight(self, home):
        reg = os.path.join(home, "bin", "fm-check-register.sh")
        if not os.access(reg, os.X_OK):
            raise Refuse(f"{reg} is missing or not executable; cannot register the wake check")

    def systemctl(self, *args):
        return self.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=60)

    def install_timer(self, name, service, timer, dry):
        changed = []
        for fname, text in ((f"{name}.service", service), (f"{name}.timer", timer)):
            path = os.path.join(self.unit_dir, fname)
            if os.path.exists(path) and open(path).read() == text:
                continue
            changed.append(f"write {path}")
            if not dry:
                os.makedirs(self.unit_dir, exist_ok=True)
                with open(path, "w") as f:
                    f.write(text)
        enabled = self.systemctl("is-enabled", f"{name}.timer").stdout.strip() == "enabled"
        active = self.systemctl("is-active", f"{name}.timer").stdout.strip() == "active"
        if changed:
            changed.append("systemctl --user daemon-reload")
        if changed or not (enabled and active):
            changed.append(f"systemctl --user enable --now {name}.timer")
        if not dry:
            for step in changed:
                if step.startswith("systemctl"):
                    r = self.systemctl(*step.split()[2:])
                    if r.returncode != 0:
                        raise RuntimeError(f"{step}: {(r.stdout + r.stderr)[:300]}")
        return changed

    def remove_service(self, name, dry):
        """Stop, disable and delete a user service an earlier version installed (the event listener); [] when absent."""
        path = os.path.join(self.unit_dir, f"{name}.service")
        if not os.path.exists(path):
            return []
        changed = [f"systemctl --user disable --now {name}.service", f"remove {path}", "systemctl --user daemon-reload"]
        if not dry:
            self.systemctl("disable", "--now", f"{name}.service")  # already stopped or unknown to systemd is fine
            os.remove(path)
            r = self.systemctl("daemon-reload")
            if r.returncode != 0:
                raise RuntimeError(f"systemctl --user daemon-reload: {(r.stdout + r.stderr)[:300]}")
        return changed

    def register_check(self, home, cid, script, dry):
        state = os.path.join(home, "state")
        path = os.path.join(state, f"{cid}.check.sh")
        trust = os.path.join(state, f"{cid}.check-trust")
        want = hashlib.sha256(script.encode()).hexdigest()
        same = (os.path.exists(path) and open(path).read() == script
                and (os.stat(path).st_mode & 0o777) == 0o700)
        trusted = os.path.exists(trust) and open(trust).read().split() == ["fm-custom-check-v1", want]
        if same and trusted:
            return []
        changed = ([] if same else [f"write {path} (mode 700)"]) + [f"{home}/bin/fm-check-register.sh {cid}"]
        if dry:
            return changed
        if not same:
            old = os.umask(0o077)
            try:
                with open(path, "w") as f:
                    f.write(script)
            finally:
                os.umask(old)
            os.chmod(path, 0o700)
        env = {k: v for k, v in os.environ.items() if k not in ("FM_ROOT_OVERRIDE", "FM_STATE_OVERRIDE")}
        env["FM_HOME"] = home
        r = self.run([os.path.join(home, "bin", "fm-check-register.sh"), cid],
                     capture_output=True, text=True, timeout=60, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"fm-check-register.sh {cid}: {(r.stdout + r.stderr)[:300]}")
        return changed


class Init:
    def __init__(self, url, login, home, captain=None, listen_to=(), operators=(), participants_project=False,
                 participants_domains=(), participants_company=None, repo_map=(), create_missing=False, dry=False,
                 force=False, cards=True, assign_captain=True, todos=False, reports=False, every_line=False, checkins=None, releases=True, inbox=False,
                 session_alert=None, pings=False, assigned_todos=False, assigned_anywhere=False, assigned_cards=False, mentions_anywhere=False, advisories=(), backup=None, timesheet=False, chats=True,
                 no_keyring=None, blocked=None, replace_people=False, runner=subprocess.run, system=None, sync_dir=HERE, out=print):
        self.account, self.project = parse_url(url)
        self.login, self.home = login, os.path.abspath(home)
        self.captain_arg, self.listen_to, self.operator_args = captain, list(listen_to), list(operators)
        # participants_company: True admits the captain's company, False never; None keeps an existing config's setting,
        # and for a new home admits the company only on a team account.
        self.participants_project, self.participants_company = participants_project, participants_company
        self.participants_domains = sorted({str(d).strip().lower().lstrip("@") for d in participants_domains})
        bad = [d for d in self.participants_domains if "." not in d or "@" in d or " " in d]
        if bad:
            raise Refuse(f"--participants-domain takes a domain like example.com, got {', '.join(bad)}")
        self.repo_map = {}
        for pair in repo_map:
            name, sep, repo = pair.partition("=")
            if not sep or not name.strip() or not repo.strip():
                raise Refuse(f"--repo-map takes <table name>=<repo>, got {pair!r}")
            self.repo_map[name.strip().lower()] = repo.strip()
        self.create_missing, self.dry, self.force, self.cards = create_missing, dry, force, cards
        self.assign_captain = assign_captain
        if not cards and (self.repo_map or create_missing):
            raise Refuse("--no-cards cannot be combined with --repo-map or --create-missing-columns")
        self.todos, self.reports, self.every_line, self.checkins = todos, reports, every_line, checkins
        self.releases, self.inbox, self.pings = releases, inbox, pings
        self.session_alert = session_alert  # minutes before the alert, or None: off
        if session_alert is not None and (not inbox or session_alert < 0):
            raise Refuse("--session-alert needs --inbox (it watches the inbox's notes) and a number of minutes, 0 or more")
        self.assigned_todos, self.assigned_anywhere = assigned_todos or assigned_anywhere, assigned_anywhere
        self.assigned_cards, self.mentions_anywhere = assigned_cards, mentions_anywhere
        self.chats = chats
        self.advisories = sorted(set(advisories))
        bad = [r for r in self.advisories if not re.fullmatch(r"[^/\s]+/[^/\s]+", r)]
        if bad:
            raise Refuse(f"--advisories takes a GitHub repository as owner/name, got {', '.join(bad)}")
        self.backup = parse_folder(backup) if isinstance(backup, str) else backup  # a folder URL, or the "backup" config
        self.timesheet = timesheet  # True writes "timesheet": {}; False keeps an existing config's setting
        # The automatic reply to blocked senders: None keeps an existing config's setting (on for a new home), False
        # leaves it off, a string is the reply's text.
        self.blocked = blocked
        # A re-run keeps an existing config's people and operators that the command line does not name, unless this says
        # to replace them with exactly the ones it names.
        self.replace_people = replace_people
        self.no_keyring = bool(os.environ.get("BASECAMP_NO_KEYRING")) if no_keyring is None else no_keyring
        if checkins is not None:
            try:
                ZoneInfo(checkins)
            except Exception:
                raise Refuse(f"--checkins takes an IANA time zone such as America/Chicago, got {checkins!r}")
        self.run = runner
        self.system = system or System()
        self.sync_dir = sync_dir
        self.notes = []  # what discover found worth saying, printed after the config
        self.account_people = None  # the account's people, read once when a named person is not on the project
        self.out = out
        self.dir = os.path.join(self.home, "data", "basecamp-sync")

    def bc(self, *args):
        cmd = ["basecamp", "-a", self.account, "-P", self.login, *args, "--json"]
        r = self.run(cmd, capture_output=True, text=True, timeout=120)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = {"ok": False, "error": (r.stdout + r.stderr)[:300]}
        if not out.get("ok"):
            raise Refuse(f"basecamp {' '.join(args[:3])} as login {self.login}: {out.get('error')}")
        return out.get("data")

    def discover(self):
        """The config the project implies, plus the columns still to create: (config, [(board, column)])."""
        problems = []
        proj = self.bc("api", "get", f"/projects/{self.project}.json") or {}
        dock = [d for d in proj.get("dock", []) if d.get("enabled")]
        tables = {}
        for d in dock if self.cards else ():
            if d.get("name") != "kanban_board":
                continue
            board = (d.get("title") or "").strip().lower()
            if board in tables:
                problems.append(f"two card tables are titled {board!r}; rename one")
            tables[board] = str(d["id"])
        if self.cards and not tables:
            problems.append("the project has no card tables in its dock (pass --no-cards to set up without the card mirror)")
        for name in self.repo_map:
            if name not in tables:
                problems.append(f"--repo-map names {name!r}, but the project has no card table titled that "
                                f"(tables: {', '.join(sorted(tables)) or 'none'})")
        registered = registered_projects(self.home)
        repos, self.skipped = {}, []
        for board in sorted(tables):
            if board in self.repo_map:
                repo = self.repo_map[board]
                if repo not in registered:
                    problems.append(f"--repo-map maps {board!r} to {repo!r}, which is not registered in data/projects.md")
                    continue
            else:
                hits = [p for p in registered if p.lower() == board]
                if not hits:
                    self.skipped.append(board)
                    continue
                if len(hits) != 1:
                    problems.append(f"card table {board!r} matches several registered projects in data/projects.md "
                                    f"({', '.join(hits)}); pass --repo-map {board}=<repo>")
                    continue
                repo = hits[0]
            if repo in repos:
                problems.append(f"repo {repo!r} is mapped to both {repos[repo]!r} and {board!r}")
                continue
            repos[repo] = board
        if tables and not repos and not problems:
            problems.append("no card table matches a registered project in data/projects.md, so there is nothing to sync "
                            f"(skipped: {', '.join(self.skipped)}); pass --repo-map <table name>=<repo>")
        included = set(repos.values())
        cfg_tables, to_create = {}, []
        for board, tid in sorted(tables.items()):
            if board not in included:
                continue
            t = self.bc("api", "get", f"/buckets/{self.project}/card_tables/{tid}.json") or {}
            cols = {}
            for lst in t.get("lists", []):
                cols.setdefault((lst.get("title") or "").strip(), []).append(str(lst["id"]))
            entry = {"table": tid}
            for col in COLUMNS:
                ids = cols.get(col, [])
                if len(ids) > 1:
                    problems.append(f"card table {board!r} has {len(ids)} columns named {col!r}; rename all but one")
                elif ids:
                    entry[col] = ids[0]
                elif self.create_missing and col in CREATABLE:
                    to_create.append((board, col))
                else:
                    hint = (" (pass --create-missing-columns to create it)" if col in CREATABLE
                            else " (a built-in column; restore it in Basecamp)")
                    problems.append(f"card table {board!r} has no column named exactly {col!r}{hint}")
            cfg_tables[board] = entry
        me = self.bc("api", "get", "/my/profile.json") or {}
        acting = me.get("id")
        people = self.bc("api", "get", f"/projects/{self.project}/people.json") or []
        captain = self.find_captain(people, acting, problems)
        listened = self.find_people(people, acting, problems)
        operators = self.find_people(people, acting, problems, self.operator_args, "--operator")
        if problems:
            raise Refuse("init refused, nothing was written:\n  - " + "\n  - ".join(problems))
        cfg = {"account": self.account, "project": self.project, "captain": captain, "profile": self.login}
        # A re-run keeps the people and operators an existing config names besides these, unless --replace-people.
        kept = {} if self.replace_people or self.existing() is None else self.existing()
        kept_people = [p for p in kept.get("people") or [] if p != captain and p not in listened]
        kept_operators = [p for p in kept.get("operators") or [] if p != captain and p not in operators]
        if kept_people or kept_operators:
            self.notes.append("kept from the existing config: " + "; ".join(
                f"{key} {', '.join(map(str, ids))}" for key, ids in (("people", kept_people), ("operators", kept_operators))
                if ids) + " (pass --replace-people to keep only the ones this command names)")
        if listened or "people" in kept:
            cfg["people"] = [captain] + sorted(set(listened + kept_people) - {captain})
        if set(operators + kept_operators) - {captain}:
            cfg["operators"] = sorted(set(operators + kept_operators) - {captain})
        company = self.participants_company
        if company is None and self.existing() is not None:  # an existing home keeps its own
            company = (self.existing().get("participants") or {}).get("company")
        elif company is None:  # a new home: the captain only on a personal account, everyone in the company on a team one
            company = self.team_account(people, captain, acting) or None
        if self.participants_project or self.participants_domains or company not in (None, False):
            cfg["participants"] = {**({"project": True} if self.participants_project else {}),
                                   **({"domains": self.participants_domains} if self.participants_domains else {}),
                                   **({"company": company} if company not in (None, False) else {})}
            if self.participants_domains and not any(visible(p) for p in people if p.get("id") != acting):
                self.out(f"warning: the login {self.login} sees no one else's email address on this project (Basecamp "
                         "shows them only to account admins), so --participants-domain admits nobody; use "
                         "--participants-project or --listen-to instead")
        if self.cards:
            cfg.update(repos=dict(sorted(repos.items())), tables=cfg_tables)
            if not self.assign_captain:
                cfg["assign_captain"] = False
        chats = [d["id"] for d in dock if d.get("name") == "chat"] if self.chats else []
        if chats:
            cfg["chats"] = [{"chat": c, "every_line": True} for c in chats] if self.every_line else chats
        boards = [d["id"] for d in dock if d.get("name") == "message_board"]
        opted = ((self.every_line, "--every-line", "chat", None),
                 (self.reports, "--reports", "message_board", "message_board"),
                 (self.todos, "--todos", "todoset", "todos"),
                 (self.checkins is not None, "--checkins", "questionnaire", "checkins"))
        for wanted, flag, kind, key in opted:
            if not wanted:
                continue
            ids = [str(d["id"]) for d in dock if d.get("name") == kind]
            if not ids or (len(ids) > 1 and key is not None):
                problems.append(f"{flag} needs {'an' if key is None else 'one'} enabled {kind} in the project's dock, "
                                f"found {len(ids)}")
            elif key == "message_board":
                cfg[key] = ids[0]
            elif key == "todos":
                cfg[key] = {"todoset": ids[0]}
            elif key == "checkins":
                cfg[key] = {"questionnaires": ids, "timezone": self.checkins}
        if self.inbox:
            cfg["inbox"] = {}
        if self.session_alert is not None:
            alert_chats = [d["id"] for d in dock if d.get("name") == "chat"]
            if not alert_chats:
                problems.append("--session-alert needs an enabled chat in the project's dock, found none")
            else:
                cfg["session_alert"] = {"chat": alert_chats[0], "after_minutes": self.session_alert}
        if self.pings:
            cfg["pings"] = {}
        if self.assigned_todos:
            cfg["assigned_todos"] = {"scope": "account"} if self.assigned_anywhere else {}
        if self.assigned_cards:
            cfg["assigned_cards"] = {}
        if self.mentions_anywhere:
            cfg["mentions"] = {"scope": "account"}
        if self.advisories:
            cfg["advisories"] = {"repos": self.advisories}
        if self.backup:
            cfg["backup"] = self.backup
        timesheet = {} if self.timesheet else (self.existing() or {}).get("timesheet")  # an existing home keeps its own
        if timesheet not in (None, False):
            cfg["timesheet"] = timesheet
        blocked = {"reply": self.blocked} if isinstance(self.blocked, str) else {} if self.blocked is None else None
        if self.blocked is None and self.existing() is not None:
            blocked = self.existing().get("blocked")  # an existing home keeps its own
        if blocked not in (None, False):
            cfg["blocked"] = blocked
        # With requests on, a new home asks the owner about a dated request's timing and does a repeating one on its due
        # date; an existing home keeps its own (none: its defaults file's wording stands).
        schedule = self.existing().get("schedule") if self.existing() is not None else (
            {"dated": "ask", "recurring": "due"} if self.assigned_todos or self.assigned_cards else None)
        if schedule:
            cfg["schedule"] = schedule
        # A new home puts 👀 on everything it reads and 👍 on a deciding comment once it is carried out; an existing home
        # keeps its own (none: 👀 on a question, 👍 on anything else, as before).
        acknowledge = self.existing().get("acknowledge") if self.existing() is not None else {"read": "👀", "decided": "👍"}
        if acknowledge:
            cfg["acknowledge"] = acknowledge
        if problems:
            raise Refuse("init refused, nothing was written:\n  - " + "\n  - ".join(problems))
        gh_repos = {}
        # Without cards, every registered repo is a release source, named after itself.
        sources = repos if self.cards else {r: r for r in registered}
        for repo, board in sorted(sources.items()) if self.releases else ():
            full = self.github_repo(repo)
            if full:
                gh_repos[full] = board
        if len(boards) == 1 and gh_repos:
            cfg["releases"] = {"board": str(boards[0]), "repos": gh_repos}
        return cfg, to_create

    def side_files(self):
        """The hand-kept files to create; without cards only the pending file applies."""
        return SIDE_FILES if self.cards else {"pending-comments.jsonl": None}

    def github_repo(self, repo):
        """owner/name of <home>/projects/<repo>'s GitHub origin, following renames through gh; None if not GitHub."""
        try:
            r = self.run(["git", "-C", os.path.join(self.home, "projects", repo), "remote", "get-url", "origin"],
                         capture_output=True, text=True, timeout=30)
        except Exception:
            return None
        m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", r.stdout.strip()) if r.returncode == 0 else None
        if not m:
            return None
        full = f"{m.group(1)}/{m.group(2)}"
        try:
            v = self.run(["gh", "repo", "view", full, "--json", "nameWithOwner"], capture_output=True, text=True, timeout=30)
            if v.returncode == 0:
                return json.loads(v.stdout).get("nameWithOwner") or full
        except Exception:
            pass
        return full

    def existing(self):
        """The home's config as it is now, or None for a new home."""
        path = os.path.join(self.dir, "config.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def team_account(self, people, captain, acting):
        """True on a team account: someone besides the captain and the login, not a client, is in the captain's company
        in Basecamp. Reads the account's people a page at a time (/people.json), stopping at the first such person; a
        failed read counts as a personal account (the narrower default), with a warning. Says which it found."""
        def company(p):
            return (p.get("company") or {}).get("id")
        mine = next((company(p) for p in people if p.get("id") == captain and company(p) is not None), None)
        found, page = None, 1
        try:
            while found is None and page <= 20:
                batch = self.bc("api", "get", f"/people.json?page={page}") or []
                if not batch:
                    break
                mine = mine if mine is not None else next((company(p) for p in batch if p.get("id") == captain), None)
                found = next((p for p in batch if mine is not None and company(p) == mine and p.get("client") is not True
                              and p.get("id") not in (captain, acting)), None)
                page += 1
        except Refuse as e:
            self.notes.append(f"warning: the account's people could not be read ({e}); listening only to the captain")
            return False
        if found is None:
            self.notes.append("a personal account (no one else is in the captain's company): the agent listens only to "
                              "the captain; pass --participants-company to let everyone in the company ask it things")
            return False
        self.notes.append("a team account (others are in the captain's company): everyone in the company may ask the "
                          'agent things ("participants": {"company": true}); pass --no-participants-company to listen '
                          "only to the captain")
        return True

    def find_captain(self, people, acting, problems):
        if self.captain_arg:
            arg = str(self.captain_arg).strip()
            if arg.isdigit():
                hits = [p for p in people if p.get("id") == int(arg)]
            else:
                hits = [p for p in people if (p.get("email_address") or "").lower() == arg.lower()]
            if len(hits) != 1:
                problems.append(f"--captain {arg} matches no person on the project"
                                + ("" if arg.isdigit() else " (emails can be hidden from this login; pass the person id)"))
                return None
        else:
            hits = [p for p in people if p.get("owner") and p.get("id") != acting]
            if len(hits) != 1:
                names = ", ".join(f"{p.get('name')} ({p.get('id')})" for p in hits) or "none"
                problems.append(f"cannot tell who the captain is: account owners on the project other than the "
                                f"login: {names}; pass --captain <person id or email>")
                return None
        if hits[0].get("id") == acting:
            problems.append(f"the captain {hits[0].get('name')} ({acting}) is the login {self.login} itself; "
                            "sign the login in as a separate user (docs/firstmate-account.md)")
            return None
        return hits[0]["id"]

    def find_people(self, people, acting, problems, args=None, flag="--listen-to"):
        """The person ids each --listen-to (or `flag`, naming `args`) names (an id, an email or an exact name), refusing a
        miss, a tie or the login. Someone not on the project is looked up among the account's people, accepted when
        exactly one matches, and named in a note. An email matches only when the login can see it: Basecamp masks other
        people's email addresses unless the login is an account admin."""
        def matching(pool, arg):
            if arg.isdigit():
                return [p for p in pool if p.get("id") == int(arg)]
            hits = [p for p in pool if (p.get("email_address") or "").lower() == arg.lower()]
            return hits or [p for p in pool if (p.get("name") or "").strip().lower() == arg.lower()]

        found, outside = [], []
        for arg in (str(a).strip() for a in (self.listen_to if args is None else args)):
            hits, where, unread = matching(people, arg), "on the project", None
            if not hits:
                try:
                    hits, where = matching(self.everyone(), arg), "in the account"
                except Refuse as e:
                    unread = e
            if len(hits) != 1:
                pool = people if where == "on the project" else self.account_people or people
                names = ", ".join(f"{p.get('name')} ({p.get('id')})" for p in hits)
                masked = "@" in arg and not hits and any(not visible(p) for p in pool if p.get("id") != acting)
                problems.append(f"{flag} {arg} matches " + (f"several people {where}: {names}; pass the person id"
                                                            if hits else "no person on the project or in the account" + (
                                                                f" (the account's people could not be read: {unread})"
                                                                if unread else "") + (
                                                                " (Basecamp hides other people's email addresses unless the "
                                                                "login is an account admin; pass the person id or exact name)"
                                                                if masked else "")))
                continue
            if hits[0].get("id") == acting:
                problems.append(f"{flag} {arg} is the login {self.login} itself; the agent never listens to its own lines")
                continue
            found.append(hits[0]["id"])
            if where == "in the account":
                outside.append(f"{hits[0].get('name')} ({hits[0]['id']})")
        if outside:
            self.notes.append(f"{flag}: not members of project {self.project}, found in the account and written all the "
                              f"same: {', '.join(outside)}; they are heard wherever they reach the agent, and in this "
                              "project once they join it")
        return found

    def everyone(self):
        """The account's people (/people.json, every page), read once; a failed read is refused."""
        if self.account_people is None:
            everyone, page = [], 1
            while page <= 100:
                batch = self.bc("api", "get", f"/people.json?page={page}") or []
                if not batch:
                    break
                everyone += batch
                page += 1
            self.account_people = everyone
        return self.account_people

    def environment(self):
        """The unit's Environment= lines: the file credential store when --no-keyring."""
        return "Environment=BASECAMP_NO_KEYRING=1\n" if self.no_keyring else ""

    def units(self, config_path):
        name = unit_name(self.home)
        service = ("[Unit]\n"
                   f"Description=Mirror the firstmate backlog at {self.home} into Basecamp project {self.project}\n\n"
                   "[Service]\nType=oneshot\nTimeoutStartSec=240\n" + self.environment() +
                   f"ExecStart={sd_quote(os.path.join(self.sync_dir, 'run.sh'))} {sd_quote(self.home)} {sd_quote(config_path)}\n")
        timer = ("[Unit]\n"
                 f"Description=Basecamp sync for the firstmate backlog at {self.home}, every 30 seconds\n\n"
                 f"[Timer]\nOnBootSec=2min\nOnUnitActiveSec={TIMER_SECONDS}s\nAccuracySec=5s\n\n"
                 "[Install]\nWantedBy=timers.target\n")
        return name, service, timer

    def main(self):
        for sub in ("data", "state"):
            if not os.path.isdir(os.path.join(self.home, sub)):
                raise Refuse(f"{self.home} is not a firstmate home (no {sub}/)")
        if os.path.realpath(self.dir).startswith(os.path.realpath(self.sync_dir) + os.sep):
            raise Refuse(f"{self.dir} is inside this repo; the config lives in the home, never here")
        self.system.preflight(self.home)
        cfg, to_create = self.discover()
        config_path = os.path.join(self.dir, "config.json")
        existing = json.load(open(config_path)) if os.path.exists(config_path) else None
        plan = []
        # A config that differs only in the retired "listen" is rewritten without it, no --force needed.
        retired = existing is not None and "listen" in existing and {k: v for k, v in existing.items() if k != "listen"} == cfg
        if retired:
            plan.append(f'drop the retired "listen" from {config_path} (notifications replaced the event listener)')
        for board in self.skipped:
            self.out(f"skipped card table {board!r}: no matching repo; pass --repo-map {board}=<repo> to include it")
        for board, col in to_create:
            plan.append(f"create column {col!r} in card table {board!r}")
            cfg["tables"][board][col] = "<new>"
        if existing is None:
            plan.append(f"write {config_path}")
        elif existing != cfg and not retired:
            fields = diff_fields(existing, cfg)
            if not self.force:
                msg = (f"{config_path} already exists and differs in: {', '.join(fields)}. "
                       "Check the discovered config; pass --force to replace it.")
                if not self.dry:
                    raise Refuse(msg + "\n" + json.dumps(cfg, indent=1))
                plan.append("REFUSE: " + msg)
            else:
                plan.append(f"replace {config_path} (differs in: {', '.join(fields)})")
        for name in self.side_files():
            if not os.path.exists(os.path.join(self.dir, name)):
                plan.append(f"create empty {os.path.join(self.dir, name)}")
        defaults = os.path.join(self.dir, "defaults.md")
        if not os.path.exists(defaults):
            plan.append(f"copy the shipped workflow defaults to {defaults} (the home's to edit; never overwritten)")
        self.out(json.dumps(cfg, indent=1))
        for note in self.notes:
            self.out(note)
        if self.dry:
            plan += self.system.install_timer(*self.units(config_path), dry=True)
            plan += self.system.remove_service(unit_name(self.home) + "-listen", dry=True)
            plan += self.system.register_check(self.home, CHECK_ID, CHECK_INBOX if self.inbox else CHECK, dry=True)
            self.out("dry run, nothing written. Would:" if plan else "dry run: nothing to change")
            for p in plan:
                self.out(f"  {p}")
            for board in self.skipped:
                self.out(f"  skip card table {board!r} (no matching repo)")
            return plan + [f"skip card table {b!r}" for b in self.skipped]
        for board, col in to_create:
            t = cfg["tables"][board]
            data = self.bc("cards", "column", "create", col, "--card-table", t["table"], "-p", self.project) or {}
            t[col] = str(data["id"])
            self.out(f"created column {col!r} in {board!r}: {t[col]}")
        os.makedirs(self.dir, exist_ok=True)
        if existing != cfg:
            tmp = config_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=1)
            os.replace(tmp, config_path)
        for name, empty in self.side_files().items():
            path = os.path.join(self.dir, name)
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write("" if empty is None else json.dumps(empty) + "\n")
        if not os.path.exists(defaults):
            with open(DEFAULTS) as src, open(defaults, "w") as f:
                f.write(src.read())
        done = [p for p in plan if not p.startswith("create column")]
        done += self.system.install_timer(*self.units(config_path), dry=False)
        done += self.system.remove_service(unit_name(self.home) + "-listen", dry=False)
        done += self.system.register_check(self.home, CHECK_ID, CHECK_INBOX if self.inbox else CHECK, dry=False)
        self.out("done:" if done else "nothing to change")
        for p in done:
            self.out(f"  {p}")
        return done


def visible(person):
    """True when `person`'s email address is shown in full, not masked (Basecamp shows it only to them and to admins)."""
    email = str(person.get("email_address") or "")
    return "@" in email and "\u2022" not in email


def cli(argv, **kw):
    ap = argparse.ArgumentParser(prog="sync.py init", description="Set a firstmate home up to mirror into one Basecamp project.")
    ap.add_argument("url", help="the Basecamp project URL, e.g. https://app.basecamp.com/<account>/projects/<project>")
    ap.add_argument("--login", required=True, help="the basecamp CLI profile the sync acts as")
    ap.add_argument("--home", required=True, help="the firstmate home")
    ap.add_argument("--captain", help="the captain's person id or email (default: the project's account owner)")
    ap.add_argument("--listen-to", action="append", default=[], metavar="PERSON",
                    help="also relay this person's lines, comments and boosts as information, never as the captain's "
                         "decisions: a person id, email or exact name on the project or, failing that, in the account "
                         "(repeatable; the captain is always listened to)")
    ap.add_argument("--operator", action="append", default=[], metavar="PERSON",
                    help="someone whose word authorizes the agent as the captain's does (approvals, decisions): a person "
                         "id, email or exact name on the project or, failing that, in the account (repeatable)")
    ap.add_argument("--replace-people", action="store_true",
                    help="write exactly the --listen-to and --operator given as people and operators (a re-run otherwise "
                         "keeps the ones an existing config names besides them)")
    ap.add_argument("--participants-project", action="store_true",
                    help="also relay what anyone on the project (not a client) writes to the agent, as questions whose "
                         "word never authorizes it")
    ap.add_argument("--participants-company", action="store_true", default=None,
                    help="the same for anyone whose Basecamp profile is in the captain's company (the default for a new "
                         "home on a team account, where others are in that company)")
    ap.add_argument("--no-participants-company", dest="participants_company", action="store_false",
                    help="listen only to the captain and the named people, even on a team account (a re-run without "
                         "either flag keeps an existing config's setting)")
    ap.add_argument("--participants-domain", action="append", default=[], metavar="DOMAIN",
                    help="the same for anyone whose email is at DOMAIN (repeatable); Basecamp shows other people's emails "
                         "only to account admins, so for most logins this admits nobody")
    ap.add_argument("--repo-map", action="append", default=[], metavar="TABLE=REPO",
                    help="map a card table to a backlog repo when their names differ (repeatable)")
    ap.add_argument("--create-missing-columns", action="store_true",
                    help="create a missing Figuring it out, In progress or Ready for QA column")
    ap.add_argument("--no-cards", action="store_true",
                    help="set up without the card-table mirror: no card tables are read or written")
    ap.add_argument("--no-assign-captain", action="store_true",
                    help="never assign the owner to a card held for them (and unassign them from cards the mirror "
                         "assigned); the agent sends each decision to its main home as a to-do instead")
    ap.add_argument("--todos", action="store_true",
                    help="decision to-dos: create the owner's decisions on the dock's to-do set and relay the owner's comments")
    ap.add_argument("--reports", action="store_true", help="let the agent post reports on the dock's message board")
    ap.add_argument("--every-line", action="store_true", help="relay every owner line in the chats, not only questions")
    ap.add_argument("--checkins", metavar="TIME_ZONE",
                    help="record due Automatic Check-ins questions, scheduled in this IANA time zone")
    ap.add_argument("--inbox", action="store_true",
                    help="deliver each new pending record as a note in this home's firstmate inbox (the wake)")
    ap.add_argument("--session-alert", nargs="?", const=15, type=int, metavar="MINUTES",
                    help="with --inbox: tell the owner once in the dock's chat when no firstmate session holds this "
                         "home's lock while an inbox note has waited MINUTES (default 15); clears when one does again")
    ap.add_argument("--listen", action="store_true", help=argparse.SUPPRESS)  # retired: notifications replaced it
    ap.add_argument("--pings", action="store_true",
                    help="relay the owner's Pings (direct messages) to the --login; the agent answers with sync.py reply")
    ap.add_argument("--assigned-todos", action="store_true",
                    help="treat a to-do the owner assigns to the --login as a request: relayed, acknowledged with a 👀 "
                         "and tracked; the agent completes it with sync.py todo complete")
    ap.add_argument("--assigned-todos-anywhere", action="store_true",
                    help="--assigned-todos in every project of the account, not just this one; run it in one home per "
                         "login only (the main home, which routes each request to the right domain)")
    ap.add_argument("--mentions-anywhere", action="store_true",
                    help="relay the owner's @mentions of the --login, and comments on threads it follows, in every "
                         "project of the account, not just this one; run it in one home per login only (the main home)")
    ap.add_argument("--assigned-cards", action="store_true",
                    help="treat a card the owner assigns to the --login in the project as a request, like --assigned-todos; "
                         "cards the sync mirrors from the backlog never are")
    ap.add_argument("--advisories", action="append", default=[], metavar="OWNER/NAME",
                    help="record each new security advisory (triage or draft) of this GitHub repository once, for the "
                         "agent to route (repeatable); the gh login needs admin or security-manager access")
    ap.add_argument("--backup", metavar="FOLDER_URL",
                    help="keep a daily snapshot of the config and state files in this Docs & Files folder "
                         "(https://3.basecamp.com/<account>/buckets/<project>/vaults/<folder>)")
    ap.add_argument("--timesheet", action="store_true",
                    help="post the agent's active time on each to-do or card it names with sync.py timesheet start "
                         "to that timesheet, once per day (a re-run without it keeps an existing setting)")
    ap.add_argument("--blocked-reply", metavar="TEXT",
                    help="the automatic reply to someone the sync does not listen to who @mentions, Pings or assigns "
                         "the --login (default: a short line saying the agent cannot act on it)")
    ap.add_argument("--no-blocked-reply", action="store_true",
                    help="post no automatic reply to blocked senders; what they write still never reaches the agent "
                         "(a re-run without either flag keeps an existing config's setting)")
    ap.add_argument("--no-chats", action="store_true", help="leave the dock's chats out: no chat inbox")
    ap.add_argument("--no-keyring", action="store_true", default=None,
                    help="run the units with BASECAMP_NO_KEYRING=1 (the file credential store); "
                         "on by default when BASECAMP_NO_KEYRING is set")
    ap.add_argument("--no-releases", action="store_true", help="leave release announcements out of the config")
    ap.add_argument("--force", action="store_true", help="replace an existing config.json that differs")
    ap.add_argument("--dry-run", action="store_true", help="print the discovered config and planned installs; write nothing")
    a = ap.parse_args(argv)
    if a.listen:
        print("--listen is no longer needed: notifications on the timer replaced the event listener", file=sys.stderr)
    if a.blocked_reply is not None and (a.no_blocked_reply or not a.blocked_reply.strip()):
        print("--blocked-reply takes the reply's text, and cannot be combined with --no-blocked-reply", file=sys.stderr)
        return 2
    try:
        Init(a.url, a.login, a.home, captain=a.captain, listen_to=a.listen_to, operators=a.operator,
             participants_project=a.participants_project, participants_domains=a.participants_domain,
             participants_company=a.participants_company, repo_map=a.repo_map,
             create_missing=a.create_missing_columns, dry=a.dry_run, force=a.force,
             cards=not a.no_cards, assign_captain=not a.no_assign_captain, todos=a.todos, reports=a.reports, every_line=a.every_line,
             checkins=a.checkins, releases=not a.no_releases, inbox=a.inbox, session_alert=a.session_alert, pings=a.pings,
             assigned_todos=a.assigned_todos, assigned_anywhere=a.assigned_todos_anywhere, assigned_cards=a.assigned_cards, mentions_anywhere=a.mentions_anywhere, advisories=a.advisories, backup=a.backup, timesheet=a.timesheet, chats=not a.no_chats, no_keyring=a.no_keyring,
             blocked=False if a.no_blocked_reply else a.blocked_reply, replace_people=a.replace_people, **kw).main()
    except Refuse as e:
        print(e, file=sys.stderr)
        return 2
    return 0
