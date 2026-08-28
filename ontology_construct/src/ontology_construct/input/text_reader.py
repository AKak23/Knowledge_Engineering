from __future__ import annotations

import codecs
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from pypdf import PdfReader

SUPPORTED_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".log",
    ".json",
    ".jsonl",
}
TEXT_FALLBACK_ENCODINGS = ("utf-8", "gb18030")
BOM_ENCODINGS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
LOG_MAX_EVENT_TEMPLATES = 120
LOG_MAX_SAMPLES_PER_TEMPLATE = 2
LOG_MAX_SAMPLE_CHARS = 260
LOG_MAX_VALUE_EXAMPLES = 8
LOG_TIMESTAMP_RE = re.compile(
    r"\b(?:\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?"
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?)\b"
)
LOG_LEVEL_RE = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|ERR|ALARM|CRITICAL|FATAL)\b", re.I)
LOG_KV_RE = re.compile(
    r"(?P<key>[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_.:\-\u4e00-\u9fff]{0,63})\s*[:=]\s*"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;，；]+)"
)
LOG_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
LOG_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
LOG_LONG_ID_RE = re.compile(r"\b[A-Za-z]*\d[A-Za-z0-9_-]{5,}\b")
LOG_NUMBER_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")


@dataclass
class LogTemplateStats:
    template: str
    count: int = 0
    levels: Counter[str] = field(default_factory=Counter)
    first_timestamp: str = ""
    last_timestamp: str = ""
    samples: list[str] = field(default_factory=list)
    field_names: Counter[str] = field(default_factory=Counter)
    field_values: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))


def read_text(path: Path | str) -> str:
    input_path = Path(path)
    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_TEXT_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_TEXT_SUFFIXES))
        raise ValueError(f"Unsupported input suffix {suffix!r}. Supported: {supported}")
    if suffix == ".pdf":
        return read_pdf_text(input_path)
    if suffix == ".docx":
        return read_docx_text(input_path)
    if suffix == ".log":
        return read_log_text(input_path)
    if suffix == ".json":
        return read_json_text(input_path)
    if suffix == ".jsonl":
        return read_jsonl_text(input_path)
    return read_encoded_text(input_path)


def read_log_text(path: Path) -> str:
    raw_text = read_encoded_text(path)
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"No log records found in log file: {path}")

    level_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    important_values: dict[str, Counter[str]] = defaultdict(Counter)
    templates: dict[str, LogTemplateStats] = {}
    timestamp_count = 0

    for line in lines:
        parsed = _parse_log_line(line)
        if parsed["timestamp"]:
            timestamp_count += 1
        level = parsed["level"] or "UNKNOWN"
        level_counts[level] += 1
        fields = parsed["fields"]
        field_counts.update(fields.keys())
        for key, value in fields.items():
            if _is_important_log_field(key):
                important_values[key][value] += 1

        template = parsed["template"]
        stats = templates.get(template)
        if stats is None:
            stats = LogTemplateStats(template=template)
            templates[template] = stats
        stats.count += 1
        stats.levels[level] += 1
        if parsed["timestamp"]:
            stats.first_timestamp = stats.first_timestamp or parsed["timestamp"]
            stats.last_timestamp = parsed["timestamp"]
        stats.field_names.update(fields.keys())
        for key, value in fields.items():
            if _is_important_log_field(key):
                stats.field_values[key][value] += 1
        if len(stats.samples) < LOG_MAX_SAMPLES_PER_TEMPLATE:
            stats.samples.append(_truncate_log_sample(line))

    sorted_templates = sorted(
        templates.values(),
        key=lambda item: (
            -item.count,
            -sum(item.levels[level] for level in ("ERROR", "ERR", "ALARM", "CRITICAL", "FATAL")),
            item.template,
        ),
    )[:LOG_MAX_EVENT_TEMPLATES]

    return _render_log_summary(
        source_name=path.name,
        total_lines=len(lines),
        timestamp_count=timestamp_count,
        level_counts=level_counts,
        field_counts=field_counts,
        important_values=important_values,
        templates=sorted_templates,
        total_template_count=len(templates),
    )


def read_encoded_text(path: Path) -> str:
    data = path.read_bytes()

    for bom, encoding in BOM_ENCODINGS:
        if data.startswith(bom):
            return data.decode(encoding)

    errors: list[UnicodeDecodeError] = []
    for encoding in TEXT_FALLBACK_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(exc)

    raise UnicodeDecodeError(
        errors[0].encoding,
        errors[0].object,
        errors[0].start,
        errors[0].end,
        f"Unable to decode text file {path}. Tried: "
        f"{', '.join(TEXT_FALLBACK_ENCODINGS)}",
    )


def _parse_log_line(line: str) -> dict[str, object]:
    timestamp_match = LOG_TIMESTAMP_RE.search(line)
    timestamp = timestamp_match.group(0) if timestamp_match else ""
    level_match = LOG_LEVEL_RE.search(line)
    level = level_match.group(1).upper() if level_match else ""
    if level == "WARNING":
        level = "WARN"
    if level == "ERR":
        level = "ERROR"
    fields = {
        match.group("key"): _strip_log_value(match.group("value"))
        for match in LOG_KV_RE.finditer(line)
    }
    template = _normalize_log_template(line)
    return {
        "timestamp": timestamp,
        "level": level,
        "fields": fields,
        "template": template,
    }


