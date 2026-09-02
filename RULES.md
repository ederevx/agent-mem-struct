# Memory rules

Mandatory operational checklist for every memory task. Read the agent root
control header first, then the root `RULES.md` before scoped memory work.

**Compactness is mandatory.** Keep this file short. New or changed rules must
be concise, action-oriented, non-duplicative, and operationally complete.
Consolidate existing rules when possible; put rationale, examples, and history
in `STRUCTURE.md`, `changelog.md`, or on-demand memory instead.

1. **Check protocol control.** Read root `memory/MEMORY.md`, resolve its
   `Structure: ../STRUCTURE.md`, and compare `Structure-Version` with canonical
   `STRUCTURE.md`. If stale, apply `MIGRATION.md` in order before memory work;
   then read root `RULES.md`.

2. **Apply mandatory scope before nodes.** Always read the shared half —
   `shared/MEMORY.md` and its mandatory groups — at the start of every session
   and task, memory work or not; its conventions bind every agent. Then read
   every applicable group `MEMORY.md` from the `local/` or `shared/` half-root
   through the target and obey all **Mandatory conventions**. Nodes are
   on-demand: load only the relevant index, active nodes, and explicitly
   required context.

3. **Keep current truth separate from history.** Active `.md` files hold only
   current state. Before every semantic edit, preserve any state made
   non-current in the same-named `log/<file>.md`; logs are historical,
   non-authoritative, and on-demand. Use no `archive/`, active-body `## Log`,
   lifecycle history metadata, or `log/log/`; Git handles mechanical edits.

4. **Maintain active/log pairs.** Create and rename an active `.md` and its
   same-named log counterpart together, updating links on rename. A log may
   remain after its active identity is intentionally retired.

5. **Honor prerequisites and routing.** `requires_read` is a hard prerequisite;
   unavailable prerequisites block edits, and frontmatter exists only for
   non-empty prerequisites. Indexes describe what nodes contain, not factual
   conclusions that duplicate node truth.

6. **Classify by structure.** Choose `local/` versus `shared/`, then the
   narrowest applicable group. Standing behavior belongs in concise,
   operationally complete **Mandatory conventions**; facts, decisions,
   rationale, and project records belong in on-demand nodes. Required behavior
   must never be hidden in a rationale node.

7. **Respect write boundaries.** Other agents' `local/` trees are read-only.
   Shared edits use the common `.shared/` tree, are narrowly staged, and are
   committed and pushed to its private remote before the turn ends.

8. **Keep protocol documents focused.** `STRUCTURE.md` defines shape and
   invariants, `MIGRATION.md` handles version changes, and `changelog.md`
   preserves rationale. Do not let historical material override current rules
   or current memory.

9. **Keep this checkout current.** This is convention, not a hook-enforced
   gate. Confirm this checkout sits on the latest `protocol-v*` tag reachable
   from `origin/main`, and check for stale branches against `origin/main`;
   reconcile anything with unmerged value into `main` first, then drop the
   stale branch. Never push to or merge directly into `main` yourself —
   reconcile through a PR and let the user land it. Iterating may happen in
   an isolated test checkout that isn't the one actually installed; that
   never counts as done on its own — land it on `main`, cut the next
   `protocol-v*` tag on the merged HEAD, and reinstall the actual host(s)
   from that tag before relying on the change.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE).