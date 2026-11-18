"""Tests for the login guard (login.py, `sync.py renew`): the `basecamp auth` CLI is stubbed and the credentials file
is a temporary one; nothing touches the network or the real ~/.config/basecamp."""
import json, os, shutil, stat, sys, tempfile, unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BASECAMP_MATE_RUNS", tempfile.mkdtemp(prefix="basecamp-mate-runs-"))
import login, sync  # noqa: E402

NOW = 1_800_000_000
FM = {"access_token": "a1", "refresh_token": "r1", "expires_at": NOW + 600, "oauth_type": "bc5"}
CALEB = {"access_token": "c1", "refresh_token": "cr1", "expires_at": NOW + 3000, "oauth_type": "bc5"}
OPERATOR = {"access_token": "o1", "refresh_token": "or1", "expires_at": NOW + 3000, "oauth_type": "bc5"}
AGENT_ID, CALEB_ID, OPERATOR_ID = 53286738, 1001, 50519852  # who each login signs in as


def load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


class Home:
    """The parts of Tools the guard uses: the config (with its "agent"), state files beside it, the log, and
    /my/profile.json, which reads back `me` (the person signed in; None: the read fails)."""

    def __init__(self, d, profile="firstmate", agent=AGENT_ID):
        self.dir, self.cfg, self.lines = d, {"profile": profile} if profile else {}, []
        self.agent_id, self.me, self.reads = agent, agent, 0

    def bc(self, *args, tries=3):
        assert args == ("api", "get", "/my/profile.json"), args
        self.reads += 1
        if self.me is None:
            raise RuntimeError("basecamp api get: Not authenticated")
        return {"id": self.me}

    def path(self, name):
        return os.path.join(self.dir, name)

    def load(self, name, default):
        p = self.path(name)
        return load(p, default)

    def save_json(self, name, value):
        with open(self.path(name), "w") as f:
            json.dump(value, f)

    def log(self, msg):
        self.lines.append(msg)


