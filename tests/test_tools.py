"""Tests for the to-do and message tools, the to-do comment reader, the CLI and the layer boundary.

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, itertools, json, os, sys, unittest, unittest.mock
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, Base, Stub, item, line  # noqa: E402
import sync  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TodoStub(Stub):
    """Adds the to-do, comment-create and message calls to the basecamp stub, keeping each call's stdin."""

    def __init__(self):
        super().__init__()
        self.inputs, self.todo_records, self.fail_create = [], {}, False
        self.notes, self.note_ids, self.inbox_exit = [], {}, 0  # fm-inbox.sh notes (request id, body, FM_HOME)

    def __call__(self, cmd, **kw):
        if cmd[0].endswith("/bin/fm-inbox.sh"):
            if isinstance(self.inbox_exit, Exception):
                raise self.inbox_exit
            assert cmd[1:3] == ["note", "--request-id"] and cmd[4:] == ["--json", "-"], cmd
            rid = cmd[3]
            if self.inbox_exit:
                return SimpleNamespace(stdout="", stderr="not woken", returncode=self.inbox_exit)
            outcome = "replay" if rid in self.note_ids else "created"
            if outcome == "created":
                self.note_ids[rid] = len(self.note_ids) + 1
                self.notes.append((rid, kw["input"], kw["env"]["FM_HOME"], cmd[0]))
            return SimpleNamespace(stdout=json.dumps({"outcome": outcome, "note_id": self.note_ids[rid]}), stderr="", returncode=0)
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            core = args[2:] if args[:1] == ["-P"] else args
            mine = (core[:2] in (["todos", "create"], ["todos", "complete"], ["subtasks", "create"], ["subtasks", "complete"], ["comments", "create"], ["messages", "create"])
                    or (core[:2] == ["api", "get"] and "/todos/" in core[2]))
            if mine:
                self.profiles.append(args[1] if args[:1] == ["-P"] else None)
                self.calls.append(core)
                self.inputs.append(kw.get("input"))
                return SimpleNamespace(stdout=json.dumps(self.answer(core, kw.get("input"))), stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def answer(self, core, text):
        self.next_id += 1
        if core[:2] == ["todos", "create"]:
            if self.fail_create:
                return {"ok": False, "error": "boom"}
            self.todo_records[str(self.next_id)] = {"id": self.next_id, "content": core[2], "completed": False}
            return {"ok": True, "data": {"id": self.next_id, "app_url": f"https://x/todos/{self.next_id}"}}
        if core[:2] == ["comments", "create"]:
            c = {"id": self.next_id, "creator": {"id": self.me}, "content": text}
            self.comments.setdefault(core[2], []).append(c)
            return {"ok": True, "data": c}
        if core[:2] == ["messages", "create"]:
            return {"ok": True, "data": {"id": self.next_id, "app_url": f"https://x/messages/{self.next_id}"}}
        if core[:2] == ["subtasks", "create"]:
            return {"ok": True, "data": {"id": self.next_id, "title": core[3], "completed": False}}
        if core[:2] == ["subtasks", "complete"]:
            return {"ok": True, "data": None}
        if core[:2] == ["todos", "complete"]:
            self.todo_records[core[2]]["completed"] = True
            return {"ok": True, "data": None}
        tid = core[2].split("/")[-1].split(".")[0]
        return {"ok": True, "data": dict(self.todo_records.get(tid, {"id": int(tid), "content": "Old one"}),
                                         app_url=f"https://x/todos/{tid}", created_at="t0")}

    def made(self, verb):
        return [c for c in self.calls if c[:2] == verb.split()]


def comment(id, who=CAPTAIN, content="<p>Merge it</p>"):
    return {"id": id, "creator": {"id": who}, "content": content, "created_at": "t", "app_url": f"https://x/todos/c{id}"}


class ToolBase(Base):
    def setUp(self):
        super().setUp()
        self.stub = TodoStub()
        self.cfg(profile="agent", todos={"todoset": "66"}, message_board="99")

    def cfg(self, drop=(), **kw):
        p = os.path.join(self.cfgdir, "config.json")
        cfg = json.load(open(p))
        for k in drop:
            cfg.pop(k, None)
        cfg.update(kw)
        json.dump(cfg, open(p, "w"))

    def todos(self):
        p = os.path.join(self.cfgdir, "todos.json")
        return json.load(open(p)) if os.path.exists(p) else {}

    def pending(self):
        p = os.path.join(self.cfgdir, "pending-comments.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def create(self, key="ta-x", title="Merge? The fix", body="Evidence: https://github.com/o/r/pull/1", **kw):
        return self.sync(dry=kw.pop("dry", False)).todo_create(key, title, body, **kw)

    def poll(self, dry=False):
        self.sync(dry=dry).main([])


class TodoCreate(ToolBase):
    def test_loose_on_the_todoset_assigned_to_owner_and_tracked(self):
        tid = self.create()
        [call] = self.stub.made("todos create")
        self.assertEqual(call, ["todos", "create", "Merge? The fix", "--loose", "--todoset", "66", "--assignee", str(CAPTAIN),
                                "--description=Evidence: https://github.com/o/r/pull/1"])
        rec = self.todos()["ta-x"]
        self.assertEqual((rec["todo"], rec["url"], rec["cursor"], rec["title"]), (tid, f"https://x/todos/{tid}", 0, "Merge? The fix"))
        self.assertEqual(set(self.stub.profiles), {"agent"})

    def test_list_from_flag_or_config_and_bare_loose(self):
        self.create("a", todolist="7", due="2026-10-09")
        self.cfg(todos={"list": "8"})
        self.create("b", body="")
        self.cfg(todos={})
        self.create("c")
        a, b, c = self.stub.made("todos create")
        self.assertEqual(a[3:5] + a[-2:], ["--list", "7", "--due", "2026-10-09"])
        self.assertEqual(b[3:5], ["--list", "8"])
        self.assertFalse(any(x.startswith("--description") for x in b))
        self.assertEqual(c[3:5], ["--loose", "--assignee"])

    def test_tracked_key_never_duplicated(self):
        self.create()
        self.assertIsNone(self.create(title="Again"))
        self.assertEqual(len(self.stub.made("todos create")), 1)

    def test_refused_as_owner_without_profile_or_in_dry_run(self):
        self.stub.me = CAPTAIN
        self.assertIsNone(self.create())
        self.stub.me = ACTING
        self.assertIsNone(self.create(dry=True))
        self.cfg(profile=None)
        self.assertIsNone(self.create())
        self.assertEqual(self.stub.made("todos create"), [])
        self.assertEqual(self.todos(), {})

    def test_needs_config_and_a_safe_title(self):
        with self.assertRaises(ValueError):
            self.create(title="-rf")
        self.cfg(drop=("todos",))
        with self.assertRaises(RuntimeError):
            self.create()

    def test_failed_create_tracks_nothing(self):
        self.stub.fail_create = True
        with self.assertRaises(RuntimeError):
            self.create()
        self.assertEqual(self.todos(), {})


class TodoSubtasks(ToolBase):
    QS = ["Merge the fix?", "Backport it to 1.x?"]

    def test_every_decision_is_a_native_subtask_left_unassigned(self):
        tid = self.create(title="The fix", subtasks=self.QS)
        [call] = self.stub.made("todos create")
        self.assertEqual(call[-1], "--description=Evidence: https://github.com/o/r/pull/1")  # context only
        self.assertEqual(self.stub.made("subtasks create"),
                         [["subtasks", "create", str(tid), q] for q in self.QS])
        rec = self.todos()["ta-x"]
        self.assertEqual(rec["subtasks"], [{"text": "Merge the fix?", "done": None, "id": tid + 1},
                                           {"text": "Backport it to 1.x?", "done": None, "id": tid + 2}])
        self.assertEqual(set(self.stub.profiles), {"agent"})

    def test_completing_each_completes_its_subtask_and_the_last_completes_the_todo(self):
        tid = self.create(subtasks=self.QS)
        self.assertTrue(self.sync().todo_subtask("ta-x", 2))
        self.assertEqual(self.stub.made("subtasks complete"), [["subtasks", "complete", str(tid + 2)]])
        self.assertEqual(self.stub.made("todos complete"), [])
        self.assertFalse(self.sync().todo_subtask("ta-x", 2))  # already done
        self.assertTrue(self.sync().todo_subtask(str(tid), 1))
        self.assertEqual(self.stub.made("subtasks complete")[-1], ["subtasks", "complete", str(tid + 1)])
        self.assertEqual(self.stub.made("todos complete"), [["todos", "complete", str(tid)]])
        rec = self.todos()["ta-x"]
        self.assertTrue(rec["completed"] and all(q["done"] for q in rec["subtasks"]))

    def test_bad_number_dry_run_and_owner_refusal_change_nothing(self):
        self.create(subtasks=self.QS)
        with self.assertRaises(ValueError):
            self.sync().todo_subtask("ta-x", 3)
        self.assertFalse(self.sync(dry=True).todo_subtask("ta-x", 1))
        self.stub.me = CAPTAIN
        self.assertFalse(self.sync().todo_subtask("ta-x", 1))
        self.assertEqual(self.stub.made("subtasks complete"), [])
        self.assertFalse(any(q["done"] for q in self.todos()["ta-x"]["subtasks"]))

    def test_dry_run_creates_no_subtasks(self):
        self.assertIsNone(self.create(subtasks=self.QS, dry=True))
        self.assertEqual(self.stub.made("subtasks create"), [])

    def test_without_subtasks_the_todo_is_unchanged_and_has_none_to_complete(self):
        self.create()
        self.assertNotIn("subtasks", self.todos()["ta-x"])
        self.assertEqual(self.stub.made("subtasks create"), [])
        with self.assertRaises(ValueError):
            self.sync().todo_subtask("ta-x", 1)
        with self.assertRaises(ValueError):
            self.create("ta-y", subtasks=["ok", " "])
        with self.assertRaises(ValueError):  # every decision is a subtask, so never just one
            self.create("ta-y", subtasks=["Merge the fix?"])
        self.assertEqual(len(self.stub.made("todos create")), 1)


class TodoComments(ToolBase):
    def setUp(self):
        super().setUp()
        self.tid = self.create()
        self.c = str(self.tid)

    def test_owner_comment_recorded_once_with_its_kind(self):
        self.stub.comments[self.c] = [comment(1, content="<p>Merge it &amp; ship</p>"), comment(2, who=1), comment(3, who=ACTING)]
        self.poll()
        self.poll()
        [rec] = self.pending()
        self.assertEqual(rec, {"kind": "todo-comment", "key": "ta-x", "todo": self.tid, "comment": 1, "question": False,
                               "url": "https://x/todos/c1", "text": "Merge it & ship", "at": "t", "author": {"id": CAPTAIN, "name": None}, "captain": True, "role": "captain"})
        self.assertEqual(self.todos()["ta-x"]["cursor"], 3)
        self.assertEqual(self.stub.posted(), [("1", "👍")])

    def test_question_gets_eyes_and_reply_comments_on_the_todo(self):
        self.stub.comments[self.c] = [comment(1, content="<p>there is an unresolved review comment?</p>")]
        self.poll()
        self.assertTrue(self.pending()[0]["question"])
        self.assertEqual(self.stub.posted(), [("1", "👀")])
        self.assertTrue(self.sync().reply(1, "Fixing it on the PR."))
        self.assertEqual(self.stub.made("comments create"), [["comments", "create", self.c, "-"]])
        self.assertEqual(self.stub.inputs[-1], "Fixing it on the PR.")
        self.assertEqual(self.stub.boosts["1"], [])
        self.assertEqual(self.todos()["ta-x"]["replied"], [1])
        self.assertFalse(self.sync().reply(1, "again"))
        self.poll()
        self.assertEqual(len(self.pending()), 1)  # the agent's own reply is not relayed

    def test_each_todo_keeps_its_own_cursor(self):
        other = self.create("ta-y")
        self.stub.comments[self.c] = [comment(1)]
        self.poll()
        self.stub.comments[str(other)] = [comment(5)]
        self.stub.comments[self.c].append(comment(9))
        self.poll()
        self.assertEqual([(r["key"], r["comment"]) for r in self.pending()], [("ta-x", 1), ("ta-x", 9), ("ta-y", 5)])
        self.assertEqual({k: v["cursor"] for k, v in self.todos().items()}, {"ta-x": 9, "ta-y": 5})

    def test_dry_run_records_and_boosts_nothing(self):
        self.stub.comments[self.c] = [comment(1)]
        self.poll(dry=True)
        self.assertEqual((self.pending(), self.stub.posted()), ([], []))
        self.assertEqual(self.todos()["ta-x"]["cursor"], 0)

    def test_owner_profile_records_but_never_boosts(self):
        self.stub.comments[self.c] = [comment(1)]
        self.stub.me = CAPTAIN
        self.poll()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.stub.posted(), [])

    def test_completed_todo_is_no_longer_read(self):
        self.assertTrue(self.sync().todo_complete("ta-x"))
        self.assertEqual(self.stub.made("todos complete"), [["todos", "complete", self.c]])
        self.assertFalse(self.sync().todo_complete(self.c))  # by id; already completed
        self.stub.comments[self.c] = [comment(1)]
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertFalse([c for c in self.stub.calls if c[:2] == ["comments", "list"]])

    def test_reader_off_without_todos_config(self):
        self.stub.comments[self.c] = [comment(1)]
        self.cfg(drop=("todos",))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertFalse([c for c in self.stub.calls if c[:2] == ["comments", "list"]])


class TodoCommandsRefused(ToolBase):
    def setUp(self):
        super().setUp()
        self.create()

    def test_comment_posts_markdown_on_a_tracked_todo(self):
        self.assertTrue(self.sync().todo_comment("ta-x", "- one\n- two"))
        self.assertEqual(self.stub.inputs[-1], "- one\n- two")
        with self.assertRaises(RuntimeError):
            self.sync().todo_comment("nope", "x")

    def test_comment_and_complete_refused_as_owner_or_dry(self):
        self.stub.me = CAPTAIN
        self.assertFalse(self.sync().todo_comment("ta-x", "x"))
        self.assertFalse(self.sync().todo_complete("ta-x"))
        self.stub.me = ACTING
        self.assertFalse(self.sync(dry=True).todo_comment("ta-x", "x"))
        self.assertFalse(self.sync(dry=True).todo_complete("ta-x"))
        self.assertEqual(self.stub.made("comments create") + self.stub.made("todos complete"), [])
        self.assertNotIn("completed", self.todos()["ta-x"])


class TodoTrack(ToolBase):
    def test_adopts_a_todo_without_relaying_its_history(self):
        self.stub.comments["42"] = [comment(3), comment(4)]
        self.assertTrue(self.sync().todo_track("old", 42))
        rec = self.todos()["old"]
        self.assertEqual((rec["todo"], rec["cursor"], rec["title"]), (42, 4, "Old one"))
        self.assertEqual(self.stub.made("todos create"), [])
        self.poll()
        self.assertEqual(self.pending(), [])
        self.stub.comments["42"].append(comment(8))
        self.poll()
        self.assertEqual([r["comment"] for r in self.pending()], [8])
        self.assertFalse(self.sync().todo_track("other", 42))


class PostMessage(ToolBase):
    def test_posts_on_the_configured_board(self):
        self.assertTrue(self.sync().post_message("Report: the deploy", "## Findings\n- one"))
        [call] = self.stub.made("messages create")
        self.assertEqual(call, ["messages", "create", "Report: the deploy", "-", "--message-board", "99"])
        self.assertEqual(self.stub.inputs[-1], "## Findings\n- one")

    def test_refused_as_owner_dry_or_unconfigured(self):
        self.stub.me = CAPTAIN
        self.assertFalse(self.sync().post_message("S", "b"))
        self.stub.me = ACTING
        self.assertFalse(self.sync(dry=True).post_message("S", "b"))
        self.assertEqual(self.stub.made("messages create"), [])
        self.cfg(drop=("message_board",))
        with self.assertRaises(RuntimeError):
            self.sync().post_message("S", "b")


class Cli(ToolBase):
    def run_cli(self, *argv, body="Body text"):
        f = os.path.join(self.tmp, "body.md")
        open(f, "w").write(body)
        argv = [a.replace("BODY", f) for a in argv]
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli([*argv[:2], "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"), *argv[2:]]
                            if argv[0] == "todo" else
                            [argv[0], "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"), *argv[1:]],
                            runner=self.stub)
        return code, out.getvalue()

    def test_todo_subtasks(self):
        self.assertEqual(self.run_cli("todo", "create", "--key", "k", "--title", "Two things",
                                      "--subtask", "A?", "--subtask", "B?")[0], 0)
        self.assertEqual([q["text"] for q in self.todos()["k"]["subtasks"]], ["A?", "B?"])
        self.assertEqual(self.run_cli("todo", "subtask", "--todo", "k", "--number", "1")[0], 0)
        self.assertTrue(self.todos()["k"]["subtasks"][0]["done"])

    def test_todo_lifecycle(self):
        self.assertEqual(self.run_cli("todo", "create", "--key", "k", "--title", "Merge?", "--body-file", "BODY")[0], 0)
        tid = self.todos()["k"]["todo"]
        self.assertEqual(self.run_cli("todo", "comment", "--todo", "k", "--body-file", "BODY")[0], 0)
        self.assertEqual(self.run_cli("todo", "complete", "--todo", str(tid))[0], 0)
        self.assertIn("completed", self.todos()["k"])
        self.assertEqual(self.run_cli("todo", "complete", "--todo", "missing")[0], 1)
        self.assertIn("FAILED todo complete", open(os.path.join(self.cfgdir, "sync.log")).read())

    def test_post_message_and_behaviors(self):
        self.assertEqual(self.run_cli("post-message", "--subject", "Report", "--body-file", "BODY")[0], 0)
        code, out = self.run_cli("behaviors")
        self.assertEqual(code, 0)
        on = {ln.split()[0]: ln.split()[1] for ln in out.splitlines()}
        self.assertEqual(on, {"card-mirror": "on", "chat-inbox": "off", "chat-watch": "off", "chat-asks": "off", "release-announcements": "off", "security-advisories": "off",
                              "checkin-answering": "off", "decision-todos": "on", "assigned-todos": "off", "assigned-cards": "off", "reports": "on",
                              "pings": "off", "timesheet": "off", "inbox-delivery": "off", "session-alert": "off", "backup": "off", "notifications": "on",
                              "blocked-replies": "off"})


