"""
Fixed ServiceNow MCP SSE Server for Copilot Studio compatibility.

This fixes the routing issue where GET requests to /messages/ were being
handled by the POST-only handler.
"""

import argparse
import os
from typing import Dict, Union

import uvicorn
from dotenv import load_dotenv
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import json

from servicenow_mcp.server import ServiceNowMCP
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig


def create_starlette_app(mcp_server: ServiceNowMCP, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can serve the provided mcp server with SSE."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        """Handle SSE connection requests."""
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp_server.mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.mcp_server.create_initialization_options(),
            )

    async def handle_messages(request: Request) -> Response:
        """Handle both GET and POST requests to /messages/."""
        if request.method == "GET":
            # Handle GET request - list available tools
            session_id = request.query_params.get("session_id")
            if not session_id:
                return JSONResponse(
                    {"error": "session_id is required"}, 
                    status_code=400
                )
            
            try:
                # Get available tools from the MCP server
                tools = await mcp_server._list_tools_impl()
                tools_data = [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema
                    }
                    for tool in tools
                ]
                return JSONResponse(tools_data)
            except Exception as e:
                return JSONResponse(
                    {"error": f"Failed to list tools: {str(e)}"}, 
                    status_code=500
                )
        
        elif request.method == "POST":
            # Handle POST request - delegate to SSE transport
            return await sse.handle_post_message(request)
        
        else:
            return JSONResponse(
                {"error": f"Method {request.method} not allowed"}, 
                status_code=405
            )

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["GET", "POST"]),
        ],
    )


class ServiceNowSSEMCP(ServiceNowMCP):
    """
    ServiceNow MCP Server implementation with fixed SSE support.
    """

    def __init__(self, config: Union[Dict, ServerConfig]):
        """Initialize the ServiceNow MCP server."""
        super().__init__(config)

    def start(self, host: str = "0.0.0.0", port: int = 8080):
        """
        Start the MCP server with fixed SSE transport using Starlette and Uvicorn.

        Args:
            host: Host address to bind to
            port: Port to listen on
        """
        # Create Starlette app with fixed SSE transport
        starlette_app = create_starlette_app(self, debug=True)

        print(f"Starting ServiceNow MCP SSE server on {host}:{port}")
        print("Available endpoints:")
        print(f"  - SSE Connection: http://{host}:{port}/sse")
        print(f"  - List Tools (GET): http://{host}:{port}/messages/?session_id=<id>")
        print(f"  - Invoke Tool (POST): http://{host}:{port}/messages/?session_id=<id>")

        # Run using uvicorn
        uvicorn.run(starlette_app, host=host, port=port)


def create_servicenow_mcp(instance_url: str, username: str, password: str):
    """
    Create a ServiceNow MCP server with minimal configuration.

    Args:
        instance_url: ServiceNow instance URL
        username: ServiceNow username
        password: ServiceNow password

    Returns:
        A configured ServiceNowSSEMCP instance ready to use
    """
    # Create basic auth config
    auth_config = AuthConfig(
        type=AuthType.BASIC, 
        basic=BasicAuthConfig(username=username, password=password)
    )

    # Create server config
    config = ServerConfig(instance_url=instance_url, auth=auth_config)

    # Create and return server
    return ServiceNowSSEMCP(config)


def main():
    load_dotenv()

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run ServiceNow MCP SSE-based server with Copilot Studio support")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--instance-url", help="ServiceNow instance URL", default=os.getenv("SERVICENOW_INSTANCE_URL"))
    parser.add_argument("--username", help="ServiceNow username", default=os.getenv("SERVICENOW_USERNAME"))
    parser.add_argument("--password", help="ServiceNow password", default=os.getenv("SERVICENOW_PASSWORD"))
    args = parser.parse_args()

    # Validate required parameters
    if not args.instance_url:
        print("Error: ServiceNow instance URL is required. Set SERVICENOW_INSTANCE_URL environment variable or use --instance-url")
        return
    if not args.username:
        print("Error: ServiceNow username is required. Set SERVICENOW_USERNAME environment variable or use --username")
        return
    if not args.password:
        print("Error: ServiceNow password is required. Set SERVICENOW_PASSWORD environment variable or use --password")
        return

    print(f"Connecting to ServiceNow instance: {args.instance_url}")
    print(f"Username: {args.username}")
    
    server = create_servicenow_mcp(
        instance_url=args.instance_url,
        username=args.username,
        password=args.password,
    )
    server.start(host=args.host, port=args.port)


if __name__ == "__main__":
    main()