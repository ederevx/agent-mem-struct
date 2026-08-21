# agent-mem-struct

Shared structural specification for a cross-agent persistent memory tree with
mandatory scoped conventions, on-demand nodes, deterministic per-file semantic
logs, and a structure-first leaf model.

The protocol documents have distinct roles:

- `RULES.md` — mandatory operational rules for every memory task.
- `STRUCTURE.md` — the current canonical tree/model.
- `MIGRATION.md` — ordered procedures for upgrading older memory trees.
- `changelog.md` — historical context and rationale for protocol changes.

The specification is designed for multiple AI coding agents that keep separate
private memory while sharing one common-memory subtree.

## Using this

Clone the repository and create one structural-document symlink at each agent
root:

```sh
ln -sf ~/agent-mem-struct/STRUCTURE.md <agent-home>/STRUCTURE.md
```

The agent's root `memory/MEMORY.md` points to `../STRUCTURE.md`, records the
applied `Structure-Version`, and is checked on every memory task. The agent then
reads `RULES.md` from beside the resolved canonical structure before scoped
memory work. If the version is stale, follow `MIGRATION.md` first.

The separate `memory/shared` symlink is part of shared-memory data topology, not
a duplicate structural-document link.

## License

Licensed under [Creative Commons Attribution 4.0 International
(CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/) — see
[LICENSE](LICENSE) for the full legal text.

You are free to share and adapt this work for any purpose, including
commercially, as long as you give appropriate credit. Attribution must name
the original author and be preserved in every copy or derivative, including
further edits or forks.