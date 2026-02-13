# MCP (Model Context Protocol) Integration

Connects external tools to EvoScientist agents via [MCP](https://modelcontextprotocol.io/).

Included with `pip install evoscientist` (requires `langchain-mcp-adapters`).

## Quick Start

Edit `~/.config/evoscientist/mcp.yaml`:

```yaml
# Sequential Thinking — structured reasoning and problem decomposition
sequential-thinking:
  transport: stdio
  command: npx
  args: ["-y", "@modelcontextprotocol/server-sequential-thinking"]

# Context7 — up-to-date library documentation
context7:
  transport: stdio
  command: npx
  args: ["-y", "@upstash/context7-mcp@latest"]

# Brave Search — web, image, video, news search (requires API key)
brave-search:
  transport: stdio
  command: npx
  args: ["-y", "@brave/brave-search-mcp-server"]
  env:
    BRAVE_API_KEY: "${BRAVE_API_KEY}"
```

Then restart the agent (`/new` in interactive mode).

More servers: [MCP Server Directory](https://github.com/modelcontextprotocol/servers)

## Config Fields

| Field | Required | Description |
|-------|----------|-------------|
| `transport` | Yes | `stdio`, `http`, `sse`, `websocket` |
| `command` | stdio only | Command to run (e.g. `npx`) |
| `args` | stdio only | Arguments list (varies per MCP package) |
| `env` | No | Environment variables for subprocess |
| `url` | http/sse/ws | Server URL |
| `headers` | No | HTTP headers (e.g. auth tokens) |
| `tools` | No | Tool allowlist with glob wildcards (omit = all tools) |
| `expose_to` | No | Target agents (default: `["main"]`) |

## Tool Routing

Use `expose_to` to send tools to specific sub-agents:

```yaml
postgres:
  transport: stdio
  command: npx
  args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"]
  expose_to: [data-analysis-agent]

github:
  transport: stdio
  command: npx
  args: ["-y", "@modelcontextprotocol/server-github"]
  expose_to: [main, research-agent]
```

Available agents: `main`, `planner-agent`, `research-agent`, `code-agent`, `debug-agent`, `data-analysis-agent`, `writing-agent`.

## Tool Filtering with Wildcards

Use the `tools` field to filter which tools from a server are exposed. Supports glob-style wildcards:

```yaml
# Exact matching (original behavior)
exa:
  transport: http
  url: https://mcp.exa.ai/mcp
  tools:
    - web_search_exa
    - get_code_context_exa
    - company_research_exa

# Wildcard: all tools ending with _exa
exa:
  transport: http
  url: https://mcp.exa.ai/mcp
  tools:
    - "*_exa"

# Multiple patterns
filesystem:
  transport: stdio
  command: npx
  args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
  tools:
    - "read_*"      # read_file, read_directory, etc.
    - "write_*"     # write_file, etc.
    - "list_*"      # list_directory, etc.

# Mix wildcards and exact matches
mixed:
  transport: http
  url: https://example.com/mcp
  tools:
    - "search_*"       # All search tools
    - "get_metadata"   # Plus this specific tool
```

### Wildcard Patterns

| Pattern | Matches | Example |
|---------|---------|---------|
| `*` | Any sequence of characters | `*_exa` matches `web_search_exa`, `get_code_context_exa` |
| `?` | Any single character | `tool_?` matches `tool_1`, `tool_2` but not `tool_10` |
| `[seq]` | Any character in sequence | `tool_[abc]` matches `tool_a`, `tool_b`, `tool_c` |
| `[0-9]` | Any character in range | `version_[0-9]` matches `version_0` through `version_9` |
| `[!seq]` | Any character NOT in sequence | `tool_[!0-9]` matches `tool_a` but not `tool_1` |

Wildcards work with exact patterns — you can mix both in the same `tools` list.

## CLI Commands

```
/mcp                List configured servers
/mcp add <name> <transport> <command-or-url> [args...]
/mcp edit <name> --field value
/mcp remove <name>
```

Or from the terminal: `EvoSci mcp list`, `EvoSci mcp add ...`, etc.

Examples:

```bash
# Sequential Thinking
EvoSci mcp add sequential-thinking stdio npx -- -y "@modelcontextprotocol/server-sequential-thinking"

# Context7 with tool routing
EvoSci mcp add context7 stdio npx -e main,research-agent,code-agent -- -y "@upstash/context7-mcp@latest"

# Brave Search with env var
EvoSci mcp add brave-search stdio npx --env "BRAVE_API_KEY=your-key" -- -y "@brave/brave-search-mcp-server"
```

Use `--` to separate MCP server args from CLI options. Put flags like `-e`, `--env` before `--`. Quote `@`-scoped package names.

## Environment Variables

Use `${VAR}` in YAML values to reference environment variables:

```yaml
headers:
  Authorization: "Bearer ${MY_API_KEY}"
```

Missing variables are replaced with empty string and logged as a warning.

## How It Works

1. On agent startup (`/new` or `create_cli_agent()`), reads `~/.config/evoscientist/mcp.yaml`
2. Connects to each server via the configured transport
3. Retrieves available tools from each server
4. Filters tools by `tools` allowlist (if set)
5. Routes tools to target agents by `expose_to`
6. Tools are injected into the agent's tool list automatically

MCP servers that fail to connect are skipped with a warning — they don't block startup.
