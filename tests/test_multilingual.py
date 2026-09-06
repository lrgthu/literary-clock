from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from litclock.db import connect_database, initialize_database
from litclock.models import CandidateConfidence, TimeConfidence
from litclock.multilingual import (
    candidate_quality_rejection,
    import_high_confidence,
    mine_multilingual_sources,
    multilingual_identity,
    normalized_multilingual_text,
)
from litclock.multilingual_sources import (
    ChineseGutenbergAdapter,
    FrenchGutenbergAdapter,
    MultilingualSource,
    load_source_manifest,
)
from litclock.selector import NoQuoteAvailable, QuoteSelector
from litclock.stats import calculate_stats
from litclock.timeparse_fr import detect_french_time_expressions, parse_french_number
from litclock.timeparse_zh import detect_chinese_time_expressions, parse_chinese_number
from litclock.xhtml import extract_quote_context, sentence_spans


def _fr(text: str):
    detections = detect_french_time_expressions(text)
    assert len(detections) == 1
    return detections[0]


def _zh(text: str):
    detections = detect_chinese_time_expressions(text)
    assert len(detections) == 1
    return detections[0]


@pytest.mark.parametrize(
    ("text", "minute", "possibilities", "rule"),
    [
        ("trois heures", None, (180, 900), "fr_hour"),
        ("trois heures cinq", None, (185, 905), "fr_hour_minute"),
        ("trois heures et quart", None, (195, 915), "fr_et_quart"),
        ("trois heures et demie", None, (210, 930), "fr_et_demie"),
        ("quatre heures moins le quart", None, (225, 945), "fr_moins_le_quart"),
        ("quatre heures moins vingt", None, (220, 940), "fr_moins"),
        ("quinze heures", 900, (900,), "fr_hour"),
        ("quinze heures trente", 930, (930,), "fr_hour_minute"),
        ("midi", 720, (720,), "fr_midi_minuit"),
        ("minuit", 0, (0,), "fr_midi_minuit"),
        ("midi et demi", 750, (750,), "fr_midi_minuit"),
        ("minuit et quart", 15, (15,), "fr_midi_minuit"),
        ("midi moins vingt", 700, (700,), "fr_midi_minuit"),
        ("minuit moins dix", 1430, (1430,), "fr_midi_minuit"),
        ("midi quarante-sept", 767, (767,), "fr_midi_minuit"),
        ("minuit vingt", 20, (20,), "fr_midi_minuit"),
        ("midi un quart", 735, (735,), "fr_midi_minuit"),
        ("trois heures du matin", 180, (180,), "fr_hour"),
        ("trois heures de l'après-midi", 900, (900,), "fr_hour"),
        ("trois heures de l’après-midi", 900, (900,), "fr_hour"),
    ],
)
def test_french_time_grammar(
    text: str, minute: int | None, possibilities: tuple[int, ...], rule: str
) -> None:
    detection = _fr(text)
    assert detection.text == text
    assert detection.minute_of_day == minute
    assert detection.possible_minutes == possibilities
    assert detection.parser_rule == rule
    assert detection.language == "fr"
    if minute is None:
        assert detection.candidate_confidence == CandidateConfidence.AMBIGUOUS_CLOCKFACE
        assert detection.semantic_type == "CLOCKFACE_12H"
    else:
        assert detection.candidate_confidence == CandidateConfidence.HIGH


@pytest.mark.parametrize(
    "text",
    [
        "Elle avait trois enfants et deux livres.",
        "CHAPITRE TROIS",
        "Projet Gutenberg, édition quinze.",
        "L'œuvre comprend vingt chapitres.",
    ],
)
def test_french_non_times_do_not_match(text: str) -> None:
    assert detect_french_time_expressions(text) == []


@pytest.mark.parametrize("text", ["25 heures", "trois heures soixante-dix"])
def test_french_impossible_times_are_explicit_rejections(text: str) -> None:
    detection = _fr(text)
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.candidate_confidence == CandidateConfidence.REJECT
    assert detection.minute_of_day is None
    assert detection.possible_minutes == ()


@pytest.mark.parametrize(
    ("text", "possibilities"),
    [
        ("vers midi", (720,)),
        ("bientôt minuit", (0,)),
        ("plus de minuit", (0,)),
        ("midi passé", (720,)),
        ("vers trois heures", (180, 900)),
        ("vers les deux heures du matin", (120,)),
        ("il pouvait être huit heures du soir", (1200,)),
        ("deux ou trois heures du matin", (180,)),
        ("vingt-quatre heures", (0,)),
    ],
)
def test_french_approximate_ranges_and_twenty_four_hour_idiom_are_review_only(
    text: str, possibilities: tuple[int, ...]
) -> None:
    detection = _fr(text)
    assert detection.candidate_confidence == CandidateConfidence.MEDIUM
    assert detection.possible_minutes == possibilities


