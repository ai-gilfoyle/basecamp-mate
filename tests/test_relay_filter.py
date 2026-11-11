"""Tests for what reaches the agent from a thread it only follows: an @mention or a Basecamp Reply to it, and the next
comment after its `reply --awaiting`; everything else there is skipped, and so is an emoji-only boost there.

The example comments are made up; every Basecamp call is stubbed.
"""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, line  # noqa: E402
from test_tools import boost  # noqa: E402
from test_notifications import EYES, PROJECT, NotifBase, my_boost, note  # noqa: E402
import behaviors  # noqa: E402
import sync  # noqa: E402
import tools  # noqa: E402

THUMBS = "\U0001F44D"


def doc(id, who=CAPTAIN, content="<p>status: the import ran</p>", boosts=0, at=None):
    """A comment on document 4."""
    return {"id": id, "creator": {"id": who, "name": {CAPTAIN: "Cap", ACTING: "Agent"}.get(who, "Pat")}, "content": content,
            "created_at": at or f"2099-01-01T00:{id % 60:02d}:00Z", "app_url": f"https://x/docs/4#c{id}",
            "boosts_count": boosts, "parent": {"id": 4, "type": "Document", "title": "Roadmap", "app_url": "https://x/docs/4"}}


class Base(NotifBase):
    def threads(self):
        return self.state("threads.json")

    def mention_thread(self):
        """Document 4 known to the thread reader: the captain's Reply to the agent (a Mention notification) relayed."""
        self.stub.comments["4"] = [doc(9, content="<p>which import do you mean</p>")]
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents", title="Re: Roadmap"))
        self.poll()
        self.assertEqual([(r["kind"], r["comment"]) for r in self.pending()], [("mention", 9)])

    def comment_later(self, id, n, **kw):
        """Comment `id` on document 4, announced by a plain comment notification `n` seconds into the hour."""
        self.stub.comments["4"].append(doc(id, **kw))
        self.notify(note(10, thread=4, anchor=id, path="documents", at=f"2099-01-01T01:00:{n:02d}Z", title="Re: Roadmap"))
        self.poll()


class Comments(Base):
    def test_a_plain_comment_on_someone_elses_thread_is_skipped_with_one_log_line(self):
        self.stub.comments["4"] = [doc(9)]
        self.notify(note(10, thread=4, anchor=9, path="documents"))
        self.poll()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.stub.posted(), [])  # no 👀 or 👍 on it
        self.assertEqual(self.stub.marked, [["10"]])
        self.assertEqual(self.threads()[f"{PROJECT}:4"]["cursor"], 9)
        self.assertEqual(self.log().count("skipped comment 9 by Cap on Document 4"), 1)

    def test_a_comment_that_at_mentions_someone_else_is_skipped(self):
        self.stub.comments["4"] = [doc(9, content='<p><bc-attachment sgid="sgid-pat"></bc-attachment> is it okay to merge?</p>')]
        self.notify(note(10, thread=4, anchor=9, path="documents"))
        self.poll()
        self.assertEqual(self.pending(), [])

    def test_an_at_mention_of_the_agent_is_relayed(self):
        self.stub.comments["4"] = [doc(9, content=f'<p><bc-attachment sgid="sgid-{ACTING}"></bc-attachment> look</p>')]
        self.notify(note(10, thread=4, anchor=9, path="documents"))
        self.poll()
        self.assertEqual([(r["kind"], r["comment"]) for r in self.pending()], [("mention", 9)])
        self.assertEqual(self.stub.posted(), [("9", EYES)])

    def test_a_reply_to_the_agent_without_an_at_mention_is_relayed_as_its_mention_notification_says(self):
        self.mention_thread()
        self.assertEqual(self.stub.posted(), [("9", EYES)])

    def test_a_reply_read_before_its_mention_notification_is_recorded_when_that_arrives(self):
        """Regression: a Reply landing between the notifications read and the comments read was skipped as a plain
        comment and passed by the cursor, so its Mention notification on the next run found nothing new."""
        self.mention_thread()
        self.stub.comments["4"] += [doc(2001), doc(2002, content="<p>the nightly one</p>")]
        self.notify(note(11, thread=4, anchor=2001, path="documents", at="2099-01-01T01:00:01Z", title="Re: Roadmap"))
        self.poll()
        self.assertIn("skipped comment 2002", self.log())
        self.assertEqual(self.threads()[f"{PROJECT}:4"]["cursor"], 2002)
        self.notify(note(12, "Mention", thread=4, anchor=2002, path="documents", at="2099-01-01T01:00:02Z",
                         title="Re: Roadmap"))
        self.poll()
        self.poll()
        self.assertEqual([(r["kind"], r["comment"]) for r in self.pending()], [("mention", 9), ("mention", 2002)])
        self.assertEqual(self.stub.posted(), [("9", EYES), ("2002", EYES)])

    def test_a_late_mention_already_recorded_is_not_recorded_again(self):
        self.mention_thread()
        self.notify(note(10, "Mention", thread=4, anchor=9, path="documents", at="2099-01-01T01:00:01Z", title="Re: Roadmap"))
        self.poll()
        self.assertEqual([r["comment"] for r in self.pending()], [9])

    def test_the_dry_run_logs_the_skip_and_records_nothing(self):
        self.stub.comments["4"] = [doc(9)]
        self.notify(note(10, thread=4, anchor=9, path="documents"))
        self.poll(dry=True)
        self.assertEqual((self.pending(), self.threads()), ([], {}))
        self.assertIn("dry skipped comment 9 by Cap on Document 4", self.log())


