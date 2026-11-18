"""Tests for the login guard: a timer run, backup, timesheet post or watch signed in as anyone but the agent ("agent" in
the config), or with no "agent", reads only /my/profile.json, writes no state and logs a FAILED line; init records the
agent and refuses a re-run signed in as someone else.

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, json, os, sys, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, line  # noqa: E402
from test_tools import TodoStub, ToolBase  # noqa: E402
import test_init  # noqa: E402
import init_home, sync  # noqa: E402

OTHER = 50519852  # an operator, signed in by mistake


class GuardStub(TodoStub):
    """Adds the acting person's name to /my/profile.json, a failing profile read on demand, and a sign-in that changes to
    `then` once `reads` more profile reads are done."""

    def __init__(self):
        super().__init__()
        self.profile_fails, self.then, self.reads = False, None, 1

    def __call__(self, cmd, **kw):
        out = super().__call__(cmd, **kw)
        if self.calls and self.calls[-1][:3] == ["api", "get", "/my/profile.json"]:
            if self.profile_fails:
                return type(out)(stdout=json.dumps({"ok": False, "error": "Not authenticated"}), stderr="", returncode=1)
            data = json.loads(out.stdout)
            data["data"]["name"] = {ACTING: "Gilfoyle AI", OTHER: "Jay Park"}.get(self.me)
            self.reads -= 1
            if self.then is not None and self.reads == 0:
                self.me, self.then = self.then, None
            return type(out)(stdout=json.dumps(data), stderr="", returncode=0)
        return out


class Base(ToolBase):
    def setUp(self):
        super().setUp()
        self.stub = GuardStub()
        self.cfg(cards=False, chats=[77])
        self.stub.lines = {"77": [line(1)]}

    def run_timer(self, *extra):
        with redirect_stdout(io.StringIO()):
            return sync.cli([*extra, "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")],
                            runner=self.stub)

    def snapshot(self):
        """Every file beside the config but sync.log, with its content, and the run registry's files."""
        out = {}
        for root in (self.cfgdir, os.environ["BASECAMP_MATE_RUNS"]):
            for d, _, names in os.walk(root):
                for n in names:
                    if n != "sync.log":
                        out[os.path.join(d, n)] = open(os.path.join(d, n), "rb").read()
        return out

    def log(self):
        p = os.path.join(self.cfgdir, "sync.log")
        return open(p).read() if os.path.exists(p) else ""

    def failed(self):
        return [ln for ln in self.log().splitlines() if "FAILED" in ln]


