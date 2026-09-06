from __future__ import annotations

from pathlib import Path

from litclock.models import TimeConfidence
from litclock.timeparse import detect_time_expressions
from litclock.xhtml import extract_paragraphs, extract_quote_context, sentence_spans

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
  <body epub:type="bodymatter z3998:fiction">
    <article id="chapter-1">
      <h2>Chapter One</h2>
      <nav><p>At 01:00 Navigation</p></nav>
      <table><tr><td><p>At 02:00 Timetable</p></td></tr></table>
      <p>He waited in silence. At <em>4:37 p.m.</em> the old train finally arrived
      at the empty station. Nobody stepped down.</p>
      <aside epub:type="footnote"><p>At 03:00 a note.</p></aside>
    </article>
  </body>
</html>
"""


def test_semantic_xhtml_extraction_excludes_navigation_tables_and_notes(tmp_path: Path) -> None:
    path = tmp_path / "chapter-1.xhtml"
    path.write_text(XHTML, encoding="utf-8")
    paragraphs = extract_paragraphs(path)
    assert len(paragraphs) == 1
    paragraph = paragraphs[0]
    assert paragraph.section == "chapter-1"
    assert paragraph.paragraph_index == 3
    assert "4:37 p.m." in paragraph.text
    assert "Navigation" not in paragraph.text
    assert "Timetable" not in paragraph.text
    assert "a note" not in paragraph.text


def test_highlight_offsets_survive_xhtml_and_sentence_context_extraction(tmp_path: Path) -> None:
    path = tmp_path / "chapter-1.xhtml"
    path.write_text(XHTML, encoding="utf-8")
    paragraph = extract_paragraphs(path)[0]
    detection = detect_time_expressions(paragraph.text)[0]
    context = extract_quote_context(paragraph.text, detection)
    assert detection.confidence == TimeConfidence.EXACT_PM
    assert detection.minute_of_day == 16 * 60 + 37
    assert context.quote[context.highlight_start : context.highlight_end] == "4:37 p.m."
    assert context.quote.startswith("At 4:37 p.m.")
    assert context.quote.endswith("Nobody stepped down.")
    assert 80 <= len(context.quote) <= 350
    assert context.rejection_reason is None


def test_sentence_splitting_does_not_break_at_titles_or_meridiem() -> None:
    text = "Dr. Smith arrived at 4:37 p.m. He knocked."
    spans = sentence_spans(text)
    assert [text[start:end] for start, end in spans] == [
        "Dr. Smith arrived at 4:37 p.m.",
        "He knocked.",
    ]


def test_sentence_splitting_does_not_break_at_geographic_abbreviations() -> None:
    text = "At noon they reached lat. 72° S. The ship turned east."
    spans = sentence_spans(text)
    assert [text[start:end] for start, end in spans] == [
        "At noon they reached lat. 72° S.",
        "The ship turned east.",
    ]

    inline = "They reached the 82° S. depot at 10:30 a.m. and stopped."
    assert [inline[start:end] for start, end in sentence_spans(inline)] == [inline]

    units = "They travelled 1,000 yds. on the 7th and stopped at 10:30 a.m."
    assert [units[start:end] for start, end in sentence_spans(units)] == [units]


def test_frontmatter_file_is_excluded_even_when_it_contains_a_time(tmp_path: Path) -> None:
    path = tmp_path / "titlepage.xhtml"
    path.write_text(XHTML, encoding="utf-8")
    assert extract_paragraphs(path) == []


def test_lowercase_sentence_fragment_is_not_high_confidence_context() -> None:
    text = (
        "and at 04:37 the old train finally reached the empty country station, "
        "where nobody was waiting for it."
    )
    detection = detect_time_expressions(text)[0]
    context = extract_quote_context(text, detection)
    assert context.rejection_reason == "sentence context begins mid-sentence"
