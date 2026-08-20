# agent-mem-struct

The shared structural specification for a cross-agent persistent memory tree —
a recursive group `MEMORY.md` / `nodes/` / `submemory/` model, inline mandatory
conventions with scoped inheritance, active-vs-archived knowledge, and a
structure-first leaf model with optional prerequisite frontmatter.

The structural documents have distinct roles:

- `STRUCTURE.md` — the current canonical model.
- `MIGRATION.md` — ordered procedures for upgrading older memory trees.
- `changelog.md` — historical context and rationale for structural changes.

The specification is meant to be read and contributed to by multiple AI coding
agents (currently Claude Code and Codex CLI) that each maintain their own
private memory tree on the same machine while sharing the same structural
protocol and a common shared-memory subtree.

## Using this

Clone the repo and symlink `STRUCTURE.md` into each agent's home and memory
directory, per the "Discoverability" section of the spec. Start with
`STRUCTURE.md`; when its version differs from the agent's applied marker, follow
`MIGRATION.md` before advancing that marker.

## License

Licensed under [Creative Commons Attribution 4.0 International
(CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/) — see
[LICENSE](LICENSE) for the full legal text.

You are free to share and adapt this work for any purpose, including
commercially, as long as you give appropriate credit. Attribution must name
the original author and be preserved in every copy or derivative, including
further edits or forks — do not remove or obscure the copyright notice at the
end of `STRUCTURE.md` when redistributing or building on it.
