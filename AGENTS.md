# basecamp-mate

Read these before changing the code:

- `README.md`: what the sync does, its commands, config and tests.
- `VISION.md`: what the project is for and what it does not decide.
- `docs/firstmate-account.md`: how to set up a Basecamp account for the agent.
- `skills/basecamp-sync/SKILL.md`: the skill an agent follows to use the sync.

Run the tests with:

```
python3 -m unittest discover -s tests -v
```

## Rules

- Judge a Basecamp record by its own timestamps, not the time the reader runs.
- To undo something the sync created, use the id it stored, not a text match.
- Treat a not_found from one to-do or card as about that record only, never as a refusal by the whole project.
