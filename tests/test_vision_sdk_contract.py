"""Offline integration with the installed SDK and the real host image parser."""

from __future__ import annotations

import ast
import asyncio
import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, List, Tuple

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = PLUGIN_ROOT.parents[1]
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+a92kAAAAASUVORK5CYII="
)


def load_source_module(path, name, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def installed_sdk_source():
    package = importlib.util.find_spec("maibot_sdk")
    if package is not None and package.submodule_search_locations:
        path = Path(next(iter(package.submodule_search_locations))) / "capabilities" / "llm.py"
        if path.is_file():
            return path
    path = HOST_ROOT / ".venv" / "Lib" / "site-packages" / "maibot_sdk" / "capabilities" / "llm.py"
    if path.is_file():
        return path
    pytest.skip("The installed MaiBot SDK is not available in this checkout")


@pytest.fixture
def host_contract(monkeypatch):
    context_path = HOST_ROOT / "src" / "llm_models" / "payload_content" / "context_item.py"
    service_path = HOST_ROOT / "src" / "services" / "llm_service.py"
    if not context_path.is_file() or not service_path.is_file():
        pytest.skip("The MaiBot host source is not available in this checkout")

    sdk = load_source_module(installed_sdk_source(), "maizone_contract_sdk_llm", monkeypatch)
    context = load_source_module(context_path, "maizone_contract_host_context", monkeypatch)
    parser_names = {"_parse_data_url_image", "_append_image_content", "_append_content_parts"}
    service_tree = ast.parse(service_path.read_text(encoding="utf-8-sig"), filename=str(service_path))
    parser_nodes = [
        node
        for node in service_tree.body
        if isinstance(node, ast.FunctionDef) and node.name in parser_names
    ]
    assert {node.name for node in parser_nodes} == parser_names
    parser_globals = {
        "Any": Any,
        "List": List,
        "Tuple": Tuple,
        "ContextItemBuilder": context.ContextItemBuilder,
    }
    # Execute the host's actual parsing functions without starting its services.
    parser_module = ast.Module(body=parser_nodes, type_ignores=[])
    exec(compile(parser_module, str(service_path), "exec"), parser_globals)
    return sdk, context, parser_globals["_append_content_parts"]


def test_sdk_image_request_reaches_real_host_image_parts(host_contract, monkeypatch):
    sdk, context, append_parts = host_contract
    vision = load_source_module(PLUGIN_ROOT / "vision.py", "maizone_contract_vision", monkeypatch)
    captured = []

    class FakeContext:
        async def call_capability(self, capability, **payload):
            assert capability == "llm.generate"
            # Round-trip through JSON as the plugin RPC transport does.
            captured.append(json.loads(json.dumps(payload)))
            return {"success": True, "response": "offline vision response", "model_name": "test-vision"}

    manager = vision.SDKImageManager(sdk.LLMCapability(FakeContext()))
    description = asyncio.run(manager.get_image_description(PNG_BASE64))

    assert "offline vision response" in description
    assert len(captured) == 1
    payload = captured[0]
    assert payload["task_name"] == "vlm"
    assert payload["model"] == ""
    assert payload["timeout_ms"] == 120_000
    assert isinstance(payload["prompt"], list) and len(payload["prompt"]) == 1
    message = payload["prompt"][0]
    assert message["role"] == "user"

    builder = context.ContextItemBuilder().set_role(context.RoleType.User)
    append_parts(builder, message["content"])
    item = builder.build()
    image_parts = [part for part in item.parts if isinstance(part, context.ContextImagePart)]
    text_parts = [part for part in item.parts if isinstance(part, context.ContextTextPart)]
    assert len(image_parts) == 1
    assert text_parts and all(part.text.strip() for part in text_parts)
    assert image_parts[0].image_format == "png"
    assert image_parts[0].image_base64 == PNG_BASE64
    assert base64.b64decode(image_parts[0].image_base64, validate=True) == base64.b64decode(PNG_BASE64)
