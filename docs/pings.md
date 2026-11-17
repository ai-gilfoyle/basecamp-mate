# Pings

A Ping (a direct message) is a chat in a bucket of its own (a "circle"), not in the
project. With `"pings": {}` (`init --pings`), a new line in a Ping makes its notification
change, and the Ping reader runs for that Ping in the same run. The reader's own sweep
(hourly with notifications) reads `GET /my/readings.json` as the
acting login: its `pings` section lists each recently active Ping (read or unread) with
the bucket and chat ids in its `subscription_url`. Only Pings whose participants or
creator include the owner are read, at most `limit` a run (`{"limit": <n>}`, default 10),
the most recently active first; a quiet Ping drops out of the readings and a new line
brings it back. For each, the chat's lines are read
(`/buckets/<circle>/chats/<chat>/lines.json`) and every new owner line is recorded once
as a `ping` record and acknowledged with a 👀, like a chat question; the owner's boosts
on lines there are `boost` records with surface `ping`. `pings.json` keeps `since` (the
first run) and a line cursor per Ping chat: history from before `since` is never relayed,
while a Ping that starts later is relayed from its first line. The agent answers with
`sync.py reply --recording <line>`, which posts a new line in that Ping (the `basecamp
chat` commands refuse circle buckets, so it posts to the lines endpoint) and removes the
👀, under the same refusal rules as every reply. An `@First.Last` or `@First` in the reply
becomes a real mention, as in a chat reply (see [Safety bounds](safety-bounds.md)). Without a `profile`, or with one that
signs in as the owner, nothing is read: the readings would be the owner's own.
