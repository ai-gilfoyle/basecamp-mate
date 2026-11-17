"""Tests for the login guard: a timer run signed in as anyone but the agent ("agent" in the config, else the first agent
id in the relay ledger) reads only /my/profile.json, writes no state and logs one FAILED line naming both people; the
commands refuse to post; init records the agent and refuses a re-run signed in as someone else.

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, json, os, sys, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, line  # noqa: E402
from test_tools import TodoStub, ToolBase  # noqa: E402
import test_init  # noqa: E402
import doctor, init_home, sync  # noqa: E402

OTHER = 50519852  # an operator, signed in by mistake


class GuardStub(TodoStub):
    """Adds the acting person's name to /my/profile.json, and a failing profile read on demand."""

    def __init__(self):
        super().__init__()
        self.profile_fails = False

    def __call__(self, cmd, **kw):
        out = super().__call__(cmd, **kw)
        if self.calls and self.calls[-1][:3] == ["api", "get", "/my/profile.json"]:
            if self.profile_fails:
                return type(out)(stdout=json.dumps({"ok": False, "error": "Not authenticated"}), stderr="", returncode=1)
            data = json.loads(out.stdout)
            data["data"]["name"] = {ACTING: "Gilfoyle AI", OTHER: "Jay Park"}.get(self.me)
            return type(out)(stdout=json.dumps(data), stderr="", returncode=0)
        return out


class Base(ToolBase):
    def setUp(self):
        super().setUp()
        self.stub = GuardStub()
        self.cfg(cards=False, chats=[77], agent=ACTING)
        self.stub.lines = {"77": [line(1)]}

    def run_timer(self, *extra):
        with redirect_stdout(io.StringIO()):
            return sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"), *extra],
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

    def test_the_failed_line_is_logged_once_an_hour_while_it_lasts(self):
        self.stub.me = OTHER
        for _ in range(3):
            self.run_timer()
        self.assertEqual(len(self.failed()), 1)
        p = os.path.join(self.cfgdir, "sync.log")
        old = open(p).read().replace(self.log()[:4], "2000", 1)  # the line's year: long ago
        open(p, "w").write(old)
        self.run_timer()
        self.assertEqual(len(self.failed()), 2)

    def test_an_unreadable_profile_skips_the_run(self):
        self.stub.profile_fails = True
        self.assertEqual(self.run_timer(), 1)
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json", "sync.log"])
        [failed] = self.failed()
        self.assertIn("login check: could not read who the sign-in agent is", failed)

    def test_a_dry_run_prints_and_logs_nothing(self):
        self.stub.me = OTHER
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli(["--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"), "--dry-run"],
                            runner=self.stub)
        self.assertEqual(code, 1)
        self.assertIn("FAILED wrong login", out.getvalue())
        self.assertEqual(sorted(os.listdir(self.cfgdir)), ["config.json"])


class LedgerFallback(Base):
    """A home set up before init recorded "agent" takes the first agent id its relay ledger wrote for the profile."""

    def setUp(self):
        super().setUp()
        self.cfg(drop=("agent",))

    def ledger(self, *ids, profile="agent"):
        with open(os.path.join(self.cfgdir, "relay.jsonl"), "w") as f:
            f.write("not json\n")
            for i in ids:
                f.write(json.dumps({"event": "relayed", "agent": {"home": self.home, "profile": profile, "id": i}}) + "\n")

    def test_the_first_ledger_id_is_the_agent(self):
        self.ledger(ACTING, OTHER)
        self.stub.me = OTHER
        self.assertEqual(self.run_timer(), 1)
        self.assertIn(f"not the agent ({ACTING})", self.failed()[0])
        self.stub.me = ACTING
        self.assertEqual(self.run_timer(), 0)

    def test_another_profiles_ids_are_not_used(self):
        self.ledger(ACTING, profile="someone-else")
        self.stub.me = OTHER
        self.assertEqual(self.run_timer(), 0)

    def test_no_agent_and_no_ledger_runs_unchecked_as_before(self):
        self.stub.me = OTHER
        self.assertEqual(self.run_timer(), 0)
        self.assertEqual(self.failed(), [])


class Commands(Base):
    def test_post_message_refuses_to_post_as_someone_else(self):
        self.stub.me = OTHER
        body = os.path.join(self.tmp, "body.txt")
        open(body, "w").write("hi")
        with redirect_stdout(io.StringIO()):
            code = sync.cli(["post-message", "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"),
                             "--subject", "Report", "--body-file", body], runner=self.stub)
        self.assertEqual(code, 0)
        self.assertEqual([c for c in self.stub.calls if c[:2] != ["api", "get"]], [])
        self.assertIn(f"post-message: the sign-in agent is Jay Park ({OTHER}), not the agent ({ACTING}), not posting",
                      self.log())

    def test_todo_create_posts_as_the_agent(self):
        self.assertIsNotNone(self.create())


class Config(Base):
    def test_agent_must_be_a_person_id(self):
        for bad in ("gilfoyle", True, [1]):
            self.cfg(agent=bad)
            with self.assertRaises(ValueError):
                self.sync()
        self.cfg(agent=str(ACTING))
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
        self.assertIn(f"signs in as Jay Park ({OTHER}), but this home's agent is {test_init.ACTING}", str(e.exception))
        self.assertEqual(self.files(), before)

    def test_a_home_without_agent_takes_the_ledger_id(self):
        self.init().main()
        cfg = os.path.join(self.dir, "config.json")
        c = json.load(open(cfg))
        c.pop("agent")
        json.dump(c, open(cfg, "w"))
        with open(os.path.join(self.dir, "relay.jsonl"), "w") as f:
            f.write(json.dumps({"agent": {"profile": "firstmate", "id": test_init.ACTING}}) + "\n")
        self.stub.people.append({"id": OTHER, "name": "Jay Park", "owner": False})
        self.stub = self.signed_in_as(OTHER)
        with self.assertRaises(init_home.Refuse):
            self.init().main()


class Doctor(unittest.TestCase):
    def test_the_wrong_login_line_says_to_sign_in_as_the_agent(self):
        fix = doctor.Doctor.failure_fix("2026-11-11T21:52:26Z FAILED wrong login: the sign-in gilfoyle is Jay Park (1), "
                                        "not the agent (2), so nothing ran and no state changed; sign in as the agent")
        self.assertIn("not the agent's Basecamp person", fix)


if __name__ == "__main__":
    unittest.main()