def boost(id, who=CAPTAIN, content="a"):
    return {"id": id, "booster": {"id": who}, "content": content, "created_at": "tb"}


def received(rid, *boosts):
    """The /my/boosts.json items that confirm `boosts` on the agent's own recording `rid`."""
    return [dict(b, recording={"id": rid}) for b in boosts]


class TodoBoosts(ToolBase):
    def setUp(self):
        super().setUp()
        self.tid = self.create()
        self.c = str(self.tid)

    def boost_reads(self):
        return [c[2].split("/")[4] for c in self.stub.calls if c[:2] == ["api", "get"] and c[2].endswith("/boosts.json")
                and c[2] != "/my/boosts.json"]

    def test_owner_boost_on_the_todo_recorded_once(self):
        self.stub.boosts[self.c] = [boost(70, content="<div>yes</div>"), boost(71, who=ACTING)]
        self.poll()
        self.poll()
        [rec] = self.pending()
        self.assertEqual(rec, {"kind": "boost", "surface": "todo", "key": "ta-x", "todo": self.tid, "boost": 70,
                               "recording": self.tid, "text": "yes", "url": f"https://x/todos/{self.tid}", "at": "tb",
                               "author": {"id": CAPTAIN, "name": None}, "captain": True, "role": "captain"})
        self.assertEqual(self.todos()["ta-x"]["boost_seen"], [70])

    def test_owner_boost_on_the_agents_own_comment_is_an_answer(self):
        mine = dict(comment(5, who=ACTING, content="<p>Ready again</p>"), boosts_count=1)
        self.stub.comments[self.c] = [mine]
        self.stub.boosts["5"] = [boost(80)]
        self.stub.my_boosts = received(5, boost(80))
        self.poll()
        self.assertEqual([(r["kind"], r["surface"], r["boost"], r["recording"], r["text"], r["url"]) for r in self.pending()],
                         [("boost", "todo-comment", 80, 5, "a", "https://x/todos/c5")])
        reads = len(self.boost_reads())
        self.poll()  # unchanged boosts_count: the comment's boosts are not read again
        self.assertEqual(self.boost_reads()[reads:], [self.c])
        mine["boosts_count"] = 2
        self.stub.boosts["5"].append(boost(81, content="b"))
        self.stub.my_boosts += received(5, boost(81, content="b"))
        self.poll()
        self.assertEqual([r["boost"] for r in self.pending()], [80, 81])

    def test_dry_run_records_no_boost(self):
        self.stub.boosts[self.c] = [boost(70)]
        self.poll(dry=True)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.todos()["ta-x"]["boost_seen"], [])

    def test_track_skips_existing_boosts(self):
        self.stub.comments["42"] = [dict(comment(3), boosts_count=1)]
        self.stub.boosts.update({"42": [boost(90)], "3": [boost(91)]})
        self.sync().todo_track("old", 42)
        self.poll()
        self.assertEqual([r for r in self.pending() if r["key"] == "old"], [])
        self.stub.boosts["42"].append(boost(92))
        self.poll()
        self.assertEqual([r["boost"] for r in self.pending() if r["key"] == "old"], [92])


