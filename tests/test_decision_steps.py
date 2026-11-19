"""Tests for decision steps (`sync.py decide`): a decision put on the to-do or card it is about, as a comment there and a
step (a native subtask) assigned to the person who decides, with their comments and their checking of the step relayed,
and the to-do or card itself never completed by the agent.

The basecamp CLI is stubbed; nothing touches the network.
"""
import json, os, sys, unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN  # noqa: E402
from test_tools import comment  # noqa: E402
from test_notifications import PROJECT  # noqa: E402
from test_assigned_todos import OTHER, PARTNER, AssignBase, AssignStub, todo  # noqa: E402
import behaviors  # noqa: E402
import sync  # noqa: E402

OP = 15151515  # an operator


class StepStub(AssignStub):
    """Adds each step's events (/buckets/<b>/recordings/<step>/events.json), from `events` (step id -> events)."""

    def __init__(self):
        super().__init__()
        self.events = {}

    def __call__(self, cmd, **kw):
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            core = args[2:] if args[:1] == ["-P"] else args
            if core[:2] == ["api", "get"] and core[2].endswith("/events.json"):
                self.calls.append(core)
                data = self.events.get(int(core[2].split("/")[4]), [])
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": data}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)


def checked(who, at="2099-01-01T00:00:00Z"):
    return [{"action": "created", "creator": {"id": ACTING}, "created_at": "2098-01-01T00:00:00Z"},
            {"action": "completed", "creator": {"id": who, "name": f"person-{who}"}, "created_at": at}]


