# Use MemoryOps

Use this skill when working in the InferBridge repository and durable project memory would help.

## Connection

- API URL: `http://localhost:8080`
- MCP URL: `http://localhost:3003/mcp`
- Workspace ID: `019e09c0-3ffe-7783-9103-9ef82fa06660`
- Agent ID: `inferbridge`

## Workflow

1. Retrieve MemoryOps context before substantial code, DevOps, incident, architecture, migration, or release work.
2. Prefer the MemoryOps MCP server when Claude Code exposes it. Fall back to REST with `X-API-Key` when MCP is unavailable.
3. Store only durable project facts and outcomes. Use observations for raw evidence.
4. Never store secrets, credentials, private keys, tokens, unrelated personal content, or private reasoning.
5. Surface conflicting memories before acting on them.

## Retrieval defaults

```json
{
  "workspace_id": "019e09c0-3ffe-7783-9103-9ef82fa06660",
  "token_budget": 4096,
  "search_mode": "hybrid",
  "include_trace": true,
  "include_workspace_pool": true,
  "include_master_memory": false,
  "agent_id": "inferbridge",
  "repo": "Quazmoz/InferBridge"
}
```
