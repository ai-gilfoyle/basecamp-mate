"""Tests for the timesheet behavior: the agent's active time per to-do or card per day, posted once to its timesheet.

The basecamp CLI is stubbed and the Claude Code transcripts are files in a temporary directory; nothing touches the
network.
"""
import io, json, os, sys, unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, Base, Stub  # noqa: E402
import behaviors, sync, tools  # noqa: E402

PROJECT, OTHER = "22222222", "66666666"
TODO_URL = f"https://3.basecamp.com/1111111/buckets/{OTHER}/todos/4242"
DAY = "2026-10-20"  # a finished day, in America/New_York (UTC-4 then)


class TimesheetStub(Stub):
    """Adds a recording read and the timesheet entry post to the basecamp stub."""

    def __init__(self):
        super().__init__()
        self.types = {}  # recording id -> its type ("Todo" when absent)
        self.refuse = None  # an error code the entry post fails with, e.g. "not_found"
        self.listed = []  # the timesheet entries `timesheet item` lists

    def __call__(self, cmd, **kw):
        args = cmd[3:-3]
        args = args[2:] if args[:1] == ["-P"] else args
        if args[:2] == ["api", "get"] and "/recordings/" in args[2]:
            self.calls.append(args)
            rid = int(args[2].split("/")[4].split(".")[0])
            data = {"id": rid, "type": self.types.get(rid, "Todo"), "title": "Fix the import",
                    "app_url": f"https://3.basecamp.com/1111111/buckets/{args[2].split('/')[2]}/todos/{rid}"}
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
        if args[:2] == ["timesheet", "item"]:
            self.calls.append(args)
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": self.listed}), stderr="", returncode=0)
        if args[:2] == ["api", "post"] and args[2].endswith("/timesheet/entries.json"):
            self.calls.append(args)
            if self.refuse:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "code": self.refuse, "error": "Resource not found",
                                                          "retryable": False}), stderr="", returncode=1)
            data = {"id": 7000 + len(self.calls), "app_url": "https://3.basecamp.com/1111111/entries/1"}
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def entries(self):
        return [(a[2], json.loads(a[4])) for a in self.calls
                if a[:2] == ["api", "post"] and a[2].endswith("/timesheet/entries.json")]


def ev(at, tool=False):
    """One transcript line at UTC time `at` ("HH:MM" on DAY, or a full ISO time); `tool`: an assistant tool call."""
    ts = at if "T" in at else f"{DAY}T{at}:00.000Z"
    if tool:
        return {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}
    return {"type": "user", "timestamp": ts, "message": {"content": "go"}}


class TimesheetBase(Base):
    def setUp(self):
        super().setUp()
        self.stub = TimesheetStub()
        self.cfg(profile="firstmate", timezone="America/New_York", timesheet={})
        self.work = os.path.join(self.tmp, "transcripts")
        os.makedirs(self.work)

    def cfg(self, **kw):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        cfg.update(kw)
        cfg = {k: v for k, v in cfg.items() if v is not None}
        json.dump(cfg, open(p, "w"))

    def transcript(self, name, *events, where=None):
        path = os.path.join(where or self.work, name + ".jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
            f.write("not json\n")
        return path

    def track(self, key="fix-import", on=True, frm="2026-10-01T00:00:00Z", to=None, path=None):
        state = self.state()
        item = {"sources": [{"path": path or self.work, "from": frm, **({"to": to} if to else {})}]}
        if on:
            item["on"] = {"bucket": int(OTHER), "recording": 4242, "type": "Todo", "title": "Fix the import", "url": TODO_URL}
        state.setdefault("items", {})[key] = item
        self.side("timesheet.json", state)

    def state(self):
        p = os.path.join(self.cfgdir, "timesheet.json")
        return json.load(open(p)) if os.path.exists(p) else {}

    def post(self, dry=False):
        self.sync(dry=dry).behaviors["timesheet"].run()

    def log(self):
        p = os.path.join(self.cfgdir, "sync.log")
        return open(p).read() if os.path.exists(p) else ""

    def records(self):
        p = os.path.join(self.cfgdir, "pending-comments.jsonl")
        return [json.loads(ln) for ln in open(p)] if os.path.exists(p) else []

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sync.cli([*args[:2], "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"),
                             *args[2:]], runner=self.stub)
        return code, out.getvalue() + err.getvalue()


