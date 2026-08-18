# Changelog

Delta history for `STRUCTURE.md`. Each entry documents *what changed and
why*, so `STRUCTURE.md` itself only needs to describe the current model, not
narrate how it got there.

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
