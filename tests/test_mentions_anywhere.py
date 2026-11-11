"""Tests for "mentions": {"scope": "account"}: the owner's @mentions of the agent, and comments on threads it follows, in
any project of the account reach it as they would in the configured project, each record carrying its project.

Notifications, comments, chat lines and boosts are stubbed; nothing touches the network.
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, line  # noqa: E402
from test_tools import boost  # noqa: E402
from test_notifications import PROJECT, my_boost, note  # noqa: E402
from test_assigned_todos import AssignBase, PARTNER, STRANGER  # noqa: E402
import behaviors  # noqa: E402
import doctor  # noqa: E402
import runs  # noqa: E402
import tools  # noqa: E402

OTHER = 88888888  # another project of the account
EYES = "\U0001F440"
MENTION = f'<bc-attachment sgid="sgid-{ACTING}" content-type="application/vnd.basecamp.mention"></bc-attachment>'
WHERE = {"id": OTHER, "name": "Bugs", "type": "Project"}


def doc_comment(id, who=CAPTAIN, content=f"<p>{MENTION} tighten the intro</p>", boosts=0, thread=4, ptype="Document", title="Spec"):
    """A comment in project OTHER, shaped like Basecamp's (which names the bucket on every recording)."""
    return {"id": id, "creator": {"id": who, "name": "Cap"}, "content": content, "created_at": "2099-01-01T00:00:01Z",
            "app_url": f"https://x/{thread}#c{id}", "boosts_count": boosts, "bucket": WHERE,
            "parent": {"id": thread, "type": ptype, "title": title, "app_url": f"https://x/{thread}"}}


def chat_line(id, who=CAPTAIN, content=MENTION + " the alerts are firing, can you look?"):
    return dict(line(id, who=who, content=content), created_at="2099-01-01T00:00:01Z", bucket=WHERE)


class Base(AssignBase):
    def setUp(self):
        super().setUp()
        self.cfg(drop=("assigned_todos",), mentions={"scope": "account"}, inbox={})
        self.poll()
        self.stub.calls.clear()
        self.stub.on.clear()


class Threads(Base):
    def test_a_mention_on_a_document_in_another_project_is_a_mention_record_carrying_its_project(self):
        self.stub.comments["4"] = [doc_comment(9, content=MENTION + " can you check this?")]
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents", bucket=OTHER, title="Re: Spec"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual({k: rec[k] for k in ("kind", "bucket", "parent", "parent_type", "title", "comment", "project")},
                         {"kind": "mention", "bucket": OTHER, "parent": 4, "parent_type": "Document", "title": "Spec",
                          "comment": 9, "project": {"id": OTHER, "name": "Bugs"}})
        self.assertEqual(set(self.stub.projects("comments list")), {str(OTHER)})
        self.assertIn(f"/buckets/{OTHER}/recordings/9/boosts.json", self.stub.paths("post"))  # the 👀
        self.assertEqual(self.stub.marked, [["10"]])
        [(rid, body, _, _)] = self.stub.notes
        self.assertEqual(rid, "basecamp-mention-9")
        self.assertIn(f"@mention of you from the captain on Document 'Spec' in project 'Bugs' ({OTHER})", body)
        self.assertTrue(self.sync().reply(9, "Checking."))
        self.assertEqual(self.stub.projects("comments create"), [str(OTHER)])
        self.assertTrue(any(p.startswith(f"/buckets/{OTHER}/boosts/") for p in self.stub.paths("delete")))

    def test_mentions_on_a_card_the_agent_follows_there_are_relayed(self):
        self.stub.comments["600"] = [doc_comment(9, thread=600, ptype="Kanban::Card", title="Alert noise")]
        self.notify(note(20, thread=600, anchor=9, path="cards", bucket=OTHER, title="Re: Alert noise"))
        self.poll()
        self.stub.comments["600"].append(doc_comment(11, content=f"<p>{MENTION} and this one?</p>", thread=600, ptype="Kanban::Card"))
        self.stub.readings["unreads"][0].update(unread_at="2099-01-01T00:00:09Z",
                                                readable_identifier=note(0, anchor=11)["readable_identifier"])
        self.poll()
        self.assertEqual([(r["kind"], r["parent"], r["comment"], r["project"]["id"]) for r in self.pending()],
                         [("mention", 600, 9, OTHER), ("mention", 600, 11, OTHER)])
        self.assertEqual(self.state("threads.json")[f"{OTHER}:600"]["project_name"], "Bugs")

    def test_a_plain_comment_on_a_card_there_is_skipped(self):
        self.stub.comments["600"] = [doc_comment(9, content="<p>see this</p>", thread=600, ptype="Kanban::Card")]
        self.notify(note(20, thread=600, anchor=9, path="cards", bucket=OTHER, title="Re: Alert noise"))
        self.poll()
        self.assertEqual((self.pending(), self.stub.notes), ([], []))
        self.assertEqual(self.stub.marked, [["20"]])

    def test_an_owner_boost_on_the_agents_reply_there_is_relayed(self):
        self.stub.comments["4"] = [doc_comment(9)]
        self.notify(note(10, thread=4, anchor=9, path="documents", bucket=OTHER))
        self.poll()  # the thread is known from here; its boost counts are seeded
        self.stub.comments["4"].append(doc_comment(15, who=ACTING, boosts=1))
        self.stub.boosts["15"] = [boost(90, content="yes")]
        self.stub.my_boosts = [my_boost(90, 15, "Comment", parent={"id": 4, "type": "Document", "title": "Spec"},
                                        bucket=OTHER)]
        self.poll()
        self.assertEqual([(r["kind"], r.get("surface"), r.get("recording")) for r in self.pending()],
                         [("mention", None, None), ("boost", "thread-comment", 15)])

    def test_a_boost_on_a_thread_nothing_followed_there_is_left_alone(self):
        self.stub.my_boosts = [my_boost(81, 31, "Comment", parent={"id": 4, "type": "Document"}, bucket=OTHER)]
        self.poll()
        self.assertEqual(self.pending(), [])


