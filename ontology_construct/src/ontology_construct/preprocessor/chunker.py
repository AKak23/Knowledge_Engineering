from __future__ import annotations

import re
from dataclasses import dataclass

from ontology_construct.config import ChunkConfig
from ontology_construct.models import Chunk


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]")
SENTENCE_END_RE = re.compile(r"(?<=[。！？.!?])\s+")


@dataclass(frozen=True)
class TextSegment:
    text: str
    start_char: int
    end_char: int


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(TOKEN_RE.findall(text))


class TextChunker:
    def __init__(self, config: ChunkConfig):
        if config.overlap >= config.size:
            raise ValueError("chunk overlap must be smaller than chunk size")
        self.config = config

    def split(self, text: str) -> list[Chunk]:
        segments = self._build_segments(text)
        chunks: list[Chunk] = []
        current: list[TextSegment] = []
        current_tokens = 0

        for segment in segments:
            segment_tokens = estimate_tokens(segment.text)
            if current and current_tokens + segment_tokens > self.config.size:
                chunks.append(self._make_chunk(current, len(chunks) + 1))
                current = self._overlap_segments(current)
                current_tokens = sum(estimate_tokens(item.text) for item in current)

            current.append(segment)
            current_tokens += segment_tokens

        if current:
            chunks.append(self._make_chunk(current, len(chunks) + 1))

        return chunks

    def _build_segments(self, text: str) -> list[TextSegment]:
        segments: list[TextSegment] = []
        for paragraph in self._paragraphs(text):
            if estimate_tokens(paragraph.text) <= self.config.size:
                segments.append(paragraph)
                continue
            segments.extend(self._split_long_paragraph(paragraph))
        return segments

    def _paragraphs(self, text: str) -> list[TextSegment]:
        paragraphs: list[TextSegment] = []
        cursor = 0
        for part in re.split(r"(\n\s*\n)", text):
            if not part:
                continue
            start = cursor
            end = cursor + len(part)
            cursor = end
            if part.strip():
                stripped_start = start + len(part) - len(part.lstrip())
                stripped_end = end - (len(part) - len(part.rstrip()))
                paragraphs.append(
                    TextSegment(
                        text=text[stripped_start:stripped_end],
                        start_char=stripped_start,
                        end_char=stripped_end,
                    )
                )
        return paragraphs

    def _split_long_paragraph(self, paragraph: TextSegment) -> list[TextSegment]:
        sentences = self._sentences(paragraph)
        segments: list[TextSegment] = []
        for sentence in sentences:
            if estimate_tokens(sentence.text) <= self.config.size:
                segments.append(sentence)
                continue
            segments.extend(self._hard_split(sentence))
        return segments

    def _sentences(self, paragraph: TextSegment) -> list[TextSegment]:
        # Walk the regex matches so each segment's offsets come straight from
        # the split boundaries; never rely on `text.find(part, ...)`, which can
        # mis-locate parts that share a prefix with earlier content.
        segments: list[TextSegment] = []
        text = paragraph.text
        cursor = 0
        for match in SENTENCE_END_RE.finditer(text):
            segment = _build_sentence_segment(text, cursor, match.start(), paragraph.start_char)
            if segment is not None:
                segments.append(segment)
            cursor = match.end()
        tail = _build_sentence_segment(text, cursor, len(text), paragraph.start_char)
        if tail is not None:
            segments.append(tail)
        return segments

    def _hard_split(self, segment: TextSegment) -> list[TextSegment]:
        token_spans = list(TOKEN_RE.finditer(segment.text))
        if not token_spans:
            return [segment]

        pieces: list[TextSegment] = []
        step = self.config.size
        for index in range(0, len(token_spans), step):
            token_slice = token_spans[index : index + step]
            start = token_slice[0].start()
            end = token_slice[-1].end()
            pieces.append(
                TextSegment(
                    text=segment.text[start:end],
                    start_char=segment.start_char + start,
                    end_char=segment.start_char + end,
                )
            )
        return pieces

    def _overlap_segments(self, segments: list[TextSegment]) -> list[TextSegment]:
        if self.config.overlap == 0:
            return []

        selected: list[TextSegment] = []
        token_total = 0
        for segment in reversed(segments):
            segment_tokens = estimate_tokens(segment.text)
            if selected and token_total + segment_tokens > self.config.overlap:
                break
            selected.append(segment)
            token_total += segment_tokens
            if token_total >= self.config.overlap:
                break
        return list(reversed(selected))

    def _make_chunk(self, segments: list[TextSegment], index: int) -> Chunk:
        text = "\n\n".join(segment.text for segment in segments)
        source_start = segments[0].start_char
        source_end = segments[-1].end_char
        return Chunk(
            id=f"chunk_{index:04d}",
            text=text,
            start_char=source_start,
            end_char=source_end,
            token_count=estimate_tokens(text),
            metadata={
                "segment_count": len(segments),
                "position_basis": "cleaned_text",
                "estimated_tokens": True,
                "chunk_size": self.config.size,
                "chunk_overlap": self.config.overlap,
                "char_length": len(text),
                "token_estimator": "regex_cjk_char_latin_word_punctuation",
            },
        )


def _build_sentence_segment(
    text: str, start: int, end: int, paragraph_offset: int
) -> TextSegment | None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return None
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    return TextSegment(
        text=stripped,
        start_char=paragraph_offset + start + leading,
        end_char=paragraph_offset + end - trailing,
    )
