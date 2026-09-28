# Skill eval, iteration 1

Setup: subagents with the skill, dry run (commands were planned but not sent).

| # | Prompt | Behaviour | Pass |
|---|---|---|---|
| 1 | Force-push; ask only affected sessions | Ran `ccm roster`, picked the relevant member by its task, and @'d it with a self-contained first line that asks for a reply only if relevant. Explained why it skipped the others. Suggested `--force-with-lease`. | ✓ |
| 2 | Notify everyone of a gateway restart | Planned a single `ccm broadcast` from inside the session, so the permission mode is verified. First line: sender, event, and "no reply needed". Also planned a follow-up recovery broadcast. | ✓ |
| 3 | Onboard a new machine | Ran the ssh BatchMode check, then `ccm pair`, `ccm doctor` and `ccm roster`, with the expected output for each. Noted the single-hop limit. | ✓ |

This eval found that the roster did not show cwd. `ccm roster` now prints it.
No no-skill baseline was run: without the skill an agent cannot discover `ccm` or the naming scheme, so a baseline would not show anything.
