"""Unit tests for `sync.py init`. The basecamp CLI is stubbed and systemd/check registration sit behind a fake seam."""
import copy, hashlib, io, json, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stderr
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import init_home  # noqa: E402

URL = "https://app.basecamp.com/1111111/projects/22222222"
CAPTAIN, ACTING = 33333333, 44444444
COLS = {"Triage": 1, "Not now": 2, "Figuring it out": 3, "In progress": 4, "Ready for QA": 5, "Done": 6}


def table(base, title, drop=()):
    return {"id": base, "title": title,
            "lists": [{"id": base + n, "title": c} for c, n in COLS.items() if c not in drop]}


class Stub:
    """Answers the read calls init makes, like the basecamp CLI would."""

    def __init__(self):
        self.project = {"id": 22222222, "dock": [
            {"name": "message_board", "id": 9, "title": "Message Board", "enabled": True},
            {"name": "kanban_board", "id": 100, "title": "Server", "enabled": True},
            {"name": "kanban_board", "id": 200, "title": "Engine ", "enabled": True},
            {"name": "kanban_board", "id": 300, "title": "Old", "enabled": False},
            {"name": "chat", "id": 700, "title": "Updates", "enabled": True},
        ]}
        self.tables = {"100": table(100, "Server"), "200": table(200, "Engine ")}
        self.people = [{"id": CAPTAIN, "name": "Captain", "owner": True, "email_address": "cap@example.com"},
                       {"id": ACTING, "name": "Firstmate", "owner": False, "email_address": "fm@example.com"}]
        self.account = []  # people in the account who are not on the project
        self.calls = []
        self.origins, self.renames = {}, {}  # projects/<repo> -> origin URL; owner/name -> gh nameWithOwner

    def __call__(self, cmd, **kw):
        if cmd[0] == "git":
            url = self.origins.get(os.path.basename(cmd[2]))
            return SimpleNamespace(stdout=(url or "") + "\n", stderr="", returncode=0 if url else 2)
        if cmd[0] == "gh":
            full = cmd[3]
            return SimpleNamespace(stdout=json.dumps({"nameWithOwner": self.renames.get(full, full)}), stderr="", returncode=0)
        assert cmd[:5] == ["basecamp", "-a", "1111111", "-P", "firstmate"], cmd
        args = cmd[5:-1]
        self.calls.append(args)
        if args[:2] == ["api", "get"]:
            path = args[2]
            if path == "/projects/22222222.json":
                data = self.project
            elif path == "/projects/22222222/people.json":
                data = self.people
            elif path.startswith("/people.json?page="):  # the account's people, one page
                data = self.people + self.account if path.endswith("=1") else []
            elif path == "/my/profile.json":
                data = {"id": ACTING}
            else:
                data = self.tables[path.split("/")[-1].split(".")[0]]
        elif args[:3] == ["cards", "column", "create"]:
            data = {"id": 555}
        else:
            raise AssertionError(f"unexpected call {args}")
        return SimpleNamespace(stdout=json.dumps({"ok": True, "data": copy.deepcopy(data)}), stderr="", returncode=0)

    def writes(self):
        return [a for a in self.calls if a[:2] != ["api", "get"]]


class FakeSystem:
    """Stands in for systemd and fm-check-register.sh, keeping what was installed."""

    def __init__(self):
        self.units, self.checks, self.calls = {}, {}, []

    def preflight(self, home):
        pass

    def install_timer(self, name, service, timer, dry):
        self.calls.append(("timer", name, dry))
        if self.units.get(name) == (service, timer):
            return []
        if not dry:
            self.units[name] = (service, timer)
        return [f"install {name}"]

    def remove_service(self, name, dry):
        self.calls.append(("remove", name, dry))
        if name not in self.units:
            return []
        if not dry:
            del self.units[name]
        return [f"remove {name}.service"]

    def register_check(self, home, cid, script, dry):
        self.calls.append(("check", cid, dry))
        if self.checks.get(cid) == script:
            return []
        if not dry:
            self.checks[cid] = script
        return [f"register {cid}"]


