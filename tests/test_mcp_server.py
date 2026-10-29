import asyncio

from ia_mcp.mcp_servers.local_server import mcp


def test_mcp_exposes_only_read_only_tools():
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}

    assert names == {
        "ollama_status",
        "ollama_list_models",
        "civil3d_status",
        "civil3d_list_alignments",
        "word_status",
        "word_list_documents",
        "word_read_document",
    }
