"""Unit tests for `basecamp-mate setup` and `basecamp-mate doctor`, with the basecamp CLI, systemctl and run.sh stubbed."""
import copy, json, os, shutil, subprocess, sys, tempfile, time, unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doctor, init_home, setup_home  # noqa: E402
from test_init import CAPTAIN, ACTING, COLS, FakeSystem, table  # noqa: E402

ACCOUNT, PROJECT = "1111111", "22222222"


def done(data=None, ok=True, **extra):
    return SimpleNamespace(stdout=json.dumps({"ok": ok, "data": data, **extra}), stderr="", returncode=0)


class Stub:
    """The basecamp CLI, systemctl and run.sh, as setup and doctor call them."""

    def __init__(self):
        self.dock = [{"name": "chat", "id": 700, "title": "Chat", "enabled": True},
                     {"name": "todoset", "id": 800, "title": "To-dos", "enabled": True},
                     {"name": "questionnaire", "id": 900, "title": "Automatic Check-ins", "enabled": False},
                     {"name": "message_board", "id": 9, "title": "Message Board", "enabled": True}]
        self.tables = {}
        self.people = [{"id": CAPTAIN, "name": "Captain", "owner": True, "email_address": "cap@example.com"},
                       {"id": ACTING, "name": "Firstmate", "owner": False}]
        self.profiles = [{"name": "firstmate", "authenticated": True}]
        self.auth = {"authenticated": True, "expired": False}
        self.calls, self.envs, self.sync_rc, self.timeout_auth = [], [], 0, False
        self.unread = 3  # the agent's unread notifications
        self.active = {}
        self.next_id = 1000
        self.version = "0.12.0"

    def __call__(self, cmd, **kw):
        if cmd[0] in ("basecamp", "run.sh") or cmd[0].endswith("/run.sh"):
            self.envs.append((kw.get("env") or {}).get("BASECAMP_NO_KEYRING"))
        if cmd[0] == "systemctl":
            if cmd[2] == "show-environment":
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            state = self.active.get(cmd[3], ("enabled", "active"))
            return SimpleNamespace(stdout=state[0 if cmd[2] == "is-enabled" else 1] + "\n", stderr="", returncode=0)
        if cmd[0].endswith("run.sh"):
            self.calls.append(["run.sh", *cmd[1:]])
            return SimpleNamespace(stdout="", stderr="", returncode=self.sync_rc)
        if cmd[0] in ("git", "gh"):
            return SimpleNamespace(stdout="", stderr="", returncode=2)
        assert cmd[0] == "basecamp", cmd
        args, i = [], 1
        while i < len(cmd):
            if cmd[i] in ("-a", "-P"):
                i += 2
                continue
            if cmd[i] != "--json":
                args.append(cmd[i])
            i += 1
        self.calls.append(args)
        if args == ["--version"]:
            return SimpleNamespace(stdout=f"basecamp version {self.version}\n", stderr="", returncode=0)
        if args == ["auth", "status"]:
            if self.timeout_auth:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return done(self.auth)
        if args[:2] in (["auth", "login"], ["profile", "create"]):
            self.auth = {"authenticated": True, "expired": False}
            self.profiles.append({"name": "firstmate"})
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if args == ["auth", "refresh"]:
            return done({}, ok=False, error="refresh failed")
        if args == ["profile", "list"]:
            return done(self.profiles)
        if args == ["accounts", "list"]:
            return done([{"id": int(ACCOUNT), "name": "Acme"}])
        if args[:2] == ["projects", "list"]:
            return done([{"id": int(PROJECT), "name": "HQ", "status": "active", "dock": copy.deepcopy(self.dock)},
                         {"id": 5, "name": "Other", "status": "active", "dock": []}])
        if args[:2] == ["tools", "create"]:
            kind = args[args.index("--type") + 1]
            title = args[2] if args[2] != "--type" else kind
            self.next_id += 100
            self.dock.append({"name": kind, "id": self.next_id, "title": title, "enabled": True})
            if kind == "kanban_board":
                self.tables[str(self.next_id)] = table(self.next_id, title, drop=("Figuring it out", "In progress", "Ready for QA"))
            return done({"id": self.next_id})
        if args[:2] == ["tools", "enable"]:
            for d in self.dock:
                if str(d["id"]) == args[2]:
                    d["enabled"] = True
            return done({})
        if args[:3] == ["cards", "column", "create"]:
            self.next_id += 1
            t = self.tables[args[args.index("--card-table") + 1]]
            t["lists"].append({"id": self.next_id, "title": args[3]})
            return done({"id": self.next_id})
        if args[:2] == ["api", "get"]:
            path = args[2]
            if path == f"/projects/{PROJECT}.json":
                return done({"id": int(PROJECT), "name": "HQ", "dock": copy.deepcopy(self.dock)})
            if path == f"/projects/{PROJECT}/people.json":
                return done(self.people)
            if path.startswith("/people.json?page="):  # the account's people, one page
                return done(self.people if path.endswith("=1") else [])
            if path == "/my/profile.json":
                return done({"id": ACTING, "name": "Firstmate"})
            if path == "/my/readings.json":
                return done({"unreads": [{"id": i} for i in range(self.unread)], "reads": []})
            if "/card_tables/" in path:
                return done(copy.deepcopy(self.tables[path.split("/")[-1].split(".")[0]]))
        raise AssertionError(f"unexpected call {args}")

    def writes(self):
        return [a for a in self.calls if a[:2] in (["tools", "create"], ["tools", "enable"], ["cards", "column"])]