EXPECTED = {
    "account": "1111111", "project": "22222222", "captain": CAPTAIN, "profile": "firstmate", "agent": ACTING,
    "repos": {"Engine": "engine", "my-server": "server"},  # the registered spelling
    "chats": [700],
    "tables": {
        "server": {"table": "100", **{c: str(100 + n) for c, n in COLS.items()}},
        "engine": {"table": "200", **{c: str(200 + n) for c, n in COLS.items()}},
    },
    "blocked": {},  # a new home answers blocked senders
    "acknowledge": {"read": "👀", "decided": "👍"},  # and puts 👀 on what it reads, 👍 on a decision carried out
}


class InitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, "state"))
        os.makedirs(os.path.join(self.home, "data"))
        with open(os.path.join(self.home, "data", "projects.md"), "w") as f:
            f.write("- Engine [direct-PR] - the engine (added 2026-01-01)\n"
                    "- my-server [direct-PR] - the server\n")
        self.stub, self.system, self.printed = Stub(), FakeSystem(), []
        self.dir = os.path.join(self.home, "data", "basecamp-sync")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def init(self, dry=False, **kw):
        kw.setdefault("repo_map", ["server=my-server"])
        kw.setdefault("agent", ACTING)
        return init_home.Init(URL, "firstmate", self.home, dry=dry, runner=self.stub, system=self.system,
                              sync_dir=os.path.join(self.tmp, "repo"), out=self.printed.append, **kw)

    def files(self):
        out = {}
        for d, _, names in os.walk(self.home):
            for n in names:
                p = os.path.join(d, n)
                out[p] = open(p).read()
        return out

    def test_discovers_config(self):
        cfg, create = self.init().discover()
        self.assertEqual(cfg, EXPECTED)
        self.assertEqual(create, [])

    def test_releases_from_github_origins(self):
        self.stub.origins = {"Engine": "https://github.com/acme/engine.git", "my-server": "git@github.com:old/srv.git"}
        self.stub.renames = {"old/srv": "acme/server"}
        cfg, _ = self.init().discover()
        self.assertEqual(cfg["releases"], {"board": "9", "repos": {"acme/engine": "engine", "acme/server": "server"}})
        self.stub.project["dock"] = [d for d in self.stub.project["dock"] if d["name"] != "message_board"]
        self.assertNotIn("releases", self.init().discover()[0])

    def test_writes_config_side_files_and_installs(self):
        self.init().main()
        self.assertEqual(json.load(open(os.path.join(self.dir, "config.json"))), EXPECTED)
        for name in ("extra-repos.json", "figuring.json", "not-now.json", "skip.json", "boards.json",
                     "decisions.json", "pending-comments.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, name)), name)
        self.assertEqual(json.load(open(os.path.join(self.dir, "skip.json"))), [])
        (name, (service, timer)), = self.system.units.items()
        self.assertTrue(name.startswith("basecamp-sync-"))
        self.assertIn("run.sh", service)
        self.assertIn("TimeoutStartSec=240", service)
        self.assertIn("OnUnitActiveSec=30s", timer)
        self.assertEqual(list(self.system.checks), ["basecamp-sync"])
        self.assertEqual(self.stub.writes(), [])

    def test_no_chats_and_no_keyring(self):
        cfg, _ = self.init(chats=False).discover()
        self.assertNotIn("chats", cfg)
        self.init(no_keyring=True).main()
        svc, _ = self.system.units[init_home.unit_name(self.home)]
        self.assertIn("Environment=BASECAMP_NO_KEYRING=1\n", svc)
        self.assertNotIn("Environment=", self.init(no_keyring=False).units("c")[1])

    def test_missing_column_refused(self):
        self.stub.tables["200"] = table(200, "Engine", drop=("In progress",))
        with self.assertRaises(init_home.Refuse) as e:
            self.init().main()
        self.assertIn("'engine' has no column named exactly 'In progress'", str(e.exception))
        self.assertFalse(os.path.exists(self.dir))
        self.assertEqual(self.system.calls, [])

    def test_missing_column_created_only_when_asked(self):
        self.stub.tables["200"] = table(200, "Engine", drop=("Ready for QA",))
        with self.assertRaises(init_home.Refuse):
            self.init().main()
        self.init(create_missing=True, dry=True).main()
        self.assertEqual(self.stub.writes(), [])
        self.init(create_missing=True).main()
        self.assertEqual(self.stub.writes(), [["cards", "column", "create", "Ready for QA", "--card-table", "200", "-p", "22222222"]])
        self.assertEqual(json.load(open(os.path.join(self.dir, "config.json")))["tables"]["engine"]["Ready for QA"], "555")

    def test_built_in_column_never_created(self):
        self.stub.tables["200"] = table(200, "Engine", drop=("Triage",))
        with self.assertRaises(init_home.Refuse) as e:
            self.init(create_missing=True).main()
        self.assertIn("built-in", str(e.exception))
        self.assertEqual(self.stub.writes(), [])

    def add_ideas(self, **kw):
        self.stub.project["dock"].append({"name": "kanban_board", "id": 500, "title": "Ideas", "enabled": True})
        self.stub.tables["500"] = table(500, "Ideas", **kw)

    def test_unmatched_table_skipped(self):
        self.add_ideas()
        cfg, _ = self.init().discover()
        self.assertEqual(cfg, EXPECTED)
        plan = self.init(dry=True).main()
        self.assertIn("skip card table 'ideas'", plan)
        self.init().main()
        self.assertEqual(json.load(open(os.path.join(self.dir, "config.json"))), EXPECTED)
        self.assertIn("skipped card table 'ideas': no matching repo; pass --repo-map ideas=<repo> to include it",
                      self.printed)
        self.assertNotIn(["api", "get", "/buckets/22222222/card_tables/500.json"], self.stub.calls)

    def test_skipped_table_columns_never_checked(self):
        self.add_ideas(drop=("Triage", "In progress"))
        self.init(create_missing=True).main()
        self.assertEqual(json.load(open(os.path.join(self.dir, "config.json"))), EXPECTED)
        self.assertEqual(self.stub.writes(), [])

    def test_every_table_unmatched_refused(self):
        with open(os.path.join(self.home, "data", "projects.md"), "w") as f:
            f.write("- my-server [direct-PR] - the server\n")
        with self.assertRaises(init_home.Refuse) as e:
            self.init(repo_map=[]).main()
        self.assertIn("no card table matches a registered project", str(e.exception))
        self.assertIn("skipped: engine, server", str(e.exception))
        self.assertFalse(os.path.exists(self.dir))
        self.assertEqual(self.system.calls, [])

    def test_table_matching_several_repos_refused(self):
        with open(os.path.join(self.home, "data", "projects.md"), "a") as f:
            f.write("- engine [direct-PR] - a second engine\n")
        with self.assertRaises(init_home.Refuse) as e:
            self.init().discover()
        self.assertIn("card table 'engine' matches several registered projects", str(e.exception))

    def test_two_tables_one_repo_refused(self):
        self.add_ideas()
        with self.assertRaises(init_home.Refuse) as e:
            self.init(repo_map=["server=my-server", "ideas=my-server"]).discover()
        self.assertIn("repo 'my-server' is mapped to both", str(e.exception))

    def test_repo_map_must_name_a_table_and_a_registered_repo(self):
        with self.assertRaises(init_home.Refuse) as e:
            self.init(repo_map=["server=my-server", "nope=engine", "engine=unknown"]).discover()
        self.assertIn("no card table titled that", str(e.exception))
        self.assertIn("'unknown', which is not registered", str(e.exception))

    def test_ambiguous_tables_and_captain_refused(self):
        self.stub.project["dock"].append({"name": "kanban_board", "id": 400, "title": "server", "enabled": True})
        self.stub.tables["400"] = table(400, "server")
        self.stub.people.append({"id": 55555555, "name": "Other owner", "owner": True})
        with self.assertRaises(init_home.Refuse) as e:
            self.init().discover()
        self.assertIn("two card tables are titled 'server'", str(e.exception))
        self.assertIn("cannot tell who the captain is", str(e.exception))

    def test_captain_by_id_or_email(self):
        self.stub.people.append({"id": 55555555, "name": "Other owner", "owner": True, "email_address": "o@example.com"})
        self.assertEqual(self.init(captain="o@example.com").discover()[0]["captain"], 55555555)
        self.assertEqual(self.init(captain=str(CAPTAIN)).discover()[0]["captain"], CAPTAIN)
        with self.assertRaises(init_home.Refuse):
            self.init(captain=str(ACTING)).discover()
        with self.assertRaises(init_home.Refuse):
            self.init(captain="nobody@example.com").discover()

    def test_idempotent(self):
        self.init().main()
        before = self.files()
        self.printed.clear()
        self.assertEqual(self.init().main(), [])
        self.assertEqual(self.files(), before)
        self.assertEqual(self.printed[-1], "nothing to change")
        self.assertEqual(self.stub.writes(), [])

    def test_differing_config_refused_without_force(self):
        self.init().main()
        path = os.path.join(self.dir, "config.json")
        cfg = json.load(open(path))
        cfg["captain"] = 1
        json.dump(cfg, open(path, "w"))
        with self.assertRaises(init_home.Refuse) as e:
            self.init().main()
        self.assertIn("differs in: captain", str(e.exception))
        self.init(force=True).main()
        self.assertEqual(json.load(open(path)), EXPECTED)

    def test_no_cards_reads_no_card_tables(self):
        self.stub.origins = {"Engine": "https://github.com/acme/engine.git"}
        cfg, create = self.init(cards=False, repo_map=[]).discover()
        self.assertEqual(cfg, {k: v for k, v in EXPECTED.items() if k not in ("repos", "tables")}
                         | {"releases": {"board": "9", "repos": {"acme/engine": "Engine"}}})
        self.assertEqual(create, [])
        self.assertFalse([a for a in self.stub.calls if "/card_tables/" in a[2]])

    def test_no_cards_works_without_tables_and_writes_only_pending(self):
        self.stub.project["dock"] = [d for d in self.stub.project["dock"] if d["name"] != "kanban_board"]
        with self.assertRaises(init_home.Refuse):
            self.init().discover()
        self.init(cards=False, repo_map=[]).main()
        self.assertNotIn("tables", json.load(open(os.path.join(self.dir, "config.json"))))
        self.assertEqual(sorted(os.listdir(self.dir)), ["config.json", "defaults.md", "pending-comments.jsonl"])
        self.assertEqual(list(self.system.checks), ["basecamp-sync"])
        self.assertEqual(self.stub.writes(), [])

    def test_no_cards_refuses_card_options(self):
        with self.assertRaises(init_home.Refuse):
            self.init(cards=False)
        with self.assertRaises(init_home.Refuse):
            self.init(cards=False, repo_map=[], create_missing=True)

    def add_dock(self):
        self.stub.project["dock"] += [{"name": "todoset", "id": 66, "title": "Decisions", "enabled": True},
                                      {"name": "questionnaire", "id": 55, "title": "Automatic Check-ins", "enabled": True},
                                      {"name": "todoset", "id": 67, "title": "Off", "enabled": False}]

    def test_behavior_flags_are_off_by_default(self):
        self.add_dock()
        cfg, _ = self.init().discover()
        self.assertEqual(cfg, EXPECTED)

    def test_behavior_flags_add_their_keys(self):
        self.add_dock()
        cfg, _ = self.init(todos=True, reports=True, every_line=True, checkins="America/Chicago").discover()
        self.assertEqual(cfg, EXPECTED | {"chats": [{"chat": 700, "every_line": True}], "message_board": "9",
                                          "todos": {"todoset": "66"},
                                          "checkins": {"questionnaires": ["55"], "timezone": "America/Chicago"}})

    def test_inbox_turns_on_delivery_and_a_failures_only_check(self):
        cfg, _ = self.init(inbox=True).discover()
        self.assertEqual(cfg, EXPECTED | {"inbox": {}})
        self.init(inbox=True).main()
        self.assertEqual(self.system.checks["basecamp-sync"], init_home.CHECK_INBOX)
        self.assertNotIn("pending-comments", init_home.CHECK_INBOX.split("Pending records")[1])

    def test_session_alert_watches_the_inbox_and_posts_in_the_dock_chat(self):
        cfg, _ = self.init(inbox=True, session_alert=15).discover()
        self.assertEqual(cfg, EXPECTED | {"inbox": {}, "session_alert": {"chat": 700, "after_minutes": 15}})
        with self.assertRaises(init_home.Refuse):
            self.init(session_alert=15)  # needs --inbox
        self.stub.project["dock"] = [d for d in self.stub.project["dock"] if d["name"] != "chat"]
        with self.assertRaises(init_home.Refuse):
            self.init(inbox=True, session_alert=15).discover()

    def test_pings_flag_turns_the_pings_behavior_on(self):
        cfg, _ = self.init(pings=True).discover()
        self.assertEqual(cfg, EXPECTED | {"pings": {}})
        self.assertNotIn("pings", self.init().discover()[0])

    def test_no_assign_captain_writes_the_switch(self):
        self.assertEqual(self.init(assign_captain=False).discover()[0], EXPECTED | {"assign_captain": False})
        self.assertNotIn("assign_captain", self.init().discover()[0])

    def test_assigned_todos_flag_turns_the_behavior_on(self):
        cfg, _ = self.init(assigned_todos=True).discover()
        self.assertEqual(cfg, EXPECTED | {"assigned_todos": {}, "schedule": {"dated": "ask", "recurring": "due"}})
        self.assertNotIn("assigned_todos", self.init().discover()[0])

    def test_assigned_steps_flag_turns_the_behavior_on(self):
        cfg, _ = self.init(assigned_steps=True).discover()
        self.assertEqual(cfg, EXPECTED | {"assigned_steps": {}, "schedule": {"dated": "ask", "recurring": "due"}})
        self.assertNotIn("assigned_steps", self.init().discover()[0])

    def test_assigned_todos_anywhere_takes_them_account_wide(self):
        cfg, _ = self.init(assigned_anywhere=True).discover()
        self.assertEqual(cfg["assigned_todos"], {"scope": "account"})

    def test_mentions_anywhere_takes_them_account_wide(self):
        self.assertEqual(self.init(mentions_anywhere=True).discover()[0], EXPECTED | {"mentions": {"scope": "account"}})
        self.assertNotIn("mentions", self.init().discover()[0])

    def test_advisories_flag_lists_the_repositories_and_refuses_a_bad_one(self):
        cfg, _ = self.init(advisories=["acme/server", "acme/app", "acme/server"]).discover()
        self.assertEqual(cfg, EXPECTED | {"advisories": {"repos": ["acme/app", "acme/server"]}})
        self.assertNotIn("advisories", self.init().discover()[0])
        with self.assertRaises(init_home.Refuse):
            self.init(advisories=["server"])

    def test_upgrade_removes_the_listener_service_and_drops_listen_without_force(self):
        listener = init_home.unit_name(self.home) + "-listen"
        self.init().main()
        path = os.path.join(self.dir, "config.json")
        old = {k: v for k, v in EXPECTED.items() if k != "agent"}  # written by an earlier version: no "agent" either
        json.dump(old | {"listen": {"interval": 30}}, open(path, "w"))
        self.system.units[listener] = "[Service]\nExecStart=... sync.py listen ..."
        plan = self.init(dry=True).main()
        self.assertIn(f"remove {listener}.service", plan)
        self.assertIn(f'drop the retired "listen" from {path} (notifications replaced the event listener)', plan)
        self.assertIn(f"record the agent {ACTING} in {path}", plan)
        self.assertIn(listener, self.system.units)  # a dry run removes nothing
        done = self.init().main()
        self.assertIn(f"remove {listener}.service", done)
        self.assertNotIn(listener, self.system.units)
        self.assertEqual(json.load(open(path)), EXPECTED)
        self.assertEqual(self.init().main(), [])  # re-run: nothing to change
        json.dump(EXPECTED | {"listen": {}, "pings": {}}, open(path, "w"))  # differs in more than "listen": still refused
        with self.assertRaises(init_home.Refuse):
            self.init().main()

    def test_the_listen_flag_is_accepted_and_does_nothing(self):
        out = io.StringIO()
        with redirect_stderr(out):
            code = init_home.cli([URL, "--login", "firstmate", "--agent", str(ACTING), "--home", self.home, "--repo-map", "server=my-server",
                                  "--listen", "--dry-run"], runner=self.stub, system=self.system,
                                 sync_dir=os.path.join(self.tmp, "repo"), out=self.printed.append)
        self.assertEqual(code, 0)
        self.assertIn("--listen is no longer needed", out.getvalue())
        self.assertNotIn("listen", json.loads(self.printed[0]))

    def test_no_releases_reads_no_origins(self):
        self.stub.origins = {"Engine": "https://github.com/acme/engine.git"}
        cfg, _ = self.init(cards=False, repo_map=[], releases=False).discover()
        self.assertNotIn("releases", cfg)

    def test_behavior_flags_refuse_a_missing_or_doubled_dock_tool(self):
        self.stub.project["dock"] = [d for d in self.stub.project["dock"] if d["name"] not in ("chat", "message_board")]
        with self.assertRaises(init_home.Refuse) as e:
            self.init(todos=True, reports=True, every_line=True, checkins="UTC").discover()
        for flag in ("--todos needs one enabled todoset", "--reports needs one enabled message_board",
                     "--every-line needs an enabled chat", "--checkins needs one enabled questionnaire"):
            self.assertIn(flag, str(e.exception))
        with self.assertRaises(init_home.Refuse):
            self.init(checkins="Mars/Olympus")

    def test_dry_run_writes_nothing(self):
        plan = self.init(dry=True).main()
        self.assertFalse(os.path.exists(self.dir))
        self.assertEqual(self.system.units, {})
        self.assertEqual(self.system.checks, {})
        self.assertTrue(all(dry for _, _, dry in self.system.calls))
        self.assertEqual(self.stub.writes(), [])
        self.assertEqual(json.loads(self.printed[0]), EXPECTED)
        self.assertIn(f"write {os.path.join(self.dir, 'config.json')}", plan)


class SystemTest(unittest.TestCase):
    """The real seam against a temp unit dir and a stubbed systemctl/fm-check-register.sh."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, "state"))
        self.cmds, self.state = [], {"enabled": "disabled", "active": "inactive"}
        self.sys = init_home.System(runner=self.fake, unit_dir=os.path.join(self.tmp, "units"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake(self, cmd, **kw):
        self.cmds.append(cmd)
        out = ""
        if cmd[:3] == ["systemctl", "--user", "is-enabled"]:
            out = self.state["enabled"]
        elif cmd[:3] == ["systemctl", "--user", "is-active"]:
            out = self.state["active"]
        elif cmd[:3] == ["systemctl", "--user", "enable"]:
            self.state = {"enabled": "enabled", "active": "active"}
        elif cmd[0].endswith("fm-check-register.sh"):
            path = os.path.join(self.home, "state", f"{cmd[1]}.check.sh")
            with open(os.path.join(self.home, "state", f"{cmd[1]}.check-trust"), "w") as f:
                f.write("fm-custom-check-v1\n" + hashlib.sha256(open(path, "rb").read()).hexdigest() + "\n")
            assert kw["env"]["FM_HOME"] == self.home
        return SimpleNamespace(stdout=out + "\n", stderr="", returncode=0)

    def test_install_then_nothing(self):
        self.assertTrue(self.sys.install_timer("u", "S", "T", dry=True))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "units")))
        changed = self.sys.install_timer("u", "S", "T", dry=False)
        self.assertIn("systemctl --user enable --now u.timer", changed)
        self.assertEqual(self.sys.install_timer("u", "S", "T", dry=False), [])

    def test_an_old_service_is_stopped_disabled_and_removed(self):
        self.assertEqual(self.sys.remove_service("u-listen", dry=False), [])
        os.makedirs(os.path.join(self.tmp, "units"))
        path = os.path.join(self.tmp, "units", "u-listen.service")
        open(path, "w").write("S")
        self.assertEqual(self.sys.remove_service("u-listen", dry=True),
                         ["systemctl --user disable --now u-listen.service", f"remove {path}", "systemctl --user daemon-reload"])
        self.assertTrue(os.path.exists(path))
        self.sys.remove_service("u-listen", dry=False)
        self.assertFalse(os.path.exists(path))
        self.assertIn(["systemctl", "--user", "disable", "--now", "u-listen.service"], self.cmds)
        self.assertIn(["systemctl", "--user", "daemon-reload"], self.cmds)

    def test_check_registered_then_nothing(self):
        self.assertTrue(self.sys.register_check(self.home, "basecamp-sync", init_home.CHECK, dry=True))
        self.assertEqual(os.listdir(os.path.join(self.home, "state")), [])
        self.sys.register_check(self.home, "basecamp-sync", init_home.CHECK, dry=False)
        path = os.path.join(self.home, "state", "basecamp-sync.check.sh")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)
        self.assertEqual(self.sys.register_check(self.home, "basecamp-sync", init_home.CHECK, dry=False), [])


if __name__ == "__main__":
    unittest.main()
