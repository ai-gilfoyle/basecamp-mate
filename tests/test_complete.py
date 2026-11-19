"""Tests for writes that cannot fail silently: `sync.py complete` (any to-do, tracked or not, completed and read back),
the read-back after `todo complete`, `reply` and `dismiss`, and the basecamp CLI calls that raise on any answer that is
not ok (an HTTP error, a usage error, empty or unreadable output, a non-zero exit).

The basecamp CLI is stubbed; nothing touches the network.
"""
import io, json, os, sys, unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, line  # noqa: E402
from test_tools import TodoStub, ToolBase  # noqa: E402
import sync  # noqa: E402
from tools import BasecampError  # noqa: E402

BUCKET = 49226851  # another project of the account, as in Gilfoyle's "Decisions for Caleb" list
TODO = 10525772999


class CompleteStub(TodoStub):
    """`todos complete` that answers ok but changes nothing while `ignore_complete` is set, as a silent failure would."""

    def __init__(self):
        super().__init__()
        self.ignore_complete = False

    def __call__(self, cmd, **kw):
        args = cmd[3:-3] if cmd[0] == "basecamp" else []
        core = args[2:] if args[:1] == ["-P"] else args
        if self.ignore_complete and core[:2] == ["todos", "complete"]:
            self.calls.append(core)
            return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)


class Base(ToolBase):
    def setUp(self):
        super().setUp()
        self.stub = CompleteStub()
        self.cfg(drop=("todos",))  # Gilfoyle's home: decision to-dos made outside `todo create`, no "todos" config
        self.stub.todo_records[str(TODO)] = {"id": TODO, "content": "Merge?", "completed": False, "status": "active",
                                             "creator": {"id": ACTING}}  # made by the agent's login

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.cli([*args, "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json")],
                            runner=self.stub)
        return code, out.getvalue()

    def note(self, text="Done: merged https://github.com/o/r/pull/1"):
        p = os.path.join(self.tmp, "note.md")
        with open(p, "w") as f:
            f.write(text)
        return p

    def log(self):
        p = os.path.join(self.cfgdir, "sync.log")
        return open(p).read() if os.path.exists(p) else ""

    def state(self, name):
        p = os.path.join(self.cfgdir, name)
        return json.load(open(p)) if os.path.exists(p) else {}

    def made(self, verb):
        return self.stub.made(verb)


