import asyncio

from fastmcp import Client


def test_mcp_tools_exposed(monkeypatch, tmp_path):
    monkeypatch.setenv("INVOICEGUARD_STATE_DIR", str(tmp_path))
    from invoiceguard import service

    service.get_app.cache_clear()
    from invoiceguard.mcp_server import mcp

    async def run():
        async with Client(mcp) as c:
            names = {t.name for t in await c.list_tools()}
            assert {"process_invoice", "list_pending_reviews", "decide_invoice", "ap_dashboard"} <= names
            res = await c.call_tool("vendor_master_data", {"query": "Schulz"})
            assert "*" in res.data[0]["iban"]  # IBANs masked for the assistant

    asyncio.run(run())
    service.get_app.cache_clear()
