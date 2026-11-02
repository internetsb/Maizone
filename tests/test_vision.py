"""Offline request, failure, cache, and lifecycle contracts for image recognition.

These tests do not call a real model and do not claim to verify its visual accuracy.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+a92kAAAAASUVORK5CYII="
)
PNG_BYTES = base64.b64decode(PNG_BASE64)
DESCRIPTION = "画面中有一只猫，旁边写着你好。"


def run(awaitable):
    return asyncio.run(awaitable)


def encode(value):
    return base64.b64encode(value).decode("ascii")


def successful_response(text=DESCRIPTION):
    return {"success": True, "response": text}


def fake_llm(response=None, *, side_effect=None):
    return SimpleNamespace(
        generate=AsyncMock(
            return_value=successful_response() if response is None else response,
            side_effect=side_effect,
        )
    )


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vision_module():
    return load_module("maizone_vision_under_test", ROOT / "vision.py")


@pytest.fixture
def qzone_module():
    return load_module("maizone_vision_qzone_under_test", ROOT / "qzone_api.py")


def assert_image_request(llm, expected_base64, image_format):
    llm.generate.assert_awaited_once()
    kwargs = llm.generate.await_args.kwargs
    assert kwargs["task_name"] == "vlm"
    assert kwargs["timeout_ms"] == 120_000
    assert "model" not in kwargs
    assert "model_name" not in kwargs
    messages = kwargs["prompt"]
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["role"] == "user"
    parts = messages[0]["content"]
    images = [part for part in parts if part["type"] == "image"]
    texts = [part for part in parts if part["type"] == "text"]
    assert len(images) == len(texts) == 1
    assert isinstance(texts[0]["text"], str) and texts[0]["text"].strip()
    assert images[0]["image_format"] == image_format
    assert images[0]["image_base64"] == expected_base64
    assert base64.b64decode(images[0]["image_base64"], validate=True) == base64.b64decode(expected_base64)


def test_description_sends_image_bytes_to_vision_task(vision_module):
    llm = fake_llm(successful_response(f"  {DESCRIPTION}\n"))
    manager = vision_module.SDKImageManager(llm)

    assert run(manager.get_image_description(PNG_BASE64)) == f"[图片描述：{DESCRIPTION}]"
    assert_image_request(llm, PNG_BASE64, "png")


# The real PNG fixture above verifies byte transport; these signatures test routing.
@pytest.mark.parametrize(
    ("image_bytes", "image_format"),
    [
        (PNG_BYTES, "png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\xff\xd9", "jpeg"),
        (b"GIF87a\x01\x00\x01\x00", "gif"),
        (b"GIF89a\x01\x00\x01\x00", "gif"),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 ", "webp"),
    ],
    ids=["png", "jpeg", "gif87a", "gif89a", "webp"],
)
def test_image_format_uses_actual_bytes(vision_module, image_bytes, image_format):
    llm = fake_llm()
    encoded = encode(image_bytes)
    manager = vision_module.SDKImageManager(llm)

    assert run(manager.get_image_description(encoded)) == f"[图片描述：{DESCRIPTION}]"
    assert_image_request(llm, encoded, image_format)


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        " ",
        None,
        1,
        "not base64!!",
        "图片",
        "aGVsbG8",
        encode(b"<html>login required</html>"),
        encode(b"RIFF\x10\x00\x00\x00WAVEdata"),
        encode(b"GIFnotanimage"),
    ],
    ids=["empty", "whitespace", "none", "integer", "invalid", "non-ascii", "bad-padding", "html", "wav", "wrong-gif"],
)
def test_bad_images_are_rejected_before_model_call(vision_module, encoded):
    llm = fake_llm()
    manager = vision_module.SDKImageManager(llm)

    with pytest.raises(ValueError):
        run(manager.get_image_description(encoded))
    llm.generate.assert_not_awaited()


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        "not a result object",
        {},
        {"response": DESCRIPTION},
        {"success": False, "response": "provider-secret-error"},
        {"success": None, "response": DESCRIPTION},
        {"success": 1, "response": DESCRIPTION},
        {"success": "true", "response": DESCRIPTION},
        {"success": True},
        successful_response(""),
        successful_response(" \n "),
        successful_response(None),
        successful_response(10),
        successful_response({"text": DESCRIPTION}),
        successful_response([DESCRIPTION]),
        successful_response("[图片]"),
        successful_response(" 图片 "),
        successful_response("[image]"),
    ],
    ids=[
        "none", "list", "string", "empty", "missing-success", "failed", "null-success", "integer-success",
        "string-success", "missing-text", "empty-text", "blank-text", "null-text", "number-text", "dict-text",
        "list-text", "placeholder", "word-placeholder", "english-placeholder",
    ],
)
def test_unsuccessful_or_placeholder_results_are_not_descriptions(vision_module, response):
    llm = fake_llm()
    llm.generate.return_value = response
    manager = vision_module.SDKImageManager(llm)

    with pytest.raises(RuntimeError) as error:
        run(manager.get_image_description(PNG_BASE64))
    assert "provider-secret-error" not in str(error.value)


def test_provider_exception_is_not_exposed_as_a_description(vision_module):
    llm = fake_llm(side_effect=OSError("provider-secret-error"))
    manager = vision_module.SDKImageManager(llm)

    with pytest.raises(RuntimeError) as error:
        run(manager.get_image_description(PNG_BASE64))
    assert "provider-secret-error" not in str(error.value)


def test_cancellation_propagates(vision_module):
    llm = fake_llm(side_effect=asyncio.CancelledError())
    manager = vision_module.SDKImageManager(llm)

    with pytest.raises(asyncio.CancelledError):
        run(manager.get_image_description(PNG_BASE64))


def test_successful_description_is_cached(vision_module):
    llm = fake_llm()
    manager = vision_module.SDKImageManager(llm)

    first = run(manager.get_image_description(PNG_BASE64))
    second = run(manager.get_image_description(PNG_BASE64))
    assert first == second == f"[图片描述：{DESCRIPTION}]"
    llm.generate.assert_awaited_once()


def test_cache_can_be_disabled(vision_module):
    llm = fake_llm()
    manager = vision_module.SDKImageManager(llm, cache_size=0)

    run(manager.get_image_description(PNG_BASE64))
    run(manager.get_image_description(PNG_BASE64))
    assert llm.generate.await_count == 2


def test_negative_cache_capacity_is_rejected(vision_module):
    with pytest.raises(ValueError):
        vision_module.SDKImageManager(fake_llm(), cache_size=-1)


def test_cache_evicts_least_recently_used_description(vision_module):
    llm = fake_llm()
    manager = vision_module.SDKImageManager(llm, cache_size=2)
    second = encode(PNG_BYTES + b"second")
    third = encode(PNG_BYTES + b"third")

    run(manager.get_image_description(PNG_BASE64))
    run(manager.get_image_description(second))
    run(manager.get_image_description(PNG_BASE64))
    run(manager.get_image_description(third))
    run(manager.get_image_description(PNG_BASE64))
    assert llm.generate.await_count == 3
    run(manager.get_image_description(second))
    assert llm.generate.await_count == 4


@pytest.mark.parametrize("failure", ["response", "exception", "cancellation"])
def test_failed_descriptions_are_not_cached(vision_module, failure):
    error_type = RuntimeError
    if failure == "response":
        first = {"success": False, "response": "failed"}
    elif failure == "exception":
        first = OSError("offline")
    else:
        first = asyncio.CancelledError()
        error_type = asyncio.CancelledError
    llm = fake_llm(side_effect=[first, successful_response()])
    manager = vision_module.SDKImageManager(llm)

    with pytest.raises(error_type):
        run(manager.get_image_description(PNG_BASE64))
    assert run(manager.get_image_description(PNG_BASE64)) == f"[图片描述：{DESCRIPTION}]"
    assert llm.generate.await_count == 2


def test_uninitialized_qzone_manager_does_not_fake_a_description(qzone_module):
    with pytest.raises(RuntimeError):
        run(qzone_module.image_manager.get_image_description(PNG_BASE64))


def test_qzone_manager_can_be_cleared_after_unload(qzone_module):
    manager = SimpleNamespace(get_image_description=AsyncMock(return_value="actual description"))
    qzone_module.set_image_manager(manager)
    assert qzone_module.image_manager is manager

    qzone_module.set_image_manager(None)
    assert isinstance(qzone_module.image_manager, qzone_module.NoImageManager)
    with pytest.raises(RuntimeError):
        run(qzone_module.image_manager.get_image_description(PNG_BASE64))


@pytest.mark.parametrize("source", ["html", "messages"])
def test_feed_images_receive_real_adapter_descriptions(vision_module, qzone_module, monkeypatch, source):
    image_url = "https://example.test/feed.png"
    author = "222222"
    llm = fake_llm()
    qzone_module.set_image_manager(vision_module.SDKImageManager(llm))

    def handle(request):
        if source == "html":
            assert request.url.path.endswith("feeds3_html_more")
            payload = {"code": 0, "data": {"data": [{
                "appid": 311, "uin": author, "key": "image-feed", "feedstime": "今天12:26",
                "html": '<div class="f-info">正文</div>'
                f'<div class="img-box"><img src="{image_url}"></div>',
            }]}}
        else:
            assert request.url.path.endswith("emotion_cgi_msglist_v6")
            payload = {"code": 0, "logininfo": {"name": "机器人"}, "msglist": [{
                "tid": "image-feed", "content": "正文", "pic": [{"url1": image_url}],
            }]}
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, **kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(qzone_module.httpx, "AsyncClient", client)
    api = qzone_module.QzoneAPI({"uin": "o00111111", "p_skey": "test-key"})
    download = AsyncMock(return_value=PNG_BASE64)
    monkeypatch.setattr(api, "get_image_base64_by_url", download)

    feeds = run(api.get_qzone_list() if source == "html" else api.get_list(author, 1, False))
    assert len(feeds) == 1
    assert feeds[0]["images"] == [f"[图片描述：{DESCRIPTION}]"]
    assert feeds[0]["content"] == "正文"
    download.assert_awaited_once_with(image_url)
    assert_image_request(llm, PNG_BASE64, "png")


def lifecycle_namespace(vision_module, enabled):
    """Execute actual lifecycle bodies without importing SDK decorators or services."""
    tree = ast.parse((ROOT / "plugin.py").read_text(encoding="utf-8"))
    plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MaizonePlugin")
    methods = [
        node for node in plugin_class.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {"on_load", "on_unload"}
    ]
    state = SimpleNamespace(manager=None, events=[])
    llm = fake_llm()
    llm.get_available_models = AsyncMock(return_value=["replyer"])

    def register(manager):
        state.manager = manager
        state.events.append("clear" if manager is None else "register")

    class Monitor:
        def __init__(self, plugin):
            self.plugin = plugin

        async def start(self):
            assert isinstance(state.manager, vision_module.SDKImageManager)
            state.events.append("monitor-start")

        async def stop(self):
            assert state.manager is not None
            state.events.append("monitor-stop")

    class Sender(Monitor):
        async def start(self):
            assert isinstance(state.manager, vision_module.SDKImageManager)
            state.events.append("sender-start")

        async def stop(self):
            assert state.manager is not None
            state.events.append("sender-stop")

    namespace = {
        "SDKImageManager": vision_module.SDKImageManager,
        "set_image_manager": register,
        "renew_cookies": AsyncMock(return_value=True),
        "FeedMonitor": Monitor,
        "ScheduleSender": Sender,
    }
    for name in (
        "set_api_capability", "set_tasks_logger", "set_qzoneapi_logger", "set_cookie_logger",
        "set_utils_plugin_context", "set_images_plugin_context",
    ):
        namespace[name] = Mock()
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(ROOT / "plugin.py"), "exec"), namespace)
    plugin = SimpleNamespace(
        ctx=SimpleNamespace(
            llm=llm,
            api=SimpleNamespace(),
            logger=Mock(),
            config=SimpleNamespace(get=AsyncMock(return_value={"personality": "测试人格", "reply_style": "简洁"})),
        ),
        config=SimpleNamespace(
            plugin=SimpleNamespace(text_model="replyer", http_host="127.0.0.1", http_port=0, napcat_token=""),
            auto_read=SimpleNamespace(enable_auto_read=enabled),
            auto_send=SimpleNamespace(enable_auto_send=enabled),
        ),
    )
    return namespace, plugin, state


@pytest.mark.parametrize("timers_enabled", [False, True])
def test_lifecycle_registers_before_timers_and_clears_after_stop(vision_module, timers_enabled):
    namespace, plugin, state = lifecycle_namespace(vision_module, timers_enabled)

    async def scenario():
        await namespace["on_load"](plugin)
        assert state.events[0] == "register"
        assert isinstance(state.manager, vision_module.SDKImageManager)
        assert await state.manager.get_image_description(PNG_BASE64) == f"[图片描述：{DESCRIPTION}]"
        assert_image_request(plugin.ctx.llm, PNG_BASE64, "png")
        await namespace["on_unload"](plugin)
        assert state.manager is None
        assert state.events[-1] == "clear"
        if timers_enabled:
            assert state.events == ["register", "monitor-start", "sender-start", "monitor-stop", "sender-stop", "clear"]
        else:
            assert state.events == ["register", "clear"]

    run(scenario())


def test_unload_clears_manager_even_when_timer_stop_fails(vision_module):
    namespace, plugin, state = lifecycle_namespace(vision_module, True)

    async def scenario():
        await namespace["on_load"](plugin)
        plugin.feed_monitor.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        with pytest.raises(RuntimeError, match="stop failed"):
            await namespace["on_unload"](plugin)
        assert state.manager is None
        assert state.events[-1] == "clear"

    run(scenario())


@pytest.mark.parametrize("failed_timer", ["monitor", "sender", "both"])
def test_unload_attempts_both_timers_before_clearing_manager(vision_module, failed_timer):
    namespace, plugin, state = lifecycle_namespace(vision_module, True)
    monitor_error = RuntimeError("monitor stop failed")
    sender_error = RuntimeError("sender stop failed")

    async def stop_monitor():
        assert state.manager is not None
        state.events.append("monitor-stop")
        if failed_timer in {"monitor", "both"}:
            raise monitor_error

    async def stop_sender():
        assert state.manager is not None
        state.events.append("sender-stop")
        if failed_timer in {"sender", "both"}:
            raise sender_error

    async def scenario():
        await namespace["on_load"](plugin)
        plugin.feed_monitor.stop = AsyncMock(side_effect=stop_monitor)
        plugin.schedule_sender.stop = AsyncMock(side_effect=stop_sender)
        with pytest.raises(RuntimeError) as caught:
            await namespace["on_unload"](plugin)

        plugin.feed_monitor.stop.assert_awaited_once()
        plugin.schedule_sender.stop.assert_awaited_once()
        assert state.events[-3:] == ["monitor-stop", "sender-stop", "clear"]
        assert state.manager is None
        assert caught.value is (monitor_error if failed_timer == "monitor" else sender_error)
        if failed_timer == "both":
            assert caught.value.__context__ is monitor_error

    run(scenario())


@pytest.mark.parametrize("cancelled_timer", ["monitor", "sender"])
def test_unload_cancellation_still_attempts_other_timer(vision_module, cancelled_timer):
    namespace, plugin, state = lifecycle_namespace(vision_module, True)
    cancellation = asyncio.CancelledError()

    async def stop_monitor():
        assert state.manager is not None
        state.events.append("monitor-stop")
        if cancelled_timer == "monitor":
            raise cancellation

    async def stop_sender():
        assert state.manager is not None
        state.events.append("sender-stop")
        if cancelled_timer == "sender":
            raise cancellation

    async def scenario():
        await namespace["on_load"](plugin)
        plugin.feed_monitor.stop = AsyncMock(side_effect=stop_monitor)
        plugin.schedule_sender.stop = AsyncMock(side_effect=stop_sender)
        with pytest.raises(asyncio.CancelledError) as caught:
            await namespace["on_unload"](plugin)

        assert caught.value is cancellation
        plugin.feed_monitor.stop.assert_awaited_once()
        plugin.schedule_sender.stop.assert_awaited_once()
        assert state.events[-3:] == ["monitor-stop", "sender-stop", "clear"]
        assert state.manager is None

    run(scenario())


@pytest.mark.parametrize("timer_name", ["feed_monitor", "schedule_sender"])
@pytest.mark.parametrize("stop_fails", [False, True])
def test_unload_with_only_one_timer(vision_module, timer_name, stop_fails):
    namespace, plugin, state = lifecycle_namespace(vision_module, False)
    stop_error = RuntimeError("single timer stop failed")

    async def stop_timer():
        assert state.manager is not None
        state.events.append("single-stop")
        if stop_fails:
            raise stop_error

    async def scenario():
        await namespace["on_load"](plugin)
        timer = SimpleNamespace(stop=AsyncMock(side_effect=stop_timer))
        setattr(plugin, timer_name, timer)
        if stop_fails:
            with pytest.raises(RuntimeError) as caught:
                await namespace["on_unload"](plugin)
            assert caught.value is stop_error
        else:
            await namespace["on_unload"](plugin)
        timer.stop.assert_awaited_once()
        assert state.events == ["register", "single-stop", "clear"]
        assert state.manager is None

    run(scenario())


def test_plugin_imports_the_real_manager_and_registration_hook():
    tree = ast.parse((ROOT / "plugin.py").read_text(encoding="utf-8"))
    imports = {
        (node.module, alias.name)
        for node in tree.body if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("vision", "SDKImageManager") in imports
    assert ("qzone_api", "set_image_manager") in imports
