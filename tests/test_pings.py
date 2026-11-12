"""Tests for the Pings behavior: finding Pings through /my/readings.json, relaying the owner's lines, replying, notifications.

/my/readings.json and the Ping chats are stubbed; nothing touches the network.
"""
import json, os, sys, unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN  # noqa: E402
from test_tools import boost, received  # noqa: E402
from test_notifications import NotifStub, NotifBase, PROJECT  # noqa: E402
import behaviors  # noqa: E402
import sync  # noqa: E402

CIRCLE, CHAT = 55555555, 66666666
OTHER_CIRCLE, OTHER_CHAT = 55555556, 66666667
EYES = "\U0001F440"


def ping_line(id, who=CAPTAIN, content="<p>Can you make a project?</p>", at="2099-01-01T00:00:00.000Z"):
    return {"id": id, "creator": {"id": who}, "content": content, "created_at": at,
            "app_url": f"https://app.basecamp.com/1/circles/{CIRCLE}@{id}", "boosts_count": 0}


def reading(bucket=CIRCLE, chat=CHAT, people=(CAPTAIN,), section="pings", kind="Chat", updated="2099-01-01T00:00:00Z"):
    return {"id": chat + 1000 * (section != "pings"), "unread_at": updated,
            "section": section, "type": kind, "bucket_name": f"Owner + Agent {bucket}",
            "app_url": f"https://app.basecamp.com/1/circles/{bucket}", "updated_at": updated,
            "subscription_url": f"https://3.basecampapi.com/1/buckets/{bucket}/recordings/{chat}/subscription.json",
            "creator": {"id": people[0]}, "participants": [{"id": p} for p in people]}


class PingStub(NotifStub):
    """Adds /my/readings.json; the Ping chats' lines and boosts are the base stub's (keyed by chat and recording id)."""

    def __init__(self):
        super().__init__()
        self.readings = {"reads": [], "unreads": []}

    def __call__(self, cmd, **kw):
        if cmd[0] == "basecamp":
            args = cmd[3:-3]
            core = args[2:] if args[:1] == ["-P"] else args
            if core[:3] == ["api", "get", "/my/readings.json"]:
                self.calls.append(core)
                return SimpleNamespace(stdout=json.dumps({"ok": True, "data": self.readings}), stderr="", returncode=0)
        return super().__call__(cmd, **kw)

    def paths(self, verb="get"):
        return [c[2] for c in self.calls if c[:2] == ["api", verb]]


class PingBase(NotifBase):
    def setUp(self):
        super().setUp()
        self.stub = PingStub()
        self.stub.lines = {"77": [], str(CHAT): [ping_line(1, at="2000-01-01T00:00:00.000Z")]}
        self.stub.readings["unreads"] = [reading()]
        self.cfg(pings={})

    def state(self):
        return json.load(open(os.path.join(self.cfgdir, "pings.json")))

    def say(self, *lines):
        self.stub.lines[str(CHAT)] += list(lines)


