# Support Engineer MCP Server

A vendor-agnostic MCP server for technical support engineers, providing knowledge search, ticket analysis, and resolution suggestions.

## Features

- **Search Knowledge Base**: Find relevant articles, tickets, and guides by keyword.
- **Analyze Tickets**: Load ticket CSVs to get statistics and sample data.
- **List Knowledge Files**: Browse all available files in the knowledge base.
- **Suggest Resolutions**: Get recommendations based on issue descriptions (placeholder, extendable with ML).

## Installation

### Prerequisites

- Python 3.10 or later
- pip (usually included with Python)

### 1. Clone the repository

```bash
git clone https://github.com/tsabra76/support-engineer-mcp.git
cd support-engineer-mcp
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. (Optional) Customize the knowledge base

Replace the sample files in the `knowledge/` folder with your own data. The server automatically reads all `.csv` and `.md` files from that directory. You can also set the `KNOWLEDGE_ROOT` environment variable to point to a different location.

### 4. Configure your MCP client

Below are instructions for connecting the server to **Claude Code** (Anthropic) and **OpenAI Codex CLI**.

#### Claude Code

Edit your Claude Desktop configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the following entry under `mcpServers`:

```json
{
  "mcpServers": {
    "support-engineer": {
      "command": "python",
      "args": ["C:/path/to/support-engineer-mcp/support_mcp_server.py"],
      "env": {
        "KNOWLEDGE_ROOT": "C:/path/to/support-engineer-mcp/knowledge"
      }
    }
  }
}
```

Replace `C:/path/to/` with the actual absolute path to your cloned repository. The `KNOWLEDGE_ROOT` variable is optional; if omitted, the server defaults to the `knowledge/` folder inside the repository.

Restart Claude Desktop after saving the configuration.

#### OpenAI Codex CLI

Add an MCP server to your Codex configuration file (`~/.codex/config.yaml` or `%USERPROFILE%\.codex\config.yaml` on Windows):

```yaml
mcp_servers:
  - name: support-engineer
    command: python
    args:
      - "C:/path/to/support-engineer-mcp/support_mcp_server.py"
    env:
      KNOWLEDGE_ROOT: "C:/path/to/support-engineer-mcp/knowledge"
```

Again, replace the paths with the actual location of your repository. After editing the file, restart your Codex session or IDE integration.

### 5. Verify the connection

Once connected, you should see the server's tools (`search_knowledge`, `analyze_tickets`, `list_knowledge_files`, `suggest_resolution`) available in your MCP client's tool list.

## Customization

- Replace `knowledge/tickets_sample.csv` with your own ticket data.
- Replace `knowledge/articles_sample.csv` with your own knowledge articles.
- Edit `knowledge/troubleshooting_guide.md` to add your own troubleshooting steps.
- Override the knowledge root path with the `KNOWLEDGE_ROOT` environment variable.

## License

MIT
