"""Tests for the backup behavior: the config and state files kept as one latest snapshot in a Docs & Files folder.

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, json, os, sys, unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, Base  # noqa: E402
import behaviors, doctor, init_home, sync  # noqa: E402

BACKUP = {"project": 49118254, "vault": 10427170269}


class FolderStub:
    """Answers the upload calls like the basecamp CLI, keeping the folder's uploads by name."""

    def __init__(self):
        self.calls, self.next_id = [], 900
        self.folder = {}  # name -> upload id
        self.fail, self.gone = set(), set()  # names whose upload call fails; upload ids that no longer exist

    def __call__(self, cmd, **kw):
        assert cmd[:2] == ["basecamp", "-a"] and cmd[-1] == "--json", cmd
        args = cmd[3:-3]
        args = args[2:] if args[:1] == ["-P"] else args
        self.calls.append((args, cmd[-2]))
        data = None
        if args[:3] == ["api", "get", "/my/profile.json"]:
            data = {"id": ACTING}
        elif args[:2] == ["uploads", "list"]:
            data = [{"id": i, "filename": n, "title": n} for n, i in self.folder.items()]
        elif args[:2] == ["uploads", "create"]:
            name = os.path.basename(args[2])
            if name in self.fail:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            self.next_id += 1
            self.folder[name] = self.next_id
            data = {"id": self.next_id}
        elif args[:2] == ["files", "replace"]:
            if int(args[2]) in self.gone:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "code": "not_found", "error": "Not found"}),
                                       stderr="", returncode=1)
            data = {"id": int(args[2])}
        else:
            raise AssertionError(f"unexpected call {args}")
        return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)

    def made(self, what):
        return [a for a, _ in self.calls if a[:2] == what.split()]


