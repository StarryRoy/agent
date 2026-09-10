"""Local MCP server that represents an external supplier-status service."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("procurement-supplier-status", log_level="WARNING")


@server.tool()
def supplier_status(
    supplier_codes: list[str], force_failure: bool = False
) -> dict[str, object]:
    """Return current operational status for the requested external supplier codes."""

    if force_failure:
        raise RuntimeError("scripted external supplier-status outage")
    known = {
        "SUP-A": "operational",
        "SUP-B": "operational",
        "SUP-C": "capacity_warning",
        "SUP-D": "operational",
        "SUP-X": "suspended",
    }
    return {
        "source": "external_supplier_status_mcp",
        "statuses": {code: known.get(code, "unknown") for code in supplier_codes},
    }


if __name__ == "__main__":
    server.run(transport="stdio")