class ActiveTime(TimesheetBase):
    def hours(self, **kw):
        s = self.sync()
        return s.behaviors["timesheet"].hours(self.state()["items"]["fix-import"], datetime.now(timezone.utc))

    def test_short_gaps_and_tool_waits_count_long_idle_gaps_do_not(self):
        # 14:00-14:06 in 3-minute steps (6 min), 14:20 after an idle 14 minutes (not counted), a tool call at 14:20
        # answered at 14:50 (30 min), then 15:55 after an hour's idle (not counted): 36 min, 0.5 h.
        self.transcript("s1", ev("14:00"), ev("14:03"), ev("14:06"), ev("14:20", tool=True), ev("14:50"), ev("15:55"))
        self.track()
        self.assertEqual(self.hours(), {DAY: 0.5})

    def test_a_tool_wait_over_the_limit_does_not_count(self):
        self.transcript("s1", ev("14:00", tool=True), ev("15:30"))  # 90 minutes on one tool call: over 60
        self.track()
        self.assertEqual(self.hours(), {})

    def test_transcripts_of_one_key_merge_and_a_symlinked_copy_counts_once(self):
        self.transcript("s1", *[ev(f"14:{m:02d}") for m in range(0, 61, 5)][:12])  # 14:00-14:55: 55 min
        self.transcript("s2", ev("14:30"), ev("14:34"), ev("14:58"), ev("15:02"), where=os.path.join(self.work, "sub"))
        self.transcript("s1", *[ev(f"14:{m:02d}") for m in range(0, 61, 5)][:12], where=os.path.join(self.tmp, "copy"))
        self.track()
        state = self.state()
        state["items"]["fix-import"]["sources"].append({"path": os.path.join(self.tmp, "copy"), "from": "2026-10-01T00:00:00Z"})
        self.side("timesheet.json", state)
        # 14:00-14:55 and 14:58-15:02 merged: 59 minutes, 1.0 h; the copy of s1 adds nothing.
        self.assertEqual(self.hours(), {DAY: 1.0})

    def test_only_time_between_start_and_stop_counts(self):
        self.transcript("s1", *[ev(f"14:{m:02d}") for m in range(0, 60, 3)])  # 14:00-14:57
        self.track(frm=f"{DAY}T14:15:00Z", to=f"{DAY}T14:45:00Z")
        self.assertEqual(self.hours(), {DAY: 0.5})

    def test_time_splits_at_us_eastern_midnight_whatever_the_config_zone(self):
        # 03:30-04:30 UTC on Oct 21 is 23:30-00:30 in New York: half an hour on each day, in a UTC home too.
        self.cfg(timezone="UTC")
        self.transcript("s1", *[ev(f"2026-10-21T0{3 + (m // 60)}:{m % 60:02d}:00Z") for m in range(30, 91, 4)])
        self.track()
        h = self.hours()
        self.assertEqual(h.get("2026-10-20"), 0.5)
        self.assertEqual(h.get("2026-10-21"), 0.5)

    def test_quarters_round_to_the_nearest(self):
        q = behaviors.quarters
        self.assertEqual([q(timedelta(minutes=m)) for m in (7, 8, 22, 23, 37, 38)], [0, 0.25, 0.25, 0.5, 0.5, 0.75])


