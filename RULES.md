# Memory rules

Mandatory operational checklist for every memory task. Read the agent root
control header first, then the root `RULES.md` before scoped memory work.

**Compactness is mandatory.** Keep this file short. New or changed rules must
be concise, action-oriented, non-duplicative, and operationally complete.
Consolidate existing rules when possible; put rationale, examples, and history
in `STRUCTURE.md`, `changelog.md`, or on-demand memory instead.

1. **Check protocol control.** Read root `memory/MEMORY.md`, resolve
   `Structure: ../STRUCTURE.md`, and compare `Structure-Version` with canonical
   `STRUCTURE.md`; if stale, apply `MIGRATION.md` in order before memory work.
   At the current version, require a literal, unquoted `Shared:` absolute native
   path with no expansion and a pre-existing physical shared root whose
   `MEMORY.md` provides valid mandatory conventions. Reject a declared terminal
   directory that is itself a symlink, junction, or reparse-point alias;
   ancestor components may resolve normally. Report a missing or invalid
   target; the canonical `.shared/` is a discovery hint only—never create or
   fall back to it, and use no `memory/shared` alias or copy. Then read root
   `RULES.md`.

2. **Apply mandatory scope before nodes.** Always read the declared shared
   root's `MEMORY.md` and mandatory groups at the start of every session and
   task, memory work or not; its conventions bind every agent. Then read every
   applicable group `MEMORY.md` from `memory/local/` or the declared shared
   half-root through the target and obey all **Mandatory conventions**. Nodes
   are on-demand: load only the relevant index, active nodes, and explicitly
   required context.

3. **Keep current truth separate from history.** Active `.md` files and node
   attachments hold only current state. Before every semantic edit, preserve
   any state made non-current in the same-named `log/<file>.md`; logs are
   historical, non-authoritative, and on-demand. Use no `archive/`, active-body
   `## Log`, lifecycle history metadata, or `log/log/`; Git handles mechanical
   edits and tracked attachment bytes.

4. **Maintain node-owned paths.** Create and rename an active `.md` and its
   same-named log counterpart together, updating links on rename. Keep only
   materially significant real files or scripts in an optional sibling
   directory named exactly for the leaf stem; document them in the node and
   move or remove that directory with the leaf. A log may remain after
   retirement.

5. **Honor prerequisites and routing.** `requires_read` is a hard prerequisite;
   unavailable prerequisites block edits, and frontmatter exists only for
   non-empty prerequisites. Indexes describe what nodes contain, not factual
   conclusions that duplicate node truth.

6. **Classify by structure.** Choose private `memory/local/` versus the
   declared shared root, then the narrowest applicable group. Standing behavior
   belongs in concise, operationally complete **Mandatory conventions**;
   facts, decisions, rationale, and project records belong in on-demand nodes.
   Required behavior must never be hidden in a rationale node.

7. **Respect write boundaries.** Other agents' `local/` trees are read-only.
   Shared edits use exactly the root `Shared:` target, are narrowly staged, and
   are committed and pushed to its private remote before the turn ends. A
   subagent reads the same root memory and rules as the parent that spawned it
   but never writes memory or shared content; route any needed addition back to
   the parent instead.

8. **Keep protocol documents focused.** `STRUCTURE.md` defines shape and
   invariants, `MIGRATION.md` handles version changes, and `changelog.md`
   preserves rationale. Do not let historical material override current rules
   or current memory.

9. **Keep this checkout current.** This is convention, not a hook-enforced
   gate. Confirm this checkout sits on the latest `v1.x` tag reachable
   from `origin/main`, and check for stale branches against `origin/main`;
   reconcile anything with unmerged value into `main` first, then drop the
   stale branch. Never push to or merge directly into `main` yourself —
   reconcile through a PR and let the user land it. Iterating may happen in
   an isolated test checkout that isn't the one actually installed; that
   never counts as done on its own — land it on `main`, cut the next
   `v1.x` tag on the merged HEAD, and reinstall the actual host(s)
   from that tag before relying on the change.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE).
