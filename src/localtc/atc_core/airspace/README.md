# Airspace data

`centers.json.gz` and `approaches.json.gz` are the outlines of the enroute centres (FIRs, ARTCCs) and
the approach areas (TRACONs) LocalTC hands flights between, and the Live Map draws. They are built by
`python tools/make_airspace.py` from two datasets maintained by the VATSIM community:

| File | Source | Licence |
|---|---|---|
| `centers.json.gz` | [VATSpy Data Project](https://github.com/vatsimnetwork/vatspy-data-project) — `Boundaries.geojson` and the names in `VATSpy.dat` | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `approaches.json.gz` | [SimAware TRACON Project](https://github.com/vatsimnetwork/simaware-tracon-project) — `TRACONBoundaries.geojson` | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |

**These two files are licensed CC BY-SA 4.0, not under LocalTC's AGPL.** They are an adaptation of
the sources above: outlines simplified (about a mile for centres, a third of one for approach areas),
coordinates rounded to four decimals, sub-sectors folded into the centre they belong to, military
areas left out, and names cleaned up for radio use ("London TMA (Up to FL195) - London" is worked by
"London Control"). Anyone may share and adapt them under the same licence, with credit to the VATSpy
Data Project and the SimAware TRACON Project contributors.

The data describes simulated airspace for flight simulation. It is not an official source, is not
kept to the AIRAC cycle, and must not be used for real-world navigation.

To refresh it, run the build script: it downloads the current versions of all three files. With the
files already on disk, `python tools/make_airspace.py --from DIR` builds from them instead.
