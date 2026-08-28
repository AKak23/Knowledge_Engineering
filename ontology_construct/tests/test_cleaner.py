from ontology_construct.preprocessor import clean_text


def test_clean_text_normalizes_line_endings_and_blank_lines() -> None:
    raw = " 第一行 \r\n\r\n\r\n第二行\t\n\x00第三行 "

    assert clean_text(raw) == "第一行\n\n第二行\n第三行"