def test_french_number_helper_is_structural() -> None:
    assert parse_french_number("trente et un") == 31
    assert parse_french_number("cinquante-neuf") == 59
    assert parse_french_number("70") == 70
    assert parse_french_number("soixante-dix") == 70
    assert parse_french_number("septante") is None


def test_french_unicode_offsets_preserve_accents_apostrophe_and_ligature() -> None:
    text = "L'œuvre était déjà fermée à trois heures de l’après-midi, paraît-il."
    detection = _fr(text)
    assert detection.text == "trois heures de l’après-midi"
    assert text[detection.start : detection.end] == detection.text
    assert len(text[: detection.start].encode("utf-8")) > detection.start
    context = extract_quote_context(text, detection)
    assert context.quote[context.highlight_start : context.highlight_end] == detection.text
    assert "œuvre" in context.quote and "déjà" in context.quote and "l’après-midi" in context.quote


def test_french_pipeline_gate_rejects_geographic_midi_and_duration() -> None:
    geographic = "Il connaissait tous les patois du midi et les récits de cette région lointaine."
    geographic_detection = _fr(geographic)
    assert candidate_quality_rejection("fr", geographic, geographic_detection) is not None
    duration = "On estima que le navire arriverait avec vingt heures de retard sur l'horaire prévu."
    duration_detection = _fr(duration)
    assert candidate_quality_rejection("fr", duration, duration_detection) is not None
    temporal = "À midi, le voyageur ferma la porte et quitta lentement la maison silencieuse."
    assert candidate_quality_rejection("fr", temporal, _fr(temporal)) is None