class Posting(TimesheetBase):
    def setUp(self):
        super().setUp()
        self.transcript("s1", *[ev(f"14:{m:02d}") for m in range(0, 60, 3)], ev("15:00"))  # 14:00-15:00: 1 h

    def test_off_by_default_makes_no_call_and_writes_nothing(self):
        self.cfg(timesheet=None)
        self.track()
        before = self.state()
        s = self.sync()
        self.assertFalse(s.behaviors["timesheet"].on)
        s.main([])
        self.assertEqual(self.stub.entries(), [])
        self.assertFalse([c for c in self.stub.calls if "timesheet" in " ".join(map(str, c)) or "/recordings/" in str(c)])
        self.assertNotIn("timesheet", self.log())
        self.assertEqual(self.state(), before)
        code, out = self.cli("timesheet", "post")
        self.assertEqual(code, 2)
        self.assertIn('no "timesheet" in the config', out)
        self.assertEqual(self.cli("timesheet", "start", "--key", "k", "--workdir", self.tmp)[0], 2)
        self.assertEqual(self.stub.calls, [c for c in self.stub.calls if "/recordings/" not in str(c)])

    def test_dry_run_prints_what_it_would_post_and_posts_nothing(self):
        self.track()
        code, out = self.cli("timesheet", "post", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(f"dry timesheet: post 1 h on {DAY} for fix-import on {TODO_URL}", out)
        self.assertEqual(self.stub.entries(), [])
        self.assertNotIn("posted", self.state())
        self.post(dry=True)
        self.assertEqual(self.stub.entries(), [])

    def test_posts_one_entry_per_day_once(self):
        self.track()
        self.post()
        self.assertEqual(self.stub.entries(), [(f"/projects/{OTHER}/recordings/4242/timesheet/entries.json",
                                                {"date": DAY, "hours": "1", "description": "Active agent time: fix-import"})])
        self.assertEqual(self.state()["posted"]["fix-import"][DAY]["hours"], 1.0)
        self.post()
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)
        self.assertIn(f"timesheet: posted 1 h on {DAY} for fix-import", self.log())

    def test_a_timer_run_posts_it_and_today_waits_for_the_day_to_end(self):
        now = datetime.now(timezone.utc)
        self.transcript("today", *[ev((now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")) for m in range(40, 0, -2)])
        self.track()
        self.sync().main([])
        self.assertEqual([b["date"] for _, b in self.stub.entries()], [DAY])
        self.assertNotIn("settled", self.state()["items"]["fix-import"])

    def test_a_stopped_key_with_every_day_posted_is_not_read_again(self):
        self.track(to=f"{DAY}T23:00:00Z")
        self.post()
        self.assertTrue(self.state()["items"]["fix-import"]["settled"])
        self.stub.calls.clear()
        self.post()
        self.assertEqual(self.stub.calls, [])

    def test_unmapped_work_is_skipped_and_reported_once_then_posted_once_it_has_a_to_do(self):
        self.track(key="no-card", on=False)
        self.post()
        self.post()
        self.assertEqual(self.stub.entries(), [])
        self.assertEqual(self.log().count(f"timesheet: no-card has no to-do or card, so 1 h on {DAY} is not posted"), 1)
        recs = [r for r in self.records() if r["kind"] == "timesheet-unmapped"]
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["key"], recs[0]["days"]), ("no-card", {DAY: 1.0}))
        rid, body = behaviors.inbox_note(recs[0], "1111111", PROJECT)
        self.assertTrue(rid.startswith("basecamp-timesheet-unmapped-no-card-"))
        self.assertIn("sync.py timesheet start --key no-card --on", body)
        self.side("todos.json", {"no-card": {"todo": 5150, "url": "https://x/todos/5150"}})
        self.post()
        self.assertEqual([p for p, _ in self.stub.entries()], [f"/projects/{PROJECT}/recordings/5150/timesheet/entries.json"])
        self.assertNotIn("no-card", self.state()["unmapped"])

    def test_a_mirrored_card_is_the_target_of_its_task(self):
        self.track(key="task-a", on=False)
        self.side("map.json", {"task-a": {"card": 9001}})
        self.post()
        self.assertEqual([p for p, _ in self.stub.entries()], [f"/projects/{PROJECT}/recordings/9001/timesheet/entries.json"])

    def test_an_entry_already_in_basecamp_is_only_added_to_the_ledger(self):
        # A post whose answer was lost: Basecamp has the entry, timesheet.json does not.
        self.track()
        self.stub.listed = [{"id": 8123, "date": DAY, "hours": "1.0", "description": "Active agent time: fix-import",
                             "creator": {"id": ACTING}, "app_url": "https://3.basecamp.com/1111111/entries/8123"}]
        self.post()
        self.assertEqual(self.stub.entries(), [])
        self.assertIn(["timesheet", "item", "4242", "--person", str(ACTING), "--start", DAY, "--end", DAY],
                      self.stub.calls)
        self.assertEqual(self.state()["posted"]["fix-import"][DAY]["entry"], 8123)
        self.assertIn(f"timesheet: found an entry already posted 1 h on {DAY} for fix-import", self.log())

    def test_an_entry_by_someone_else_or_another_feature_does_not_stop_the_post(self):
        self.track()
        self.stub.listed = [{"id": 1, "date": DAY, "description": "Active agent time: fix-import", "creator": {"id": 9}},
                            {"id": 2, "date": DAY, "description": "Code review", "creator": {"id": ACTING}}]
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)
        self.assertEqual(self.state()["posted"]["fix-import"][DAY]["entry"], 7000 + len(self.stub.calls))

    def test_a_refused_project_is_reported_once_and_its_entries_tried_every_run(self):
        self.transcript("s2", *[ev(f"2026-10-21T14:{m:02d}:00Z") for m in range(0, 31, 3)])  # a second day, 0.5 h
        self.track()
        self.track(key="other-task")  # another to-do in the same project
        self.stub.refuse = "not_found"
        self.post()
        self.assertEqual(len(self.stub.entries()), 4)  # every entry of the project is tried
        self.assertIn(f"FAILED timesheet: fix-import 1 h on {DAY} on {TODO_URL}, retrying next run", self.log())
        self.post()
        self.assertEqual(len(self.stub.entries()), 8)
        recs = [r for r in self.records() if r["kind"] == "timesheet-refused"]
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["bucket"], recs[0]["code"], recs[0]["date"]), (int(OTHER), "not_found", DAY))
        _, body = behaviors.inbox_note(recs[0], "1111111", PROJECT)
        self.assertIn("timesheet tracking may be off in that project", body)
        self.assertEqual(self.state()["posted"], {})
        self.stub.refuse = None
        self.post()
        self.assertEqual(sorted(self.state()["posted"]), ["fix-import", "other-task"])
        self.assertEqual(self.state()["refused"], {})

    def test_other_failures_are_retried_every_run_and_not_reported_as_refused(self):
        self.track()
        self.stub.refuse = "api_error"
        for _ in range(4):
            self.post()
        self.assertEqual(len(self.stub.entries()), 4)
        self.assertEqual(self.log().count("retrying next run"), 4)
        self.assertFalse(self.state().get("refused"))
        self.assertFalse([r for r in self.records() if r["kind"] == "timesheet-refused"])

    def test_nothing_is_posted_as_the_owner_or_without_a_profile(self):
        self.track()
        self.cfg(profile=None)
        self.post()
        self.assertEqual(self.stub.entries(), [])
        self.assertIn("timesheet: acting user is the owner or unset, not posting", self.log())
        self.assertNotIn("posted", self.state())