class ZoneStub(TodoStub):
    """Answers the captain's person record with a Basecamp time zone, or fails it."""

    def __init__(self, zone="America/Chicago", fail=False):
        super().__init__()
        self.zone, self.fail = zone, fail

    def __call__(self, cmd, **kw):
        if cmd[0] == "basecamp" and "/people/" in " ".join(cmd):
            self.calls.append(cmd[3:-3])
            if self.fail:
                return SimpleNamespace(stdout=json.dumps({"ok": False, "error": "boom"}), stderr="", returncode=1)
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": {"id": CAPTAIN, "time_zone": self.zone}}),
                                   stderr="", returncode=0)
        return super().__call__(cmd, **kw)


class TimeZone(ToolBase):
    def people_calls(self):
        return [c for c in self.stub.calls if "/people/" in " ".join(c)]

    def test_record_times_are_also_written_in_the_captains_basecamp_zone(self):
        self.stub = ZoneStub()
        s = self.sync()
        s.record({"kind": "comment", "at": "2026-10-21T14:14:00Z", "due_on": "2026-10-22"})
        s.record({"kind": "advisory", "created_at": "2026-01-05T14:14:00.000-05:00"})
        a, b = self.pending()
        self.assertEqual(a["at"], "2026-10-21T14:14:00Z")  # the machine time stays UTC as read
        self.assertEqual(a["at_local"], "2026-10-21 09:14 CDT")
        self.assertNotIn("due_on_local", a)
        self.assertEqual(b["created_at_local"], "2026-01-05 13:14 CST")
        self.assertEqual(len(self.people_calls()), 1)  # read once a run

    def test_config_timezone_overrides_the_basecamp_zone(self):
        self.stub = ZoneStub()
        self.cfg(timezone="Europe/London")
        s = self.sync()
        self.assertEqual(s.local_time("2026-10-21T14:14:00Z"), "2026-10-21 15:14 BST")
        self.assertEqual(self.people_calls(), [])

    def test_unreadable_zone_falls_back_to_utc_without_failing_the_run(self):
        for stub in (ZoneStub(fail=True), ZoneStub(zone=None), ZoneStub(zone="Central Time (US & Canada)")):
            self.stub = stub
            s = self.sync()
            self.assertEqual(s.local_time("2026-10-21T14:14:00Z"), "2026-10-21 14:14 UTC")
            self.assertEqual(s.errors, 0)

    def test_unreadable_time_is_left_as_is(self):
        self.stub = ZoneStub()
        self.assertEqual(self.sync().local_time("t0"), "t0")

    def test_config_timezone_must_be_an_iana_name(self):
        self.cfg(timezone="Not/AZone")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.sync()