class Complete(Base):
    def test_completes_an_untracked_todo_with_its_comment_and_verifies_it(self):
        code, out = self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note())
        self.assertEqual(code, 0, self.log())
        self.assertIn(f"complete {BUCKET}/{TODO}: completed and verified", out)
        self.assertEqual(self.made("comments create"), [["comments", "create", str(TODO), "-"]])
        self.assertEqual(self.stub.comments[str(TODO)][0]["content"], "Done: merged https://github.com/o/r/pull/1")
        self.assertEqual(self.made("todos complete"), [["todos", "complete", str(TODO)]])
        reads = [i for i, c in enumerate(self.stub.calls) if c == ["api", "get", f"/buckets/{BUCKET}/todos/{TODO}.json"]]
        self.assertGreater(reads[-1], self.stub.calls.index(["todos", "complete", str(TODO)]))  # read back after
        self.assertTrue(self.stub.todo_records[str(TODO)]["completed"])
        rec = self.state("completions.json")[f"{BUCKET}/{TODO}"]
        self.assertTrue(rec["comment"] and rec["completed"])

    def test_a_completion_the_read_back_shows_open_fails_non_zero(self):
        self.stub.ignore_complete = True
        code, out = self.cli("complete", "--todo", f"{BUCKET}/{TODO}")
        self.assertEqual(code, 1)
        self.assertIn(f"FAILED complete {BUCKET}/{TODO} RuntimeError: complete {BUCKET}/{TODO}: Basecamp still shows "
                      f"todo {TODO} open after the completion call", self.log())
        self.assertIn("FAILED", out)  # the agent that ran it sees the failure, not only sync.log
        self.assertNotIn("completed", self.state("completions.json").get(f"{BUCKET}/{TODO}", {}))

    def test_a_retry_after_a_failed_completion_posts_the_comment_once(self):
        self.stub.ignore_complete = True
        self.assertEqual(self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note())[0], 1)
        self.stub.ignore_complete = False
        self.assertEqual(self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note())[0], 0)
        self.assertEqual(len(self.made("comments create")), 1)
        self.assertEqual(len(self.made("todos complete")), 2)

    def test_a_todo_already_completed_gets_no_comment_and_no_write(self):
        self.stub.todo_records[str(TODO)]["completed"] = True
        code, out = self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note())
        self.assertEqual(code, 0)
        self.assertIn("already completed in Basecamp", out)
        self.assertEqual(self.made("comments create") + self.made("todos complete"), [])

    def test_a_todo_neither_tracked_nor_made_by_the_agent_is_refused(self):
        self.stub.todo_records[str(TODO)]["creator"] = {"id": CAPTAIN}
        code, _ = self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note())
        self.assertEqual(code, 1)
        self.assertIn("not created by the agent's login", self.log())
        self.assertEqual(self.made("comments create") + self.made("todos complete"), [])

    def test_a_url_or_a_bare_id_names_the_todo(self):
        s = self.sync()
        self.assertEqual(s.todo_ref(f"https://3.basecamp.com/1111111/buckets/{BUCKET}/todos/{TODO}"), (str(BUCKET), TODO))
        self.assertEqual(s.todo_ref(str(TODO)), ("22222222", TODO))
        for bad in ("abc", f"https://3.basecamp.com/999/buckets/{BUCKET}/todos/{TODO}"):
            with self.assertRaises(ValueError):
                s.todo_ref(bad)

    def test_a_tracked_todo_is_recorded_completed(self):
        self.cfg(todos={"todoset": "66"})
        tid = self.create()
        self.assertEqual(self.cli("complete", "--todo", str(tid))[0], 0)
        self.assertTrue(self.todos()["ta-x"]["completed"])

    def test_a_tracked_todo_recorded_completed_but_open_in_basecamp_is_completed(self):
        self.cfg(todos={"todoset": "66"})
        tid = self.create()
        state = self.todos()
        state["ta-x"]["completed"] = "2026-11-19T10:00:00Z"  # recorded by a completion that silently failed
        json.dump(state, open(os.path.join(self.cfgdir, "todos.json"), "w"))
        self.assertEqual(self.cli("complete", "--todo", str(tid))[0], 0)
        self.assertEqual(self.made("todos complete"), [["todos", "complete", str(tid)]])
        self.assertTrue(self.stub.todo_records[str(tid)]["completed"])

    def test_refused_signed_in_as_anyone_but_the_agent(self):
        self.stub.me = CAPTAIN
        self.assertEqual(self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note())[0], 1)
        self.assertEqual(self.made("comments create") + self.made("todos complete"), [])
        self.assertIn("FAILED wrong login", self.log())

    def test_dry_run_writes_nothing(self):
        code, _ = self.cli("complete", "--todo", f"{BUCKET}/{TODO}", "--comment-file", self.note(), "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.made("comments create") + self.made("todos complete"), [])


class TodoCompleteReadBack(Base):
    def setUp(self):
        super().setUp()
        self.cfg(todos={"todoset": "66"})

    def test_todo_complete_reads_back_and_records_nothing_while_basecamp_shows_it_open(self):
        tid = self.create()
        self.stub.ignore_complete = True
        code, _ = self.cli("todo", "complete", "--todo", "ta-x")
        self.assertEqual(code, 1)
        self.assertIn(f"Basecamp still shows todo {tid} open", self.log())
        self.assertNotIn("completed", self.todos()["ta-x"])
        self.stub.ignore_complete = False
        self.assertEqual(self.cli("todo", "complete", "--todo", "ta-x")[0], 0)  # a retry completes it
        self.assertTrue(self.todos()["ta-x"]["completed"])


class Answer:
    """One basecamp CLI answer: stdout, stderr, exit status."""

    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode

    def __call__(self, cmd, **kw):
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)