class Commands(TimesheetBase):
    def test_start_names_the_to_do_and_the_workdir_and_stop_ends_it(self):
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "claude")
        try:
            code, _ = self.cli("timesheet", "start", "--key", "fix-import", "--on", TODO_URL,
                               "--workdir", "/work/tree.1/repo", "--workdir", "/work/review")
            self.assertEqual(code, 0)
            self.assertEqual(self.cli("timesheet", "start", "--key", "fix-import", "--workdir", "/work/review")[0], 0)
        finally:
            del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertIn(["api", "get", f"/buckets/{OTHER}/recordings/4242.json"], self.stub.calls)
        item = self.state()["items"]["fix-import"]
        self.assertEqual(item["on"], {"bucket": int(OTHER), "recording": 4242, "type": "Todo", "title": "Fix the import",
                                      "url": TODO_URL})
        root = os.path.join(self.tmp, "claude", "projects")
        self.assertEqual([s["path"] for s in item["sources"]],  # the repeated workdir: already counting, nothing added
                         [os.path.join(root, "-work-tree-1-repo"), os.path.join(root, "-work-review")])
        self.assertTrue(all(s["from"] and "to" not in s for s in item["sources"]))
        self.assertEqual(self.cli("timesheet", "stop", "--key", "fix-import")[0], 0)
        self.assertTrue(all(s.get("to") for s in self.state()["items"]["fix-import"]["sources"]))
        self.assertEqual(self.cli("timesheet", "stop", "--key", "missing")[0], 1)

    def test_start_refuses_a_recording_that_is_not_a_to_do_or_card_and_needs_a_workdir(self):
        self.stub.types[4242] = "Message"
        self.assertEqual(self.cli("timesheet", "start", "--key", "k", "--on", TODO_URL, "--workdir", self.tmp)[0], 1)
        self.assertEqual(self.cli("timesheet", "start", "--key", "k")[0], 1)
        self.assertEqual(self.cli("timesheet", "start", "--key", "k", "--on", "4242", "--workdir", self.tmp)[0], 1)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.cli("timesheet", "start", "--key", "k", "--on", "https://3.basecamp.com/9/buckets/1/todos/2",
                                  "--workdir", self.tmp)[0], 1)
        self.assertIn("not this home's account", self.log())

    def test_parse_recording(self):
        self.assertEqual(tools.parse_recording("https://3.basecamp.com/1111111/buckets/5/card_tables/cards/77", "1111111"),
                         ("5", 77))
        self.assertEqual(tools.parse_recording("https://app.basecamp.com/1111111/buckets/5/todos/78#__recording_1", "1111111"),
                         ("5", 78))
        for bad in ("79", "https://3.basecamp.com/1111111/projects/5"):
            with self.assertRaises(ValueError):
                tools.parse_recording(bad, "1111111")

    def test_malformed_config_refused(self):
        for bad in ([], {"gap_minutes": 5}, {"tool_minutes": 60}, {"extra": 1}, True):
            self.cfg(timesheet=bad)
            with self.assertRaises(ValueError):
                self.sync()


class InitFlag(unittest.TestCase):
    def test_init_writes_it_and_a_re_run_keeps_it(self):
        import test_init
        t = test_init.InitTest("run")
        t.setUp()
        try:
            t.init(timesheet=True).main()
            cfg = json.load(open(os.path.join(t.dir, "config.json")))
            self.assertEqual(cfg["timesheet"], {})
            t.init().main()
            self.assertEqual(json.load(open(os.path.join(t.dir, "config.json")))["timesheet"], {})
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
