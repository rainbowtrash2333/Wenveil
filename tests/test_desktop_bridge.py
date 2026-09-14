import base64
import json
from pathlib import Path
import subprocess
import sys

from desktop.bridge.sidecar import handle_request
from organize.core import TextOrganizer


def test_sidecar_processes_and_restores_a_text_file(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-note.txt"
    source.write_text("联系人：张三，电话：13800138000\n", encoding="utf-8")
    output = tmp_path / "output"
    events: list[dict] = []

    result = handle_request(
        {
            "op": "process",
            "files": [{"id": "fixture-001", "name": source.name, "path": str(source)}],
            "steps": {"ocr": False, "organize": True, "mask": True, "audit": True},
            "entities": ["PERSON", "PHONE"],
            "password": "fixture-password",
            "outputDir": str(output),
        },
        events.append,
    )

    assert result["files"][0]["status"] == "done"
    assert any(event["type"] == "progress" for event in events)
    output_names = result["files"][0]["outputNames"]
    masked = next(output / name for name in output_names if name.endswith(".masked.md"))
    mapping = next(output / name for name in output_names if name.endswith(".mapping.enc"))

    restored = handle_request(
        {
            "op": "restore",
            "maskedPath": str(masked),
            "mappingPath": str(mapping),
            "password": "fixture-password",
            "outputDir": str(output),
        }
    )
    restored_text = (output / restored["outputName"]).read_text(encoding="utf-8")
    assert "13800138000" in restored_text


def test_sidecar_health_is_safe_and_structured() -> None:
    response = handle_request({"op": "health"})
    assert response == {"status": "ready", "capabilities": ["process", "restore"]}
    assert "synthetic" not in json.dumps(response)


def test_sidecar_accepts_file_contents_and_honors_intermediate_setting(tmp_path: Path) -> None:
    content = "联系人：测试用户，电话：13800138000\n".encode("utf-8")
    output = tmp_path / "output"
    result = handle_request(
        {
            "op": "process",
            "files": [{
                "id": "inline-001",
                "name": "synthetic.txt",
                "extension": ".txt",
                "size": len(content),
                "contentBase64": base64.b64encode(content).decode("ascii"),
            }],
            "steps": {"ocr": False, "organize": True, "mask": True, "audit": True},
            "entities": ["PHONE"],
            "password": "fixture-password",
            "outputDir": str(output),
            "keepIntermediate": False,
        }
    )
    names = result["files"][0]["outputNames"]
    assert not any(name.endswith(".normalized.md") for name in names)
    masked = next(output / name for name in names if name.endswith(".masked.md"))
    mapping = next(output / name for name in names if name.endswith(".mapping.enc"))

    restored = handle_request(
        {
            "op": "restore",
            "maskedPath": "masked-label.md",
            "mappingPath": "mapping-label.enc",
            "maskedContentBase64": base64.b64encode(masked.read_bytes()).decode("ascii"),
            "mappingContentBase64": base64.b64encode(mapping.read_bytes()).decode("ascii"),
            "password": "fixture-password",
            "outputDir": str(output),
        }
    )
    expected = TextOrganizer().organize_text(content.decode("utf-8"))
    assert (output / restored["outputName"]).read_text(encoding="utf-8") == expected


def test_sidecar_reports_malformed_json_without_internal_traceback() -> None:
    completed = subprocess.run(
        [sys.executable, "desktop/bridge/sidecar.py"],
        input="{not-json}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    response = json.loads(completed.stdout.strip())
    assert completed.returncode == 0
    assert response["type"] == "error"
    assert "Traceback" not in completed.stdout
    assert "Traceback" not in completed.stderr


def test_sidecar_rejects_invalid_inline_content_safely(tmp_path: Path) -> None:
    events: list[dict] = []
    result = handle_request(
        {
            "op": "process",
            "files": [{"id": "invalid-001", "name": "input.txt", "contentBase64": "not-base64"}],
            "steps": {"ocr": False, "organize": True, "mask": False, "audit": False},
            "outputDir": str(tmp_path / "output"),
        },
        events.append,
    )
    item = result["files"][0]
    assert item["status"] == "error"
    assert item["message"] == "输入文件内容无效"
    assert any(event["event"]["status"] == "error" for event in events)
    assert "not-base64" not in json.dumps(result)
