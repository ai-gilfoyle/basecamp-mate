"""Tests for the duplicate-run guard (runs.py): a timer run claims what its config watches and refuses to run beside
another home's live run that watches the same thing with the same login; `sync.py status` lists the runs.

The basecamp CLI is stubbed and the registry lives in a temporary directory; nothing touches the network.
"""
import io, json, os, shutil, subprocess, sys, time, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, Base  # noqa: E402
import doctor  # noqa: E402
import runs  # noqa: E402
import sync  # noqa: E402
import test_setup  # noqa: E402


class GuardBase(Base):
    def setUp(self):
        super().setUp()
        self.config = os.path.join(self.cfgdir, "config.json")
        self.cfg(cards=False)
        self.other_home = os.path.join(self.tmp, "other")
        os.makedirs(self.other_home)
        self.other = os.path.join(self.other_home, "config.json")
        with open(self.other, "w") as f:
            json.dump(self.load(), f)

    def load(self):
        with open(self.config) as f:
            return json.load(f)

    def cfg(self, **kw):
        cfg = dict(self.load(), **kw)
        with open(self.config, "w") as f:
            json.dump(cfg, f)

    def claim_other(self, cfg=None, last_run=None, pid=None, person=None, start=None):
        """Write the other home's claim as its own run would have."""
        reg = runs.Registry()
        os.makedirs(reg.dir, exist_ok=True)
        claim = {"config": os.path.realpath(self.other), "home": self.other_home, "profile": (cfg or {}).get("profile", ""),
                 "person": person, "watches": runs.watches(cfg or self.load()), "pid": pid, "process_start": start,
                 "last_run": time.time() if last_run is None else last_run}
        with open(reg.file(self.other), "w") as f:
            json.dump(claim, f)

    def timer(self, *extra):
        with redirect_stdout(io.StringIO()):
            return sync.cli(["--home", self.home, "--config", self.config, *extra], runner=self.stub)

    def log(self):
        p = os.path.join(self.cfgdir, "sync.log")
        return open(p).read() if os.path.exists(p) else ""


class Guard(GuardBase):
    def test_a_run_claims_its_watches(self):
        self.assertEqual(self.timer(), 0)
        [claim] = runs.Registry().claims()
        self.assertEqual((claim["config"], claim["watches"], claim["pid"]),
                         (os.path.realpath(self.config), ["project 1111111/22222222"], os.getpid()))

    def test_another_homes_live_run_on_the_same_project_and_login_refuses_and_names_it(self):
        self.claim_other()
        self.assertEqual(self.timer(), 1)
        self.assertIn("FAILED duplicate run", self.log())
        self.assertIn(f"home {self.other_home} (config {os.path.realpath(self.other)})", self.log())
        self.assertIn("also watching project 1111111/22222222", self.log())
        self.assertEqual([c for c in self.stub.calls if c != ["api", "get", "/my/profile.json"]], [])  # read nothing else
        self.assertEqual([c["config"] for c in runs.Registry().claims()], [os.path.realpath(self.other)])

    def test_the_refusal_is_logged_once_then_hourly(self):
        self.claim_other()
        self.timer()
        self.timer()
        self.assertEqual(self.log().count("FAILED duplicate run"), 1)
        timer = os.path.join(self.cfgdir, "timer.json")
        with open(timer) as f:
            state = json.load(f)
        state["duplicate"]["noted"] -= sync.DUPLICATE_NOTE
        with open(timer, "w") as f:
            json.dump(state, f)
        self.timer()
        self.assertEqual(self.log().count("FAILED duplicate run"), 2)

    def test_the_same_config_twice_is_no_duplicate(self):
        self.assertEqual(self.timer(), 0)
        self.assertEqual(self.timer(), 0)
        self.assertNotIn("duplicate", self.log())

    def test_another_project_or_another_login_runs(self):
        self.claim_other(cfg=dict(self.load(), project="999"))
        self.assertEqual(self.timer(), 0)
        self.claim_other(cfg=dict(self.load(), profile="someone-else"))
        self.assertEqual(self.timer(), 0)
        self.assertNotIn("duplicate", self.log())

    def test_the_same_person_under_another_profile_name_is_a_duplicate(self):
        self.cfg(profile="agent")
        self.claim_other(cfg=dict(self.load(), profile="agent-2"), person=ACTING)
        self.assertEqual(self.timer(), 1)

    def test_pings_and_account_wide_requests_overlap_across_projects(self):
        self.cfg(pings={})
        self.claim_other(cfg=dict(self.load(), project="999", pings={}))
        self.assertEqual(self.timer(), 1)
        self.assertIn("also watching pings 1111111", self.log())

    def test_a_stale_claim_or_a_removed_config_stops_blocking(self):
        self.claim_other(last_run=time.time() - runs.STALE - 1)
        self.assertEqual(self.timer(), 0)
        self.claim_other()
        os.remove(self.other)
        self.assertEqual(self.timer(), 0)
        self.assertEqual([c["config"] for c in runs.Registry().claims()], [os.path.realpath(self.config)])  # pruned

    def test_a_claim_whose_run_is_still_going_blocks_however_old(self):
        p = subprocess.Popen(["sleep", "30"])
        try:
            self.claim_other(last_run=0, pid=p.pid, start=runs.process_start(p.pid))
            self.assertEqual(self.timer(), 1)
            self.assertIn(f"running now as pid {p.pid}", self.log())
            self.claim_other(last_run=0, pid=p.pid, start="recycled")  # the pid now belongs to another process
            self.assertEqual(self.timer(), 0)
        finally:
            p.kill()
            p.wait()

    def test_the_refusal_clears_when_the_other_run_goes(self):
        self.claim_other()
        self.timer()
        self.claim_other(last_run=time.time() - runs.STALE - 1)
        self.assertEqual(self.timer(), 0)
        self.assertIn("duplicate run cleared", self.log())

    def test_the_override_runs_both_and_says_so_once(self):
        self.claim_other()
        self.assertEqual(self.timer("--allow-duplicate"), 0)
        self.cfg(allow_duplicate=True)
        self.assertEqual(self.timer(), 0)
        self.assertEqual(self.log().count("running beside another home's live run (allow_duplicate)"), 1)
        self.assertNotIn("FAILED", self.log())

    def test_a_dry_run_checks_but_claims_nothing(self):
        self.assertEqual(self.timer("--dry-run"), 0)
        self.assertEqual(runs.Registry().claims(), [])
        self.claim_other()
        self.assertEqual(self.timer("--dry-run"), 1)

    def test_commands_are_never_refused(self):
        self.claim_other()
        with redirect_stdout(io.StringIO()):
            code = sync.cli(["behaviors", "--home", self.home, "--config", self.config], runner=self.stub)
        self.assertEqual(code, 0)


