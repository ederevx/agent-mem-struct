# Changelog

Delta history for `STRUCTURE.md`. Each entry documents *what changed and
why*, so `STRUCTURE.md` itself only needs to describe the current model, not
narrate how it got there.

## 2026-08-18T16:00:00-04:00 — `.shared/` gets a private remote; commit-and-push made mandatory

An audit pass over the freshly-split trees found an edit sitting
uncommitted in `.shared/` from a prior session — the "commit right after
editing" rule existed only in `STRUCTURE.md`'s version-control section,
nowhere an agent actually reads before touching a node, so it went
unfollowed. Also found four references inside shared kernel nodes pointing
at `[[feedback-commit-convention]]` and similar `[[...]]` names that the
split had moved into Claude's private `local/` — unresolvable to any other
agent.

- `.shared/` now has its own **private** remote (previously local-only, no
  remote) — a repository distinct from this one, its location deliberately
  not recorded here since it may carry personal or sensitive content. Every
  edit under `.shared/` must now be committed **and pushed** to that remote
  before the turn ends, not just committed.
- Moved the commit-after-edit rule into `.shared/conventions/memory_change_protocol.md`
  — the file both agents actually read as part of the mandatory node-edit
  protocol — with explicit per-agent trailer guidance and a narrow-staging
  warning (another agent's uncommitted work may already be sitting in the
  same working tree; never `git add -A` blindly).
- Fixed the four broken `[[feedback-commit-convention]]`-family links inside
  shared kernel nodes by replacing the specific cross-boundary link name
  with generic prose ("the agent's own commit-attribution convention") —
  each agent keeps a differently-named copy of that convention in its own
  `local/`, so a shared node can reference the concept but not a specific
  agent's private node.
- One pre-existing dead link, `[[local-coding-model]]` in
  `submemory/msm8998-kernel/nodes/lineage_419_port.md` (`.shared/`, not this
  repo), predates this migration and was left as-is rather than guessed at.

## 2026-08-18T14:10:00-04:00 — local/shared split, drop linking protocol

Replaced the single-tree-with-`visibility`-flags model with a physical
local/shared split, per-agent:

- Every agent's memory root now holds exactly two mandatory child groups:
  `local/` (private, real directory, mutually read-only across agents) and
  `shared/` (a symlink to one common directory, `~/agent-mem-struct/.shared/`,
  that every agent reads and writes directly).
- `.shared/` physically consolidates all content that was previously marked
  `visibility: shared` and duplicated (via linked stubs) across agents'
  trees. Content was migrated mechanically (frontmatter/marker stripping via
  script, no manual retyping) to avoid transcription drift on large files.
  Where both agents had a copy, Claude's side was kept as the source (Codex's
  side was, in every case, already just a linking stub pointing back to it);
  Codex's genuinely original shared nodes were carried over as-is.
- `.shared/` is now its own local-only git repository (`git init`, no
  remote) — it lives inside the `agent-mem-struct` working tree but is
  excluded from that repo via `.gitignore`, since `agent-mem-struct` is
  public and `.shared/` is personal infra/project content.
- Dropped, as made redundant by having one physical shared copy instead of
  N per-agent copies:
  - the `visibility: shared|private` frontmatter field (privacy is now
    structural — which directory a node lives in — not a flag on it);
  - the cross-agent linking protocol (linked stubs, `**Origin:**`/
    `**Linked-modified:**` provenance lines, source-drift detection);
  - numbered marker blocks (`N: ***...***`) used to anchor a linking stub to
    one sub-part of a source node;
  - the `Tree-Version:`/`Tracked-Tree-Version:` handshake used to detect
    newly-added shared nodes across trees needing a link.
- Kept unchanged: the `Structure-Version:` handshake (canonical
  `STRUCTURE.md` timestamp vs. each agent's root `MEMORY.md` first line);
  the recursive group model (`MEMORY.md`/`conventions/`/`nodes/`/
  `submemory/<name>/`); the leaf frontmatter fields `name`, `description`,
  `requires_read`, and `metadata.{node_type, type, originAgent,
  originSessionId, modified}`.
- Added a third per-agent discoverability symlink,
  `<agent-home>/memory/shared` → `~/agent-mem-struct/.shared`, alongside the
  two pre-existing `STRUCTURE.md` symlinks.
