# Agent Tooling

This repository uses Graft and Ponytail together. Graft's configuration is repo-local; the `graft` CLI is installed machine-wide and is not part of this repository. The local graph cache under `graft/` is ignored and can be rebuilt with `graft build`.

## Files and ownership

Graft owns its fenced regions in `AGENTS.md` and `.github/copilot-instructions.md`, plus these whole files: `.cursor/rules/graft.mdc`, `.kiro/steering/graft.md`, `.claude/skills/graft/SKILL.md`, and the generated Claude/Cursor helpers and settings. Do not add content to the whole-file graft targets; `graft init` may replace them. Graft also registers `mcpServers.graft` in `.mcp.json`, `.cursor/mcp.json`, and `.kiro/settings/mcp.json`.

Ponytail is appended as this repository's convention:

```text
<!-- graft:start -->
graft-owned section
<!-- graft:end -->
<!-- ponytail:start -->
Ponytail canonical compact body
<!-- ponytail:end -->
```

Upstream Ponytail does not recognize these markers. Its body is copied from upstream `AGENTS.md` with only the repo-self-referential closing paragraph removed. The copies in `AGENTS.md` and `.github/copilot-instructions.md` are identical. Ponytail governs how much code to write, never whether to keep a safeguard; repo-specific rules win, and Graft answers its reuse-rung question from the repository graph.

## Host coverage

Claude Code receives Graft hooks, statusline, skill, and MCP wiring. Codex receives the repo-root `AGENTS.md` instruction tier; this Codex 0.139.0 installation has plugins but no repo-local config surface, so no `.codex/` files were added. Cursor receives its Graft rule, repo-local hooks, and MCP; Kiro receives its steering rule and MCP. GitHub Copilot Chat receives `.github/copilot-instructions.md`; Copilot CLI can use the same instruction tier, while its full plugin tier is optional and user-global. Ponytail has no Graft adapter for Devin. Devin is covered incidentally only if the installed Devin version reads repo-root `AGENTS.md`; otherwise it has no Graft coverage.

The collision matrix was verified: `AGENTS.md` and Copilot instructions use disjoint fences; Cursor and Kiro use different filenames; MCP entries use distinct keys; `CLAUDE.md` is unclaimed by Graft. Ponytail hooks were not copied into `.claude/settings.json`: their `${CLAUDE_PLUGIN_ROOT}` commands require the user-global Claude plugin registry, and plugin hooks are additive to project settings. The two hook registries therefore remain separate.

The upstream `ponytail-mcp` server is private and requires its own checkout plus `npm install`; it is not wired into the repository MCP files because that would add an unavailable runtime and dependency. The installed Graft `graft` key remains intact in every repository MCP file. Use Ponytail's native host plugins for its supported global integrations instead.

## Optional user-global plugins

These commands affect all repositories and are documented only. The Claude and Codex plugin lifecycle hooks need `node` on the non-interactive shell PATH.

```text
Claude Code:  /plugin marketplace add DietrichGebert/ponytail
              /plugin install ponytail@ponytail
Codex:        codex plugin marketplace add DietrichGebert/ponytail
              codex plugin add ponytail@ponytail
              then /hooks and trust the hooks
Copilot CLI:  copilot plugin marketplace add DietrichGebert/ponytail
              copilot plugin install ponytail@ponytail
Devin CLI:    devin plugins install DietrichGebert/ponytail
```

Claude's two slash commands require separate prompts. Devin intentionally uses plural `plugins`.

## Updating and uninstalling

Run `graft init --agents claude agents cursor kiro copilot --no-global` to refresh Graft-owned content and preserve the fenced user regions. Run `graft build` afterward. To remove a Graft block, remove the corresponding selected host or edit only its fenced section; do not edit whole-file owned targets. To remove Ponytail's repo tier, delete only the `ponytail` fence and the adjacent Tool precedence paragraph. Remove optional plugins with each host's plugin manager; those user-global changes are outside this repository.