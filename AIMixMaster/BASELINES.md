# Verified Baselines

## BUSS Builder v1

Status: frozen verified baseline.

Fixture: `SampleChopVol1/Ummu Gulsum/ummu gulsum Project/Golden Step_RECOVERED.als`

Runtime verification: Ableton Live opened the fixture and confirmed that `DRUM BUSS`
contains exactly `EQ Eight -> Glue Compressor -> Utility` as direct devices.

The paired `.aimixmaster-backup` fixture is the pre-build regression source. Changes to
the BUSS Builder must keep the regression test passing before this baseline is changed.