class TimerRun(Base):
    def test_a_matching_login_runs_as_before(self):
        self.assertEqual(self.run_timer(), 0)
        self.assertEqual(self.failed(), [])
        self.assertIn("notifications: started", self.log())
        self.assertTrue(os.path.exists(os.path.join(self.cfgdir, "timer.json")))
        self.assertIn(["api", "get", "/my/readings.json"], self.stub.calls)

    def test_another_person_signed_in_reads_and_writes_nothing(self):
        self.assertEqual(self.run_timer(), 0)  # an established home: cursors and state exist
        before = self.snapshot()
        self.stub.calls.clear()
        self.stub.me = OTHER
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(self.stub.calls, [["api", "get", "/my/profile.json"]])
        self.assertEqual(self.snapshot(), before)
        [failed] = self.failed()
        self.assertIn(f"wrong login: the sign-in agent is Jay Park ({OTHER}), not the agent ({ACTING})", failed)
        self.assertIn("basecamp auth login -P agent", failed)

    def test_a_new_home_signed_in_as_someone_else_creates_no_state(self):
        self.stub.me = OTHER
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json", "sync.log"])
        self.assertFalse(os.path.exists(os.environ["BASECAMP_MATE_RUNS"]))

    def test_a_sign_in_that_changes_while_the_run_waits_for_the_lock_writes_nothing(self):
        self.stub.then = OTHER
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(self.stub.calls, [["api", "get", "/my/profile.json"]] * 2)
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json", "sync.lock", "sync.log"])
        self.assertFalse(os.path.exists(os.environ["BASECAMP_MATE_RUNS"]))
        [failed] = self.failed()
        self.assertIn(f"wrong login: the sign-in agent is Jay Park ({OTHER})", failed)

    def test_a_sign_in_that_changes_while_the_run_waits_to_claim_writes_no_claim(self):
        self.stub.then, self.stub.reads = OTHER, 2
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(self.stub.calls, [["api", "get", "/my/profile.json"]] * 3)
        self.assertEqual(sync.runs.Registry().claims(), [])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "timer.json")))
        [failed] = self.failed()
        self.assertIn(f"wrong login: the sign-in agent is Jay Park ({OTHER})", failed)

    def test_each_refused_run_logs_its_line(self):
        self.stub.me = OTHER
        for _ in range(2):
            self.run_timer()
        self.assertEqual(len(self.failed()), 2)

    def test_an_unreadable_profile_skips_the_run(self):
        self.stub.profile_fails = True
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json", "sync.log"])
        [failed] = self.failed()
        self.assertIn("login check: could not read who the sign-in agent is", failed)

    def test_a_dry_run_with_an_unreadable_profile_prints_and_logs_nothing(self):
        self.stub.profile_fails = True
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"), "--dry-run"],
                            runner=self.stub)
        self.assertEqual(code, 1)
        self.assertIn("FAILED login check: could not read who the sign-in agent is", out.getvalue())
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json"])

    def test_a_dry_run_prints_and_logs_nothing(self):
        self.stub.me = OTHER
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"), "--dry-run"],
                            runner=self.stub)
        self.assertEqual(code, 1)
        self.assertIn("FAILED wrong login", out.getvalue())
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json"])


class NoAgent(Base):
    """With a profile but no "agent" in the config, nothing runs, whoever is signed in, and nothing is learned."""

    def setUp(self):
        super().setUp()
        self.cfg(drop=("agent",))

    def test_nothing_runs_and_the_line_says_what_to_configure(self):
        with open(os.path.join(self.cfgdir, "relay.jsonl"), "w") as f:
            f.write(json.dumps({"event": "relayed", "agent": {"home": self.home, "profile": "agent", "id": ACTING}}) + "\n")
        before = self.snapshot()
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(self.stub.calls, [])
        self.assertEqual(self.snapshot(), before)
        [failed] = self.failed()
        self.assertIn('login check: the config has no "agent", so nothing ran; add "agent"', failed)
        self.assertNotIn("agent", json.load(open(os.path.join(self.cfgdir, "config.json"))))

    def test_without_a_profile_too(self):
        self.cfg(drop=("profile",))
        self.assertEqual(self.run_timer(), 1)
        self.assertIn('the config has no "agent"', self.failed()[0])


class DefaultLogin(Base):
    """Without a profile, the CLI's default login is checked the same way."""

    def setUp(self):
        super().setUp()
        self.cfg(drop=("profile",))

    def test_someone_else_is_refused_and_the_agent_runs(self):
        self.stub.me = OTHER
        self.assertEqual(self.run_timer(), 1)
        self.assertIn(f"wrong login: the default sign-in is Jay Park ({OTHER})", self.failed()[0])
        self.stub.me = ACTING
        self.assertEqual(self.run_timer(), 0)