class Reader(PingBase):
    def test_owner_line_recorded_once_acknowledged_with_eyes_and_linked(self):
        self.say(ping_line(2))
        self.poll()
        self.poll()
        [rec] = self.pending()
        self.assertEqual({k: rec[k] for k in ("kind", "bucket", "chat", "line", "text", "url")},
                         {"kind": "ping", "bucket": CIRCLE, "chat": CHAT, "line": 2, "text": "Can you make a project?",
                          "url": f"https://app.basecamp.com/1/circles/{CIRCLE}@2"})
        self.assertEqual(self.stub.posted(), [("2", EYES)])
        self.assertIn(f"/buckets/{CIRCLE}/recordings/2/boosts.json", self.stub.paths("post"))
        self.assertEqual(self.state()["pings"][str(CHAT)]["cursor"], 2)

    def test_history_from_before_the_first_run_is_never_relayed(self):
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.state()["pings"][str(CHAT)]["cursor"], 1)
        self.assertIn("since", self.state())

    def test_a_ping_first_seen_later_is_relayed_from_its_first_line(self):
        self.poll()
        self.stub.readings["unreads"].append(reading(OTHER_CIRCLE, OTHER_CHAT))
        self.stub.lines[str(OTHER_CHAT)] = [ping_line(5, content="hello")]
        self.poll()
        self.assertEqual([(r["chat"], r["line"], r["text"]) for r in self.pending()], [(OTHER_CHAT, 5, "hello")])

    def test_other_people_and_the_agents_own_lines_only_move_the_cursor(self):
        self.say(ping_line(2, who=1), ping_line(3, who=ACTING))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.state()["pings"][str(CHAT)]["cursor"], 3)

    def test_only_pings_with_the_owner_are_read(self):
        self.stub.readings = {"reads": [reading(OTHER_CIRCLE, OTHER_CHAT, people=(1,)),
                                        reading(section="chats", kind="Chat::Transcript"),
                                        reading(section="inbox", kind="Reminder")], "unreads": []}
        self.poll()
        self.assertFalse(any(f"/buckets/{OTHER_CIRCLE}/" in p for p in self.stub.paths()))

    def test_reads_at_most_limit_pings_newest_first(self):
        self.cfg(pings={"limit": 1})
        self.stub.readings["reads"] = [reading(OTHER_CIRCLE, OTHER_CHAT, updated="2098-01-01T00:00:00Z")]
        self.stub.lines[str(OTHER_CHAT)] = []
        self.sync().read_pings(1)  # the repair sweep's read
        self.assertEqual([p for p in self.stub.paths() if "/chats/" in p and "/77/" not in p],
                         [f"/buckets/{CIRCLE}/chats/{CHAT}/lines.json"])

    def test_owner_boost_on_a_ping_line_is_a_boost_record(self):
        mine = dict(ping_line(2, who=ACTING, content="Done."), boosts_count=0)
        self.say(mine)
        self.poll()  # seeds the boost counts
        mine["boosts_count"] = 1
        self.stub.boosts["2"] = [boost(42, content="🎉")]
        self.stub.my_boosts = received(2, boost(42, content="🎉"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["surface"], rec["recording"], rec["chat"], rec["text"]), ("boost", "ping", 2, CHAT, "🎉"))
        self.assertIn(f"/buckets/{CIRCLE}/recordings/2/boosts.json", self.stub.paths())

    def test_refused_without_a_profile_or_as_the_owner(self):
        self.say(ping_line(2))
        self.stub.me = CAPTAIN
        self.poll()
        self.cfg(profile=None)
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertNotIn("/my/readings.json", self.stub.paths())

    def test_dry_run_reads_and_records_nothing(self):
        self.poll()
        self.say(ping_line(2))
        self.poll(dry=True)
        self.assertEqual((self.pending(), self.stub.posted()), ([], []))
        self.assertEqual(self.state()["pings"][str(CHAT)]["cursor"], 1)

    def test_off_reads_no_ping(self):
        self.cfg(pings=False)
        self.poll()
        self.stub.readings["unreads"] = [reading(updated="2099-01-02T00:00:00Z")]
        self.say(ping_line(2))
        self.poll()
        self.assertFalse(any(f"/buckets/{CIRCLE}/" in p for p in self.stub.paths()))
        self.assertEqual((self.pending(), self.stub.marked), ([], []))

    def test_malformed_config_refused(self):
        for bad in ([], {"limit": 0}):
            self.cfg(pings=bad)
            with self.assertRaises(ValueError):
                self.sync()


