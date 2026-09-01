from pathlib import Path

from ableton.ableton_metadata import normalize_keyword, read_xmp_keywords, scan_ableton_folder_info


XMP_WITH_KEYWORDS = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">
  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\"
           xmlns:ablFR=\"http://ns.adobe.com/abl/1.0/file-resolver/\">
    <rdf:Description ablFR:filePath=\"Factory Packs/Beat Tools/Drums/Bonzo Kit.adg\">
      <ablFR:keywords>
        <rdf:Bag>
          <rdf:li>Genres|Trap</rdf:li>
          <rdf:li>Type|One Shot</rdf:li>
          <rdf:li>Drums|Kick</rdf:li>
          <rdf:li>Character|Punchy</rdf:li>
          <rdf:li>Key|C</rdf:li>
        </rdf:Bag>
      </ablFR:keywords>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
"""

XMP_WITHOUT_KEYWORDS = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">
  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\"
           xmlns:ablFR=\"http://ns.adobe.com/abl/1.0/file-resolver/\">
    <rdf:Description ablFR:filePath=\"Factory Packs/Beat Tools/Drums/No Keywords.adg\" />
  </rdf:RDF>
</x:xmpmeta>
"""


def test_normalize_keyword_splits_group_and_value() -> None:
    parsed = normalize_keyword("Genres|Trap")

    assert parsed["group"] == "Genres"
    assert parsed["value"] == "Trap"


def test_read_xmp_keywords_parses_grouped_keywords(tmp_path: Path) -> None:
    xmp_path = tmp_path / "fixture.xmp"
    xmp_path.write_text(XMP_WITH_KEYWORDS)

    parsed = read_xmp_keywords(xmp_path)

    assert parsed["file_path"] == "Factory Packs/Beat Tools/Drums/Bonzo Kit.adg"
    assert parsed["keywords"] == [
        "Genres|Trap",
        "Type|One Shot",
        "Drums|Kick",
        "Character|Punchy",
        "Key|C",
    ]
    assert parsed["genres"] == ["Trap"]
    assert parsed["types"] == ["One Shot"]
    assert parsed["drums"] == ["Kick"]
    assert parsed["characters"] == ["Punchy"]
    assert parsed["keys"] == ["C"]


def test_read_xmp_keywords_returns_empty_lists_when_missing_keywords(tmp_path: Path) -> None:
    xmp_path = tmp_path / "fixture_no_keywords.xmp"
    xmp_path.write_text(XMP_WITHOUT_KEYWORDS)

    parsed = read_xmp_keywords(xmp_path)

    assert parsed["file_path"] == "Factory Packs/Beat Tools/Drums/No Keywords.adg"
    assert parsed["keywords"] == []
    assert parsed["genres"] == []
    assert parsed["types"] == []
    assert parsed["drums"] == []
    assert parsed["characters"] == []
    assert parsed["keys"] == []


def test_scan_ableton_folder_info_maps_by_file_path(tmp_path: Path) -> None:
    folder_info = tmp_path / "Factory Packs" / "Beat Tools" / "Ableton Folder Info" / "12"
    folder_info.mkdir(parents=True)
    xmp_path = folder_info / "item.xmp"
    xmp_path.write_text(XMP_WITH_KEYWORDS)

    metadata_map = scan_ableton_folder_info(tmp_path)

    assert "Factory Packs/Beat Tools/Drums/Bonzo Kit.adg" in metadata_map
    assert metadata_map["Factory Packs/Beat Tools/Drums/Bonzo Kit.adg"]["genres"] == ["Trap"]
