import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from seedream_mcp.client import SeedreamClient, SeedreamError, image_uri, prepare, redacted
from seedream_mcp.models import ImageRequest, LocalOptions
from seedream_mcp.server import create_server


def png(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


def test_minimal_and_no_null_fields():
    body, warnings = prepare(ImageRequest(prompt="商品摄影"), LocalOptions(save_images=False))
    assert body == {"model": "doubao-seedream-5-0-pro-260628", "prompt": "商品摄影"}
    assert not warnings


def test_full_sdk_field_mapping():
    req = ImageRequest(prompt="图层", image=["https://example.com/a.png"], size="2K", response_format="url",
        output_format="png", watermark=False, seed=0, guidance_scale=2.5, optimize_prompt=False,
        optimize_prompt_options={"mode": "fast", "thinking": "enabled"}, tools=[{"type": "web_search"}],
        sequential_image_generation="disabled", layer_decomposition=True, stream=False,
        extra_body={"future_official_option": {"enabled": True}})
    body, warnings = prepare(req, LocalOptions(allow_unverified_parameters=True, save_images=False))
    expected = req.model_dump(exclude_none=True, exclude={"extra_body"})
    expected["model"] = "doubao-seedream-5-0-pro-260628"
    expected.update(req.extra_body)
    assert body == expected
    assert len(warnings) == 2


@pytest.mark.parametrize("ratio", ["1:1", "9:16", "16:9", "1:16", "16:1", "3:4"])
@pytest.mark.parametrize("resolution", ["1K", "2K"])
def test_aspect_ratios(ratio, resolution):
    body, _ = prepare(ImageRequest(prompt="test"), LocalOptions(aspect_ratio=ratio, resolution=resolution))
    w, h = map(int, body["size"].split("x"))
    a, b = map(float, ratio.split(":"))
    assert abs(w / h - a / b) < 0.06
    assert "aspect_ratio" not in body and "resolution" not in body
    assert body["response_format"] == "b64_json"


@pytest.mark.parametrize("fields", [{"stream": True}, {"size": "4K"}, {"size": "1x1"},
    {"size": "4096x4096"}, {"sequential_image_generation": "auto"},
    {"sequential_image_generation_options": {"max_images": 2}}, {"seed": 42},
    {"extra_body": {"model": "wrong"}}, {"extra_body": {"Authorization": "secret"}},
    {"image": []}, {"image": ["https://example.com/a.png"] * 11}, {"image": "file:///secret"}])
def test_invalid_options(fields):
    with pytest.raises(ValueError):
        prepare(ImageRequest(prompt="test", **fields), LocalOptions())


def test_conflicting_size_and_typo():
    with pytest.raises(ValueError):
        prepare(ImageRequest(prompt="test", size="2K"), LocalOptions(aspect_ratio="9:16"))
    with pytest.raises(ValidationError):
        ImageRequest(prompt="test", quality="high")


def test_order_and_image_redaction(tmp_path):
    paths = [tmp_path / "商品 一.png", tmp_path / "商品 二.png"]
    for path, color in zip(paths, ["red", "blue"]):
        path.write_bytes(png(color))
    body, _ = prepare(ImageRequest(prompt="test"), LocalOptions(reference_images=list(map(str, paths))))
    assert body["image"] == [image_uri(str(path)) for path in paths]
    assert png("red") == base64.b64decode(body["image"][0].split(",")[1])
    preview = redacted(body)
    assert "data:image" not in json.dumps(preview)
    with pytest.raises(ValueError):
        prepare(ImageRequest(prompt="test", image="https://example.com/a"), LocalOptions(reference_images=[str(paths[0])]))


def test_invalid_reference(tmp_path):
    path = tmp_path / "not-image.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        image_uri(str(path))
    assert "secret" not in json.dumps(redacted({"image": "https://example.com/private?signature=secret"}))


def test_generate_preserves_layers_and_saves(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert request.headers["Authorization"] == "Bearer test-only"
        assert json.loads(request.content)["response_format"] == "b64_json"
        return httpx.Response(200, headers={"x-request-id": "fake-request-id"}, json={
            "model": "test-model", "usage": {"generated_images": 2}, "created_at": 123,
            "data": [{"b64_json": base64.b64encode(png()).decode(), "size": "32x32", "z_index": 0},
                     {"b64_json": base64.b64encode(png("blue")).decode(), "z_index": 1,
                      "bounding_box": {"absolute": [1, 2, 3, 4]}, "name": "../not-a-path"}]})
    result = asyncio.run(SeedreamClient(api_key="test-only", transport=httpx.MockTransport(handler),
        output_dir=tmp_path).generate(ImageRequest(prompt="test"), LocalOptions()))
    assert len(calls) == 1
    assert result["usage"]["generated_images"] == 2
    assert result["data"][1]["bounding_box"] == {"absolute": [1, 2, 3, 4]}
    assert len(list(tmp_path.glob("*.png"))) == 2
    for entry in result["data"]:
        assert Path(entry["local_path"]).parent == tmp_path.resolve()
        assert "b64_json" not in entry


@pytest.mark.parametrize("kind", ["timeout", "provider_error", "non_json", "no_data"])
def test_errors_no_retry_no_secrets(kind, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("secret echo")
        if kind == "non_json":
            return httpx.Response(502, text="secret echo")
        if kind == "no_data":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(400, json={"error": {"code": "InvalidParameter", "message": "secret echo"}})
    with pytest.raises(SeedreamError) as error:
        asyncio.run(SeedreamClient(api_key="secret", transport=httpx.MockTransport(handler), output_dir=tmp_path)
                    .generate(ImageRequest(prompt="test"), LocalOptions()))
    assert len(calls) == 1
    assert "secret" not in str(error.value)


def test_missing_key_no_network():
    def handler(request):
        pytest.fail("Network called without key")
    with pytest.raises(SeedreamError, match="not configured"):
        asyncio.run(SeedreamClient(api_key="", transport=httpx.MockTransport(handler))
                    .generate(ImageRequest(prompt="test"), LocalOptions()))


def test_url_output_does_not_download(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://provider.example/a.png"}]})
    result = asyncio.run(SeedreamClient(api_key="fake", transport=httpx.MockTransport(handler), output_dir=tmp_path)
        .generate(ImageRequest(prompt="test", response_format="url"), LocalOptions()))
    assert len(calls) == 1 and result["data"][0]["url"].endswith("a.png")
    assert not list(tmp_path.iterdir())


def test_mcp_tools_schema():
    server = create_server()
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"seedream_generate", "seedream_capabilities", "seedream_preview_request", "seedance_capabilities", "seedance_preview_request", "seedance_create_task", "seedance_get_task", "video_enhance_capabilities", "video_enhance_preview_request", "video_enhance_upload", "video_enhance_create_task", "video_enhance_get_task"}
    tool = next(tool for tool in tools if tool.name == "seedream_generate")
    assert not tool.annotations.idempotentHint
    schema = json.dumps(tool.inputSchema)
    for name in ImageRequest.model_fields:
        assert f'"{name}"' in schema
