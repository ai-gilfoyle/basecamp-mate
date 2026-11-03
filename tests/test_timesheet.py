"""Tests for the timesheet behavior: the agent's active time per to-do or card per day, posted once to its timesheet.

The basecamp CLI is stubbed and the Claude Code transcripts are files in a temporary directory; nothing touches the
network.
"""
import io, json, os, shutil, sys, unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, Base, Stub  # noqa: E402
import behaviors, sync, tools  # noqa: E402

PROJECT, OTHER = "22222222", "66666666"
TODO_URL = f"https://3.basecamp.com/1111111/buckets/{OTHER}/todos/4242"
DAY = "2026-10-20"  # a finished day, in America/New_York (UTC-4 then)


class TimesheetStub(Stub):
    """Adds a recording read, the timesheet entry read and `basecamp timesheet entry create` to the basecamp stub."""

    def __init__(self):
        super().__init__()
        self.types = {}  # recording id -> its type ("Todo" when absent)
        self.refuse = None  # an error code the entry post fails with, e.g. "not_found"
        self.listed = []  # the timesheet entries `timesheet item` lists
        self.retryable = False  # whether a refused entry post says it may be retried
        self.gone = set()  # recording ids whose entry post fails with "not_found"

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
        if args[:3] == ["timesheet", "entry", "create"]:
            self.calls.append([*args, "-p", cmd[-2]])
            refuse = "not_found" if int(args[3]) in self.gone else self.refuse
            if refuse:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "code": refuse, "error": "Resource not found",
                                                          "retryable": self.retryable}), stderr="", returncode=1)
            data = {"id": 7000 + len(self.calls), "app_url": "https://3.basecamp.com/1111111/entries/1"}
            self.listed.append({**entry_body(args), "id": data["id"], "creator": {"id": self.me}})  # Basecamp lists it
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def entries(self):
        """((project, recording id), {date, hours, description}) of each `timesheet entry create` tried, in order."""
        return [((a[-1], int(a[3])), entry_body(a)) for a in self.calls if a[:3] == ["timesheet", "entry", "create"]]


def entry_body(args):
    """The date, hours and description a `timesheet entry create` call's flags give."""
    flags = dict(zip(args[4::2], args[5::2]))
    return {"date": flags["--date"], "hours": flags["--hours"], "description": flags["--description"]}


