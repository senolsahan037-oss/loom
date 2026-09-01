from pathlib import Path

from ableton.library_scanner import scan_ableton_library


XMP_WITH_GENRE = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">
  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\"
           xmlns:ablFR=\"http://ns.adobe.com/abl/1.0/file-resolver/\">
    <rdf:Description ablFR:filePath=\"Factory Packs/Beat Tools/Drums/Bonzo Kit.adg\">
      <ablFR:keywords>
        <rdf:Bag>
          <rdf:li>Genres|Trap</rdf:li>
          <rdf:li>Type|Drum Kit</rdf:li>
          <rdf:li>Drums|Kick</rdf:li>
        </rdf:Bag>
      </ablFR:keywords>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
"""


def test_scan_ableton_library_includes_source_native_xmp_metadata(tmp_path: Path) -> None:
    pack_root = tmp_path / "Factory Packs" / "Beat Tools"
    drums_dir = pack_root / "Drums"
    folder_info = pack_root / "Ableton Folder Info" / "12"

    drums_dir.mkdir(parents=True)
    folder_info.mkdir(parents=True)

    bonzo_path = drums_dir / "Bonzo Kit.adg"
    bonzo_path.write_text("kit")
    (folder_info / "pack.xmp").write_text(XMP_WITH_GENRE)

    result = scan_ableton_library([tmp_path])

    bonzo_item = next(item for item in result["items"] if item["name"] == "Bonzo Kit")
    assert bonzo_item["source_native"]["ableton_file_path"] == "Factory Packs/Beat Tools/Drums/Bonzo Kit.adg"
    assert bonzo_item["source_native"]["ableton_genres"] == ["Trap"]
    assert bonzo_item["source_native"]["ableton_types"] == ["Drum Kit"]
    assert bonzo_item["source_native"]["ableton_drums"] == ["Kick"]
    assert bonzo_item["derived"]["musical_category"] == "Drums"
    assert bonzo_item["derived"]["instrument_hint"] == "drum"
    assert bonzo_item["derived"]["confidence"] == "fallback_path_heuristic"
