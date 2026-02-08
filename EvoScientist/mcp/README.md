# MCP (Model Context Protocol) Integration

Optional feature for connecting external tools to EvoScientist agents via [MCP](https://modelcontextprotocol.io/).

## Install

```bash
pip install "evoscientist[mcp]"
```

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
| `tools` | No | Tool allowlist (omit = all tools) |
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
