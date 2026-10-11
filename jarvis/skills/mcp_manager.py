"""Model Context Protocol (MCP) bridge and external server runner for Jarvis 2.0."""
import json
import logging
from pathlib import Path
from typing import Dict, Any, List
import asyncio

logger = logging.getLogger("Jarvis.MCP")


class MCPManager:
    """Manages connections to external MCP servers defined in config/mcp_servers.json."""

    def __init__(self, config: Dict[str, Any]):
        base_dir = Path(config.get("_base_dir", "."))
        cfg_rel_path = config.get("mcp", {}).get("config_path", "config/mcp_servers.json")
        self.config_path = base_dir / cfg_rel_path
        self.servers_config = self._load_servers_config()
        self.active_sessions = {}

    def _load_servers_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("mcpServers", {})
        except Exception as e:
            logger.error(f"Failed to parse MCP config: {e}")
            return {}

    async def list_available_mcp_tools(self) -> List[Dict[str, Any]]:
        """Queries enabled MCP servers for exposed tools."""
        tools = []
        for server_name, server_cfg in self.servers_config.items():
            if not server_cfg.get("enabled", False):
                continue
            # For enabled servers, tool discovery can be performed asynchronously via stdio_client
            logger.info(f"MCP server '{server_name}' enabled.")
        return tools
