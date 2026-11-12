"""Tests for confirming boosts against the agent's received-boosts feed (/my/boosts.json): a boost on the agent's own
recording counts only when this run's fresh read of that feed lists it, and its booster and content come from there.

The basecamp CLI is stubbed; nothing touches the network.
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sync import ACTING, CAPTAIN, Base, item, line  # noqa: E402
from test_tools import boost, received  # noqa: E402
from test_notifications import NotifBase  # noqa: E402

MATE = 12121212  # a listened-to person who is not the captain


def ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Confirmed(NotifBase):
    """Boosts on the agent's own chat line, read by the chat reader when the line's boosts_count changes."""

    def setUp(self):
        super().setUp()
        self.mine = dict(line(2, who=ACTING, content="Done, see the PR."), boosts_count=0)
        self.stub.lines["77"].append(self.mine)
        self.poll()  # seeds the line's boost count

    def boosted(self, *boosts):
        self.mine["boosts_count"] += len(boosts)
        self.stub.boosts.setdefault("2", []).extend(boosts)

    def boosts(self):
        return [(r["boost"], r["author"]["id"], r["text"]) for r in self.pending() if r["kind"] == "boost"]

    def boost_reads(self):
        return [c[2] for c in self.stub.calls if c[:2] == ["api", "get"] and c[2].endswith("/recordings/2/boosts.json")]

    def test_a_listed_boost_is_recorded_with_the_feeds_booster_and_content(self):
        self.boosted(dict(boost(60, content="from the recording"), created_at=ago(5)))
        self.stub.my_boosts = received(2, dict(boost(60, content="thanks"), created_at=ago(5),
                                               booster={"id": CAPTAIN, "name": "Cap"}))
        self.poll()
        self.assertEqual(self.boosts(), [(60, CAPTAIN, "thanks")])
        self.assertEqual(self.pending()[-1]["author"]["name"], "Cap")

    def test_one_feed_read_a_run_serves_notifications_and_every_reader(self):
        self.boosted(dict(boost(60), created_at=ago(5)))
        self.stub.my_boosts = received(2, dict(boost(60), created_at=ago(5)))
        self.stub.calls.clear()
        self.poll()
        self.assertEqual(self.stub.gets().count("/my/boosts.json"), 1)
        self.assertEqual([b[0] for b in self.boosts()], [60])

    def test_an_unlisted_recent_boost_is_retried_until_the_feed_lists_it(self):
        self.boosted(dict(boost(60, content="thanks"), created_at=ago(5)))
        self.poll()  # landed after this run's feed read
        self.assertEqual(self.boosts(), [])
        self.stub.my_boosts = received(2, dict(boost(60, content="thanks"), created_at=ago(5)))
        self.poll()
        self.poll()
        self.assertEqual(self.boosts(), [(60, CAPTAIN, "thanks")])

    def test_an_unlisted_old_boost_is_dropped_logged_and_not_read_again(self):
        self.boosted(dict(boost(60, content="thanks"), created_at=ago(3600)))
        self.poll()
        self.assertEqual(self.boosts(), [])
        self.assertIn("boost 60 on 2 is not among the agent's received boosts; not recorded", self.log())
        reads = len(self.boost_reads())
        self.stub.my_boosts = received(2, dict(boost(60, content="thanks"), created_at=ago(3600)))
        self.poll()
        self.assertEqual((self.boosts(), len(self.boost_reads())), ([], reads))

    def test_a_feed_entry_by_another_booster_or_on_another_recording_confirms_nothing(self):
        self.boosted(dict(boost(60), created_at=ago(3600)), dict(boost(61), created_at=ago(3600)))
        self.stub.my_boosts = received(2, dict(boost(60, who=MATE), created_at=ago(3600))) + received(
            9, dict(boost(61), created_at=ago(3600)))
        self.poll()
        self.assertEqual(self.boosts(), [])

    def test_a_failed_feed_read_retries_the_boost(self):
        self.boosted(dict(boost(60, content="thanks"), created_at=ago(3600)))
        self.stub.fail.add("/my/boosts.json")
        self.poll()
        self.assertEqual(self.boosts(), [])
        self.stub.fail.clear()
        self.stub.my_boosts = received(2, dict(boost(60, content="thanks"), created_at=ago(3600)))
        self.poll()
        self.assertEqual(self.boosts(), [(60, CAPTAIN, "thanks")])

    def test_a_boost_on_someone_elses_recording_is_skipped(self):
        """It is not aimed at the agent (one person's boost on another's line): logged, never recorded."""
        theirs = dict(line(3, content="ship it"), boosts_count=0)
        self.stub.lines["77"].append(theirs)
        self.poll()
        theirs["boosts_count"] = 1
        self.stub.boosts["3"] = [dict(boost(70, content="yes"), created_at=ago(3600))]
        self.poll()
        self.assertEqual([r for r in self.pending() if r["kind"] == "boost"], [])
        self.assertIn("skipped boost 70 by 33333333 on 3: not on the agent's own recording", open(os.path.join(self.cfgdir, "sync.log")).read())


class CardApproval(NotifBase):
    """The captain's 👍 on an assigned card the agent created is an approval only when the feed lists it."""

    WAIT = dict(hold="q", hold_kind="captain")

    def setUp(self):
        super().setUp()
        self.sync().main([item("a", **self.WAIT)])  # creates and seeds card 501
        self.stub.recordings[501] = {"id": 501, "creator": {"id": ACTING}}

    def approvals(self):
        return [r["boost"] for r in self.pending() if r["kind"] == "approval"]

    def test_listed_approval_is_recorded(self):
        self.stub.boosts["501"] = [dict(boost(7, content="👍"), created_at=ago(10))]
        self.stub.my_boosts = received(501, dict(boost(7, content="👍"), created_at=ago(10)))
        self.sync().main([item("a", **self.WAIT)])
        self.assertEqual(self.approvals(), [7])

    def test_unlisted_approval_is_never_recorded(self):
        self.stub.boosts["501"] = [dict(boost(7, content="👍"), created_at=ago(3600))]
        self.sync().main([item("a", **self.WAIT)])
        self.sync().main([item("a", **self.WAIT)])
        self.assertEqual(self.approvals(), [])
        self.assertIn("boost 7 on 501 is not among the agent's received boosts", self.log())


class NoAgent(Base):
    """Without a profile no agent acts and there is no feed: boosts are recorded as read, as before."""

    def test_approval_without_a_feed(self):
        self.stub.boosts = {"501": [boost(7, content="👍")]}
        self.sync().main([item("a", hold="q", hold_kind="captain")])  # creates the card
        self.sync().main([item("a", hold="q", hold_kind="captain")])
        self.assertNotIn(["api", "get", "/my/boosts.json"], self.stub.calls)
        p = os.path.join(self.cfgdir, "pending-comments.jsonl")
        with open(p) as f:
            self.assertIn('"kind": "approval"', f.read())


if __name__ == "__main__":
    unittest.main()
