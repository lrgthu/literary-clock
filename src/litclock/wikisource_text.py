"""Wikitext parsing, licensing evidence, and prose extraction for Wikisource."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import mwparserfromhell
from mwparserfromhell.nodes import Template

_PD_TEMPLATE_PREFIXES = (
    "pd-old",
    "pd-us",
    "pd-1923",
    "pd-1996",
    "pd-anon",
    "pd-author",
    "pd-not-renewed",
    "pd-posthumous",
    "pd-edictgov",
    "pd-usgov",
)
_NAVIGATION_TEMPLATES = {
    "header",
    "subpage list",
    "authority control",
    "defaultsort",
    "featured text",
    "textquality",
    "similar",
    "versions",
    "translations",
    "wikipediaref",
    "plain sister",
    "portal",
    "rh",
    "runningheader",
    "page break",
    "nop",
}
_TEXT_TEMPLATES = {
    "sic": 1,
    "corr": 2,
    "lang": -1,
    "nowrap": -1,
    "smaller": -1,
    "larger": -1,
    "smallcaps": -1,
    "sc": -1,
    "uc": -1,
    "center": -1,
    "right": -1,
    "left": -1,
    "hws": -1,
    "hwe": 1,
}
_NONLITERARY_TITLE_RE = re.compile(
    r"\b(?:dictionary|encyclop(?:a|ae)dia|catalog(?:ue)?|bibliography|gazette|"
    r"statute|public law|constitution|executive order|treaty|schedule|timetable|"
    r"parliamentary debates?|congressional record|bible|commentar(?:y|ies)|index|"
    r"summa theologica|doctrine|confession of faith|legal opinion|trial judgment|"
    r"commission report|accident report|investigation|verdict|court|case law|"
    r"research paper|transcribed interview|testimony|memorandum|calendar|guide book)\b|\bv\.\s",
    re.IGNORECASE,
)
_LINE_ARTIFACT_RE = re.compile(r"^(?:__NOTOC__|__NOEDITSECTION__|\s*[-|!+]\s*)$", re.I)


@dataclass(frozen=True, slots=True)
class WorkMetadata:
    title: str
    author: str
    year: int | None
    source_license: str | None
    license_evidence: str | None
    license_status: str
    literary_status: str
    metadata_text: str


def _template_name(template: Template) -> str:
    return " ".join(str(template.name).replace("_", " ").casefold().split())


def _plain(value: object) -> str:
    return " ".join(
        mwparserfromhell.parse(str(value)).strip_code(normalize=True, collapse=True).split()
    )


def _parameter(template: Template, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for parameter in template.params:
        if str(parameter.name).strip().casefold() in wanted:
            return _plain(parameter.value)
    return ""


def extract_license_evidence(
    wikitext: str, *, publication_cutoff: int = 1930
) -> tuple[str | None, str | None, str]:
    """Return only explicit or publication-year-backed work-level rights evidence."""
    code = mwparserfromhell.parse(wikitext)
    names = [_template_name(template) for template in code.filter_templates(recursive=True)]
    pd_template = next(
        (name for name in names if name.startswith(_PD_TEMPLATE_PREFIXES)),
        None,
    )
    if pd_template:
        return (
            "Public domain in the United States",
            f"English Wikisource template {{{{{pd_template}}}}}",
            "PUBLIC_DOMAIN",
        )
    if any(name.startswith(("copyrighted", "permission", "fair use")) for name in names):
        return None, "restrictive/permission copyright template", "RESTRICTED"

    year = extract_work_metadata(wikitext, infer_license=False).year
    if year is not None and year <= publication_cutoff:
        return (
            "Public domain in the United States",
            f"work/edition publication year {year} is at or before {publication_cutoff}",
            "PUBLIC_DOMAIN",
        )
    return None, None, "UNKNOWN"


def _parse_year(value: str) -> int | None:
    match = re.search(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)", value)
    return int(match.group()) if match else None


def extract_work_metadata(wikitext: str, *, infer_license: bool = True) -> WorkMetadata:
    code = mwparserfromhell.parse(wikitext)
    title = author = ""
    year: int | None = None
    metadata_templates: list[str] = []
    for template in code.filter_templates(recursive=True):
        name = _template_name(template)
        if name not in {
            "header",
            "index",
            ":mediawiki:proofreadpage index template",
        } and not name.endswith(" index template"):
            continue
        metadata_templates.append(str(template)[:1000])
        title = title or _parameter(template, "title", "work", "name")
        author = author or _parameter(template, "author")
        year_value = _parameter(template, "year", "date", "publication date")
        year = year or _parse_year(year_value)
    if re.fullmatch(r"(?:\.\./)+", title.strip()):
        title = ""
    source_license: str | None = None
    evidence: str | None = None
    status = "UNKNOWN"
    if infer_license:
        source_license, evidence, status = extract_license_evidence(wikitext)
    literary = (
        "NONLITERARY"
        if _NONLITERARY_TITLE_RE.search(title)
        else "LITERARY"
        if title and author
        else "REVIEW"
    )
    return WorkMetadata(
        title,
        author,
        year,
        source_license,
        evidence,
        status,
        literary,
        "\n".join(metadata_templates),
    )


def proofread_quality(wikitext: str) -> int | None:
    code = mwparserfromhell.parse(wikitext)
    for tag in code.filter_tags(recursive=True):
        if str(tag.tag).strip().casefold() != "pagequality":
            continue
        for attribute in tag.attributes:
            if str(attribute.name).strip().casefold() == "level":
                value = str(attribute.value).strip(" \"'")
                return int(value) if value.isdigit() else None
    match = re.search(r"<pagequality\b[^>]*\blevel\s*=\s*[\"']?(\d)", wikitext, re.I)
    return int(match.group(1)) if match else None


def _replace_templates(code) -> None:
    for template in list(code.filter_templates(recursive=True)):
        name = _template_name(template)
        replacement = ""
        if name in _TEXT_TEMPLATES and template.params:
            position = _TEXT_TEMPLATES[name]
            # Wikisource's text-preserving templates occur in several historical
            # arities.  Prefer the configured argument, but retain the final
            # supplied value when an abbreviated form omits it.
            parameter = (
                template.params[position]
                if 0 <= position < len(template.params)
                else template.params[-1]
            )
            replacement = str(parameter.value)
        elif name in {"gap", "dhr", "rule", "bar"}:
            replacement = " "
        elif name in _NAVIGATION_TEMPLATES or name.startswith(_PD_TEMPLATE_PREFIXES):
            replacement = ""
        try:
            code.replace(template, replacement, recursive=True)
        except ValueError:
            # A containing node was already removed from the syntax tree.
            continue


def _replace_tags(code) -> None:
    for tag in list(code.filter_tags(recursive=True)):
        name = str(tag.tag).strip().casefold()
        if name in {"noinclude", "ref", "references", "pagequality"}:
            replacement = ""
        elif name in {"br", "hr"}:
            replacement = "\n"
        elif name in {"poem", "div", "span", "blockquote", "section", "includeonly"}:
            replacement = str(tag.contents or "")
        else:
            replacement = str(tag.contents or "")
        try:
            code.replace(tag, replacement, recursive=True)
        except ValueError:
            # A containing noinclude/ref node was already removed.
            continue


def clean_wikitext(wikitext: str) -> list[str]:
    """Parse markup structurally and return readable prose paragraphs."""
    code = mwparserfromhell.parse(wikitext)
    _replace_tags(code)
    _replace_templates(code)
    rendered = html.unescape(code.strip_code(normalize=True, collapse=False)).replace("\r", "")
    paragraphs: list[str] = []
    current: list[str] = []
    in_table = False
    for raw_line in rendered.splitlines():
        line = raw_line.strip()
        if line.startswith("{|"):
            in_table = True
            continue
        if in_table:
            if line.startswith("|}"):
                in_table = False
            continue
        if not line or _LINE_ARTIFACT_RE.match(line):
            if current:
                paragraph = " ".join(current)
                paragraph = " ".join(paragraph.split())
                if paragraph:
                    paragraphs.append(paragraph)
                current = []
            continue
        if line.startswith(("==", "[[Category:", "{{DEFAULTSORT")):
            continue
        current.append(line)
    if current:
        paragraph = " ".join(current)
        paragraph = " ".join(paragraph.split())
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def work_key_for_page(namespace: int, title: str) -> str:
    if namespace == 104 and title.startswith("Page:"):
        page_name = title.removeprefix("Page:")
        return "Index:" + page_name.rsplit("/", 1)[0]
    if namespace == 106 and title.startswith("Index:"):
        return title
    return title.split("/", 1)[0]


def title_is_literary(title: str) -> bool:
    return not bool(_NONLITERARY_TITLE_RE.search(title))
