from ontology_construct.config import ChunkConfig
from ontology_construct.preprocessor import TextChunker, estimate_tokens


def test_estimate_tokens_counts_cjk_and_latin_words() -> None:
    assert estimate_tokens("苹果公司 Apple Inc. 成立于 1976 年。") == 13


def test_chunker_keeps_stable_ids_and_metadata() -> None:
    text = "第一段介绍苹果公司。\n\n第二段介绍史蒂夫乔布斯。\n\n第三段介绍1976年。"
    chunker = TextChunker(ChunkConfig(size=12, overlap=0))

    chunks = chunker.split(text)

    assert [chunk.id for chunk in chunks] == ["chunk_0001", "chunk_0002", "chunk_0003"]
    assert chunks[0].start_char == 0
    assert chunks[0].metadata["position_basis"] == "cleaned_text"
    assert all(chunk.token_count <= 12 for chunk in chunks)


def test_chunker_preserves_overlap_segments() -> None:
    text = "第一段介绍苹果公司。\n\n第二段介绍账单。\n\n第三段介绍客户。"
    chunker = TextChunker(ChunkConfig(size=18, overlap=8))

    chunks = chunker.split(text)

    assert len(chunks) == 2
    assert "第二段介绍账单" in chunks[0].text
    assert "第二段介绍账单" in chunks[1].text


def test_chunker_offsets_stay_accurate_with_repeating_sentences() -> None:
    # A paragraph that repeats the same sentence twice would defeat the old
    # `find(part, local_start)` heuristic if the cursor lagged; ensure
    # the rewritten offset tracking still maps each segment back to the
    # right slice of the source text.
    text = "苹果公司成立。 苹果公司成立。"
    chunker = TextChunker(ChunkConfig(size=4, overlap=0))

    chunks = chunker.split(text)

    for chunk in chunks:
        assert text[chunk.start_char:chunk.end_char].strip() == chunk.text.strip()