class LoudCalls(Base):
    def call(self, answer, *args):
        s = self.sync()
        s.run = answer
        return s.bc(*args)

    def test_an_http_error_raises_with_its_endpoint_code_and_exit(self):
        out = json.dumps({"ok": False, "error": "Resource not found", "code": "not_found", "retryable": False})
        with self.assertRaises(BasecampError) as e:
            self.call(Answer(out, returncode=2), "api", "get", f"/buckets/{BUCKET}/todos/1.json")
        self.assertEqual(str(e.exception), f"basecamp api get /buckets/{BUCKET}/todos/1.json: Resource not found "
                                           "(code not_found, exit 2)")
        self.assertEqual(e.exception.code, "not_found")

    def test_a_usage_error_raises(self):
        # The call that left Gilfoyle's decision to-dos open: `api post` without -d never reaches Basecamp.
        out = json.dumps({"ok": False, "error": "--data required", "code": "usage", "retryable": False})
        with self.assertRaises(BasecampError) as e:
            self.call(Answer(out, returncode=1), "api", "post", f"/buckets/{BUCKET}/todos/{TODO}/completion.json")
        self.assertIn(f"api post /buckets/{BUCKET}/todos/{TODO}/completion.json: --data required (code usage, exit 1)",
                      str(e.exception))

    def test_empty_or_unreadable_output_raises(self):
        for answer, said in ((Answer(""), "empty answer from the CLI"),
                             (Answer("not json", "panic", 2), "no JSON answer from the CLI: not jsonpanic"),
                             (Answer("[1, 2]"), "no JSON answer from the CLI: [1, 2]"),
                             (Answer(json.dumps({"data": {}})), "None")):
            with self.assertRaises(BasecampError) as e:
                self.call(answer, "todos", "complete", str(TODO))
            self.assertIn(said, str(e.exception))

    def test_an_ok_answer_with_a_non_zero_exit_raises(self):
        with self.assertRaises(BasecampError) as e:
            self.call(Answer(json.dumps({"ok": True, "data": None}), "boom", 3), "todos", "complete", str(TODO))
        self.assertIn("the CLI answered ok but exited 3: boom", str(e.exception))
        self.assertIn("exit 3", str(e.exception))


class ReplyReadBack(Base):
    def setUp(self):
        super().setUp()
        self.cfg(chats=[77])
        self.stub.lines = {"77": [line(1)]}
        self.side("chats.json", {"77": {"lines": [1]}})

    def test_a_reply_basecamp_answers_without_an_id_fails(self):
        real = self.stub.__call__

        def no_id(cmd, **kw):
            out = real(cmd, **kw)
            if "post" in cmd and any(str(a).endswith("/chats/77/lines.json") for a in cmd):
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
            return out
        s = self.sync()
        s.run = no_id
        with self.assertRaises(RuntimeError) as e:
            s.reply(1, "Yes.")
        self.assertIn("without an id", str(e.exception))
        self.assertNotIn("replied", self.state("chats.json")["77"])

    def test_a_read_boost_basecamp_still_shows_after_removing_it_fails(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        real = self.stub.__call__

        def keep(cmd, **kw):
            if "delete" in cmd:  # answers ok, but the boost stays
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": None}), stderr="", returncode=0)
            return real(cmd, **kw)
        s = self.sync()
        s.run = keep
        with self.assertRaises(RuntimeError) as e:
            s.remove_eyes(1)
        self.assertIn("still shows 👀 boost 5", str(e.exception))

    def test_a_read_boost_removed_is_read_back_once(self):
        self.stub.boosts["1"] = [{"id": 5, "booster": {"id": ACTING}, "content": "👀"}]
        self.sync().remove_eyes(1)
        self.assertEqual(self.stub.boosts["1"], [])


if __name__ == "__main__":
    unittest.main()