class Chats(Base):
    def test_a_mention_in_another_projects_chat_is_a_chat_question_answered_there(self):
        self.stub.lines["78"] = [line(1), chat_line(3)]
        self.notify(note(10, "Mention", thread=78, anchor=3, path="chats", section="chats", bucket=OTHER))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["chat"], rec["line"], rec["mention"], rec["project"]),
                         ("chat-question", 78, 3, True, {"id": OTHER, "name": "Bugs"}))
        self.assertIn(f"/buckets/{OTHER}/chats/78/lines/3.json", self.stub.paths())
        self.assertIn(f"/buckets/{OTHER}/recordings/3/boosts.json", self.stub.paths("post"))
        self.assertEqual(self.stub.marked, [["10"]])
        [(_, body, _, _)] = self.stub.notes
        self.assertIn(f"chat line from the captain in project 'Bugs' ({OTHER})", body)
        self.assertTrue(self.sync().reply(3, "Looking."))
        self.assertIn(f"/buckets/{OTHER}/chats/78/lines.json", self.stub.paths("post"))
        self.assertEqual(self.stub.projects("api post")[-1], str(OTHER))
        self.assertTrue(any(p.startswith(f"/buckets/{OTHER}/boosts/") for p in self.stub.paths("delete")))

    def test_a_chat_line_there_that_does_not_mention_the_agent_stays_ignored(self):
        self.stub.lines["78"] = [line(1), chat_line(3, content="lunch?")]
        self.notify(note(10, "Chat", thread=78, path="chats", section="chats", bucket=OTHER))
        self.poll()
        self.assertEqual((self.pending(), self.stub.marked), ([], []))
        self.assertFalse([p for p in self.stub.paths() if p.startswith(f"/buckets/{OTHER}/")])


class Access(Base):
    def test_someone_not_listened_to_is_blocked_with_the_automatic_reply_there(self):
        self.cfg(blocked={})
        self.poll()  # starts the clock
        self.stub.comments["4"] = [doc_comment(9, who=STRANGER, content=MENTION + " look")]
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents", bucket=OTHER, who=STRANGER))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.projects("comments create"), [str(OTHER)])
        self.assertEqual(self.stub.inputs[-1], tools.BLOCKED_REPLY)
        self.assertEqual(self.stub.marked, [["10"]])

    def test_named_people_count_everywhere_and_project_people_only_in_their_project(self):
        LOCAL, THERE = 77777777, 99999999
        self.cfg(project_people={str(PROJECT): [LOCAL], str(OTHER): [THERE]})
        self.stub.comments["4"] = [doc_comment(9, who=PARTNER), doc_comment(10, who=LOCAL), doc_comment(11, who=THERE)]
        self.notify(note(20, thread=4, anchor=9, path="documents", bucket=OTHER))
        self.poll()
        self.assertEqual([(r["comment"], r["role"]) for r in self.pending()], [(9, "participant"), (11, "participant")])


