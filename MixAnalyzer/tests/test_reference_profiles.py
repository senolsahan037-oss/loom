from __future__ import annotations

from subverse_mix.reference_profiles import (
    DEFAULT_TRACKS_PATH,
    load_track_list,
    rank_candidates,
)


def test_track_list_ships_with_the_engine_and_has_enough_sources() -> None:
    document = load_track_list(DEFAULT_TRACKS_PATH)
    ids = [genre["id"] for genre in document["genres"]]
    assert ids == ["hiphop", "trap", "electronic", "pop", "rock"]
    for genre in document["genres"]:
        assert len(genre["tracks"]) >= 20
        assert all(track["artist"] and track["title"] for track in genre["tracks"])


def test_rank_candidates_prefers_the_label_upload_and_rejects_lyric_videos() -> None:
    candidates = [
        {"id": "lyr", "title": "Kendrick Lamar - HUMBLE. (Lyrics)", "channel": "7clouds", "duration": 178},
        {"id": "vid", "title": "Kendrick Lamar - HUMBLE.", "channel": "Kendrick Lamar", "duration": 184},
        {"id": "top", "title": "HUMBLE.", "channel": "Kendrick Lamar", "duration": 177},
        {"id": "rmx", "title": "Kendrick Lamar - HUMBLE. (Skrillex Remix) [Official Audio]", "channel": "Skrillex", "duration": 157},
        {"id": "spd", "title": "HUMBLE. (sped up)", "channel": "Kendrick Lamar", "duration": 150},
        {"id": "long", "title": "HUMBLE.", "channel": "Kendrick Lamar", "duration": 3600},
        {"id": "other", "title": "Kendrick Lamar HUMBLE. reaction", "channel": "Someone", "duration": 600},
    ]
    ranked = rank_candidates("Kendrick Lamar", "HUMBLE.", candidates)
    assert [item["id"] for item in ranked] == ["top", "vid"]
    assert ranked[0]["match_kind"] == "topic_or_artist_audio"
    assert ranked[1]["match_kind"] == "artist_channel"


def test_rank_candidates_requires_the_song_title() -> None:
    ranked = rank_candidates(
        "Queen",
        "Bohemian Rhapsody",
        [{"id": "x", "title": "Don't Stop Me Now", "channel": "Queen", "duration": 200}],
    )
    assert ranked == []