class Steps(AssignBase):
    def setUp(self):
        super().setUp()
        self.stub = StepStub()
        self.stub.lines = {"77": []}
        self.cfg(todos={}, assigned_todos=False, operators=[OP], people=[PARTNER])
        self.stub.recordings[70] = {"id": 70, "type": "Todo", "title": "Ship the importer", "app_url": "https://x/todos/70"}
        self.stub.comments["70"] = [comment(5, who=PARTNER, content="<p>old talk</p>")]

    def decide(self, on="70", key="importer", **kw):
        return self.sync(dry=kw.pop("dry", False)).decide(on, "Ship it Friday?", "Evidence: tests pass.", key, **kw)

    def made(self, verb):
        return [(c, p) for c, p in self.stub.on if c[:2] == verb.split()]

    def step(self, key="importer", n=0):
        return self.todos()[key]["subtasks"][n]

    def test_a_comment_there_and_a_step_assigned_to_the_captain_tracked_from_now(self):
        self.assertEqual(self.decide(), "importer")
        [(c, _)] = self.made("comments create")
        self.assertEqual((c[2], self.stub.inputs[-2]), ("70", "Evidence: tests pass."))
        [(s, _)] = self.made("subtasks create")
        self.assertEqual(s[2:], ["70", "Ship it Friday?", "--assignees", str(CAPTAIN)])
        rec = self.todos()["importer"]
        self.assertEqual((rec["on"], rec["title"], rec["subtasks"][0]["decider"]), (True, "Ship the importer", CAPTAIN))
        self.poll()
        self.assertEqual(self.pending(), [])  # neither the old talk nor the agent's own comment is relayed

    def test_the_captains_comment_there_is_relayed_as_on_that_todo(self):
        self.decide()
        self.stub.comments["70"].append(comment(900, content="<p>Yes, Friday.</p>"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["key"], rec["on"]), ("todo-comment", "importer", "Ship the importer"))
        self.assertIn("on 'Ship the importer', where you put a decision step (importer)", behaviors.inbox_note(rec, "1", "2")[1])

    def test_the_decider_checking_the_step_is_recorded_once(self):
        self.decide()
        self.stub.events[self.step()["id"]] = checked(CAPTAIN)
        self.poll()
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["number"], rec["question"], rec["author"]["id"]),
                         ("decision-step-done", 1, "Ship it Friday?", CAPTAIN))
        self.assertTrue(self.step()["done"])
        self.assertIn("decision step 'Ship it Friday?' on 'Ship the importer' (importer) checked by the captain",
                      behaviors.inbox_note(rec, "1", "2")[1])

    def test_the_agent_checking_it_records_nothing(self):
        self.decide()
        self.stub.events[self.step()["id"]] = checked(ACTING)
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertTrue(self.step()["done"])

    def test_completing_the_step_or_the_record_never_completes_the_todo_itself(self):
        self.decide()
        self.assertTrue(self.sync().todo_subtask("importer", 1))
        self.assertTrue(self.todos()["importer"]["completed"])
        self.assertEqual(len(self.made("subtasks complete")), 1)
        self.assertEqual(self.made("todos complete") + self.made("cards done"), [])
        self.decide(key="again")
        self.assertTrue(self.sync().todo_complete("again"))
        self.assertEqual(len(self.made("subtasks complete")), 2)  # its open step, never the to-do
        self.assertEqual(self.made("todos complete"), [])

    def test_on_a_tracked_request_the_step_joins_it_and_never_completes_it(self):
        self.cfg(assigned_todos={})
        self.add(todo(42))
        self.poll()  # request-42
        self.stub.recordings[42] = {"id": 42, "type": "Todo", "title": "Rotate the API keys"}
        self.assertEqual(self.decide(on="42", key=None), "request-42")
        self.assertEqual(self.step("request-42")["decider"], CAPTAIN)
        self.assertTrue(self.sync().todo_subtask("request-42", 1))
        self.assertNotIn("completed", self.todos()["request-42"])

    def request_step(self):
        """A decision step on tracked request-42, completed once and then reopened in Basecamp."""
        self.cfg(assigned_todos={})
        self.add(todo(42))
        self.poll()  # request-42
        self.stub.recordings[42] = {"id": 42, "type": "Todo", "title": "Rotate the API keys"}
        self.decide(on="42", key=None)
        self.assertTrue(self.sync().todo_subtask("request-42", 1))
        self.stub.subtasks_done.clear()  # reopened in Basecamp: the step reads back open
        return len(self.made("subtasks complete"))

    def test_a_step_on_a_closed_request_is_not_completed_again(self):
        before = self.request_step()
        state = self.todos()
        state["request-42"]["closed"] = "unassigned"  # as the reader records it
        json.dump(state, open(os.path.join(self.cfgdir, "todos.json"), "w"))
        self.assertFalse(self.sync().todo_subtask("request-42", 1))
        self.assertEqual(len(self.made("subtasks complete")), before)

    def test_a_step_on_a_request_reassigned_to_someone_else_is_not_completed_again(self):
        before = self.request_step()
        self.edit(42, assignees=[{"id": CAPTAIN, "name": "Caleb"}])
        self.assertFalse(self.sync().todo_subtask("request-42", 1))
        self.assertEqual(len(self.made("subtasks complete")), before)
        self.assertIn("no longer shows the request active and assigned to you",
                      open(os.path.join(self.cfgdir, "sync.log")).read())

    def test_an_operator_may_decide_and_no_one_else(self):
        self.decide(decider=OP)
        self.assertEqual(self.step()["decider"], OP)
        for who in (PARTNER, 99):
            with self.assertRaises(ValueError):
                self.decide(key=f"k{who}", decider=who)

    def test_a_url_names_the_project_and_cards_work_too(self):
        self.stub.recordings[71] = {"id": 71, "type": "Kanban::Card", "title": "Importer card"}
        self.decide(on=f"https://3.basecamp.com/1/buckets/{OTHER}/card_tables/cards/71", key="card")
        rec = self.todos()["card"]
        self.assertEqual((rec["todo"], rec["bucket"], rec["type"]), (71, OTHER, "card"))
        self.assertEqual({p for _, p in self.made("comments create") + self.made("subtasks create")}, {str(OTHER)})

    def test_refusals_and_dry_run(self):
        with self.assertRaises(ValueError):
            self.decide(key=None)  # nothing tracks it yet
        self.stub.recordings[72] = {"id": 72, "type": "Document"}
        with self.assertRaises(ValueError):
            self.decide(on="72", key="doc")
        with self.assertRaises(ValueError):
            self.decide(on="not a link")
        self.assertIsNone(self.decide(dry=True))
        self.assertEqual((self.made("comments create"), self.todos()), ([], {}))

    def test_cli(self):
        with open(os.path.join(self.tmp, "c.md"), "w") as f:
            f.write("Evidence.")
        self.assertEqual(sync.cli(["decide", "--home", self.home, "--config", os.path.join(self.cfgdir, "config.json"),
                                   "--on", "70", "--question", "Ship it?", "--body-file", os.path.join(self.tmp, "c.md"),
                                   "--key", "cli"], runner=self.stub), 0)
        self.assertIn("cli", self.todos())


if __name__ == "__main__":
    unittest.main()