@pytest.mark.parametrize(
    ("text", "minute", "possibilities", "confidence"),
    [
        ("三点", None, (180, 900), CandidateConfidence.MEDIUM),
        ("三點", None, (180, 900), CandidateConfidence.MEDIUM),
        ("三点钟", None, (180, 900), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三點鐘", None, (180, 900), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三时", None, (180, 900), CandidateConfidence.MEDIUM),
        ("三時", None, (180, 900), CandidateConfidence.MEDIUM),
        ("三点五分", None, (185, 905), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三點五分", None, (185, 905), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三点十五分", None, (195, 915), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三点一刻", None, (195, 915), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三點一刻", None, (195, 915), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("三点半", None, (210, 930), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("四点三刻", None, (285, 1005), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("差一刻五点", None, (285, 1005), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("差十分五点", None, (290, 1010), CandidateConfidence.AMBIGUOUS_CLOCKFACE),
        ("十五点三十分", 930, (930,), CandidateConfidence.HIGH),
        ("十五時三十分", 930, (930,), CandidateConfidence.HIGH),
        ("凌晨三点", 180, (180,), CandidateConfidence.HIGH),
        ("上午九点", 540, (540,), CandidateConfidence.HIGH),
        ("中午十二点", 720, (720,), CandidateConfidence.HIGH),
        ("下午三点", 900, (900,), CandidateConfidence.HIGH),
        ("晚上八点", 1200, (1200,), CandidateConfidence.HIGH),
    ],
)
def test_chinese_time_grammar(
    text: str,
    minute: int | None,
    possibilities: tuple[int, ...],
    confidence: CandidateConfidence,
) -> None:
    detection = _zh(text)
    assert detection.text == text
    assert detection.minute_of_day == minute
    assert detection.possible_minutes == possibilities
    assert detection.candidate_confidence == confidence
    assert detection.language == "zh"


@pytest.mark.parametrize(
    "text",
    ["一点也不", "一点办法", "三点意见", "第一、第二、第三点", "提出三点建议"],
)
def test_chinese_false_positive_points_are_rejected(text: str) -> None:
    detection = _zh(text)
    assert detection.candidate_confidence == CandidateConfidence.REJECT
    assert detection.confidence == TimeConfidence.INVALID
    assert detection.minute_of_day is None
    assert detection.possible_minutes == ()
    assert detection.rejection_reason == "false_positive:NON_TEMPORAL_POINT"


@pytest.mark.parametrize("text", ["25点", "十三点七十分", "二十四点一分"])
def test_chinese_impossible_times_are_rejected(text: str) -> None:
    detection = _zh(text)
    assert detection.candidate_confidence == CandidateConfidence.REJECT
    assert detection.minute_of_day is None


def test_chinese_number_helper_handles_arabic_chinese_and_mixed_common_forms() -> None:
    assert parse_chinese_number("〇三") == 3
    assert parse_chinese_number("零九") == 9
    assert parse_chinese_number("十一") == 11
    assert parse_chinese_number("二十四") == 24
    assert parse_chinese_number("兩") == 2
    assert parse_chinese_number("15") == 15
    assert parse_chinese_number("两三") is None


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        ("風停了，下午三点鐘聲響起。", "下午三点鐘"),
        ("風停了，下午三點鐘聲響起。", "下午三點鐘"),
        ("夜裡寂靜；十五時三十分，門開了。", "十五時三十分"),
    ],
)
def test_chinese_unicode_offsets_are_codepoints_not_utf8_bytes(text: str, matched: str) -> None:
    detection = _zh(text)
    assert text[detection.start : detection.end] == matched
    assert len(text[: detection.start].encode("utf-8")) > detection.start
    context = extract_quote_context(text, detection)
    assert context.quote[context.highlight_start : context.highlight_end] == matched


def test_chinese_pipeline_gate_rejects_severe_mojibake() -> None:
    quote = "如有畎妐፥ಊﯿ酎멡潰몂ಌ槿，現在已經下午三點多鐘了，眾人還在等待。"
    assert candidate_quality_rejection("zh", quote, _zh(quote)) is not None


@pytest.mark.parametrize(
    ("text", "possibilities"),
    [
        ("下午三点多钟", (900,)),
        ("晚上八点来钟", (1200,)),
        ("约下午三点", (900,)),
        ("三点左右", (180, 900)),
        ("晚上九点钟以前", (1260,)),
        ("一过晚上十点钟", (1320,)),
    ],
)
def test_chinese_approximate_forms_are_review_only(
    text: str, possibilities: tuple[int, ...]
) -> None:
    detection = _zh(text)
    assert detection.confidence == TimeConfidence.APPROXIMATE
    assert detection.candidate_confidence == CandidateConfidence.MEDIUM
    assert detection.possible_minutes == possibilities


def test_language_aware_sentence_segmentation_retains_original_offsets() -> None:
    french = "M. Hugo attendit. À quinze heures, il partit ! Puis il revint."
    assert [french[start:end] for start, end in sentence_spans(french, language="fr")] == [
        "M. Hugo attendit.",
        "À quinze heures, il partit !",
        "Puis il revint.",
    ]
    chinese = "他等著。下午三点，鐘響了！她問：到了嗎？眾人沉默；後來……門開了。"
    assert [chinese[start:end] for start, end in sentence_spans(chinese, language="zh")] == [
        "他等著。",
        "下午三点，鐘響了！",
        "她問：到了嗎？",
        "眾人沉默；",
        "後來……",
        "門開了。",
    ]


def test_language_is_part_of_dedup_identity_and_script_is_not_converted() -> None:
    assert normalized_multilingual_text("L'œuvre était déjà là.") == "lœuvreétaitdéjàlà"
    assert normalized_multilingual_text("三點鐘") == "三點鐘"
    assert normalized_multilingual_text("三点钟") == "三点钟"
    assert multilingual_identity("fr", "Même texte", (180,)) != multilingual_identity(
        "en", "Même texte", (180,)
    )
    assert multilingual_identity("zh", "三點鐘", (180,)) != multilingual_identity(
        "zh", "三点钟", (180,)
    )


def _source(path: Path, language: str, title: str, author: str) -> MultilingualSource:
    return MultilingualSource(
        source_project="Project Gutenberg",
        source_id=f"test-{language}",
        language=language,
        script_variant="zh-Hant" if language == "zh" else None,
        title=title,
        author=author,
        translator_editor=None,
        source_url=f"https://www.gutenberg.org/ebooks/test-{language}",
        download_url=f"https://www.gutenberg.org/cache/epub/test-{language}/pg.txt",
        source_license="Public domain in the USA.",
        rights_evidence="Project Gutenberg catalog rights field in test fixture",
        publication_metadata=json.dumps({"fixture": True}),
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        filename=path.name,
    )


def test_staged_pipeline_imports_only_high_and_preserves_language_filtering(
    tmp_path: Path,
) -> None:
    data = tmp_path / "sources"
    (data / "fr").mkdir(parents=True)
    (data / "zh").mkdir(parents=True)
    french = data / "fr" / "fr.txt"
    chinese = data / "zh" / "zh.txt"
    french.write_text(
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n\n"
        "À quinze heures trente, Élodie ferma doucement la porte de l'œuvre, "
        "puis elle regarda longuement la rue devenue silencieuse.\n\n"
        "À trois heures, elle hésita longtemps devant la fenêtre.\n\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\n",
        encoding="utf-8",
    )
    chinese.write_text(
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n\n"
        "下午三點鐘，院子裡忽然安靜下來，老人慢慢關上木門，回頭看著眾人。\n\n"
        "三點，這份文稿提出三點意見，眾人依舊坐在桌旁爭論不休。\n\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\n",
        encoding="utf-8",
    )
    database = tmp_path / "corpus.sqlite3"
    connection = connect_database(database)
    initialize_database(connection)
    sources = [
        _source(french, "fr", "Œuvre française", "Élodie Test"),
        _source(chinese, "zh", "測試小說", "測試作者"),
    ]
    fr_run = mine_multilingual_sources(connection, sources, data, language="fr")
    zh_run = mine_multilingual_sources(connection, sources, data, language="zh")
    assert fr_run["sources_scanned"] == 1
    assert zh_run["sources_scanned"] == 1
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM multilingual_candidates WHERE language = 'fr'"
        ).fetchone()[0]
        == 2
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM multilingual_candidates WHERE language = 'zh'"
        ).fetchone()[0]
        >= 2
    )

    assert import_high_confidence(connection, language="fr")["imported"] == 1
    assert import_high_confidence(connection, language="zh")["imported"] == 1
    assert (
        connection.execute("SELECT quote FROM quotes WHERE language = 'fr'")
        .fetchone()[0]
        .find("Élodie")
        >= 0
    )
    assert (
        "下午三點鐘"
        in connection.execute("SELECT quote FROM quotes WHERE language = 'zh'").fetchone()[0]
    )

    fr_stats = calculate_stats(connection, language="fr")
    zh_stats = calculate_stats(connection, language="zh")
    mixed_stats = calculate_stats(connection, language="mixed")
    assert fr_stats["unique_selectable_quotes"] == 1
    assert zh_stats["unique_selectable_quotes"] == 1
    assert mixed_stats["unique_selectable_quotes"] == 2
    assert mixed_stats["minutes_covered"] == 2

    with pytest.raises(NoQuoteAvailable):
        QuoteSelector(connection).preview("15:30")
    assert QuoteSelector(connection, language="fr").preview("15:30").language == "fr"
    assert QuoteSelector(connection, language="zh").preview("15:00").language == "zh"
    connection.close()