class Reply(PingBase):
    def setUp(self):
        super().setUp()
        self.poll()
        self.say(ping_line(2))
        self.poll()

    def test_reply_posts_a_line_in_the_ping_and_removes_eyes(self):
        self.assertTrue(self.sync().reply(2, "On it.\n\nDone soon."))
        self.assertEqual(self.stub.chat_posts(), [(str(CHAT), "<div>On it.</div><div>Done soon.</div>")])
        post = [c for c in self.stub.calls if c[:2] == ["api", "post"] and "/chats/" in c[2]][0]
        self.assertEqual(post[2], f"/buckets/{CIRCLE}/chats/{CHAT}/lines.json")
        self.assertEqual(json.loads(post[4])["content_type"], "text/html")
        self.assertEqual(self.stub.boosts["2"], [])
        self.assertTrue(any(d.startswith(f"/buckets/{CIRCLE}/boosts/") for d in self.stub.deletes()))
        self.assertEqual(self.state()["pings"][str(CHAT)]["replied"], [2])

    def test_second_reply_needs_again(self):
        self.sync().reply(2, "x")
        self.assertFalse(self.sync().reply(2, "y"))
        self.assertTrue(self.sync().reply(2, "y", again=True))
        self.assertEqual(len(self.stub.chat_posts()), 2)

    def test_refused_as_the_owner_or_in_a_dry_run(self):
        self.assertFalse(self.sync(dry=True).reply(2, "x"))
        self.stub.me = CAPTAIN
        self.assertFalse(self.sync().reply(2, "x"))
        self.assertEqual(self.stub.chat_posts(), [])

    def test_cli_reply(self):
        body = os.path.join(self.tmp, "body.txt")
        open(body, "w").write("Yes.")
        cfg = os.path.join(self.cfgdir, "config.json")
        self.assertEqual(sync.cli(["reply", "--home", self.home, "--config", cfg, "--recording", "2", "--body-file", body],
                                  runner=self.stub), 0)
        self.assertEqual(self.stub.chat_posts(), [(str(CHAT), "<div>Yes.</div>")])


class Notifications(PingBase):
    def setUp(self):
        super().setUp()
        self.poll()  # since, cursors and the notifications seeded
        self.stub.calls.clear()

    def test_a_new_line_runs_that_pings_reader_delivers_and_marks_it_read_in_one_run(self):
        self.cfg(inbox={})
        self.poll()
        self.stub.calls.clear()
        self.stub.readings["unreads"].append(reading(OTHER_CIRCLE, OTHER_CHAT))
        self.stub.lines[str(OTHER_CHAT)] = []
        self.poll()  # a quiet Ping comes into view: seen, nothing to relay
        self.stub.calls.clear()
        self.say(ping_line(2))
        self.stub.readings["unreads"][0] = reading(updated="2099-01-02T00:00:00Z")
        self.stub.marked.clear()
        self.sync().behaviors["notifications"].run()
        self.assertEqual(self.kinds(), ["ping"])
        self.assertEqual([n[0] for n in self.stub.notes], ["basecamp-ping-2"])
        self.assertEqual([p for p in self.stub.paths() if "/chats/" in p], [f"/buckets/{CIRCLE}/chats/{CHAT}/lines.json"])
        self.assertEqual(self.stub.marked, [[str(CHAT)]])

    def test_an_owner_boost_on_the_agents_line_in_a_ping_is_relayed(self):
        mine = dict(ping_line(2, who=ACTING, content="Done."), boosts_count=0)
        self.say(mine)
        self.poll()  # seeds the boost counts
        mine["boosts_count"] = 1
        self.stub.boosts["2"] = [boost(42, content="🎉")]
        self.stub.my_boosts = [{"id": 42, "content": "🎉", "booster": {"id": CAPTAIN}, "created_at": "tb",
                                "recording": {"id": 2, "type": "Chat::Lines::RichText", "app_url": "https://x/c@2",
                                              "bucket": {"id": CIRCLE, "name": "Owner + Agent", "type": "Circle"},
                                              "parent": {"id": CHAT, "type": "Chat::Transcript"}}}]
        self.sync().behaviors["notifications"].run()
        self.assertEqual([(r["kind"], r["surface"], r["chat"]) for r in self.pending()], [("boost", "ping", CHAT)])


