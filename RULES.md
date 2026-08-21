# Memory rules

Mandatory operational rules for every memory task. Read this file after the
agent root control header is checked and before any scoped memory work.

1. **Check protocol control first.** Read the agent's root
   `memory/MEMORY.md`, resolve its `Structure: ../STRUCTURE.md` pointer, and
   compare the recorded `Structure-Version` with canonical `STRUCTURE.md`.
   If they differ, apply `MIGRATION.md` in order before using or changing
   memory. This version check is mandatory on every memory task.

2. **Read mandatory conventions before acting.** Determine the target half and
   scope, then read every applicable group `MEMORY.md` from `local/` or
   `shared/` through the target group. Apply every **Mandatory convention** in
   that chain before node retrieval or task execution. Do not skip a scoped
   convention because the immediate task appears unrelated.

3. **Keep nodes on-demand.** Read the relevant node-collection `MEMORY.md` and
   only the active nodes the task needs. Do not preload all leaves. A node
   linked from a convention for rationale remains on-demand unless an explicit
   prerequisite requires it.

4. **Keep active memory current.** Active `.md` files contain present truth,
   current decisions, current plans, and current routing only. Do not leave a
   superseded state in the active file merely because it may be useful later.

5. **Run the log trigger before every semantic edit.** Before changing any
   active memory `.md`, determine whether the edit makes existing semantic
   information non-current. If so, preserve that displaced state in the
   same-named `log/<file>.md` counterpart before rewriting the active file.
   Preserve enough context to understand what changed and why; do not make a
   mechanical full snapshot unless that detail is genuinely useful.

6. **Use one history mechanism.** Semantic history belongs only in the paired
   `log/` counterpart. Do not maintain active-body `## Log` chronology,
   `archive/` trees, lifecycle metadata, or competing historical stores.
   Ordinary Git history remains responsible for textual/mechanical revisions.

7. **Logs are deterministic and on-demand.** Every active `.md` has one
   same-named counterpart in the sibling `log/` directory. Logs are historical
   and non-authoritative. Read them only for history, provenance, regressions,
   prior attempts, or when active memory makes that context relevant. Never
   create `log/log/`.

8. **Create and rename pairs together.** When creating an active `.md`, create
   its `log/<same-file>.md` counterpart at the same time. When renaming an
   active file, rename its counterpart too and update affected links. A log may
   remain after its active counterpart is intentionally retired.

9. **Treat `requires_read` as a hard prerequisite.** Before changing a node,
   read the applicable mandatory group chain, its parent collection index, the
   target, and every declared `requires_read` path. An unavailable prerequisite
   blocks the edit. New nodes may use frontmatter only when they actually have
   non-empty prerequisites.

10. **Keep indexes for routing, not truth duplication.** Index entries describe
    what a node contains. Do not copy factual conclusions into indexes where
    they can become stale competing truth.

11. **Classify by structure.** Choose `local/` versus `shared/` first, then the
    narrowest applicable group. Standing behavior belongs in the group's
    **Mandatory conventions**; facts, findings, decisions, rationale, and
    project records belong in on-demand nodes.

12. **Keep mandatory conventions concise and complete.** A convention contains
    the rule, at most a short rationale, and only the execution detail required
    to obey it. Required behavior must not be hidden in an on-demand node.

13. **Respect shared-tree ownership.** Other agents' `local/` trees are
    read-only. Shared-memory edits are made in the one common `.shared/` tree,
    narrowly staged, committed, and pushed to its private remote before the
    turn ends.

14. **Use the structural spec only when needed beyond these rules.**
    `STRUCTURE.md` defines shape and invariants; `MIGRATION.md` handles version
    changes; `changelog.md` explains historical rationale. Do not substitute
    old changelog or log content for current rules or current memory.

---

© 2026 Edrick Sinsuan. Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE](LICENSE).