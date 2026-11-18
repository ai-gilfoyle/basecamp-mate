"""`basecamp-mate setup`: the guided setup, for someone who has never used a terminal much.

It checks what the sync needs and offers to install what is missing, signs the agent's
own Basecamp person in with a link and a code (the file credential store, so a locked
system keyring never hangs it), lists that person's projects to pick from, asks a few
plain yes/no questions, turns on the project tools the chosen behaviors need (a to-do
list, check-ins, a chat, a message board, a card table per backlog repo), then hands
everything to `sync.py init` (init_home.Init: it discovers the ids, creates missing
card-table columns, writes the config, installs the background services with
BASECAMP_NO_KEYRING=1 and registers the wake check), runs one test sync, and ends with
a short summary of what is on and how to change it.

Defaults are the safe ones: the chat inbox, decision to-dos and check-ins on; the card
mirror, release announcements, reports and Pings off. The agent's Basecamp notifications
are always read (within about half a minute), since setup signs the agent in as its own
person.

Non-interactive (for tests and agents): --answers <file.json> supplies the answers by
key (see ANSWERS) and --yes takes the default for every answer the file leaves out;
anything that needs a person (a sign-in, a project to pick) then refuses instead of asking.
"""
import argparse, json, os, re, shutil, subprocess, sys, time

import init_home
from init_home import Init, Refuse

HERE = os.path.dirname(os.path.abspath(__file__))
NO_KEYRING = {"BASECAMP_NO_KEYRING": "1"}
INSTALL_CLI = "curl -fsSL https://basecamp.com/install-cli | bash"
MIN_BASECAMP = (0, 12, 0)  # the first CLI with `basecamp subtasks`, which decision to-dos use

# The yes/no questions, in the order asked: (answer key, question, default).
QUESTIONS = (
    ("chat", "Relay your questions from the project's chat (Campfire) to the agent?", True),
    ("todos", "Let the agent put decisions to you as to-dos assigned to you? (In this project to start; ask the agent "
              "to use another project, list or card table any time.)", True),
    ("checkins", "Let the agent answer the project's automatic check-in questions?", True),
    ("cards", "Mirror the agent's task list onto card tables (one per code repo)?", False),
    ("assign", "Assign you to a card when its task waits on your decision? (No: the agent sends each decision to "
               "its main home as a to-do instead)", True),
    ("releases", "Announce new GitHub releases on the Message Board?", False),
    ("reports", "Let the agent post reports on the Message Board?", False),
    ("pings", "Relay your Pings (direct messages) to the agent?", False),
)
ANSWERS = ("home", "login", "account", "project", "captain", "agent", "timezone", "replace", "install_cli",
           "inbox", "session_alert", *(k for k, _, _ in QUESTIONS))
LABELS = {"chat": "chat inbox", "todos": "decision to-dos", "checkins": "check-in answers",
          "cards": "card mirror", "assign": "card assignment for decisions", "releases": "release announcements",
          "reports": "Message Board reports", "pings": "Pings", "inbox": "firstmate inbox notes",
          "session_alert": "session-down alert"}
# The dock tool each behavior needs, and whether there must be exactly one.
TOOLS = {"chat": ("chat", "Chat", False), "todos": ("todoset", "To-dos", True),
         "checkins": ("questionnaire", "Automatic Check-ins", True),
         "releases": ("message_board", "Message Board", True), "reports": ("message_board", "Message Board", True)}



def cli_version(run, **kw):
    """The installed basecamp CLI's version as a tuple, or None when it does not say one."""
    try:
        r = run(["basecamp", "--version"], capture_output=True, text=True, timeout=15, **kw)
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", r.stdout or "")
    return tuple(int(g) for g in m.groups()) if m else None

class Stop(Exception):
    """Setup cannot go on; the message says why in plain words and what to do."""


def bc_env():
    return {**os.environ, **NO_KEYRING}


class Basecamp:
    """The basecamp CLI with the file credential store and a timeout on every call."""

    def __init__(self, runner, login=None, account=None):
        self.run, self.login, self.account = runner, login, account

    def call(self, *args, timeout=30, account=True, login=True):
        cmd = ["basecamp", *args]
        if login and self.login:
            cmd += ["-P", self.login]
        if account and self.account:
            cmd += ["-a", str(self.account)]
        try:
            r = self.run(cmd + ["--json"], capture_output=True, text=True, timeout=timeout, env=bc_env())
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": "timeout", "error": f"no answer in {timeout} seconds"}
        except FileNotFoundError:
            return {"ok": False, "code": "missing", "error": "the basecamp command is not installed"}
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {"ok": False, "error": (r.stdout + r.stderr).strip()[:300] or f"exit {r.returncode}"}

    def data(self, *args, **kw):
        out = self.call(*args, **kw)
        if not out.get("ok"):
            raise Stop(f"Basecamp said no to `basecamp {' '.join(args[:3])}`: {out.get('error')}")
        return out.get("data")


class Prompter:
    """Plain-language questions on the terminal, or answers from a file."""

    def __init__(self, answers=None, interactive=True, inp=input, out=print):
        self.answers, self.interactive, self.inp, self.out = dict(answers or {}), interactive, inp, out

    def _ask(self, prompt):
        try:
            return self.inp(prompt).strip()
        except EOFError:
            raise Stop("setup was cancelled (no more input)")

    def yes(self, key, question, default):
        if key in self.answers:
            return bool(self.answers[key])
        if not self.interactive:
            return default
        while True:
            got = self._ask(f"{question} [{'Y/n' if default else 'y/N'}] ").lower()
            if not got:
                return default
            if got in ("y", "yes", "n", "no"):
                return got.startswith("y")
            self.out("  Please answer y or n.")

    def text(self, key, question, default=None):
        if key in self.answers:
            return str(self.answers[key])
        if not self.interactive:
            if default is None:
                raise Stop(f"no answer for {key!r}; add it to the answers file")
            return default
        got = self._ask(f"{question}" + (f" [{default}] " if default else " "))
        return got or default

    def choose(self, key, question, options, match=None):
        """One of `options` ([(value, label)]): the answer's value (or `match` of it), else a numbered pick."""
        if key in self.answers:
            want = str(self.answers[key])
            hits = [v for v, _ in options if (match or str)(v) == want or str(v) == want]
            if len(hits) != 1:
                raise Stop(f"the answer {key}={want!r} matches none of: " + ", ".join(lbl for _, lbl in options))
            return hits[0]
        if len(options) == 1:
            self.out(f"{question} {options[0][1]}")
            return options[0][0]
        if not self.interactive:
            raise Stop(f"no answer for {key!r}; choose one of: " + ", ".join(lbl for _, lbl in options))
        self.out(question)
        for i, (_, lbl) in enumerate(options, 1):
            self.out(f"  {i}. {lbl}")
        while True:
            got = self._ask(f"Type a number from 1 to {len(options)}: ")
            if got.isdigit() and 1 <= int(got) <= len(options):
                return options[int(got) - 1][0]
            self.out("  That is not one of the numbers above.")