class Scope(Base):
    def test_off_by_default_and_with_the_project_scope(self):
        for cfg in ({"drop": ("mentions",)}, {"mentions": {}}, {"mentions": {"scope": "project"}}):
            self.cfg(**cfg)
            self.assertFalse(self.sync().behaviors["notifications"].anywhere)
            self.stub.comments["4"] = [doc_comment(9, content=MENTION + " look")]
            self.stub.lines["78"] = [chat_line(3)]
            self.notify(note(10, "Mention", thread=4, anchor=9, path="documents", bucket=OTHER),
                        note(11, "Mention", thread=78, anchor=3, path="chats", section="chats", bucket=OTHER))
            self.poll()
            self.assertEqual((self.pending(), self.stub.marked), ([], []))
            self.assertEqual(self.stub.projects("comments list"), [])

    def test_malformed_config_refused(self):
        for bad in ([], {"scope": "everywhere"}, {"scope": "account", "limit": 3}):
            self.cfg(mentions=bad)
            with self.assertRaises(ValueError):
                self.sync()

    def test_needs_notifications(self):
        self.cfg(notifications=False)
        self.assertFalse(self.sync().behaviors["notifications"].anywhere)

    def test_the_project_itself_reads_as_before_without_a_project_field(self):
        self.stub.comments["4"] = [dict(doc_comment(9, content=MENTION + " look"), bucket={"id": PROJECT, "name": "Home"})]
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents"))
        self.poll()
        [rec] = self.pending()
        self.assertEqual((rec["kind"], rec["bucket"], "project" in rec), ("mention", PROJECT, False))


class OneHomePerLogin(unittest.TestCase):
    CFG = {"account": 1, "project": 2, "captain": 3, "profile": "agent"}

    def test_the_account_wide_scope_is_a_watch_of_its_own(self):
        self.assertNotIn("mentions 1", runs.watches(self.CFG))
        self.assertNotIn("mentions 1", runs.watches(dict(self.CFG, mentions={"scope": "project"})))
        self.assertIn("mentions 1", runs.watches(dict(self.CFG, mentions={"scope": "account"})))
        a = {"profile": "agent", "watches": runs.watches(dict(self.CFG, mentions={"scope": "account"}))}
        b = {"profile": "agent", "watches": runs.watches(dict(self.CFG, project=9, mentions={"scope": "account"}))}
        self.assertEqual(runs.overlap(a, b), ["mentions 1"])

    def test_doctor_flags_two_homes_taking_mentions_anywhere_with_one_login(self):
        said = []
        d = doctor.Doctor(out=said.append)
        cfg = dict(self.CFG, mentions={"scope": "account"})
        d.overlaps([("/h1", cfg), ("/h2", dict(cfg, project=9))])
        text = "\n".join(said)
        self.assertIn("2 homes take @mentions and followed threads anywhere in account 1 with the sign-in 'agent'", text)
        self.assertIn('keep "mentions": {"scope": "account"} only in the main home', text)
        said.clear()
        d.overlaps([("/h1", cfg), ("/h2", dict(self.CFG, project=9))])
        self.assertNotIn("2 homes take @mentions", "\n".join(said))

    def test_doctor_flags_mentions_anywhere_beside_a_home_reading_its_own_project(self):
        said = []
        d = doctor.Doctor(out=said.append)
        cfg = dict(self.CFG, mentions={"scope": "account"})
        d.overlaps([("/h1", cfg), ("/h2", dict(self.CFG, project=9))])
        text = "\n".join(said)
        self.assertIn("/h1 takes @mentions and followed threads anywhere in account 1 with the sign-in 'agent'", text)
        self.assertIn("would reach both: /h2", text)
        self.assertEqual(d.problems, 1)

    def test_doctor_passes_mentions_anywhere_beside_other_logins_accounts_or_opt_outs(self):
        cfg = dict(self.CFG, mentions={"scope": "account"})
        for other in (dict(self.CFG, project=9, profile="other"), dict(self.CFG, account=5, project=9),
                      dict(self.CFG, project=9, notifications=False)):
            said = []
            d = doctor.Doctor(out=said.append)
            d.overlaps([("/h1", cfg), ("/h2", other)])
            self.assertNotIn("@mentions", "\n".join(said), other)
        said = []
        d = doctor.Doctor(out=said.append)
        d.overlaps([("/h1", dict(cfg, allow_duplicate=True)), ("/h2", dict(self.CFG, project=9, allow_duplicate=True))])
        self.assertNotIn("@mentions", "\n".join(said))


class Note(unittest.TestCase):
    def test_a_record_in_the_configured_project_does_not_name_it(self):
        rec = {"kind": "thread-comment", "comment": 9, "parent_type": "Document", "title": "Spec", "role": "captain"}
        _, body = behaviors.inbox_note(rec, "1", "2")
        self.assertNotIn("in project", body)
        _, body = behaviors.inbox_note(dict(rec, project={"id": 5, "name": "Bugs"}), "1", "2")
        self.assertIn("comment from the captain on Document 'Spec' in project 'Bugs' (5)", body)


if __name__ == "__main__":
    unittest.main()