class Status(GuardBase):
    def status(self, processes=()):
        out = []
        runs.status(processes=lambda: list(processes), out=out.append)
        return "\n".join(out)

    def test_lists_live_and_stale_claims_their_watches_and_overlaps(self):
        self.cfg(allow_duplicate=True)
        self.timer()
        self.claim_other()
        text = self.status()
        self.assertIn(f"live   home {self.home} (config {os.path.realpath(self.config)})", text)
        self.assertIn("watches: project 1111111/22222222", text)
        self.assertIn(f"overlaps with: {self.other_home}", text)
        self.claim_other(last_run=time.time() - runs.STALE - 1)
        self.assertIn(f"stale  home {self.other_home}", self.status())

    def test_names_a_running_sync_with_no_claim(self):
        self.assertIn("No sync runs recorded", self.status())
        self.assertIn("unrecorded  pid 4242 runs sync.py with config /x/config.json", self.status([(4242, "/x/config.json")]))

    def test_the_cli(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(sync.cli(["status"]), 0)
        self.assertIn("No sync runs recorded", out.getvalue())


class Doctor(test_setup.Base):
    doctor, healthy = test_setup.DoctorTest.doctor, test_setup.DoctorTest.healthy

    def two_homes(self, **kw):
        self.healthy()
        home2 = os.path.join(self.tmp, "home2")
        shutil.copytree(self.home, home2)
        config2 = os.path.join(home2, "data", "basecamp-sync", "config.json")
        for c in (self.config, config2):
            with open(c) as f:
                cfg = json.load(f)
            with open(c, "w") as f:
                json.dump(dict(cfg, **kw), f)
        d = doctor.Doctor(runner=self.stub, system=self.system, which=lambda n: "/usr/bin/" + n, out=self.printed.append)
        d.homes = lambda: [(self.home, self.config), (home2, config2)]
        return d.main()

    def test_two_homes_on_one_project_and_login_warn(self):
        self.assertEqual(self.two_homes(), 1)
        self.assertIn(f"2 homes watch Basecamp project {test_setup.PROJECT} with the sign-in 'firstmate'", self.text())
        self.assertIn('set "allow_duplicate": true', self.text())

    def test_two_homes_that_allow_it_pass(self):
        self.two_homes(allow_duplicate=True)  # the copied home has no timer of its own, so doctor still exits 1
        self.assertNotIn("homes watch Basecamp project", self.text())

    def test_a_refused_run_explains_itself(self):
        self.healthy()
        with open(os.path.join(os.path.dirname(self.config), "sync.log"), "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " FAILED duplicate run: another home's ...\n")
        self.assertEqual(self.doctor(), 1)
        self.assertIn("basecamp-mate status", self.text())


if __name__ == "__main__":
    unittest.main()