class Inbox(ToolBase):
    def setUp(self):
        super().setUp()
        self.cfg(chats=[77], checkins={"questionnaires": [55], "timezone": "UTC"})
        self.stub.lines = {"77": [line(1)]}
        self.stub.questions = []
        self.poll()  # cursor set; inbox still off
        self.stub.lines["77"].append(line(2))
        self.poll()  # one record from before the inbox was turned on
        self.cfg(inbox={})

    def test_new_records_become_notes_once(self):
        self.poll()
        self.assertEqual(self.stub.notes, [])  # the record from before is not delivered
        tid = self.create()
        self.stub.lines["77"].append(line(3, content="ship it?"))
        self.stub.comments[str(tid)] = [comment(9, content="<p>Merge it</p>")]
        self.stub.boosts[str(tid)] = [boost(70)]
        self.poll()
        self.poll()
        self.assertEqual([n[0] for n in self.stub.notes],
                         ["basecamp-chat-question-3", "basecamp-todo-comment-9", "basecamp-boost-70"])
        rid, body, fm_home, cmd = self.stub.notes[1]
        self.assertEqual((fm_home, cmd), (self.home, os.path.join(self.home, "bin", "fm-inbox.sh")))
        self.assertEqual(body.splitlines()[:3], ["Basecamp comment from the captain on decision to-do ta-x:",
                                                 "Merge it", "https://x/todos/c9"])
        self.assertIn("drain --ack", body)
        self.assertEqual(json.load(open(os.path.join(self.cfgdir, "inbox.json")))["cursor"], len(self.pending()))

    def test_failed_note_logged_once_and_retried_in_order(self):
        self.poll()
        self.stub.lines["77"] += [line(3), line(4)]
        self.stub.inbox_exit = 3
        self.poll()
        self.poll()
        log = open(os.path.join(self.cfgdir, "sync.log")).read()
        self.assertEqual(log.count("FAILED inbox note basecamp-chat-question-3"), 1)
        self.assertIn("still failing", log)
        self.assertEqual(self.stub.notes, [])
        self.stub.inbox_exit = FileNotFoundError("fm-inbox.sh")
        self.poll()  # a missing fm-inbox.sh never fails the run
        self.stub.inbox_exit = 0
        self.poll()
        self.assertEqual([n[0] for n in self.stub.notes], ["basecamp-chat-question-3", "basecamp-chat-question-4"])
        self.assertEqual(json.load(open(os.path.join(self.cfgdir, "inbox.json")))["failing"], [])

    def test_replay_is_not_a_new_note(self):
        self.poll()
        self.stub.lines["77"].append(line(3))
        self.poll()
        os.remove(os.path.join(self.cfgdir, "inbox.json"))
        state = {"cursor": 0, "failing": []}
        json.dump(state, open(os.path.join(self.cfgdir, "inbox.json"), "w"))
        self.poll()  # every record again: the earlier one is created, the delivered one replays
        self.assertEqual([n[0] for n in self.stub.notes], ["basecamp-chat-question-3", "basecamp-chat-question-2"])

    def test_other_home_dry_run_and_off(self):
        self.cfg(inbox={"fm_home": os.path.join(self.tmp, "main")})
        self.poll(dry=True)
        self.stub.lines["77"].append(line(3))
        self.poll(dry=True)
        self.assertEqual(self.stub.notes, [])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "inbox.json")))
        self.poll()
        self.stub.lines["77"].append(line(4))
        self.poll()
        self.assertEqual([(n[0], n[2]) for n in self.stub.notes],
                         [("basecamp-chat-question-3", os.path.join(self.tmp, "main")),
                          ("basecamp-chat-question-4", os.path.join(self.tmp, "main"))])

    def test_note_for_every_kind(self):
        recs = [{"task": "t", "card": 5, "comment": 9, "text": "ok"}, {"kind": "question", "task": "t", "card": 5, "comment": 10, "text": "why?"},
                {"kind": "approval", "task": "t", "card": 5, "boost": 11, "url": "u"},
                {"kind": "chat-question", "chat": 1, "line": 12, "text": "hi", "url": "u"},
                {"kind": "checkin", "question": 13, "date": "2026-10-02", "title": "Open issues?", "url": "u"},
                {"kind": "todo-comment", "key": "k", "comment": 14, "text": "x", "url": "u"},
                {"kind": "boost", "surface": "todo-comment", "key": "k", "boost": 15, "recording": 3, "text": "a", "url": "u"}]
        ids = [sync.behaviors.inbox_note(r, "1", "2")[0] for r in recs]
        self.assertEqual(ids, ["basecamp-comment-9", "basecamp-question-10", "basecamp-approval-11", "basecamp-chat-question-12",
                               "basecamp-checkin-13-2026-10-02", "basecamp-todo-comment-14", "basecamp-boost-15"])
        _, body = sync.behaviors.inbox_note(recs[1], "1", "2")
        self.assertIn("https://app.basecamp.com/1/buckets/2/card_tables/cards/5", body)
        self.assertIn("sync.py reply --recording 10", body)
        self.assertIn("Open issues?", sync.behaviors.inbox_note(recs[4], "1", "2")[1])
        self.assertIn("a comment on decision to-do k", sync.behaviors.inbox_note(recs[6], "1", "2")[1])