class Note(unittest.TestCase):
    def test_ping_and_ping_boost_notes(self):
        rid, body = behaviors.inbox_note({"kind": "ping", "line": 2, "title": "Owner + Agent", "text": "Make a project?",
                                          "url": "https://app.basecamp.com/1/circles/5@2"}, "1", "2")
        self.assertEqual(rid, "basecamp-ping-2")
        self.assertIn("Ping (a direct message) from the captain in 'Owner + Agent'", body)
        self.assertIn("sync.py reply --recording 2", body)
        self.assertIn("https://app.basecamp.com/1/circles/5@2", body)
        _, body = behaviors.inbox_note({"kind": "boost", "surface": "ping", "boost": 9, "recording": 2}, "1", "2")
        self.assertIn("a line in a Ping", body)



class GroupPings(PingBase):
    """A Ping with three or more people is read like a chat: only lines that @mention the agent, or directly follow
    its own line, are relayed."""

    OPERATOR = 7

    def setUp(self):
        super().setUp()
        self.cfg(people=[self.OPERATOR])
        self.stub.readings["unreads"] = [reading(people=(CAPTAIN, self.OPERATOR, 8))]

    def test_a_group_ping_relays_only_lines_for_the_agent(self):
        self.say(ping_line(2, content="<p>Erica, did the import finish?</p>"),
                 ping_line(3, who=self.OPERATOR, content="<p>yes, all done</p>"),
                 ping_line(4, content=f'<p><bc-attachment sgid="sgid-{ACTING}"></bc-attachment> can you check?</p>'),
                 ping_line(5, who=ACTING, content="<p>Checking now.</p>"),
                 ping_line(6, who=self.OPERATOR, content="<p>thanks</p>"),
                 ping_line(7, content="<p>and the logs too</p>"),
                 ping_line(8, content="<p>Gilfoyle, the staging one as well</p>"),  # a name is no @mention
                 ping_line(9, who=1, content="<p>who is this?</p>"))
        self.poll()
        self.assertTrue(self.state()["pings"][str(CHAT)]["group"])
        self.assertEqual([r["line"] for r in self.pending()], [4, 6])
        self.assertIn(f"skipped line 2 by {CAPTAIN} in group Ping {CHAT}", self.log())
        self.assertIn("skipped line 7", self.log())
        self.assertIn("skipped line 8", self.log())
        self.assertFalse([p for p in self.stub.paths("post") if "/chats/" in p])  # no automatic reply to person 1

    def test_a_ping_with_one_person_besides_the_agent_still_relays_every_line(self):
        self.stub.readings["unreads"] = [reading(people=(CAPTAIN,))]
        self.say(ping_line(2, content="<p>Erica, did the import finish?</p>"))
        self.poll()
        self.assertEqual([r["line"] for r in self.pending()], [2])
        self.assertFalse(self.state()["pings"][str(CHAT)]["group"])


class PingAttachments(PingBase):
    def upload(self, id, who=CAPTAIN):
        return dict(ping_line(id, who=who, content=""), type="Chat::Lines::Upload",
                    attachments=[{"filename": "image.png", "url": f"https://x/{id}.png"}])

    def test_an_upload_right_after_the_persons_line_folds_into_its_note(self):
        self.say(ping_line(2), self.upload(3), self.upload(4))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["line"], [a["url"] for a in rec["attachments"]]), (2, ["https://x/3.png", "https://x/4.png"]))
        self.assertEqual(self.stub.posted(), [("2", EYES)])
        _, body = behaviors.inbox_note(rec, "1", str(PROJECT))
        self.assertIn("Attachments: image.png (https://x/3.png); image.png (https://x/4.png)", body)

    def test_an_upload_alone_is_its_own_note_with_its_attachment(self):
        self.say(self.upload(2))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["line"], rec["text"], rec["attachments"]), (2, "", [{"filename": "image.png", "url": "https://x/2.png"}]))


if __name__ == "__main__":
    unittest.main()