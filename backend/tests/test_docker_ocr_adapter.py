from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.db.session import SessionLocal
from app.models import GatewayConfig
from app.providers.ocr import PaddleXLayoutParsingOcrClient
from app.services.gateways import seed_default_gateways


@pytest.mark.asyncio
async def test_paddlex_layout_adapter_preserves_blocks_and_raw_response(
    tmp_path: Path,
) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"not-a-real-jpeg-needed-by-mock")
    raw_path = tmp_path / "raw.json"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/layout-parsing"
        body = json.loads(request.content)
        assert body["fileType"] == 1
        assert body["returnMarkdownImages"] is False
        assert body["useChartRecognition"] is True
        assert body["visualize"] is False
        assert body["temperature"] == 0
        return httpx.Response(
            200,
            json={
                "logId": "request-1",
                "errorCode": 0,
                "errorMsg": "Success",
                "result": {
                    "layoutParsingResults": [
                        {
                            "prunedResult": {
                                "parsing_res_list": [
                                    {
                                        "block_bbox": [10, 20, 110, 70],
                                        "block_label": "text",
                                        "block_content": "PaddleOCR-VL 示例",
                                        "block_id": 3,
                                        "block_order": 1,
                                    },
                                    {
                                        "block_bbox": [10, 80, 210, 130],
                                        "block_label": "display_formula",
                                        "block_content": "$$E=mc^2$$",
                                        "block_id": 4,
                                        "block_order": 2,
                                    },
                                ]
                            },
                            "markdown": {
                                "text": "PaddleOCR-VL 示例\n\n$$E=mc^2$$",
                                "images": None,
                            },
                            "outputImages": None,
                            "inputImage": None,
                        }
                    ]
                },
            },
        )

    client = PaddleXLayoutParsingOcrClient(
        base_url="http://127.0.0.1:8202",
        transport=httpx.MockTransport(handler),
    )
    result = await client.recognize(
        image,
        model="PaddleOCR-VL-1.6-0.9B",
        raw_path=raw_path,
        frame_id="frame_000001",
        media_timestamp=12.5,
        language="zh",
    )

    assert result.provider == "paddlex-layout-parsing-v1"
    assert result.frame_id == "frame_000001"
    assert result.media_timestamp == 12.5
    assert [item.text for item in result.blocks] == [
        "PaddleOCR-VL 示例",
        "$$E=mc^2$$",
    ]
    assert result.blocks[0].bounding_box == (10.0, 20.0, 110.0, 70.0)
    assert result.blocks[1].block_type == "display_formula"
    assert raw_path.is_file()


def test_default_catalog_records_external_docker_gateway_without_managing_it() -> None:
    with SessionLocal() as session:
        seed_default_gateways(session)
        gateway = (
            session.query(GatewayConfig)
            .filter_by(name="docker-paddleocr-vl")
            .one()
        )
        assert gateway.protocol == "paddlex-layout-parsing-v1"
        assert gateway.enabled is False
        assert gateway.settings["managed_lifecycle"] is False
        assert gateway.models[0].capabilities["layout_parsing"] is True
