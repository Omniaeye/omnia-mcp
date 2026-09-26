# Compatibility

OMNIA MCP 0.1.0 uses the official Python MCP SDK 2.2.0 and Python 3.11+. Its exposed transport is **stdio**.

| Client | Configuration | Example |
| --- | --- | --- |
| Claude Code | Project `.mcp.json` | [Claude Code](../examples/claude-code/.mcp.json) |
| Claude Desktop | `claude_desktop_config.json` | [Claude Desktop](../examples/claude-desktop/claude_desktop_config.json) |
| Cursor | `.cursor/mcp.json` | [Cursor](../examples/cursor/mcp.json) |
| VS Code | `.vscode/mcp.json` | [VS Code](../examples/vscode/mcp.json) |

These recipes follow vendor-documented configuration formats. They do not certify every client version or enterprise policy. Clients may restrict MCP, executable paths, permissions, and environment variables.

Automated stdio tests exercise a process boundary. They do not drive Claude, Cursor, or VS Code user interfaces. Record the actual client version, operating system, server version, and outcome for each manual check.

## Client check

1. Use the intended environment and an absolute executable path if necessary.
2. Confirm the client lists the four documented tools.
3. Read `omnia://guide` and `omnia://schemas` if resources are available.
4. Call `omnia_capabilities` without contacting a provider.
5. With an explicitly configured provider, submit a small evaluation and retrieve its decision ID.
6. Confirm invalid input or missing credentials produce useful errors without secrets.
7. Close the client and confirm its stdio server terminates.

Live evaluation may contact a paid provider or load model weights. Ordinary CI does neither.

## Boundaries

- HTTP MCP hosting and OAuth are not part of 0.1.0.
- LAYA has a separate dependency and hardware compatibility surface.
- Source installation from a Git URL needs Git; runtime execution after installation does not.
- Windows desktop clients may require the full path to `omnia-mcp.exe`.
- A result is an assessment, not authorization to execute an external action.

References: [Claude Code](https://code.claude.com/docs/en/mcp), [Claude Desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop), [Cursor](https://cursor.com/docs/mcp), [VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).
