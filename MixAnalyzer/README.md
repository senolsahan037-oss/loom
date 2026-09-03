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
Tests: `python3 -m pytest tests/ -q` (27, synthetic audio only).
