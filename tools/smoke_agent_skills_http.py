"""Real Streamable HTTP acceptance for BirdEye's Agent Skills/BOM implementation.

Starts an isolated BirdEye server on an OS-selected loopback port, speaks MCP,
checks skills catalog and live project mapping, then shuts down the test server.
Does not touch the canonical 8766 listener.
"""
from __future__ import annotations
import asyncio
import argparse
import hashlib
import tempfile
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def exercise(url: str) -> dict:
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            listed = await client.list_tools()
            skills = next((x for x in listed.tools if x.name == "skills"), None)
            assert skills is not None, "skills tool missing"
            assert "catalog" in skills.input_schema["properties"]["operation"]["enum"]
            mapped = await client.call_tool("local_projects", {"project": "brew"})
            assert not mapped.is_error, "BOM mapping returned an MCP error"
            project = json.loads(mapped.content[0].text)["projects"]["brew"]
            assert project["exists"] is True and project["resolution_source"] == "registered_live_workspace"
            catalog_result = await client.call_tool("skills", {
                "operation": "catalog", "query": "code", "max_results": 3,
            })
            assert not catalog_result.is_error, "catalog call returned an MCP error"
            catalog = json.loads(catalog_result.content[0].text)
            assert catalog["ok"] and catalog["format"] == "agent-skills"
            assert catalog["results"], "metadata catalog unexpectedly empty"
            for item in catalog["results"]:
                assert "name" in item and "description" in item and "sha256" in item
                assert "content" not in item and "discovery_excerpt" not in item
            # Use an actual curated package and its existing reference file.
            # All task lock writes occur in a disposable workspace.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            import mcp_server
            root = mcp_server.SKILLS_ROOT
            selected = None
            for skill in sorted(root.rglob("SKILL.md")):
                rel_skill = skill.relative_to(root).as_posix()
                meta = mcp_server._agent_skill_metadata(rel_skill, skill.read_text(encoding="utf-8-sig"))
                if meta is None:
                    continue
                ref_dir = skill.parent / "references"
                if not ref_dir.is_dir():
                    continue
                files = sorted(x for x in ref_dir.rglob("*.md") if x.is_file() and 0 < x.stat().st_size <= 100000)
                if files:
                    selected = (skill, meta, files[0])
                    break
            assert selected is not None, "no current portable package with an actual reference file"
            skill, metadata, reference = selected
            skill_path = skill.relative_to(root).as_posix()
            resource_rel = reference.relative_to(skill.parent).as_posix()
            with tempfile.TemporaryDirectory(prefix="birdeye-skill-mcp-") as workspace:
                params = {
                    "workspace_root": workspace, "task": "live-transport-acceptance",
                    "domain": "testing", "query": metadata["name"],
                    "prefix": skill.parent.relative_to(root).as_posix() + "/",
                    "fetch_top": 1,
                }
                response = await client.call_tool("skills", {"operation": "resolve", **params})
                assert not response.is_error, "task resolution returned MCP error"
                resolved = json.loads(response.content[0].text)
                assert resolved["ok"] and resolved["skills"][0]["path"] == skill_path
                pinned = resolved["skills"][0]["sha256"]
                response = await client.call_tool("skills", {
                    "operation": "fetch_locked", "workspace_root": workspace,
                    "task": params["task"], "domain": params["domain"],
                })
                assert not response.is_error
                locked = json.loads(response.content[0].text)
                assert locked["skills"][0]["sha256"] == pinned
                response = await client.call_tool("skills", {
                    "operation": "resource", "workspace_root": workspace,
                    "task": params["task"], "domain": params["domain"],
                    "rel": resource_rel,
                })
                assert not response.is_error, "locked resource returned MCP error"
                resource = json.loads(response.content[0].text)
                assert resource["ok"] and resource["complete"] and not resource["executable"]
                assert resource["sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest()
                assert resource["encoding"] == "utf-8"
                response = await client.call_tool("skills", {
                    "operation": "complete_task", "workspace_root": workspace,
                    "task": params["task"],
                })
                assert not response.is_error
                assert json.loads(response.content[0].text)["ok"]
            return {
                "status": "PASS", "transport": "streamable-http",
                "tools": len(listed.tools), "catalog_total_filtered": catalog["total"],
                "selected_sample": [item["name"] for item in catalog["results"]],
                "mapped_brew_root": project["local_path"],
                "mapping_drift": project["mapping_drift"],
                "resolved_skill": skill_path,
                "locked_skill_sha256": pinned,
                "reference_resource": resource_rel,
                "resource_sha256": resource["sha256"],
                "task_completed": True,
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="")
    args = parser.parse_args()
    if args.endpoint:
        result = asyncio.run(exercise(args.endpoint))
        print(json.dumps(result, indent=2))
        return
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, BIRDEYE_HTTP_HOST="127.0.0.1", BIRDEYE_HTTP_PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, "http_mcp_server.py"], cwd=str(root), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        url = f"http://127.0.0.1:{port}"
        health = None
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"isolated BirdEye exited during startup: {proc.returncode}")
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as response:
                    health = json.load(response)
                break
            except (OSError, ValueError):
                time.sleep(0.3)
        assert health and health["ok"] and health["server"] == "letterblack-birdeye"
        result = asyncio.run(exercise(url + "/mcp"))
        result["isolated_http_port"] = port
        print(json.dumps(result, indent=2))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    main()
