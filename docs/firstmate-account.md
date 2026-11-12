# A Basecamp account for firstmate

Run the sync as its own Basecamp user instead of the owner's. Ids below are placeholders.

1. **Invite the user.** In Basecamp, invite a new person for firstmate using an email you
   control. Give it access only to the project(s) being synced. Depending on your plan it
   may count as a paid seat.
2. **Sign in as that user in a private browser window**, so your own session is not used.
3. **Log the CLI in as a separate profile:**
   ```sh
   basecamp auth login -P firstmate --account 1111111 --no-browser
   ```
   Open the printed link in the private window and approve it.
4. **Verify it is the firstmate user, not you:**
   ```sh
   basecamp api get /my/profile.json -P firstmate -a 1111111
   ```
   If it shows you, run `basecamp auth logout -P firstmate` and repeat from step 2.
   Confirm project access:
   ```sh
   basecamp api get /projects/22222222.json -P firstmate -a 1111111
   ```
5. **Token lifetime.** This login's token lasts about an hour. `basecamp auth refresh -P firstmate`
   renews it unattended, and `run.sh` does that when it is within 15 minutes of expiry
   (`sync.py renew`, inside its 240-second cap). That step backs up the saved login
   first and restores it if a renewal loses it; see "Run it once" in the README.
6. **Configure the sync.** Set `"profile": "firstmate"` in the config. Keep `"captain"` as
   your own person id: it is who cards are assigned to and whose comments and 👍 count. The
   firstmate user's own comments and boosts are ignored.
7. **What changes for you.** Card changes show as made by the firstmate user, and you now
   get Basecamp notifications when a card is assigned to you (Basecamp does not notify people
   about assignments they make themselves).
