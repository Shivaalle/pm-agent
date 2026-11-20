"""Runnable MCP server entrypoint for the three PM Agent connectors.

The connector definitions live in ``backend/mcp_servers.py`` as real
``mcp.server.mcpserver.MCPServer`` instances with ``@tool()`` registrations.
The application calls them in-process; this module lets an external MCP client
connect to the same connectors over stdio (default), SSE, or streamable HTTP.

Examples:
    .venv/bin/python -m backend.mcp_app jira
    .venv/bin/python -m backend.mcp_app github
    .venv/bin/python -m backend.mcp_app teams
    MCP_TRANSPORT=streamable-http .venv/bin/python -m backend.mcp_app jira
"""

from __future__ import annotations

import argparse
import os

from backend.mcp_servers import github_mcp, jira_mcp, teams_mcp

SERVERS = {
    "jira": jira_mcp,
    "github": github_mcp,
    "teams": teams_mcp,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a PM Agent MCP connector")
    parser.add_argument("connector", choices=sorted(SERVERS))
    parser.add_argument(
        "--transport",
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        choices=["stdio", "sse", "streamable-http"],
    )
    args = parser.parse_args()
    server = SERVERS[args.connector]
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