def ev(at, tool=None, result=None):
    """One transcript line at UTC time `at` ("HH:MM" on DAY, or a full ISO time); `tool`: an assistant line calling the
    tool with that id; `result`: the user line with the result of the tool call with that id."""
    ts = at if "T" in at else f"{DAY}T{at}:00.000Z"
    if tool:
        return {"type": "assistant", "timestamp": ts,
                "message": {"content": [{"type": "tool_use", "id": tool, "name": "Bash"}]}}
    if result:
        return {"type": "user", "timestamp": ts,
                "message": {"content": [{"type": "tool_result", "tool_use_id": result, "content": "ok"}]}}
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

    def track(self, key="fix-import", on=True, frm="2026-10-01T00:00:00Z", to=None, path=None, rec=4242):
        state = self.state()
        item = {"sources": [{"path": path or self.work, "from": frm, **({"to": to} if to else {})}]}
        if on:
            item["on"] = {"bucket": int(OTHER), "recording": rec, "type": "Todo", "title": "Fix the import",
                          "url": TODO_URL.replace("4242", str(rec))}
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
        self.transcript("s1", ev("14:00"), ev("14:03"), ev("14:06"), ev("14:20", tool="t1"), ev("14:50", result="t1"), ev("15:55"))
        self.track()
        self.assertEqual(self.hours(), {DAY: 0.5})

    def test_a_tool_wait_lasts_until_its_result_across_the_lines_between(self):
        # A tool call at 14:00, a progress line at 14:01, its result at 14:30: 30 minutes, 0.5 h.
        progress = {"type": "progress", "timestamp": f"{DAY}T14:01:00Z"}
        self.transcript("s1", ev("14:00", tool="t1"), progress, ev("14:30", result="t1"), ev("15:10"))
        self.track()
        self.assertEqual(self.hours(), {DAY: 0.5})

    def test_a_tool_wait_over_the_limit_does_not_count(self):
        self.transcript("s1", ev("14:00", tool="t1"), ev("15:30", result="t1"))  # 90 minutes on one tool call: over 60
        self.track()
        self.assertEqual(self.hours(), {})

    def test_a_long_tool_wait_with_a_progress_line_between_counts_only_the_short_gaps(self):
        # A call at 14:00, progress at 14:30 and its result at 15:30: a 90-minute wait, over 60, so none of it counts;
        # 15:30-15:45 in 3-minute steps after it (15 min) does: 0.25 h.
        progress = {"type": "progress", "timestamp": f"{DAY}T14:30:00Z"}
        self.transcript("s1", ev("14:00", tool="t1"), progress, ev("15:30", result="t1"),
                        *[ev(f"15:{m:02d}") for m in range(33, 46, 3)])
        self.track()
        self.assertEqual(self.hours(), {DAY: 0.25})

    def test_a_tool_call_with_no_result_counts_no_wait(self):
        self.transcript("s1", ev("14:00", tool="t1"), ev("14:40"))  # interrupted: a new prompt, no result
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

    def test_time_splits_at_midnight_in_the_config_zone_default_new_york(self):
        # 03:30-04:30 UTC on Oct 21 is 23:30-00:30 in New York: half an hour on each day without a config zone; one
        # hour on Oct 21 with "timezone": "UTC".
        self.transcript("s1", *[ev(f"2026-10-21T0{3 + (m // 60)}:{m % 60:02d}:00Z") for m in range(30, 91, 4)])
        self.track()
        self.cfg(timezone=None)
        self.assertEqual(self.hours(), {"2026-10-20": 0.5, "2026-10-21": 0.5})
        self.assertEqual(self.stub.calls, [])  # the default is New York, not the captain's Basecamp zone
        self.cfg(timezone="UTC")
        self.assertEqual(self.hours(), {"2026-10-21": 1.0})

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
        self.assertEqual(self.stub.entries(), [((OTHER, 4242),
                                                {"date": DAY, "hours": "1", "description": "Active agent time: fix-import"})])
        self.assertIn(["timesheet", "entry", "create", "4242", "--hours", "1", "--date", DAY, "--description",
                       "Active agent time: fix-import", "-p", OTHER], self.stub.calls)
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

    def test_a_day_waits_until_its_last_tool_wait_is_known(self):
        self.track()
        for at, posts in (("2026-10-21T04:30:00", 0), ("2026-10-21T05:00:00", 1)):  # 00:30, then 01:00 in New York
            fixed = datetime.fromisoformat(at).replace(tzinfo=timezone.utc)

            class Now(datetime):
                @classmethod
                def now(cls, tz=None):
                    return fixed

            with mock.patch.object(behaviors, "datetime", Now):
                self.post()
            self.assertEqual(len(self.stub.entries()), posts)

    def test_an_entry_post_is_tried_once_and_the_next_run_reads_first(self):
        self.track()
        self.stub.refuse, self.stub.retryable = "api_error", True
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)
        self.assertIn("retrying next run", self.log())
        self.stub.refuse = None
        self.stub.listed = [{"id": 8124, "date": DAY, "description": "Active agent time: fix-import",
                             "creator": {"id": ACTING}}]  # the failed post had in fact made the entry
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)
        self.assertEqual(self.state()["posted"]["fix-import"][DAY]["entry"], 8124)

    def test_an_unreadable_transcript_defers_the_key_and_the_full_hours_post_once_it_reads(self):
        s2 = self.transcript("s2", *[ev(f"15:{m:02d}") for m in range(0, 31, 3)])  # 15:00-15:30: with s1, 1.5 h
        self.track(to=f"{DAY}T23:00:00Z")
        real = open

        def unreadable(f, *a, **kw):  # a permission error, also when run as root
            if f == s2:
                raise PermissionError(13, "Permission denied", f)
            return real(f, *a, **kw)

        with mock.patch("builtins.open", unreadable):
            self.post()
        self.assertEqual(self.stub.entries(), [])
        self.assertNotIn("fix-import", self.state().get("posted") or {})
        self.assertNotIn("settled", self.state()["items"]["fix-import"])
        self.assertIn("FAILED timesheet: fix-import not posted, a transcript could not be read", self.log())
        self.post()
        self.post()
        self.assertEqual([b["hours"] for _, b in self.stub.entries()], ["1.5"])
        self.assertTrue(self.state()["items"]["fix-import"]["settled"])

    def test_a_transcript_removed_while_read_is_skipped_and_logged(self):
        gone = self.transcript("s2", *[ev(f"15:{m:02d}") for m in range(0, 31, 3)])
        real = os.path.getmtime

        def getmtime(f):
            if f == gone:
                os.remove(gone)
            return real(f)

        self.track()
        with mock.patch.object(tools.os.path, "getmtime", getmtime):
            self.post()
        self.assertEqual([b["hours"] for _, b in self.stub.entries()], ["1"])
        self.assertIn(f"timesheet: transcript {gone} is gone, skipped", self.log())

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
        self.assertEqual([p for p, _ in self.stub.entries()], [(PROJECT, 5150)])
        self.assertNotIn("no-card", self.state()["unmapped"])

    def test_a_mirrored_card_is_the_target_of_its_task(self):
        self.track(key="task-a", on=False)
        self.side("map.json", {"task-a": {"card": 9001}})
        self.post()
        self.assertEqual([p for p, _ in self.stub.entries()], [(PROJECT, 9001)])

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

    def test_two_keys_on_one_to_do_and_day_both_post(self):
        # Several pieces of work share one to-do (e.g. one for work without its own card): each gets its own entry.
        self.track(key="task-a")
        self.track(key="task-b")
        self.post()
        self.assertEqual(sorted(b["description"] for _, b in self.stub.entries()),
                         ["Active agent time: task-a", "Active agent time: task-b"])
        self.assertEqual({p for p, _ in self.stub.entries()}, {(OTHER, 4242)})
        self.assertEqual(sorted(self.state()["posted"]), ["task-a", "task-b"])
        self.assertNotIn("found an entry already posted", self.log())
        self.post()
        self.assertEqual(len(self.stub.entries()), 2)

    def test_an_entry_by_someone_else_or_another_feature_does_not_stop_the_post(self):
        self.track()
        self.stub.listed = [{"id": 1, "date": DAY, "description": "Active agent time: fix-import", "creator": {"id": 9}},
                            {"id": 2, "date": DAY, "description": "Code review", "creator": {"id": ACTING}}]
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)
        self.assertEqual(self.state()["posted"]["fix-import"][DAY]["entry"], 7000 + len(self.stub.calls))

    def refused_yesterday(self):
        """Move the refusal's last try to an earlier day, as if a day had passed."""
        state = self.state()
        state["refused"][OTHER]["probed"] = "2026-10-01"
        self.side("timesheet.json", state)

    def test_a_refused_project_is_reported_once_then_tried_with_one_entry_a_day(self):
        self.transcript("s2", *[ev(f"2026-10-21T14:{m:02d}:00Z") for m in range(0, 31, 3)])  # a second day, 0.5 h
        self.track()
        self.track(key="other-task")  # another to-do in the same project
        self.stub.refuse = "forbidden"
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)  # the project's other entries wait
        self.assertEqual(self.log().count("FAILED"), 1)
        self.assertIn(f"FAILED timesheet: project {OTHER}'s timesheet refused fix-import's 1 h on {DAY}", self.log())
        self.post()
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)  # tried again only the next day
        recs = [r for r in self.records() if r["kind"] == "timesheet-refused"]
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["bucket"], recs[0]["code"], recs[0]["date"]), (int(OTHER), "forbidden", DAY))
        _, body = behaviors.inbox_note(recs[0], "1111111", PROJECT)
        self.assertIn("timesheet tracking may be off in that project", body)
        self.refused_yesterday()
        self.post()
        self.assertEqual(len(self.stub.entries()), 2)  # one probe
        self.assertIn(f"timesheet: project {OTHER} still refuses entries", self.log())
        self.assertEqual(self.log().count("FAILED"), 1)
        self.assertEqual(len([r for r in self.records() if r["kind"] == "timesheet-refused"]), 1)
        self.assertEqual(self.state()["posted"], {})
        self.stub.refuse = None
        self.refused_yesterday()
        self.post()  # the probe posts, and the project's other entries follow in the same run
        self.assertIn(f"timesheet: project {OTHER} accepts entries again", self.log())
        self.assertEqual(sorted(self.state()["posted"]), ["fix-import", "other-task"])
        self.assertEqual(len(self.state()["posted"]["fix-import"]), 2)
        self.assertEqual(self.state()["refused"], {})

    def test_a_stopped_key_in_a_refused_project_keeps_its_days_and_posts_them_with_the_daily_try(self):
        self.track(to=f"{DAY}T23:00:00Z")
        self.stub.refuse = "forbidden"
        self.post()
        item = self.state()["items"]["fix-import"]
        self.assertEqual(item["unposted"], {DAY: 1.0})
        self.assertNotIn("settled", item)
        self.assertIn(f"timesheet: fix-import stopped with 1 h on {DAY} not posted: project {OTHER} refuses entries",
                      self.log())
        self.post()
        self.assertEqual(len(self.stub.entries()), 1)  # tried again only the next day
        self.refused_yesterday()
        self.post()
        self.assertEqual(len(self.stub.entries()), 2)  # the daily try
        self.assertEqual(self.log().count("stopped with"), 1)
        self.assertEqual(self.log().count("FAILED"), 1)
        shutil.rmtree(self.work)  # its days come from "unposted", not the transcripts
        self.stub.refuse = None
        self.refused_yesterday()
        self.post()
        self.assertEqual([b["date"] for _, b in self.stub.entries()[2:]], [DAY])
        self.assertEqual(self.state()["posted"]["fix-import"][DAY]["hours"], 1.0)
        item = self.state()["items"]["fix-import"]
        self.assertTrue(item["settled"])
        self.assertNotIn("unposted", item)
        self.stub.calls.clear()
        self.post()
        self.assertEqual(self.stub.calls, [])  # settled: never read or tried again

    def test_a_missing_to_do_is_reported_and_tried_daily_alone_while_the_project_still_posts(self):
        self.track(key="a-task")
        self.track(key="b-task", rec=5150)  # another to-do in the same project
        self.stub.gone = {4242}
        self.post()
        self.post()
        self.assertEqual([(r, b["description"]) for (_, r), b in self.stub.entries()],
                         [(4242, "Active agent time: a-task"), (5150, "Active agent time: b-task")])
        self.assertEqual(sorted(self.state()["posted"]), ["b-task"])
        self.assertEqual(self.state()["refused"], {})
        recs = [r for r in self.records() if r["kind"] == "timesheet-missing"]
        self.assertEqual([(r["key"], r["code"], r["date"]) for r in recs], [("a-task", "not_found", DAY)])
        _, body = behaviors.inbox_note(recs[0], "1111111", PROJECT)
        self.assertIn("timesheet start --key a-task --on", body)
        self.assertEqual(self.log().count("FAILED"), 1)
        state = self.state()
        state["missing"]["a-task"]["probed"] = "2026-10-01"
        self.side("timesheet.json", state)
        self.post()
        self.assertEqual(len(self.stub.entries()), 3)  # one try a day
        self.assertIn(f"timesheet: a-task's to-do or card {TODO_URL} still not found", self.log())
        self.assertEqual(self.log().count("FAILED"), 1)
        self.assertEqual(self.cli("timesheet", "start", "--key", "a-task", "--on", TODO_URL.replace("4242", "5150"),
                                  "--workdir", self.tmp)[0], 0)
        self.assertNotIn("a-task", self.state()["missing"])
        self.post()
        self.assertEqual(sorted(self.state()["posted"]), ["a-task", "b-task"])

    def test_a_probe_that_finds_its_to_do_missing_leaves_the_projects_try_to_the_next_key(self):
        self.track(key="a-task")
        self.track(key="b-task", rec=5150)
        self.stub.refuse = "forbidden"
        self.post()
        self.stub.refuse, self.stub.gone = None, {4242}
        self.refused_yesterday()
        self.post()  # a-task's probe finds its to-do gone; b-task probes the project in the same run and posts
        self.assertEqual(sorted(self.state()["posted"]), ["b-task"])
        self.assertEqual(self.state()["refused"], {})
        self.assertEqual(list(self.state()["missing"]), ["a-task"])
        self.assertEqual([r["key"] for r in self.records() if r["kind"] == "timesheet-missing"], ["a-task"])
        self.assertIn(f"timesheet: project {OTHER} accepts entries again", self.log())

    def test_a_missing_keys_retry_refused_by_the_project_records_the_refusal(self):
        self.track(key="a-task")
        self.stub.gone = {4242}
        self.post()
        state = self.state()
        state["missing"]["a-task"]["probed"] = "2026-10-01"
        self.side("timesheet.json", state)
        self.stub.gone, self.stub.refuse = set(), "forbidden"
        self.post()
        self.assertEqual(self.state()["refused"][OTHER]["code"], "forbidden")
        self.assertEqual([r["code"] for r in self.records() if r["kind"] == "timesheet-refused"], ["forbidden"])
        self.track(key="b-task", rec=5150)
        self.post()
        self.assertEqual(len(self.stub.entries()), 2)  # the project's try for today is used: b-task waits

    def test_an_unreadable_directory_defers_the_key_until_it_reads(self):
        sub = os.path.join(self.work, "s1", "subagents")
        self.transcript("agent-1", *[ev(f"15:{m:02d}") for m in range(0, 31, 3)], where=sub)  # with s1, 1.5 h
        self.track(to=f"{DAY}T23:00:00Z")
        real = os.scandir

        def scandir(p="."):
            if p == sub:
                raise PermissionError(13, "Permission denied", p)
            return real(p)

        with mock.patch.object(tools.os, "scandir", scandir):
            self.post()
        self.assertEqual(self.stub.entries(), [])
        self.assertNotIn("settled", self.state()["items"]["fix-import"])
        self.assertIn("FAILED timesheet: fix-import not posted, a transcript could not be read", self.log())
        self.post()
        self.assertEqual([b["hours"] for _, b in self.stub.entries()], ["1.5"])

    def test_start_keeps_a_stopped_keys_unposted_days_and_adds_its_new_days(self):
        self.track(to=f"{DAY}T23:00:00Z")
        self.stub.refuse = "forbidden"
        self.post()
        shutil.rmtree(self.work)
        self.assertEqual(self.cli("timesheet", "start", "--key", "fix-import", "--workdir", self.tmp)[0], 0)
        self.assertEqual(self.state()["items"]["fix-import"]["unposted"], {DAY: 1.0})
        state = self.state()
        new = os.path.join(self.tmp, "w2")
        state["items"]["fix-import"]["sources"][-1].update(path=new, **{"from": "2026-10-21T00:00:00Z"})
        self.side("timesheet.json", state)
        self.transcript("s3", *[ev(f"2026-10-21T14:{m:02d}:00Z") for m in range(0, 31, 3)], where=new)  # 0.5 h
        self.stub.refuse = None
        self.refused_yesterday()
        self.post()
        self.assertEqual({k: v["hours"] for k, v in self.state()["posted"]["fix-import"].items()},
                         {DAY: 1.0, "2026-10-21": 0.5})

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

    def test_start_refuses_a_directory_another_key_is_counting(self):
        self.assertEqual(self.cli("timesheet", "start", "--key", "task-a", "--workdir", "/work/tree.1/repo")[0], 0)
        before = self.state()
        code, _ = self.cli("timesheet", "start", "--key", "task-b", "--workdir", "/work/tree.1/repo")
        self.assertEqual(code, 1)
        self.assertIn("key task-a is already counting", self.log())
        self.assertIn("never the home", self.log())
        self.assertEqual(self.state(), before)
        self.assertEqual(self.cli("timesheet", "start", "--key", "task-a", "--workdir", "/work/tree.1/repo")[0], 0)
        self.assertEqual(self.cli("timesheet", "stop", "--key", "task-a")[0], 0)
        self.assertEqual(self.cli("timesheet", "start", "--key", "task-b", "--workdir", "/work/tree.1/repo")[0], 0)
        self.assertEqual(sorted(self.state()["items"]), ["task-a", "task-b"])

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
