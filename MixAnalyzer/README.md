# MixAnalyzer — the Mix Check engine

`subverse_mix` is the measurement core of SubverseLab Mix Check, kept here as
the single copy. Loom's MCP exposes it as `mix_measure`, `mix_analyze` and
`mix_profiles`; the Launchpad web service imports the same package instead of
carrying its own.

Install standalone:

```
pip install "git+https://github.com/senolsahan037-oss/loom#subdirectory=MixAnalyzer"
```

Every number is a direct signal measurement or pyloudnorm's BS.1770 loudness.
Genre profiles are technical distributions of released masters, not genre
definitions; nearest-profile ranking is never a classification and says so.
The shipped catalog (`subverse_mix/data/genre_profiles.json`) is built by
`python3 -m subverse_mix.reference_profiles` from `data/reference_tracks.json`:
20 of the most widely known released masters per genre (Hip-Hop, Trap,
Electronic, Pop, Rock), resolved to the artist's or label's own YouTube
upload, measured locally and deleted; only aggregates and provenance are kept.
Ranking distance is RMS dB from the profile median, never divided by the
profile's spread, and a genre profile is used as a correction target only when it
leads the runner-up by at least 1 dB (`closest_profile_status`); otherwise the
track is compared with the pooled "Released masters (all genres)" profile
(`mode: pooled`), built with `--pooled` from every reference master.
Tests: `python3 -m pytest tests/ -q` (27, synthetic audio only).