class System(FakeSystem):
    """FakeSystem with systemctl answered by the stub, for doctor's service checks."""

    def __init__(self, runner, unit_dir):
        super().__init__()
        self.runner, self.unit_dir = runner, unit_dir

    def systemctl(self, *args):
        return self.runner(["systemctl", "--user", *args])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        for d in ("data", "state", "bin"):
            os.makedirs(os.path.join(self.home, d))
        reg = os.path.join(self.home, "bin", "fm-check-register.sh")
        with open(reg, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(reg, 0o755)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for f in ("run.sh", "sync.py"):
            open(os.path.join(self.repo, f), "w").close()
        self.stub, self.printed = Stub(), []
        self.system = System(self.stub, os.path.join(self.tmp, "units"))
        self.config = os.path.join(self.home, "data", "basecamp-sync", "config.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def setup(self, interactive=False, inputs=(), **answers):
        answers.setdefault("home", self.home)
        feed = list(inputs)
        prompter = setup_home.Prompter(answers, interactive=interactive, inp=lambda q: feed.pop(0), out=self.printed.append)
        s = setup_home.Setup(prompter, runner=self.stub, system=self.system, which=lambda n: "/usr/bin/" + n,
                             sync_dir=self.repo, out=self.printed.append)
        return s.main()

    def text(self):
        return "\n".join(self.printed)


class SetupTest(Base):
    def test_defaults(self):
        self.assertEqual(self.setup(project=PROJECT, timezone="America/Chicago"), 0)
        cfg = json.load(open(self.config))
        self.assertEqual(cfg, {"account": ACCOUNT, "project": PROJECT, "captain": CAPTAIN, "profile": "firstmate", "agent": ACTING,
                               "chats": [700], "todos": {"todoset": "800"},
                               "checkins": {"questionnaires": ["900"], "timezone": "America/Chicago"}, "blocked": {},
                               "acknowledge": {"read": "👀", "decided": "👍"}})
        # the disabled check-ins tool was turned back on, nothing else written in Basecamp
        self.assertEqual(self.stub.writes(), [["tools", "enable", "900", "-p", PROJECT]])
        # the timer's service carries the no-keyring environment, every basecamp call used the file store
        units = self.system.units
        self.assertTrue(all("Environment=BASECAMP_NO_KEYRING=1" in (u[0] if isinstance(u, tuple) else u) for u in units.values()))
        self.assertEqual(list(units), [init_home.unit_name(self.home)])
        self.assertEqual(set(self.stub.envs), {"1"})
        self.assertIn(["run.sh", self.home, self.config], self.stub.calls)
        self.assertIn("On:  chat inbox, decision to-dos, check-in answers", self.text())
        self.assertIn("Listens to: only you; anyone else gets a short automatic reply.", self.text())
        self.assertIn("basecamp-mate setup` again", self.text())

    def test_project_url_and_choices(self):
        self.assertEqual(self.setup(project=f"https://app.basecamp.com/{ACCOUNT}/projects/{PROJECT}",
                                    chat=False, todos=False, checkins=False, reports=True), 0)
        cfg = json.load(open(self.config))
        self.assertEqual(cfg, {"account": ACCOUNT, "project": PROJECT, "captain": CAPTAIN, "profile": "firstmate", "agent": ACTING,
                               "message_board": "9", "blocked": {}, "acknowledge": {"read": "👀", "decided": "👍"}})
        self.assertEqual(self.stub.writes(), [])
        self.assertEqual(list(self.system.units), [init_home.unit_name(self.home)])

    def test_inbox_turns_on_the_session_alert_unless_declined(self):
        self.assertEqual(self.setup(project=PROJECT, checkins=False, inbox=True), 0)
        cfg = json.load(open(self.config))
        self.assertEqual((cfg["inbox"], cfg["session_alert"]), ({}, {"chat": 700, "after_minutes": 15}))
        self.assertIn("session-down alert", self.text().split("Off:")[0])
        self.assertEqual(self.setup(project=PROJECT, checkins=False, inbox=True, session_alert=False, replace=True), 0)
        self.assertNotIn("session_alert", json.load(open(self.config)))

    def test_interactive_picks(self):
        # project pick (2 projects, sorted by name: HQ, Other), captain (one other person), 8 questions, inbox, time zone
        inputs = ["1", "", "", "n", "", "", "", "", "", "", ""]
        self.assertEqual(self.setup(interactive=True, inputs=inputs, home=self.home, login="firstmate"), 0)
        cfg = json.load(open(self.config))
        self.assertEqual(cfg["project"], PROJECT)
        self.assertNotIn("checkins", cfg)
        self.assertIn("Which Basecamp project should the agent use?", self.text())

    def test_card_mirror_creates_tables_and_columns(self):
        with open(os.path.join(self.home, "data", "projects.md"), "w") as f:
            f.write("- engine [direct-PR] - the engine\n")
        self.assertEqual(self.setup(project=PROJECT, cards=True, checkins=False), 0)
        cfg = json.load(open(self.config))
        self.assertEqual(sorted(cfg["tables"]["engine"]), sorted(["table", *COLS]))
        made = self.stub.writes()
        self.assertEqual(made[0][:4], ["tools", "create", "engine", "--type"])
        self.assertEqual(sorted(a[3] for a in made[1:]), ["Figuring it out", "In progress", "Ready for QA"])

    def test_card_mirror_without_owner_assignment(self):
        with open(os.path.join(self.home, "data", "projects.md"), "w") as f:
            f.write("- engine [direct-PR] - the engine\n")
        self.assertEqual(self.setup(project=PROJECT, cards=True, assign=False, checkins=False), 0)
        self.assertIs(json.load(open(self.config))["assign_captain"], False)
        self.assertIn("card assignment for decisions", self.text().split("Off:")[1])

    def test_card_mirror_without_repos_stays_off(self):
        self.assertEqual(self.setup(project=PROJECT, cards=True, checkins=False), 0)
        self.assertNotIn("tables", json.load(open(self.config)))
        self.assertIn("card mirror is off", self.text())

    def test_sign_in_with_device_code(self):
        self.stub.profiles, self.stub.auth = [], {"authenticated": False}
        with self.assertRaises(setup_home.Stop) as e:
            self.setup(project=PROJECT)
        self.assertIn("--device-code", str(e.exception))
        self.assertFalse(os.path.exists(self.config))
        inputs = ["", "", "", "", "", "", "", "", "", "", "America/Chicago"]
        self.assertEqual(self.setup(interactive=True, inputs=inputs, project=PROJECT), 0)
        self.assertIn(["profile", "create", "firstmate", "--device-code"], self.stub.calls)
        self.assertIn("private browser window", self.text())

    def test_too_old_cli_stops_naming_the_minimum(self):
        self.stub.version = "0.11.0"
        with self.assertRaises(setup_home.Stop) as e:
            self.setup(project=PROJECT)
        self.assertIn("version 0.11.0; basecamp-mate needs 0.12.0 or newer", str(e.exception))
        self.assertIn(setup_home.INSTALL_CLI, str(e.exception))

    def test_not_a_home(self):
        with self.assertRaises(setup_home.Stop) as e:
            self.setup(home=self.tmp, project=PROJECT)
        self.assertIn("is not a firstmate home", str(e.exception))

    def test_only_the_login_on_the_project(self):
        self.stub.people = [p for p in self.stub.people if p["id"] == ACTING]
        with self.assertRaises(setup_home.Stop) as e:
            self.setup(project=PROJECT)
        self.assertIn("its own Basecamp person", str(e.exception))

    def test_rerun_replaces_and_failed_test_sync(self):
        self.setup(project=PROJECT, checkins=False)
        self.stub.sync_rc = 1
        os.makedirs(os.path.dirname(self.config), exist_ok=True)
        with open(os.path.join(os.path.dirname(self.config), "sync.log"), "a") as f:
            f.write("2026-01-01T00:00:00Z FAILED BasecampError: boom\n")
        self.assertEqual(self.setup(project=PROJECT, checkins=False, pings=True), 1)
        self.assertIn("pings", json.load(open(self.config)))
        self.assertIn("FAILED BasecampError: boom", self.text())
        self.assertIn("basecamp-mate doctor", self.text())
        with self.assertRaises(setup_home.Stop):
            self.setup(project=PROJECT, replace=False)

    def test_cli_answers_file(self):
        answers = os.path.join(self.tmp, "answers.json")
        with open(answers, "w") as f:
            json.dump({"home": self.home, "project": PROJECT, "checkins": False, "todos": False}, f)
        rc = setup_home.cli(["--answers", answers], runner=self.stub, system=self.system,
                            which=lambda n: "/usr/bin/" + n, sync_dir=self.repo, out=self.printed.append)
        self.assertEqual(rc, 0)
        self.assertNotIn("todos", json.load(open(self.config)))


class DoctorTest(Base):
    def doctor(self, home=None):
        self.printed.clear()
        d = doctor.Doctor(runner=self.stub, system=self.system, which=lambda n: "/usr/bin/" + n, out=self.printed.append)
        return d.main(home)

    def healthy(self):
        with open(os.path.join(self.home, "data", "projects.md"), "w") as f:
            f.write("- engine [direct-PR] - the engine\n")
        self.stub.dock.append({"name": "kanban_board", "id": 100, "title": "engine", "enabled": True})
        self.stub.tables["100"] = table(100, "engine")
        self.setup(project=PROJECT, cards=True, checkins=False)
        os.makedirs(self.system.unit_dir, exist_ok=True)
        for name, unit in self.system.units.items():
            if isinstance(unit, tuple):
                files = {f"{name}.service": unit[0], f"{name}.timer": unit[1]}
            else:
                files = {f"{name}.service": unit}
            for fname, text in files.items():
                with open(os.path.join(self.system.unit_dir, fname), "w") as f:
                    f.write(text)
        with open(os.path.join(os.path.dirname(self.config), "sync.log"), "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " counts {}\n")

    def test_healthy(self):
        self.healthy()
        self.assertEqual(self.doctor(), 0, self.text())
        self.assertIn("Everything looks good.", self.text())
        self.assertIn(f"\n{self.home}", self.text())  # found from the unit files

    def test_too_old_cli(self):
        self.healthy()
        self.stub.version = "0.11.9"
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("is version 0.11.9; basecamp-mate needs 0.12.0 or newer", self.text())
        self.stub.version = "0.13.2"
        self.assertEqual(self.doctor(self.home), 0, self.text())
        self.assertIn("Basecamp command-line tool 0.13.2", self.text())

    def test_nothing_set_up(self):
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("not connected to Basecamp yet", self.text())
        self.assertIn("Fix: run  basecamp-mate setup", self.text())

    def test_locked_keyring_times_out(self):
        self.healthy()
        self.stub.timeout_auth = True
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("keyring", self.text())
        self.assertIn("BASECAMP_NO_KEYRING=1 basecamp auth login -P firstmate --device-code", self.text())

    def test_expired_login(self):
        self.healthy()
        self.stub.auth = {"authenticated": True, "expired": True}
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("has expired and could not be renewed", self.text())

    def test_missing_column(self):
        self.healthy()
        self.stub.tables["100"]["lists"] = [lst for lst in self.stub.tables["100"]["lists"] if lst["title"] != "In progress"]
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("missing its 'In progress' column", self.text())

    def test_timer_not_running_and_failed_sync(self):
        self.healthy()
        self.stub.active[init_home.unit_name(self.home) + ".timer"] = ("enabled", "failed")
        with open(os.path.join(os.path.dirname(self.config), "sync.log"), "a") as f:
            f.write("2026-01-01T00:00:00Z FAILED token refresh; run basecamp auth login -P firstmate\n")
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("The sync timer (every 30 seconds) is not running (enabled, failed)", self.text())
        self.assertIn("systemctl --user enable --now", self.text())
        self.assertIn("The last sync failed", self.text())
        self.assertIn("sign-in needs renewing", self.text())

    def test_a_leftover_listener_is_flagged(self):
        self.healthy()
        with open(os.path.join(self.system.unit_dir, init_home.unit_name(self.home) + "-listen.service"), "w") as f:
            f.write("[Service]\nExecStart=/usr/bin/env python3 sync.py listen\n")
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("The old event listener is still installed", self.text())
        self.assertIn("it removes the listener", self.text())

    def test_notifications_read_and_the_unread_cap(self):
        self.healthy()
        self.assertEqual(self.doctor(self.home), 0, self.text())
        self.assertIn("the agent's Basecamp notifications (3 unread)", self.text())
        self.stub.unread = 100
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("100 unread Basecamp notifications, Basecamp's limit", self.text())

    def test_wake_check_not_registered(self):
        self.healthy()
        self.system.checks.clear()
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("wake check is not registered", self.text())
        self.assertIn("fm-check-register.sh basecamp-sync", self.text())

    def test_missing_home(self):
        self.healthy()
        shutil.rmtree(os.path.join(self.home, "state"))
        self.assertEqual(self.doctor(), 1)
        self.assertIn("is not a firstmate home", self.text())

    def anywhere(self, config):
        cfg = json.load(open(config))
        cfg["assigned_todos"] = {"scope": "account"}
        json.dump(cfg, open(config, "w"))

    def test_one_home_taking_todos_anywhere_is_fine(self):
        self.healthy()
        self.anywhere(self.config)
        self.assertEqual(self.doctor(), 0, self.text())

    def test_two_homes_taking_todos_anywhere_with_one_login_warns(self):
        self.healthy()
        self.anywhere(self.config)
        home2 = os.path.join(self.tmp, "home2")
        shutil.copytree(self.home, home2)
        config2 = os.path.join(home2, "data", "basecamp-sync", "config.json")
        d = doctor.Doctor(runner=self.stub, system=self.system, which=lambda n: "/usr/bin/" + n, out=self.printed.append)
        d.homes = lambda: [(self.home, self.config), (home2, config2)]
        self.assertEqual(d.main(), 1)
        self.assertIn(f"2 homes take to-dos assigned anywhere in account {ACCOUNT} with the sign-in 'firstmate'", self.text())
        self.assertIn('keep "assigned_todos": {"scope": "account"} only in the main home', self.text())

    def test_stale_sync(self):
        self.healthy()
        with open(os.path.join(os.path.dirname(self.config), "sync.log"), "a") as f:
            f.write("2026-01-01T00:00:00Z counts {}\n")
        self.assertEqual(self.doctor(self.home), 1)
        self.assertIn("The sync has not run for", self.text())


if __name__ == "__main__":
    unittest.main()