class FeedCommands(Base):
    """backup, timesheet post and watch make the same check as the timer run."""

    def setUp(self):
        super().setUp()
        self.cfg(backup={"project": 9, "vault": 10}, timesheet={})
        open(os.path.join(self.cfgdir, "pending-comments.jsonl"), "w").write('{"kind": "comment"}\n')
        self.stub.me = OTHER

    def test_each_reads_only_the_profile_and_writes_nothing(self):
        for cmd in (["backup"], ["timesheet", "post"], ["watch", "--chat", "https://3.basecamp.com/1/buckets/9/chats/5"]):
            self.stub.calls.clear()
            before = self.snapshot()
            self.assertEqual(self.run_timer(*cmd), 1, cmd)
            self.assertEqual(self.stub.calls, [["api", "get", "/my/profile.json"]], cmd)
            self.assertEqual(self.snapshot(), before, cmd)
        self.assertEqual(len(self.failed()), 3)

    def test_the_agent_runs_them(self):
        self.stub.me = ACTING
        self.assertEqual(self.run_timer("timesheet", "post"), 0)
        self.assertEqual(self.failed(), [])


class Config(Base):
    def test_agent_must_be_a_person_id(self):
        for bad in ("gilfoyle", str(ACTING), True, [1]):
            self.cfg(agent=bad)
            with self.assertRaises(ValueError):
                self.sync()
        self.cfg(agent=ACTING)
        self.assertEqual(self.sync().agent_id, ACTING)


class Init(unittest.TestCase):
    """init records the agent's person id, and a re-run signed in as anyone else is refused, writing nothing."""

    setUp, tearDown = test_init.InitTest.setUp, test_init.InitTest.tearDown
    init, files = test_init.InitTest.init, test_init.InitTest.files

    def signed_in_as(self, pid):
        inner = self.stub

        def runner(cmd, **kw):
            out = inner(cmd, **kw)
            if cmd[5:8] == ["api", "get", "/my/profile.json"]:
                return type(out)(stdout=json.dumps({"ok": True, "data": {"id": pid, "name": "Jay Park"}}), stderr="",
                                 returncode=0)
            return out
        return runner

    def test_records_the_agent_and_refuses_another_person_on_a_re_run(self):
        self.init().main()
        cfg = os.path.join(self.dir, "config.json")
        self.assertEqual(json.load(open(cfg))["agent"], test_init.ACTING)
        before = self.files()
        self.stub.people.append({"id": OTHER, "name": "Jay Park", "owner": False})
        self.stub = self.signed_in_as(OTHER)
        with self.assertRaises(init_home.Refuse) as e:
            self.init().main()
        self.assertIn(f"signs in as Jay Park ({OTHER}), but the agent is {test_init.ACTING}", str(e.exception))
        self.assertEqual(self.files(), before)
        with self.assertRaises(init_home.Refuse):
            self.init(agent=None).main()
        self.assertEqual(self.files(), before)

    def older_config(self):
        """A config written before init recorded "agent"."""
        self.init().main()
        cfg = os.path.join(self.dir, "config.json")
        c = json.load(open(cfg))
        c.pop("agent")
        json.dump(c, open(cfg, "w"))
        return cfg

    def test_a_new_home_without_agent_is_refused_before_any_basecamp_call(self):
        with self.assertRaises(init_home.Refuse) as e:
            self.init(agent=None).main()
        self.assertIn("a new home needs --agent <the agent's Basecamp person id>", str(e.exception))
        self.assertEqual(self.stub.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "config.json")))
        self.assertEqual(self.system.units, {})

    def test_the_agent_comes_only_from_agent_or_the_config_never_from_the_login(self):
        cfg = self.older_config()
        self.stub.people.append({"id": OTHER, "name": "Jay Park", "owner": False})
        self.stub = self.signed_in_as(OTHER)
        self.init(agent=None).main()
        self.assertNotIn("agent", json.load(open(cfg)))
        self.assertTrue(any('no "agent"' in p for p in self.printed))
        with self.assertRaises(init_home.Refuse):
            self.init().main()
        self.assertNotIn("agent", json.load(open(cfg)))

    def test_an_older_config_gains_the_agent_given_without_force(self):
        self.older_config()
        self.init().main()
        self.assertEqual(json.load(open(os.path.join(self.dir, "config.json")))["agent"], test_init.ACTING)


if __name__ == "__main__":
    unittest.main()