class Backup(Base):
    def setUp(self):
        super().setUp()
        self.stub = FolderStub()
        self.cfg(backup=BACKUP)
        self.side("map.json", {"t": 1})
        self.side("todos.json", {"k": {"todo": 2}})
        for name in ("sync.log", "sync.lock", "map.json.tmp", "config.json.bak-2026-10-19"):
            open(os.path.join(self.cfgdir, name), "w").write("x")
        open(os.path.join(self.cfgdir, "pending-comments.jsonl"), "w").write('{"kind": "comment"}\n')

    def cfg(self, **kw):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg.update(kw)
        json.dump(cfg, open(p, "w"))

    def backup(self, dry=False):
        self.sync(dry=dry).behaviors["backup"].run()

    def state(self):
        p = os.path.join(self.cfgdir, "backup.json")
        return json.load(open(p))["files"] if os.path.exists(p) else {}

    def log(self):
        return open(os.path.join(self.cfgdir, "sync.log")).read()

    def test_off_by_default_and_daily(self):
        self.cfg(backup=None)
        self.assertFalse(self.sync().behaviors["backup"].on)
        self.cfg(backup=BACKUP)
        b = self.sync().behaviors["backup"]
        self.assertEqual((b.on, b.runs, b.project, b.vault), (True, "daily", "49118254", "10427170269"))

    def test_malformed_config_refused(self):
        for bad in ({}, {"project": 1}, {"project": 1, "vault": "x"}, {"project": 1, "vault": 2, "extra": 3}, [1, 2]):
            self.cfg(backup=bad)
            with self.assertRaises(ValueError):
                self.sync()

    def test_first_backup_uploads_config_and_state_only_into_the_folder(self):
        self.backup()
        names = sorted(os.path.basename(a[2]) for a in self.stub.made("uploads create"))
        self.assertEqual(names, ["config.json", "map.json", "pending-comments.jsonl", "todos.json"])
        for args, project in self.stub.calls:
            self.assertEqual(project, "49118254")
        for a in self.stub.made("uploads create"):
            self.assertEqual(a[3:], ["--vault", "10427170269"])
        self.assertEqual(sorted(self.state()), names)
        self.assertEqual(len(self.stub.made("uploads list")), 1)

    def test_unchanged_files_make_no_call_and_a_changed_one_replaces_its_upload(self):
        self.backup()
        upload = self.state()["map.json"]["upload"]
        self.stub.calls.clear()
        self.backup()
        self.assertEqual(self.stub.calls, [])
        self.side("map.json", {"t": 2})
        self.backup()
        self.assertEqual(self.stub.made("files replace"), [["files", "replace", str(upload),
                                                             os.path.join(self.cfgdir, "map.json")]])
        self.assertEqual(self.stub.made("uploads create"), [])
        self.assertEqual(self.state()["map.json"]["upload"], upload)

    def test_an_existing_upload_of_the_name_is_replaced_not_duplicated(self):
        self.stub.folder = {"map.json": 77, "notes.txt": 78}
        self.backup()
        self.assertEqual([a[2] for a in self.stub.made("files replace")], ["77"])
        self.assertNotIn("map.json", [os.path.basename(a[2]) for a in self.stub.made("uploads create")])
        self.assertEqual(self.stub.folder["notes.txt"], 78)

    def test_an_upload_gone_from_the_folder_is_uploaded_anew(self):
        self.backup()
        self.stub.gone.add(self.state()["map.json"]["upload"])
        del self.stub.folder["map.json"]
        self.side("map.json", {"t": 3})
        self.backup()
        self.assertEqual([os.path.basename(a[2]) for a in self.stub.made("uploads create")][-1], "map.json")
        self.assertEqual(self.state()["map.json"]["upload"], self.stub.folder["map.json"])

    def test_a_failed_upload_is_logged_and_retried(self):
        self.stub.fail.add("todos.json")
        self.backup()
        self.assertIn("FAILED backup todos.json", self.log())
        self.assertNotIn("todos.json", self.state())
        self.stub.fail.clear()
        self.backup()
        self.assertIn("todos.json", self.state())

    def test_a_file_holding_a_credential_is_never_uploaded(self):
        self.cfg(api_token="abc")
        self.side("threads.json", {"x": "ghp_" + "a" * 36})
        self.backup()
        sent = [os.path.basename(a[2]) for a in self.stub.made("uploads create")]
        self.assertNotIn("config.json", sent)
        self.assertNotIn("threads.json", sent)
        self.assertIn("map.json", sent)
        self.assertIn("skipped config.json, it holds a credential (key 'api_token')", self.log())

    def test_state_keys_naming_secrets_are_not_credentials(self):
        self.assertIsNone(behaviors.holds_secret("todos.json", b'{"sfc-contact-email-secret": {"todo": 1}}'))
        self.assertIsNotNone(behaviors.holds_secret("config.json", b'{"backup": {"password": "x"}}'))

    def test_dry_run_uploads_nothing(self):
        self.backup(dry=True)
        self.assertEqual(self.stub.calls, [])
        self.assertEqual(self.state(), {})

    def test_backup_command(self):
        cfg = os.path.join(self.cfgdir, "config.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(sync.cli(["backup", "--home", self.home, "--config", cfg], runner=self.stub), 0)
        self.assertEqual(len(self.stub.made("uploads create")), 4)
        self.cfg(backup=None)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(sync.cli(["backup", "--home", self.home, "--config", cfg], runner=self.stub), 2)

    def test_doctor_checks_the_folder_and_the_config(self):
        cfg = os.path.join(self.cfgdir, "config.json")
        answers = {"ok": True, "data": {"title": "Firstmate backup"}}
        bc = SimpleNamespace(call=lambda *a, **kw: answers)
        printed = []
        d = doctor.Doctor(runner=self.stub, system=SimpleNamespace(), out=printed.append)
        d.backup(bc, json.load(open(cfg)), cfg)
        self.assertEqual(d.problems, 0)
        self.assertIn("  ✓ backup folder 'Firstmate backup'", printed)
        self.backup()
        d.backup(bc, json.load(open(cfg)), cfg)
        self.assertTrue(printed[-1].startswith("  ✓ config last backed up "))
        self.cfg(token="x")
        answers = {"ok": False, "error": "Not found"}
        d.backup(bc, json.load(open(cfg)), cfg)
        self.assertEqual(d.problems, 1)
        answers = {"ok": True, "data": {}}
        d.backup(bc, json.load(open(cfg)), cfg)
        self.assertEqual(d.problems, 2)
        self.assertIn("  ✗ The config holds a credential (key 'token'), so the backup skips it.", printed)

    def test_init_takes_a_folder_url(self):
        self.assertEqual(init_home.parse_folder("https://3.basecamp.com/5/buckets/49118254/vaults/10427170269"),
                         {"project": 49118254, "vault": 10427170269})
        with self.assertRaises(init_home.Refuse):
            init_home.parse_folder("https://3.basecamp.com/5/projects/49118254")


if __name__ == "__main__":
    unittest.main()