class Boosts(ToolBase):
    """Owner boosts on every monitored surface, each recorded once with its text, recording and surface."""

    def boosts(self):
        return [(r["surface"], r["recording"], r["boost"], r["text"]) for r in self.pending() if r["kind"] == "boost"]

    def test_chat_lines_including_the_agents_own(self):
        self.cfg(chats=[77])
        self.stub.lines = {"77": [line(1)]}
        self.poll()  # seeds the cursor and boost counts
        mine = dict(line(2, who=ACTING, content="Done, see the PR."), boosts_count=1)
        self.stub.lines["77"].append(mine)
        self.stub.boosts["2"] = [boost(60, content="thanks"), boost(61, who=ACTING)]
        self.stub.my_boosts = received(2, boost(60, content="thanks"))
        self.poll()
        self.poll()
        self.assertEqual(self.boosts(), [("chat", 2, 60, "thanks")])
        self.assertEqual(self.pending()[-1]["chat"], 77)

    def test_history_is_seeded_not_replayed(self):
        self.cfg(chats=[77])
        self.stub.lines = {"77": [line(1)]}
        self.poll()
        self.stub.lines["77"][0]["boosts_count"] = 1
        self.stub.boosts["1"] = [boost(60)]
        chats = os.path.join(self.cfgdir, "chats.json")
        state = json.load(open(chats))
        state["77"].pop("boost_counts")  # chats.json from before boosts were read
        json.dump(state, open(chats, "w"))
        self.poll()
        self.assertEqual(self.boosts(), [])
        self.stub.lines["77"][0]["boosts_count"] = 2
        self.stub.boosts["1"].append(boost(62, content="more"))
        self.poll()
        self.assertEqual([b[2] for b in self.boosts()], [60, 62])  # a seeded count cannot tell old boosts apart

    def test_card_boosts_keep_approval_and_carry_text(self):
        self.sync().main([item("a", hold="q", hold_kind="captain")])  # seeds the card
        self.stub.boosts["501"] = [boost(7, content="👍"), boost(8, content="later please")]
        self.stub.comments = {"501": [dict(comment(9, who=ACTING, content="my reply"), boosts_count=1)]}
        self.stub.boosts["9"] = [boost(10, content="ok")]
        self.stub.my_boosts = received(9, boost(10, content="ok"))
        self.sync().main([item("a", hold="q", hold_kind="captain")])
        self.sync().main([item("a", hold="q", hold_kind="captain")])
        approvals = [(r["boost"], r["text"]) for r in self.pending() if r["kind"] == "approval"]
        self.assertEqual(approvals, [(7, "👍")])
        self.assertEqual(sorted(self.boosts()), [("card", 501, 8, "later please"), ("card-comment", 9, 10, "ok")])

    def test_checkin_answers_the_agent_posted(self):
        self.cfg(checkins={"questionnaires": [55], "timezone": "UTC"})
        now = sync.datetime(2026, 10, 2, 9, 30, tzinfo=sync.timezone.utc)
        q = {"id": 1, "title": "Run /stow and report", "answers_count": 2, "paused": True}
        self.stub.questions = [q]
        self.stub.answers["1"] = [{"id": 30, "creator": {"id": ACTING}, "group_on": "2026-10-01", "boosts_count": 0},
                                  {"id": 31, "creator": {"id": CAPTAIN}, "group_on": "2026-10-01", "boosts_count": 1},
                                  {"id": 32, "creator": {"id": ACTING}, "group_on": "2026-09-01", "boosts_count": 1}]

        def run():
            s = self.sync()
            s.today = lambda: now
            s.main([])
        run()  # seeds
        self.stub.answers["1"][0]["boosts_count"] = 1
        self.stub.boosts.update({"30": [boost(40, content="good")], "31": [boost(41)], "32": [boost(42)]})
        self.stub.my_boosts = received(30, boost(40, content="good"))
        run()
        run()
        self.assertEqual(self.boosts(), [("checkin-answer", 30, 40, "good")])
        self.assertEqual(self.pending()[-1]["question"], 1)

    def test_messages_the_agent_posted_and_their_comments(self):
        recent = sync.datetime.now(sync.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        msgs = [{"id": 20, "creator": {"id": ACTING}, "subject": "Report", "created_at": recent, "boosts_count": 0,
                 "comments_count": 1, "app_url": "https://x/m/20"},
                {"id": 21, "creator": {"id": CAPTAIN}, "subject": "His", "created_at": recent, "boosts_count": 1},
                {"id": 22, "creator": {"id": ACTING}, "subject": "Old", "created_at": "2020-01-01T00:00:00Z", "boosts_count": 1}]
        self.stub.boosts["99"] = msgs  # the stub answers the board's messages read from its boosts table
        self.stub.comments["20"] = [dict(comment(25), boosts_count=0)]
        self.poll()  # seeds
        msgs[0]["boosts_count"] = 1
        self.stub.comments["20"][0]["boosts_count"] = 1
        self.stub.boosts.update({"20": [boost(50, content="agree")], "25": [boost(51)], "21": [boost(52)], "22": [boost(53)]})
        self.stub.my_boosts = received(20, boost(50, content="agree"))
        self.poll()
        self.poll()
        self.assertEqual(self.boosts(), [("message", 20, 50, "agree"), ("message-comment", 25, 51, "a")])
        self.assertEqual(self.pending()[0]["subject"], "Report")

    def test_owner_profile_reads_no_agent_posts(self):
        self.stub.me = CAPTAIN
        self.poll()
        self.assertFalse([c for c in self.stub.calls if "/message_boards/" in " ".join(c)])


class MessageComments(ToolBase):
    """The owner's comments on the agent's own recent Message Board posts are relayed like card comments."""

    def setUp(self):
        super().setUp()
        recent = sync.datetime.now(sync.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.msgs = [{"id": 20, "creator": {"id": ACTING}, "subject": "Report: deploy", "created_at": recent,
                      "comments_count": 2, "app_url": "https://x/m/20"},
                     {"id": 21, "creator": {"id": CAPTAIN}, "subject": "His own", "created_at": recent, "comments_count": 1},
                     {"id": 22, "creator": {"id": ACTING}, "subject": "Old", "created_at": "2020-01-01T00:00:00Z", "comments_count": 1}]
        self.stub.boosts["99"] = self.msgs  # the stub answers the board's messages read from its boosts table
        self.stub.comments = {"20": [comment(30, content="<p>Rerun it with the new flag</p>"), comment(31, who=ACTING)],
                              "21": [comment(32)], "22": [comment(33)]}

    def test_feedback_already_on_a_recent_post_is_relayed_once(self):
        self.poll()
        self.poll()
        [rec] = [r for r in self.pending() if r["kind"] == "message-comment"]
        self.assertEqual(rec, {"kind": "message-comment", "message": 20, "subject": "Report: deploy", "comment": 30,
                               "question": False, "url": "https://x/todos/c30", "text": "Rerun it with the new flag", "at": "t",
                               "author": {"id": CAPTAIN, "name": None}, "captain": True, "role": "captain"})
        self.assertEqual(self.stub.posted(), [("30", "👍")])
        self.assertFalse([c for c in self.stub.calls if c[:3] in (["comments", "list", "21"], ["comments", "list", "22"])])

    def test_new_question_gets_eyes_and_reply_comments_on_the_post(self):
        self.poll()
        self.stub.comments["20"].append(comment(40, content="Why only one run?"))
        self.poll()
        self.assertEqual([r["comment"] for r in self.pending() if r["kind"] == "message-comment"], [30, 40])
        self.assertIn(("40", "👀"), self.stub.posted())
        self.assertTrue(self.sync().reply(40, "Because the second one was a dry run."))
        self.assertEqual(self.stub.made("comments create"), [["comments", "create", "20", "-"]])
        self.assertEqual(self.stub.boosts["40"], [])
        self.assertFalse(self.sync().reply(40, "again"))
        self.poll()
        self.assertEqual(len([r for r in self.pending() if r["kind"] == "message-comment"]), 2)

    def test_dry_run_and_owner_profile_record_nothing(self):
        self.poll(dry=True)
        self.assertEqual(self.pending(), [])
        self.assertFalse(os.path.exists(os.path.join(self.cfgdir, "messages.json")))
        self.stub.me = CAPTAIN
        self.poll()
        self.assertEqual(self.pending(), [])

    def test_delivered_to_the_inbox(self):
        self.cfg(inbox={})
        self.poll()
        [(rid, body, _, _)] = self.stub.notes
        self.assertEqual(rid, "basecamp-message-comment-30")
        self.assertEqual(body.splitlines()[:3], ["Basecamp comment from the captain on your message 'Report: deploy':",
                                                 "Rerun it with the new flag", "https://x/todos/c30"])


class ReplyBoost(ToolBase):
    """`reply --boost`: the answer is a boost on the recorded comment or line itself, for every record kind."""

    KINDS = {  # kind: (state file, its content with recording 70 recorded, the bucket the boost goes in)
        "card": ("map.json", {"t1": {"card": 9, "comments": [70]}}, "22222222"),
        "chat": ("chats.json", {"5": {"lines": [70], "bucket": 77}}, "77"),
        "ping": ("pings.json", {"pings": {"6": {"lines": [70], "bucket": 88}}}, "88"),
        "todo": ("todos.json", {"k": {"todo": 11, "comments": [70], "bucket": 55}}, "55"),
        "message": ("messages.json", {"posts": {"20": {"comments": [70]}}}, "22222222"),
        "thread": ("threads.json", {"th": {"thread": 12, "comments": [70], "bucket": 66}}, "66"),
    }

    def recorded(self, kind):
        for other, _, _ in self.KINDS.values():
            if os.path.exists(os.path.join(self.cfgdir, other)):
                os.remove(os.path.join(self.cfgdir, other))
        name, state, bucket = self.KINDS[kind]
        self.side(name, state)
        self.stub.boosts["70"] = [boost(1, who=ACTING, content="\U0001F440")]
        return name, bucket

    def replied(self, name):
        state = json.load(open(os.path.join(self.cfgdir, name)))
        state = state.get("posts") or state.get("pings") or state
        return [r.get("replied") for r in state.values()]

    def test_each_kind_boosts_the_recording_removes_the_eyes_and_records_the_reply(self):
        for kind in self.KINDS:
            with self.subTest(kind):
                self.stub = TodoStub()
                name, bucket = self.recorded(kind)
                self.assertTrue(self.sync().reply(70, boost=" Thanks! "))
                self.assertEqual(self.stub.posted(), [("70", "Thanks!")])
                self.assertEqual([c[2] for c in self.stub.calls if c[:2] == ["api", "post"]],
                                 [f"/buckets/{bucket}/recordings/70/boosts.json"])
                self.assertEqual(self.stub.made("comments create"), [])
                self.assertEqual(self.stub.deletes(), [f"/buckets/{bucket}/boosts/1.json"])
                self.assertEqual([b["content"] for b in self.stub.boosts["70"]], ["Thanks!"])
                self.assertEqual(self.replied(name), [[70]])
                self.assertFalse(self.sync().reply(70, boost="Done"))
                self.assertEqual(len(self.stub.posted()), 1)
                self.assertTrue(self.sync().reply(70, boost="Done", again=True))
                self.assertEqual(self.stub.posted()[1:], [("70", "Done")])

    def test_a_boost_that_is_the_read_acknowledgement_stays_as_the_answer(self):
        for kind, (ack, answer) in itertools.product(self.KINDS, [("\u2705", "\u2705"), ("\U0001F44D\U0001F3FD", "\U0001F44D")]):
            with self.subTest(kind=kind, ack=ack):
                self.stub = TodoStub()
                self.cfg(acknowledge={"read": ack})
                name, _ = self.recorded(kind)
                self.stub.boosts["70"] = [boost(1, who=ACTING, content=ack)]
                self.assertTrue(self.sync().reply(70, boost=answer))
                self.assertEqual(self.stub.deletes(), [])
                self.assertEqual([b["content"] for b in self.stub.boosts["70"]], [ack])
                self.assertEqual(self.replied(name), [[70]])

    def test_text_over_the_limit_or_empty_is_refused_with_no_basecamp_call(self):
        self.recorded("card")
        for text in ("x" * 17, "   "):
            with self.assertRaises(ValueError):
                self.sync().reply(70, boost=text)
        self.assertEqual(self.stub.calls, [])
        self.assertTrue(self.sync().reply(70, boost="y" * 16))

    def test_dry_run_posts_nothing(self):
        for kind in self.KINDS:
            with self.subTest(kind):
                self.stub = TodoStub()
                name, _ = self.recorded(kind)
                self.assertFalse(self.sync(dry=True).reply(70, boost="Yes"))
                self.assertEqual([c for c in self.stub.calls if c[:2] != ["api", "get"]], [])
                self.assertEqual(self.replied(name), [None])

    def test_cli_boost_or_body_file(self):
        self.recorded("chat")
        cli = Cli.run_cli.__get__(self)
        self.assertEqual(cli("reply", "--recording", "70", "--boost", "x" * 17)[0], 1)
        self.assertIn("FAILED reply 70 ValueError", open(os.path.join(self.cfgdir, "sync.log")).read())
        self.assertEqual(self.stub.posted(), [])
        self.assertEqual(cli("reply", "--recording", "70", "--boost", "Thanks")[0], 0)
        self.assertEqual(self.stub.posted(), [("70", "Thanks")])
        self.assertEqual(self.stub.chat_posts(), [])
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
                cli("reply", "--recording", "70", "--boost", "Hi", "--body-file", "BODY")

    def test_cli_unreadable_body_file_is_logged_as_failed(self):
        self.recorded("chat")
        missing = os.path.join(self.tmp, "missing.md")
        self.assertEqual(Cli.run_cli.__get__(self)("reply", "--recording", "70", "--body-file", missing)[0], 1)
        self.assertIn("FAILED reply 70 FileNotFoundError", open(os.path.join(self.cfgdir, "sync.log")).read())
        self.assertEqual(self.stub.chat_posts(), [])


class Layers(unittest.TestCase):
    def test_behaviors_never_call_a_cli_directly(self):
        src = open(os.path.join(ROOT, "behaviors.py")).read()
        for banned in ("subprocess", ".bc(", ".run(", "basecamp\"", "open("):
            self.assertNotIn(banned, src)

    def test_tools_hold_no_behavior_policy(self):
        src = open(os.path.join(ROOT, "tools.py")).read()
        self.assertNotIn("import behaviors", src)
        self.assertNotIn("COLUMNS", src)

    def test_example_config_turns_on_the_same_behaviors_as_before(self):
        s = sync.Sync("/nonexistent", os.path.join(ROOT, "examples", "config.example.json"), runner=Stub())
        self.assertEqual({n for n, b in s.behaviors.items() if b.on},
                         {"card-mirror", "chat-inbox", "chat-asks", "checkin-answering"})
        self.assertEqual(s.behaviors["chat-inbox"].every_line, {"88888888"})

    def test_old_config_runs_the_same_calls(self):
        """A pre-layers config (cards and chats, no todos or message_board) makes no new kind of call."""
        b = Base("run")
        b.setUp()
        try:
            b.stub.comments = {"501": [comment(9)]}
            b.sync().main([item("a", hold="q", hold_kind="captain")])
            kinds = {tuple(c[:2]) for c in b.stub.calls}
            self.assertEqual(kinds, {("cards", "create"), ("comments", "list"), ("api", "get")})
        finally:
            b.tearDown()


if __name__ == "__main__":
    unittest.main()