def local_timezone():
    """The computer's IANA time zone, e.g. America/Chicago; None when it cannot tell."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz:
        return tz
    try:
        m = re.search(r"zoneinfo/(.+)$", os.path.realpath("/etc/localtime"))
        return m.group(1) if m else None
    except OSError:
        return None


def is_home(path):
    return all(os.path.isdir(os.path.join(path, d)) for d in ("data", "state"))


def default_home():
    for cand in (os.environ.get("FM_HOME"), os.path.expanduser("~/firstmate")):
        if cand and is_home(cand):
            return cand
    return None


class Setup:
    def __init__(self, prompter, runner=subprocess.run, system=None, which=shutil.which, sync_dir=HERE,
                 out=print, sleep=time.sleep):
        self.ask, self.run, self.which, self.out = prompter, runner, which, out
        self.system = system or init_home.System()
        self.sync_dir, self.sleep = sync_dir, sleep
        self.bc = Basecamp(runner)

    # --- steps ---

    def prerequisites(self):
        if sys.version_info < (3, 11):
            raise Stop("basecamp-mate needs Python 3.11 or newer. Install it from https://www.python.org/downloads/ "
                       "(or your system's package manager), then run setup again.")
        if not self.which("basecamp"):
            self.out("The Basecamp command-line tool is not installed yet.")
            if not self.ask.yes("install_cli", f"Install it now? (runs: {INSTALL_CLI})", self.ask.interactive):
                raise Stop(f"Install the Basecamp command-line tool with:\n    {INSTALL_CLI}\nthen run setup again.")
            env = {**bc_env(), "BASECAMP_SKIP_SETUP": "1", "BASECAMP_SETUP_AGENT": "none"}
            r = self.run(["bash", "-c", INSTALL_CLI], timeout=600, env=env)
            if r.returncode != 0 or not self.which("basecamp"):
                raise Stop(f"Installing the Basecamp command-line tool did not work. Run this yourself and "
                           f"read what it says:\n    {INSTALL_CLI}\nthen run setup again (open a new terminal first).")
            self.out("Installed the Basecamp command-line tool.")
        version = cli_version(self.run, env=bc_env())
        if version is not None and version < MIN_BASECAMP:
            raise Stop(f"The Basecamp command-line tool is version {'.'.join(map(str, version))}; basecamp-mate needs "
                       f"{'.'.join(map(str, MIN_BASECAMP))} or newer. Update it with:\n    {INSTALL_CLI}\nthen run setup again.")
        r = self.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise Stop("This computer has no systemd user services, which keep the sync running in the background. "
                       "basecamp-mate needs a Linux computer with systemd.")
        self.out("✓ Python, the Basecamp tool and background services are ready.")

    def pick_home(self):
        home = os.path.abspath(os.path.expanduser(self.ask.text(
            "home", "Where is the firstmate home this agent works from?", default_home())))
        if not is_home(home):
            raise Stop(f"{home} is not a firstmate home (it has no data/ and state/ folders). Set firstmate up "
                       "first, then run setup again and give the folder firstmate created.")
        reg = os.path.join(home, "bin", "fm-check-register.sh")
        if not os.access(reg, os.X_OK):
            raise Stop(f"The firstmate home at {home} is missing bin/fm-check-register.sh, which wakes the agent. "
                       "Update firstmate in that home, then run setup again.")
        return home

    def sign_in(self):
        login = self.ask.text("login", "What should the agent's Basecamp sign-in be called?", "firstmate")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", login):
            raise Stop(f"{login!r} cannot be a sign-in name; use letters, numbers, dots, dashes or underscores.")
        self.bc.login = login
        profiles = self.bc.data("profile", "list", account=False, login=False) or []
        exists = any(p.get("name") == login for p in profiles)
        if exists and self.signed_in():
            self.out(f"✓ Already signed in to Basecamp as {login!r}.")
            return login
        if not self.ask.interactive:
            raise Stop(f"The Basecamp sign-in {login!r} is not signed in. Run `basecamp-mate setup` in a terminal "
                       "to sign in, or sign in yourself with:\n"
                       f"    BASECAMP_NO_KEYRING=1 basecamp auth login -P {login} --device-code")
        self.out("\nNext, sign in to Basecamp as the agent's own Basecamp person, not as yourself.\n"
                 "  1. If you have not yet, invite a new person for the agent to your project in Basecamp\n"
                 "     (an email address you control), and accept the invitation in a private browser window.\n"
                 "  2. Below, a link and a code appear. Open the link in that private window,\n"
                 "     signed in as the agent's person, and enter the code.\n"
                 "     (More detail: docs/firstmate-account.md)")
        cmd = (["basecamp", "auth", "login", "-P", login, "--device-code"] if exists
               else ["basecamp", "profile", "create", login, "--device-code"])
        r = self.run(cmd, timeout=900, env=bc_env())
        if r.returncode != 0 or not self.signed_in():
            raise Stop("Signing in to Basecamp did not finish. Run setup again to get a fresh link and code.")
        self.out(f"✓ Signed in to Basecamp as {login!r}.")
        return login

    def signed_in(self):
        st = self.bc.call("auth", "status", account=False)
        if st.get("code") == "timeout":
            raise Stop("Basecamp did not answer while checking the sign-in. Check your internet connection and run "
                       "setup again.")
        d = st.get("data") or {}
        if not (st.get("ok") and d.get("authenticated")):
            return False
        if d.get("expired"):
            return bool(self.bc.call("auth", "refresh", account=False).get("ok"))
        return True

    def pick_project(self):
        accounts = self.bc.data("accounts", "list", account=False) or []
        if not accounts:
            raise Stop("This Basecamp sign-in has no Basecamp accounts. Make sure the agent's person accepted the "
                       "project invitation, then run setup again.")
        url = str(self.ask.answers.get("project", ""))
        m = re.search(r"basecamp(?:api)?\.com/(\d+)/(?:projects|buckets)/(\d+)", url)
        if m and "account" not in self.ask.answers:
            self.ask.answers.update(account=m.group(1), project=m.group(2))
        account = self.ask.choose("account", "Which Basecamp account?",
                                  [(str(a["id"]), f"{a.get('name')} ({a['id']})") for a in accounts])
        self.bc.account = account
        projects = [p for p in self.bc.data("projects", "list", "--all", timeout=60) or []
                    if p.get("status", "active") == "active"]
        if not projects:
            raise Stop("The agent's Basecamp person is not on any project in this account. Invite it to the project "
                       "in Basecamp, then run setup again.")
        projects.sort(key=lambda p: (p.get("name") or "").lower())
        pid = self.ask.choose("project", "Which Basecamp project should the agent use?",
                              [(str(p["id"]), p.get("name") or str(p["id"])) for p in projects])
        return account, next(p for p in projects if str(p["id"]) == pid)

    def pick_captain(self, project):
        me = self.bc.data("api", "get", "/my/profile.json") or {}
        people = [p for p in self.bc.data("api", "get", f"/projects/{project}/people.json") or []
                  if p.get("id") != me.get("id") and not p.get("client")]
        if not people:
            raise Stop(f"You are signed in as {me.get('name')}, and nobody else is on the project. The agent must be "
                       "its own Basecamp person: invite one, sign in as it, and run setup again "
                       "(docs/firstmate-account.md).")
        people.sort(key=lambda p: (not p.get("owner"), (p.get("name") or "").lower()))
        return int(self.ask.choose(
            "captain", f"The agent is signed in as {me.get('name')}. Which of these people are you?",
            [(str(p["id"]), p.get("name") + (f" <{p['email_address']}>" if p.get("email_address") else ""))
             for p in people]))

    def pick_agent(self):
        """The agent's person id, typed by the person setting up (or the "agent" answer): never taken from the login."""
        me = self.bc.data("api", "get", "/my/profile.json") or {}
        got = str(self.ask.text("agent", f"The login is signed in as {me.get('name')} ({me.get('id')}). If that is the "
                                         "agent, type its person id to confirm:") or "").strip()
        if not got.isdigit():
            raise Stop(f"the agent's person id is a number, got {got!r}; run setup again")
        return int(got)

    def choose_behaviors(self):
        self.out("\nA few questions about what the agent should do (press Enter for the suggested answer):")
        chosen = {}
        for k, q, d in QUESTIONS:  # card assignment is asked only with the card mirror on
            chosen[k] = self.ask.yes(k, q, d) if k != "assign" or chosen["cards"] else False
        return chosen

    def ensure_tools(self, project, dock, chosen):
        """Turn on (or add) the dock tool each chosen behavior needs; returns the behaviors left off and why."""
        off = {}
        done = set()
        for key, (kind, title, one) in TOOLS.items():
            if not chosen.get(key) or kind in done:
                continue
            done.add(kind)
            on = [d for d in dock if d.get("name") == kind and d.get("enabled")]
            if one and len(on) > 1:
                for k, (kd, _, _) in TOOLS.items():
                    if kd == kind and chosen.get(k):
                        off[k] = f"the project has {len(on)} {title} tools; keep one and run setup again to turn it on"
                continue
            if on:
                continue
            hidden = [d for d in dock if d.get("name") == kind]
            if hidden:
                self.bc.data("tools", "enable", str(hidden[0]["id"]), "-p", project)
                self.out(f"✓ Turned the project's {title} back on.")
            else:
                self.bc.data("tools", "create", "--type", kind, "-p", project)
                self.out(f"✓ Added {title} to the project.")
        return off

    def ensure_card_tables(self, home, project, dock):
        """A card table for each registered repo that has none, asking first; False when there is nothing to mirror."""
        repos = init_home.registered_projects(home)
        if not repos:
            self.out("  The firstmate home has no code repos registered yet, so the card mirror stays off for now.")
            return False
        titles = {(d.get("title") or "").strip().lower() for d in dock if d.get("name") == "kanban_board" and d.get("enabled")}
        for repo in repos:
            if repo.lower() in titles:
                continue
            if self.ask.yes(f"card_table:{repo}", f"Add a card table named {repo!r} to the project?", True):
                self.bc.data("tools", "create", repo, "--type", "kanban_board", "-p", project)
                self.out(f"✓ Added the card table {repo!r}.")
                titles.add(repo.lower())
        if not any(r.lower() in titles for r in repos):
            self.out("  No card table matches a code repo, so the card mirror stays off.")
            return False
        return True

    def test_sync(self, home, config):
        self.out("\nRunning one test sync (this can take a minute)...")
        try:
            r = self.run([os.path.join(self.sync_dir, "run.sh"), home, config], capture_output=True, text=True,
                         timeout=300, env=bc_env())
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        if ok:
            self.out("✓ The test sync worked.")
            return True
        log = os.path.join(os.path.dirname(config), "sync.log")
        last = [ln for ln in open(log)][-1].strip() if os.path.exists(log) and os.path.getsize(log) else ""
        self.out("✗ The test sync did not work" + (f": {last}" if last else ".")
                 + "\n  Run `basecamp-mate doctor` to see what is wrong and how to fix it.")
        return False

    # --- the whole thing ---

    def main(self):
        self.out("basecamp-mate setup: connects your agent to one Basecamp project.\n")
        self.prerequisites()
        home = self.pick_home()
        login = self.sign_in()
        account, proj = self.pick_project()
        project = str(proj["id"])
        dock = proj.get("dock") or []
        captain = self.pick_captain(project)
        agent = self.pick_agent()
        chosen = self.choose_behaviors()
        chosen["inbox"] = self.ask.yes("inbox", "Deliver each new message as a firstmate inbox note?", False)
        chosen["session_alert"] = chosen["inbox"] and self.ask.yes(
            "session_alert", "Tell you in the project's chat when no firstmate session is running (after a reboot, say) "
            "while messages wait for it?", True)
        timezone = None
        if chosen["checkins"]:
            timezone = self.ask.text("timezone", "Which time zone are your check-ins in? (like America/Chicago)",
                                     local_timezone() or "America/Chicago")
        off = self.ensure_tools(project, dock, chosen)
        if chosen["cards"] and not self.ensure_card_tables(home, project, dock):
            off["cards"] = "there is no code repo with a card table yet"
        if chosen["session_alert"] and not any(d.get("name") == "chat" for d in dock):
            off["session_alert"] = "the project has no chat"
        for k in off:
            chosen[k] = False
        chosen["assign"] = chosen["assign"] and chosen["cards"]
        config = os.path.join(home, "data", "basecamp-sync", "config.json")
        force, backup = False, None
        if os.path.exists(config):
            force = self.ask.yes("replace", "This home is already connected. Replace its settings with these answers?", True)
            if not force:
                raise Stop("Nothing was changed.")
            backup = json.load(open(config)).get("backup")  # set by hand or --backup; setup keeps it
        printed = []
        init = Init(f"https://app.basecamp.com/{account}/projects/{project}", login, home, captain=captain, agent=agent,
                    create_missing=chosen["cards"], force=force, cards=chosen["cards"], assign_captain=chosen["assign"], todos=chosen["todos"],
                    reports=chosen["reports"], checkins=timezone if chosen["checkins"] else None,
                    releases=chosen["releases"], inbox=chosen["inbox"],
                    session_alert=15 if chosen["session_alert"] else None,
                    pings=chosen["pings"], backup=backup or None, chats=chosen["chat"], no_keyring=True,
                    runner=lambda cmd, **kw: self.run(cmd, **{**kw, "env": bc_env()}),
                    system=self.system, sync_dir=self.sync_dir, out=printed.append)
        try:
            init.main()
        except Refuse as e:
            raise Stop(f"Basecamp's project is not ready yet:\n{e}")
        self.out("✓ Saved the settings and started the background services.")
        cfg = json.load(open(config))
        if chosen["chat"] and not cfg.get("chats"):
            off["chat"] = "the project has no chat"
            chosen["chat"] = False
        if chosen["releases"] and not cfg.get("releases"):
            off["releases"] = "no registered code repo is on GitHub"
            chosen["releases"] = False
        worked = self.test_sync(home, config)
        self.summary(home, proj, chosen, off, worked, cfg)
        return 0 if worked else 1

    def summary(self, home, proj, chosen, off, worked, cfg):
        self.out(f"\nDone. The agent at {home} is connected to the Basecamp project {proj.get('name')!r}.")
        on = [LABELS[k] for k in LABELS if chosen.get(k)]
        self.out("  On:  " + (", ".join(on) or "nothing"))
        self.out("  Off: " + (", ".join(LABELS[k] for k in LABELS if not chosen.get(k)) or "nothing"))
        for k, why in off.items():
            self.out(f"  ({LABELS[k]} is off: {why})")
        company = (cfg.get("participants") or {}).get("company") not in (None, False)
        self.out("  Listens to: " + ("you, and everyone in your company, who may ask it things (only you decide)"
                                     if company else "only you")
                 + ("; anyone else gets a short automatic reply" if cfg.get("blocked") not in (None, False)
                    else "; anyone else is ignored") + ". Ask the agent to change who it listens to.")
        self.out("  Optional: basecamp-axi is " + ("installed, so the agent can read Basecamp with it." if self.which("basecamp-axi")
                 else "not installed; with it the agent can read Basecamp itself (npm install -g basecamp-axi)."))
        self.out("To change any of this, run `basecamp-mate setup` again and give different answers.\n"
                 "If something stops working, run `basecamp-mate doctor`.")
        if not worked:
            self.out("The setup finished, but the test sync failed; start with `basecamp-mate doctor`.")


def cli(argv, **kw):
    ap = argparse.ArgumentParser(prog="basecamp-mate setup",
                                 description="Connect an agent's firstmate home to one Basecamp project, step by step.")
    ap.add_argument("--answers", metavar="FILE", help="a JSON file of answers: " + ", ".join(ANSWERS))
    ap.add_argument("--yes", action="store_true", help="take the suggested answer for anything not in --answers")
    a = ap.parse_args(argv)
    answers = {}
    if a.answers:
        try:
            answers = json.load(open(a.answers))
        except (OSError, ValueError) as e:
            print(f"Cannot read the answers file {a.answers}: {e}", file=sys.stderr)
            return 2
    prompter = Prompter(answers, interactive=not a.yes and not a.answers and sys.stdin.isatty())
    try:
        return Setup(prompter, **kw).main()
    except Stop as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nSetup was stopped; nothing more was changed.", file=sys.stderr)
        return 130