class Auth:
    """`basecamp auth status|refresh`; `on_refresh(store)` returns the file the refresh leaves (None: deleted)."""

    def __init__(self, creds, expires_in=600, on_refresh=None, ok=True, profile="firstmate", keyring=False):
        self.creds, self.expires_in, self.on_refresh, self.ok, self.calls = creds, expires_in, on_refresh, ok, []
        self.key, self.keyring = f"profile:{profile}", keyring  # keyring: the login is in the keyring, not the file

    def __call__(self, cmd, **kw):
        assert cmd[:2] == ["basecamp", "auth"], cmd
        self.calls.append(cmd[2])
        store = load(self.creds, {})
        held = self.keyring or self.key in store
        if cmd[2] == "status":
            if not held:
                out = {"ok": True, "data": {"authenticated": False}}
            else:
                at = datetime.fromtimestamp(NOW + self.expires_in, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                out = {"ok": True, "data": {"authenticated": True, "expires_at": at,
                                            "storage": "keyring" if self.keyring else "file"}}
        elif cmd[2] == "refresh" and self.keyring:
            out = {"ok": self.ok}
        elif cmd[2] == "refresh":
            if not held:
                out = {"ok": False, "error": "no stored credential"}
            else:
                new = self.on_refresh(store) if self.on_refresh else store
                with open(self.creds, "w") as f:
                    json.dump(new, f)
                out = {"ok": self.ok}
        else:
            raise AssertionError(cmd)
        return SimpleNamespace(stdout=json.dumps(out), stderr="", returncode=0 if out["ok"] else 1)


def rotated(store):
    return {**store, "profile:firstmate": {**FM, "access_token": "a2", "refresh_token": "r2"}}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="login-guard-")
        self.cred_dir = os.path.join(self.tmp, "basecamp")
        self.home_dir = os.path.join(self.tmp, "home")
        os.makedirs(self.cred_dir)
        os.makedirs(self.home_dir)
        self.creds = os.path.join(self.cred_dir, "credentials.json")
        self.store({"profile:caleb": CALEB, "profile:firstmate": FM})
        self.home = Home(self.home_dir)
        env = mock.patch.dict(os.environ, {"BASECAMP_NO_KEYRING": "1"})  # the homes' file store
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def store(self, data):
        with open(self.creds, "w") as f:
            json.dump(data, f)
        os.chmod(self.creds, 0o600)

    def stored(self):
        return load(self.creds)

    def guard(self, auth, up=True):
        return login.Login(self.home, runner=auth, reach=lambda: up, now=lambda: NOW, cred_dir=self.cred_dir)

    def caleb(self):
        """The renewal step of a second home, whose profile "caleb" signs in as its own agent."""
        return login.Login(Home(self.home_dir, profile="caleb", agent=CALEB_ID),
                           runner=Auth(self.creds, profile="caleb", expires_in=3000),
                           reach=lambda: True, now=lambda: NOW, cred_dir=self.cred_dir)

    def verify(self, *entries):
        """Mark (key, entry, agent) as copied from a login confirmed to sign in as that agent."""
        login.Login(self.home, cred_dir=self.cred_dir).write(
            os.path.join(self.cred_dir, login.VERIFIED),
            {k: {"fingerprint": login.fingerprint(v), "agent": a} for k, v, a in entries})

    def test_a_renewal_that_empties_the_file_is_restored_from_the_backup(self):
        auth = Auth(self.creds, on_refresh=lambda s: {})
        self.assertEqual(self.guard(auth).renew(), 0)
        # Only the agent's own login was ever copied, so only it comes back.
        self.assertEqual(self.stored(), {"profile:firstmate": FM})
        self.assertEqual(stat.S_IMODE(os.stat(self.creds).st_mode), 0o600)
        for name in (login.BACKUP, login.VERIFIED):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.cred_dir, name)).st_mode), 0o600)
        self.assertEqual(len(self.home.lines), 1, self.home.lines)
        self.assertIn("login: restored", self.home.lines[0])
        self.assertIn("put back profile:firstmate", self.home.lines[0])
        self.assertNotIn("r1", "".join(self.home.lines))  # no token in the log
        self.assertNotIn("r1", open(os.path.join(self.cred_dir, login.VERIFIED)).read())  # fingerprints only

    def test_an_operator_signed_in_to_the_profile_is_never_backed_up_or_restored(self):
        self.guard(Auth(self.creds, expires_in=3000)).renew()  # the agent's login, confirmed and copied
        self.store({"profile:caleb": CALEB, "profile:firstmate": OPERATOR})  # an operator signs the profile in as themself
        self.home.me = OPERATOR_ID
        self.guard(Auth(self.creds, expires_in=3000)).renew()
        backup = load(os.path.join(self.cred_dir, login.BACKUP))
        self.assertEqual(backup, {"profile:firstmate": FM})  # still the agent's
        self.guard(Auth(self.creds, on_refresh=lambda s: {})).renew()  # the login is lost later
        self.assertEqual(self.stored(), {"profile:firstmate": FM})  # put back as the agent, never as the operator
        self.assertNotIn("or1", open(os.path.join(self.cred_dir, login.BACKUP)).read())

    def test_a_confirmed_login_is_backed_up_without_reading_the_profile_again(self):
        for _ in range(3):
            self.guard(Auth(self.creds, expires_in=3000)).renew()
        self.assertEqual(self.home.reads, 1)
        self.guard(Auth(self.creds, on_refresh=rotated)).renew()  # new tokens: confirmed once more
        self.assertEqual(self.home.reads, 2)
        backup = load(os.path.join(self.cred_dir, login.BACKUP))
        self.assertEqual(backup["profile:firstmate"]["refresh_token"], "r2")

    def test_a_login_that_cannot_be_confirmed_is_not_backed_up(self):
        self.home.me = None  # the profile read fails
        self.guard(Auth(self.creds, expires_in=3000)).renew()
        self.home.me, self.home.agent_id = AGENT_ID, None  # no "agent" in the config
        g = self.guard(Auth(self.creds, expires_in=3000))
        g.renew()
        self.assertIsNone(g.agent)
        self.assertFalse(os.path.exists(os.path.join(self.cred_dir, login.BACKUP)))
        self.assertEqual(self.home.lines, [])

    def test_the_default_login_copies_and_marks_only_its_own_entry_never_a_profiles(self):
        self.caleb().renew()  # caleb's home copies and marks its own login
        marks = os.path.join(self.cred_dir, login.VERIFIED)
        caleb_mark = load(marks)["profile:caleb"]
        self.store({"profile:caleb": CALEB, "default": FM})
        home = Home(self.home_dir, profile=None)
        login.Login(home, runner=Auth(self.creds, profile="caleb", expires_in=3000), reach=lambda: True,
                    now=lambda: NOW, cred_dir=self.cred_dir).keep()
        self.assertEqual(home.reads, 1)
        self.assertEqual(load(marks)["profile:caleb"], caleb_mark)  # still caleb's agent, not this home's
        self.assertEqual(load(marks)["default"]["agent"], AGENT_ID)
        self.assertEqual(load(os.path.join(self.cred_dir, login.BACKUP)), {"profile:caleb": CALEB, "default": FM})

    def test_an_older_copy_never_confirmed_as_the_agent_is_not_restored(self):
        g = self.guard(Auth(self.creds, expires_in=3000))
        g.write(os.path.join(self.cred_dir, login.BACKUP), {"profile:firstmate": OPERATOR})  # taken before this check
        self.store({})
        self.assertEqual(g.renew(), 0)
        self.assertEqual(self.stored(), {})
        self.assertIn("no backup copy can be restored", self.home.lines[-1])
        self.verify(("profile:firstmate", OPERATOR, OPERATOR_ID))  # confirmed, but as another config's agent
        self.guard(Auth(self.creds, expires_in=3000)).renew()
        self.assertEqual(self.stored(), {})

    def test_a_network_down_run_leaves_the_file_untouched(self):
        before, mtime = open(self.creds, "rb").read(), os.stat(self.creds).st_mtime_ns
        auth = Auth(self.creds, on_refresh=lambda s: {})
        for _ in range(3):
            self.assertEqual(self.guard(auth, up=False).renew(), login.OFFLINE)
        self.assertEqual(auth.calls, [])  # no status, no refresh
        self.assertEqual(open(self.creds, "rb").read(), before)
        self.assertEqual(os.stat(self.creds).st_mtime_ns, mtime)
        self.assertFalse(os.path.exists(os.path.join(self.cred_dir, login.BACKUP)))
        self.assertEqual(len(self.home.lines), 1, self.home.lines)  # one line per outage
        self.assertNotIn("FAILED", self.home.lines[0])  # not a login failure
        self.assertEqual(self.guard(Auth(self.creds, expires_in=3000)).renew(), 0)
        self.assertIn("can be reached again", self.home.lines[-1])

    def test_only_the_lost_profile_is_put_back_beside_the_others_current_logins(self):
        caleb2 = {**CALEB, "refresh_token": "cr2"}  # another login rotated meanwhile: its newer entry stays
        auth = Auth(self.creds, on_refresh=lambda s: {"profile:caleb": caleb2})
        self.guard(auth).renew()
        self.assertEqual(self.stored(), {"profile:caleb": caleb2, "profile:firstmate": FM})
        self.assertIn("profile:firstmate was missing", self.home.lines[0])

    def test_a_restored_login_lost_again_is_not_restored_twice_and_fails_once(self):
        auth = Auth(self.creds, on_refresh=lambda s: {k: v for k, v in s.items() if k != "profile:firstmate"}, ok=False)
        self.guard(auth).renew()  # lost, restored
        self.assertEqual(self.stored()["profile:firstmate"], FM)
        for _ in range(3):  # refused again: not put back, so no refused renewal every run
            self.guard(auth).renew()
        self.assertNotIn("profile:firstmate", self.stored())
        self.assertEqual(auth.calls.count("refresh"), 2)
        failed = [ln for ln in self.home.lines if ln.startswith("FAILED")]
        self.assertEqual(len(failed), 1, self.home.lines)
        self.assertIn("basecamp auth login -P firstmate", failed[0])

    def test_a_good_renewal_refreshes_the_backup_so_it_never_holds_a_spent_token(self):
        auth = Auth(self.creds, on_refresh=rotated)
        self.assertEqual(self.guard(auth).renew(), 0)
        backup = load(os.path.join(self.cred_dir, login.BACKUP))
        self.assertEqual(backup["profile:firstmate"]["refresh_token"], "r2")
        self.assertEqual(self.home.lines, [])

    def test_a_token_far_from_expiry_is_not_renewed(self):
        auth = Auth(self.creds, expires_in=3000, on_refresh=lambda s: {})
        self.guard(auth).renew()
        self.assertEqual(auth.calls, ["status"])
        self.assertIn("profile:firstmate", self.stored())

    def test_a_failed_renewal_that_keeps_the_login_logs_once(self):
        auth = Auth(self.creds, ok=False)
        for _ in range(3):
            self.assertEqual(self.guard(auth).renew(), 0)
        self.assertEqual([ln[:20] for ln in self.home.lines], ["FAILED token refresh"])
        self.guard(Auth(self.creds, on_refresh=rotated)).renew()
        self.assertEqual(self.home.lines[-1], "login: renewal works again")

    def test_the_backup_keeps_another_profiles_entry_the_file_already_lost(self):
        self.caleb().renew()  # caleb's home copies its own login
        self.guard(Auth(self.creds, expires_in=3000)).renew()  # firstmate's home copies its own beside it
        self.store({"profile:firstmate": FM})  # caleb's login lost elsewhere; this home backs up again
        self.guard(Auth(self.creds, expires_in=3000)).renew()
        backup = load(os.path.join(self.cred_dir, login.BACKUP))
        self.assertEqual(backup, {"profile:caleb": CALEB, "profile:firstmate": FM})
        self.caleb().renew()  # caleb's home finds its login gone and gets it back from the shared backup
        self.assertEqual(self.stored(), {"profile:caleb": CALEB, "profile:firstmate": FM})

    def test_restore_marks_are_read_and_written_under_the_cli_locks_and_kept_per_profile(self):
        self.store({"profile:caleb": CALEB})
        login.Login(self.home, cred_dir=self.cred_dir).write(os.path.join(self.cred_dir, login.BACKUP),
                                                             {"profile:caleb": CALEB, "profile:firstmate": FM})
        self.verify(("profile:caleb", CALEB, CALEB_ID), ("profile:firstmate", FM, AGENT_ID))
        held, outside = [], []
        real_lock, real_read = login.Login.cli_lock, login.Login.read

        def lock(me, name, shared=False):
            cm = real_lock(me, name, shared)

            class Held:
                def __enter__(self):
                    cm.__enter__()
                    held.append(name)

                def __exit__(self, *a):
                    held.remove(name)
                    return cm.__exit__(*a)
            return Held()

        def read(me, path):
            if path.endswith(login.RESTORED) and "store.lock" not in held:
                outside.append(path)
            return real_read(me, path)

        with mock.patch.object(login.Login, "cli_lock", lock), mock.patch.object(login.Login, "read", read):
            self.guard(Auth(self.creds, expires_in=3000)).renew()  # firstmate's login lost: put back, its mark kept
            self.store({"profile:firstmate": FM})
            self.caleb().renew()
        self.assertEqual(outside, [])
        marks = load(os.path.join(self.cred_dir, login.RESTORED))
        self.assertEqual(set(marks), {"profile:firstmate", "profile:caleb"})
        self.assertEqual(self.stored(), {"profile:caleb": CALEB, "profile:firstmate": FM})

    def test_an_emptied_file_does_not_bring_back_another_profile_already_restored_once(self):
        backup = {"profile:caleb": CALEB, "profile:firstmate": FM}
        g = self.guard(Auth(self.creds, expires_in=3000))
        g.write(os.path.join(self.cred_dir, login.BACKUP), backup)
        g.write(os.path.join(self.cred_dir, login.RESTORED), {"profile:caleb": login.fingerprint(CALEB)})
        self.verify(("profile:caleb", CALEB, CALEB_ID), ("profile:firstmate", FM, AGENT_ID))
        self.store({})
        self.guard(Auth(self.creds, expires_in=3000)).renew()
        self.assertEqual(self.stored(), {"profile:firstmate": FM})  # caleb's refused login stays out
        self.assertIn("not profile:caleb: restored once already", self.home.lines[0])

    def test_a_lost_file_login_is_restored_without_the_env_var(self):
        self.guard(Auth(self.creds, expires_in=3000)).renew()  # backs up from the file store
        self.store({"profile:caleb": CALEB})  # lost between runs; its status now names no storage
        with mock.patch.dict(os.environ, {"BASECAMP_NO_KEYRING": ""}):
            self.guard(Auth(self.creds, expires_in=3000)).renew()
        self.assertEqual(self.stored(), {"profile:caleb": CALEB, "profile:firstmate": FM})
        self.assertIn("login: restored", self.home.lines[-1])

    def test_a_keyring_login_is_not_guarded_and_logs_no_false_failure(self):
        self.store({"profile:caleb": CALEB})  # the file does not hold firstmate: its login is in the keyring
        with mock.patch.dict(os.environ, {"BASECAMP_NO_KEYRING": ""}):
            auth = Auth(self.creds, keyring=True)
            self.assertEqual(self.guard(auth).renew(), 0)
            self.assertEqual(auth.calls, ["status", "refresh"])
            self.assertEqual(self.home.lines, [])
            self.assertFalse(os.path.exists(os.path.join(self.cred_dir, login.BACKUP)))
            self.assertEqual(self.stored(), {"profile:caleb": CALEB})
            self.guard(Auth(self.creds, keyring=True, ok=False)).renew()  # a real refusal is still reported
            self.assertEqual([ln[:20] for ln in self.home.lines], ["FAILED token refresh"])

    def test_a_login_moved_to_the_keyring_is_not_restored_from_its_old_file_backup(self):
        self.guard(Auth(self.creds, expires_in=3000)).renew()  # backed up while in the file store
        self.store({"profile:caleb": CALEB})  # moved to the keyring: the file no longer holds it
        with mock.patch.dict(os.environ, {"BASECAMP_NO_KEYRING": ""}):
            self.guard(Auth(self.creds, expires_in=3000, keyring=True)).renew()
        self.assertEqual(self.stored(), {"profile:caleb": CALEB})
        self.assertEqual(self.home.lines, [])

    def test_sync_renew_exits_offline_so_run_sh_skips_the_run(self):
        cfg = os.path.join(self.home_dir, "config.json")
        with open(cfg, "w") as f:
            json.dump({"account": 1, "project": 2, "captain": 3, "profile": "firstmate"}, f)
        calls = []
        with mock.patch.object(login, "reachable", lambda: False), mock.patch.object(login, "config_dir", lambda: self.cred_dir):
            rc = sync.cli(["renew", "--home", self.home_dir, "--config", cfg], runner=lambda *a, **k: calls.append(a))
        self.assertEqual(rc, login.OFFLINE)
        self.assertEqual(calls, [])
        with open(os.path.join(self.home_dir, "sync.log")) as f:
            self.assertIn("network: Basecamp cannot be reached", f.read())


if __name__ == "__main__":
    unittest.main()