class Awaiting(Base):
    def comment_later(self, id, n, **kw):
        """As Base's, the comment dated now (within the mark) unless `at` says otherwise."""
        super().comment_later(id, n, **{"at": tools.now_iso(), **kw})

    def ask(self, **kw):
        """The agent answers the captain's Reply 9 with a question, awaiting the answer; returns its comment's id."""
        self.mention_thread()
        self.assertTrue(self.sync().reply(9, "The nightly one or the manual one?", awaiting=True, **kw))
        mine = self.stub.comments["4"][-1]["id"] if "boost" not in kw else None
        if mine is not None:
            self.stub.comments["4"][-1].update(doc(mine, who=ACTING, content="<p>The nightly one or the manual one?</p>"))
        return mine

    def test_the_next_comment_from_someone_else_is_relayed_once_then_the_mark_clears(self):
        mine = self.ask()
        mark = self.threads()[f"{PROJECT}:4"]["awaiting"]
        self.assertEqual(mark["comment"], mine)
        self.assertEqual(tools.history(mark["at"], mark["until"], slack=7 * 86400 - 5), True)  # 7 days later
        self.assertIn("reply 9: Document 4 awaits an answer until", self.log())
        self.comment_later(2001, 1, content="<p>the nightly one</p>")
        self.comment_later(2002, 2, content="<p>thanks all</p>")
        self.assertEqual([(r["kind"], r["comment"], r.get("why")) for r in self.pending()],
                         [("mention", 9, None), ("thread-comment", 2001, "awaiting")])
        self.assertNotIn("awaiting", self.threads()[f"{PROJECT}:4"])
        self.assertIn("thread 4: comment 2001 is the answer awaited there; the mark is cleared", self.log())
        self.assertIn("skipped comment 2002", self.log())
        _, body = behaviors.inbox_note(self.pending()[1], "1", str(PROJECT))
        self.assertIn("the first comment there after your reply that awaited an answer (reply --awaiting)", body)

    def test_a_mention_while_awaiting_is_the_answer_too(self):
        self.ask()
        self.comment_later(2001, 1, content=f'<p><bc-attachment sgid="sgid-{ACTING}"></bc-attachment> nightly</p>')
        self.comment_later(2002, 2)
        self.assertEqual([(r["kind"], r["comment"]) for r in self.pending()], [("mention", 9), ("mention", 2001)])
        self.assertNotIn("awaiting", self.threads()[f"{PROJECT}:4"])

    def test_the_agents_own_comment_and_someone_not_listened_to_leave_the_mark(self):
        self.ask()
        self.comment_later(2001, 1, who=ACTING, content="<p>to add: I mean the importer</p>")
        self.comment_later(2002, 2, who=1)
        self.assertEqual(len(self.pending()), 1)
        self.assertIn("awaiting", self.threads()[f"{PROJECT}:4"])
        self.comment_later(2003, 3)
        self.assertEqual([r["comment"] for r in self.pending()], [9, 2003])

    def test_the_mark_expires_after_its_days(self):
        self.ask()
        state = self.threads()
        state[f"{PROJECT}:4"]["awaiting"]["until"] = "2000-01-01T00:00:00Z"
        with open(os.path.join(self.cfgdir, "threads.json"), "w") as f:
            json.dump(state, f)
        self.comment_later(2001, 1)
        self.assertEqual(len(self.pending()), 1)
        self.assertNotIn("awaiting", self.threads()[f"{PROJECT}:4"])
        self.assertIn("did not come by 2000-01-01T00:00:00Z; the mark is cleared", self.log())

    def test_an_answer_posted_before_the_end_is_relayed_when_read_after_it(self):
        mine = self.ask()
        state = self.threads()
        state[f"{PROJECT}:4"]["awaiting"]["until"] = "2026-01-01T00:00:00Z"  # passed by the time the reader runs
        with open(os.path.join(self.cfgdir, "threads.json"), "w") as f:
            json.dump(state, f)
        self.assertLess(mine, 2001)
        self.comment_later(2001, 1, at="2025-12-31T23:59:00Z")
        self.assertEqual([(r["comment"], r.get("why")) for r in self.pending()], [(9, None), (2001, "awaiting")])
        self.assertNotIn("awaiting", self.threads()[f"{PROJECT}:4"])
        self.assertNotIn("did not come by", self.log())

    def test_a_boost_answer_awaits_the_next_comment_after_it(self):
        self.assertIsNone(self.ask(boost="which one?"))
        self.assertIsNone(self.threads()[f"{PROJECT}:4"]["awaiting"]["comment"])
        self.comment_later(2001, 1, at="2000-01-01T00:00:00Z")  # dated before the boost: not its answer
        self.comment_later(2002, 2)
        self.assertEqual([r["comment"] for r in self.pending()], [9, 2002])

    def test_dry_run_marks_nothing(self):
        self.mention_thread()
        self.assertFalse(self.sync(dry=True).reply(9, "Which one?", awaiting=True))
        self.assertNotIn("awaiting", self.threads()[f"{PROJECT}:4"])
        self.assertIn("mark it awaiting an answer for 7 days", self.log())

    def test_awaiting_anywhere_but_a_followed_thread_is_ignored(self):
        self.stub.lines["77"].append(line(2, content="ship it?"))
        self.notify(note(10, "Chat", thread=77, path="chats", section="chats"))
        self.poll()
        self.assertTrue(self.sync().reply(2, "Shipping.", awaiting=True))
        self.assertIn("reply 2: --awaiting ignored: it marks only a followed thread", self.log())
        self.assertEqual([c for c, _ in self.stub.chat_posts()], ["77"])

    def test_cli(self):
        self.mention_thread()
        cfg, body = os.path.join(self.cfgdir, "config.json"), os.path.join(self.tmp, "body.txt")
        open(body, "w").write("Which one?")
        base = ["reply", "--home", self.home, "--config", cfg, "--recording", "9", "--body-file", body]
        with self.assertRaises(SystemExit):
            sync.cli(base + ["--awaiting-days", "3"], runner=self.stub)
        self.assertEqual(sync.cli(base, runner=self.stub), 0)
        self.assertNotIn("awaiting", self.threads()[f"{PROJECT}:4"])
        self.assertEqual(sync.cli(base + ["--again", "--awaiting"], runner=self.stub), 0)
        mark = self.threads()[f"{PROJECT}:4"]["awaiting"]
        self.assertTrue(tools.history(mark["at"], mark["until"], slack=7 * 86400 - 5))
        self.assertFalse(tools.history(mark["at"], mark["until"], slack=7 * 86400 + 5))


