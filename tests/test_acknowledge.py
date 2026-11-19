"""Tests for acknowledgements: the config's "acknowledge" "read" boost on everything read (else 👀 on a question and 👍 on
anything else, as before), its "decided" boost on a deciding comment once the decision is carried out, and a boost on a
decision step (a subtask) relayed as that decision's answer.

The basecamp CLI is stubbed; nothing touches the network.
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, line  # noqa: E402
from test_tools import boost, comment, received  # noqa: E402
from test_notifications import NotifBase  # noqa: E402
import behaviors  # noqa: E402
import test_init  # noqa: E402

EYES, THUMBS, CHECK = "\U0001F440", "\U0001F44D", "✅"
MATE = 12121212


class Read(NotifBase):
    def test_without_the_setting_a_question_gets_eyes_and_anything_else_a_thumbs_up(self):
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9, content="<p>Merge it</p>"), comment(10, content="<p>why?</p>")]
        self.poll()
        self.assertEqual(sorted(self.stub.posted()), [("10", EYES), ("9", THUMBS)])

    def test_with_it_everything_read_gets_that_boost(self):
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9, content="<p>Merge it</p>")]
        self.stub.lines["77"].append(line(2, content="noted, thanks"))
        self.cfg(acknowledge={"read": EYES}, chats=[{"chat": 77, "every_line": True}])
        self.poll()
        self.assertEqual(sorted(self.stub.posted()), [("2", EYES), ("9", EYES)])

    def test_reply_takes_the_configured_read_boost_off(self):
        self.cfg(acknowledge={"read": CHECK})
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9, content="<p>why?</p>")]
        self.poll()
        self.assertEqual(self.stub.posted(), [("9", CHECK)])
        self.assertTrue(self.sync().reply(9, "Because."))
        self.assertEqual(len(self.stub.deletes()), 1)

    def test_malformed_config_refused(self):
        for bad in ({"read": ""}, {"seen": EYES}, {"decided": 1}, EYES):
            self.cfg(acknowledge=bad)
            with self.assertRaises(ValueError):
                self.sync()


class Decided(NotifBase):
    def setUp(self):
        super().setUp()
        self.cfg(acknowledge={"read": EYES, "decided": THUMBS}, people=[MATE])

    def test_completing_the_decision_thumbs_up_the_deciding_comment_and_takes_the_eyes_off(self):
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9, content="<p>Merge it</p>"), comment(10, who=MATE, content="<p>+1</p>")]
        self.poll()
        self.assertEqual(sorted(self.stub.posted()), [("10", EYES), ("9", EYES)])
        self.assertTrue(self.sync().todo_complete("ta-x"))
        self.assertIn(("9", THUMBS), self.stub.posted())
        self.assertNotIn(("10", THUMBS), self.stub.posted())  # a participant's word decides nothing
        self.assertEqual(len(self.stub.deletes()), 1)  # the 👀 on 9
        posted = len(self.stub.posted())
        self.assertTrue(self.sync().todo_complete("ta-x"))  # Basecamp shows it completed: nothing written
        self.assertEqual(len(self.stub.posted()), posted)

    def test_answering_one_step_acknowledges_what_decided_it(self):
        tid = self.create(subtasks=["Merge?", "Release?"])
        self.stub.comments[str(tid)] = [comment(9, content="<p>Merge yes</p>")]
        self.poll()
        self.assertTrue(self.sync().todo_subtask("ta-x", 1))
        self.assertIn(("9", THUMBS), self.stub.posted())
        self.assertNotIn("completed", self.todos()["ta-x"])

    def test_without_decided_nothing_is_added_after_deciding(self):
        self.cfg(acknowledge={"read": EYES})
        tid = self.create()
        self.stub.comments[str(tid)] = [comment(9, content="<p>Merge it</p>")]
        self.poll()
        self.sync().todo_complete("ta-x")
        self.assertEqual(self.stub.posted(), [("9", EYES)])


class StepBoosts(NotifBase):
    def test_a_boost_on_a_decision_step_is_its_answer(self):
        tid = self.create(subtasks=["Merge?", "Release?"])
        step = self.todos()["ta-x"]["subtasks"][0]["id"]
        self.stub.recordings[step] = {"id": step, "type": "Todo", "creator": {"id": ACTING}}
        b = boost(70, content="yes")
        self.stub.boosts[str(step)] = [b]
        self.stub.my_boosts = received(step, b)
        self.poll()
        [rec] = [r for r in self.pending() if r["kind"] == "boost"]
        self.assertEqual((rec["surface"], rec["step"], rec["recording"], rec["text"]), ("subtask", 1, step, "yes"))
        body = behaviors.inbox_note(rec, "1", "2")[1]
        self.assertIn("on step 1 ('Merge?') of decision to-do ta-x", body)
        self.assertIn("a boost on a decision step is the answer to that decision", body)
        self.assertIn("sync.py todo subtask --todo ta-x --number 1", body)
        self.poll()
        self.assertEqual(len([r for r in self.pending() if r["kind"] == "boost"]), 1)


class Init(unittest.TestCase):
    setUp, tearDown, init = test_init.InitTest.setUp, test_init.InitTest.tearDown, test_init.InitTest.init

    def test_a_new_home_gets_eyes_and_thumbs_and_an_existing_one_keeps_its_own(self):
        self.assertEqual(self.init().discover()[0]["acknowledge"], {"read": EYES, "decided": THUMBS})
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            f.write('{"account": "1111111", "project": "22222222", "captain": %d}' % CAPTAIN)
        self.assertNotIn("acknowledge", self.init().discover()[0])


if __name__ == "__main__":
    unittest.main()