def _normalize_log_template(line: str) -> str:
    template = LOG_TIMESTAMP_RE.sub("<time>", line)
    template = LOG_UUID_RE.sub("<id>", template)
    template = LOG_HEX_RE.sub("<hex>", template)
    template = LOG_KV_RE.sub(lambda match: f"{match.group('key')}=<{_value_kind(match.group('value'))}>", template)
    template = LOG_LONG_ID_RE.sub("<id>", template)
    template = LOG_NUMBER_RE.sub("<num>", template)
    template = re.sub(r"\s+", " ", template).strip()
    return template[:LOG_MAX_SAMPLE_CHARS] or "<empty log message>"


def _value_kind(value: str) -> str:
    stripped = _strip_log_value(value)
    if LOG_TIMESTAMP_RE.fullmatch(stripped):
        return "time"
    if LOG_UUID_RE.fullmatch(stripped) or LOG_LONG_ID_RE.fullmatch(stripped):
        return "id"
    if LOG_HEX_RE.fullmatch(stripped):
        return "hex"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        return "num"
    return "value"


def _strip_log_value(value: str) -> str:
    stripped = value.strip().strip(",;，；")
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _is_important_log_field(key: str) -> bool:
    normalized = key.lower()
    important_fragments = (
        "device",
        "station",
        "line",
        "workshop",
        "event",
        "code",
        "alarm",
        "error",
        "fault",
        "status",
        "state",
        "step",
        "process",
        "machine",
        "equipment",
        "工位",
        "设备",
        "产线",
        "事件",
        "告警",
        "报警",
        "状态",
    )
    return any(fragment in normalized for fragment in important_fragments)


def _truncate_log_sample(line: str) -> str:
    text = re.sub(r"\s+", " ", line).strip()
    if len(text) <= LOG_MAX_SAMPLE_CHARS:
        return text
    return f"{text[:LOG_MAX_SAMPLE_CHARS]}..."


def _render_log_summary(
    *,
    source_name: str,
    total_lines: int,
    timestamp_count: int,
    level_counts: Counter[str],
    field_counts: Counter[str],
    important_values: dict[str, Counter[str]],
    templates: list[LogTemplateStats],
    total_template_count: int,
) -> str:
    sections = [
        f"[LOG SUMMARY] {source_name}",
        "用途：这是从原始日志压缩得到的事件模板、字段和值样例摘要。构建本体时请同时抽取本体层信息和实例/事件层信息。",
        "",
        "总体统计：",
        f"- 原始日志行数：{total_lines}",
        f"- 含时间戳行数：{timestamp_count}",
        f"- 唯一事件模板数：{total_template_count}",
        f"- 输出事件模板数：{len(templates)}",
        f"- 日志级别分布：{_format_counter(level_counts)}",
        f"- 字段分布：{_format_counter(field_counts, limit=24)}",
    ]
    if important_values:
        sections.append("")
        sections.append("关键字段取值样例：")
        for key in sorted(important_values):
            sections.append(f"- {key}: {_format_counter(important_values[key], limit=LOG_MAX_VALUE_EXAMPLES)}")

    sections.append("")
    sections.append("事件模板聚合：")
    for index, stats in enumerate(templates, start=1):
        sections.extend(_render_log_template(index, stats))
    return "\n".join(sections).strip()


def _render_log_template(index: int, stats: LogTemplateStats) -> list[str]:
    lines = [
        "",
        f"## 事件模板 {index}",
        f"- 出现次数：{stats.count}",
        f"- 日志级别：{_format_counter(stats.levels)}",
    ]
    if stats.first_timestamp or stats.last_timestamp:
        lines.append(f"- 时间范围：{stats.first_timestamp or '未知'} -> {stats.last_timestamp or '未知'}")
    if stats.field_names:
        lines.append(f"- 字段：{_format_counter(stats.field_names, limit=16)}")
    for key in sorted(stats.field_values):
        lines.append(f"- {key} 样例：{_format_counter(stats.field_values[key], limit=LOG_MAX_VALUE_EXAMPLES)}")
    lines.append(f"- 模板：{stats.template}")
    lines.append("- 典型样例：")
    for sample in stats.samples:
        lines.append(f"  - {sample}")
    return lines


def _format_counter(counter: Counter[str], limit: int = 12) -> str:
    if not counter:
        return "无"
    return "，".join(f"{key}={value}" for key, value in counter.most_common(limit))


def read_pdf_text(path: Path) -> str:
    reader = PdfReader(path)
    pages: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(f"\n\n[Page {page_index}]\n{page_text}")
    text = "\n".join(pages).strip()
    if not text:
        raise ValueError(
            f"No extractable text found in PDF: {path}. "
            "Scanned PDFs require OCR, which is not implemented yet."
        )
    return text


def read_json_text(path: Path) -> str:
    raw_text = read_encoded_text(path)
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON file {path}: line {exc.lineno} column {exc.colno}"
        ) from exc
    return json.dumps(payload, ensure_ascii=False, indent=2)


def read_jsonl_text(path: Path) -> str:
    raw_text = read_encoded_text(path)
    records: list[str] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSONL file {path} at line {line_number}: {exc.msg}"
            ) from exc
        records.append(
            f"[JSONL record {len(records) + 1}]\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
    if not records:
        raise ValueError(f"No JSON records found in JSONL file: {path}")
    return "\n\n".join(records)


def read_docx_text(path: Path) -> str:
    try:
        with ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError) as exc:
        raise ValueError(f"Unable to read DOCX text from {path}") from exc

    root = ET.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [
            text_node.text or ""
            for text_node in paragraph.findall(".//w:t", namespace)
        ]
        text = "".join(texts).strip()
        if text:
            paragraphs.append(text)
    if not paragraphs:
        raise ValueError(f"No extractable text found in DOCX: {path}")
    return "\n\n".join(paragraphs)
