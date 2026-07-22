# Support Engineer MCP Server

A vendor-agnostic MCP server for technical support engineers, providing knowledge search, ticket analysis, and resolution suggestions.

## Features

- **Search Knowledge Base**: Find relevant articles, tickets, and guides by keyword.
- **Analyze Tickets**: Load ticket CSVs to get statistics and sample data.
- **List Knowledge Files**: Browse all available files in the knowledge base.
- **Suggest Resolutions**: Get recommendations based on issue descriptions (placeholder, extendable with ML).

## Quick Start

1. Install the MCP SDK:
   ```bash
   pip install mcp
   ```

2. Clone or download this repository.

3. (Optional) Replace the sample files in the `knowledge/` folder with your own data.

4. Set the `KNOWLEDGE_ROOT` environment variable if your knowledge base is elsewhere (defaults to `./knowledge`).

5. Run the server:
   ```bash
   python support_mcp_server.py
   ```

6. Configure your MCP client (e.g., Claude Desktop, Cherry Studio) to connect via stdio.

## Customization

- Replace `knowledge/tickets_sample.csv` with your own ticket data.
- Replace `knowledge/articles_sample.csv` with your own knowledge articles.
- Edit `knowledge/troubleshooting_guide.md` to add your own troubleshooting steps.
- Override the knowledge root path with the `KNOWLEDGE_ROOT` environment variable.

## License

MIT