def test_additive_schema_backfills_english_language_semantics(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "corpus.sqlite3")
    initialize_database(connection)
    created = "2026-09-06T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO quotes (
            minute_of_day, time_24h, time_text, quote, title, author, language,
            source_name, source_url, source_license, quote_hash, normalized_quote_hash,
            highlight_start, highlight_end, quality_status, created_at
        ) VALUES (180, '03:00', 'three o''clock', 'At three o''clock, she left.',
                  'Book', 'Author', 'en-GB', 'source', 'https://example.test', 'PD',
                  'quote-hash', 'normalized-hash', 3, 16, 'VERIFIED_EXACT', ?)
        """,
        (created,),
    )
    initialize_database(connection)
    semantics = connection.execute(
        "SELECT language, matched_text, possible_minutes FROM quote_time_semantics"
    ).fetchone()
    eligibility = connection.execute(
        "SELECT language, minute_of_day FROM quote_minute_eligibility"
    ).fetchone()
    assert tuple(semantics) == ("en-GB", "three o'clock", "[180]")
    assert tuple(eligibility) == ("en-GB", 180)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_source_specific_gutenberg_adapters_preserve_original_script() -> None:
    french = (
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n\n"
        "À quinze heures, l'œuvre s'ouvrit devant eux.\n\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***"
    )
    chinese = (
        "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n\n"
        "下午三點鐘，門開了。\n\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***"
    )
    assert FrenchGutenbergAdapter.paragraphs(french)[0].text == (
        "À quinze heures, l'œuvre s'ouvrit devant eux."
    )
    assert ChineseGutenbergAdapter.paragraphs(chinese)[0].text == "下午三點鐘，門開了。"


def test_committed_source_and_dump_manifests_are_pinned() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sources = load_source_manifest(project_root / "corpus" / "multilingual_sources.json")
    assert sum(source.language == "fr" for source in sources) == 6
    assert sum(source.language == "zh" for source in sources) == 21
    assert all(len(source.expected_sha256) == 64 for source in sources)
    dumps = json.loads(
        (project_root / "corpus" / "wikisource_dump_pins.json").read_text(encoding="utf-8")
    )["dumps"]
    assert {dump["language"] for dump in dumps} == {"fr", "zh"}
    assert all(len(sha1) == 40 and size > 0 for dump in dumps for _, size, sha1 in dump["files"])