class Boosts(Base):
    def boosted(self, rid, bid, content, listed=None):
        """The captain boosts the agent's comment `rid` on document 4 with `content`, which the agent's received boosts
        list as `listed` (default the same)."""
        c = next(c for c in self.stub.comments["4"] if c["id"] == rid)
        c["boosts_count"] = c.get("boosts_count", 0) + 1
        self.stub.boosts.setdefault(str(rid), []).append(boost(bid, content=content))
        self.stub.my_boosts.append(my_boost(bid, rid, "Comment", parent={"id": 4, "type": "Document", "title": "Roadmap"},
                                            content=content if listed is None else listed))
        self.poll()

    def answered(self):
        """Document 4 with the agent's plain answer 1500 to the captain's Reply 9 (no answer awaited)."""
        self.mention_thread()
        self.comment_later(1500, 1, who=ACTING, content="<p>Done.</p>")

    def boosts(self):
        return [(r["recording"], r["text"]) for r in self.pending() if r["kind"] == "boost"]

    def test_an_emoji_only_boost_on_the_agents_comment_is_skipped_and_a_worded_one_relayed(self):
        self.answered()
        self.boosted(1500, 90, THUMBS)
        self.boosted(1500, 91, "<div>❤️\U0001F389</div>")
        self.boosted(1500, 92, "yes")
        self.boosted(1500, 93, "1️⃣")
        self.boosted(1500, 94, "?")
        self.boosted(1500, 95, "\u203c\ufe0f")
        self.boosted(1500, 96, "\u2713")
        self.assertEqual(self.boosts(), [(1500, "yes"), (1500, "?"), (1500, "\u2713")])
        self.assertIn("skipped boost 90 by Cap on thread-comment 1500: emoji only", self.log())
        self.assertIn("skipped boost 91", self.log())
        self.assertIn("skipped boost 93", self.log())
        self.assertIn("skipped boost 95", self.log())
        self.assertEqual(self.state()["boosts"], [90, 91, 92, 93, 94, 95, 96])  # all handled, none replayed

    def test_an_emoji_only_boost_on_the_comment_that_awaits_an_answer_is_skipped_too(self):
        self.mention_thread()
        self.assertTrue(self.sync().reply(9, "Nightly?", awaiting=True))
        mine = self.stub.comments["4"][-1]["id"]
        self.stub.comments["4"][-1].update(doc(mine, who=ACTING, content="<p>Nightly?</p>"))
        self.boosted(mine, 90, THUMBS)
        self.assertEqual(self.boosts(), [])
        self.assertIn("awaiting", self.threads()[f"{PROJECT}:4"])

    def test_the_skip_is_judged_on_the_text_the_received_boosts_confirm(self):
        """Regression: the skip was decided on the recording's boost text before the received boosts replaced it."""
        self.answered()
        self.boosted(1500, 90, "thanks", listed=THUMBS)
        self.boosted(1500, 91, THUMBS, listed="yes")
        self.assertEqual(self.boosts(), [(1500, "yes")])
        self.assertIn("skipped boost 90", self.log())
        self.assertEqual(self.state()["boosts"], [90, 91])

    def test_an_emoji_only_boost_elsewhere_is_unchanged(self):
        self.stub.lines["77"][0]["boosts_count"] = 1
        self.stub.boosts["1"] = [boost(90, content=THUMBS)]
        self.stub.my_boosts = [my_boost(90, 1, parent={"id": 77, "type": "Chat::Transcript"}, content=THUMBS)]
        self.poll()
        self.assertEqual([(r["kind"], r["surface"], r["text"]) for r in self.pending()], [("boost", "chat", THUMBS)])


class EmojiOnly(unittest.TestCase):
    def test_emoji_and_whitespace_only(self):
        for yes in (THUMBS, "\U0001F44D\U0001F3FD", "❤️", "<div>\U0001F389 \U0001F389</div>", "1️⃣", "#\u20e3",
                    "\U0001F1FA\U0001F1F8", "\U0001F468\u200d\U0001F4BB", "\u203c\ufe0f", "\u2194\ufe0f", "\u2714\ufe0f", "©",
                    "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"):
            self.assertTrue(tools.emoji_only(yes), yes)
        for no in ("yes", "+1", "a", "1", "Done ✅", "?", "!!", "\u2713", "\u2192", "\u00b0", "\u20e3", "1\ufe0f",
                   "\U0001F44D!", "", None, "<div></div>", "<div> </div>"):
            self.assertFalse(tools.emoji_only(no), no)


if __name__ == "__main__":
    unittest.main()
