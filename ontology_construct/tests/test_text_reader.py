import pytest
from zipfile import ZipFile

from ontology_construct.input import read_text


def test_read_text_supports_markdown(tmp_path) -> None:
    path = tmp_path / "sample.md"
    path.write_text("# 标题\n正文", encoding="utf-8")

    assert read_text(path) == "# 标题\n正文"


def test_read_text_supports_utf8_bom(tmp_path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("标题\n正文", encoding="utf-8-sig")

    assert read_text(path) == "标题\n正文"


def test_read_text_supports_utf16_bom(tmp_path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("前言\n正文", encoding="utf-16")

    assert read_text(path) == "前言\n正文"


def test_read_text_supports_gb18030(tmp_path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes("标题\n正文".encode("gb18030"))

    assert read_text(path) == "标题\n正文"


def test_read_text_supports_docx(tmp_path) -> None:
    path = tmp_path / "sample.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>第一段</w:t></w:r></w:p>
        <w:p><w:r><w:t>第二段</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    assert read_text(path) == "第一段\n\n第二段"


def test_read_text_supports_json(tmp_path) -> None:
    path = tmp_path / "sample.json"
    path.write_text(
        '{"title":"标题","items":[{"name":"张三","role":"用户"}]}',
        encoding="utf-8",
    )

    assert read_text(path) == (
        '{\n'
        '  "title": "标题",\n'
        '  "items": [\n'
        '    {\n'
        '      "name": "张三",\n'
        '      "role": "用户"\n'
        '    }\n'
        '  ]\n'
        '}'
    )


def test_read_text_supports_jsonl(tmp_path) -> None:
    path = tmp_path / "sample.jsonl"
    path.write_text(
        '{"name":"张三","role":"用户"}\n\n{"name":"订单A","amount":128}\n',
        encoding="utf-8",
    )

    assert read_text(path) == (
        '[JSONL record 1]\n'
        '{\n'
        '  "name": "张三",\n'
        '  "role": "用户"\n'
        '}\n\n'
        '[JSONL record 2]\n'
        '{\n'
        '  "name": "订单A",\n'
        '  "amount": 128\n'
        '}'
    )


def test_read_text_summarizes_log_events(tmp_path) -> None:
    path = tmp_path / "factory.log"
    path.write_text(
        "\n".join(
            [
                "2026-06-23 10:00:01 INFO station=S1 device=WELD-001 event=START status=RUNNING",
                "2026-06-23 10:01:02 ERROR station=S1 device=WELD-001 event=TEMP_HIGH temperature=88.1 threshold=80",
                "2026-06-23 10:02:03 ERROR station=S1 device=WELD-001 event=TEMP_HIGH temperature=91.3 threshold=80",
                "2026-06-23 10:03:04 WARN station=S2 device=PRESS-002 event=PRESSURE_LOW pressure=0.7",
            ]
        ),
        encoding="utf-8",
    )

    text = read_text(path)

    assert text.startswith("[LOG SUMMARY] factory.log")
    assert "原始日志行数：4" in text
    assert "日志级别分布：ERROR=2，INFO=1，WARN=1" in text
    assert "event 样例：TEMP_HIGH=2" in text
    assert "event=<value>" in text
    assert "temperature=<num>" in text
    assert "典型样例" in text


def test_read_text_compresses_repetitive_log(tmp_path) -> None:
    path = tmp_path / "large.log"
    raw_lines = [
        f"2026-06-23 10:{index % 60:02d}:00 ERROR device=MOTOR-{index % 3:03d} event=OVERLOAD current={100 + index}"
        for index in range(200)
    ]
    path.write_text("\n".join(raw_lines), encoding="utf-8")

    text = read_text(path)

    assert "原始日志行数：200" in text
    assert "出现次数：200" in text
    assert len(text) < len("\n".join(raw_lines))


def test_read_text_rejects_invalid_jsonl(tmp_path) -> None:
    path = tmp_path / "sample.jsonl"
    path.write_text('{"name":"张三"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSONL file .* at line 2"):
        read_text(path)


def test_read_text_rejects_unknown_suffix(tmp_path) -> None:
    path = tmp_path / "sample.doc"
    path.write_text("正文", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported input suffix"):
        read_text(path)
