#!/usr/bin/env python3
import importlib.util
import os
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as runtime_tmp:
    os.environ["INCIDENT_DIR"]=runtime_tmp
    spec=importlib.util.spec_from_file_location("observer_agent", ROOT/"observer-agent"/"app.py")
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)/"fs"/"btrfs"/"synthetic-fs"
        (base/"devinfo"/"1").mkdir(parents=True)
        (base/"commit_stats").write_text("commits 7\nlast_commit_ms 250\nmax_commit_ms 900\n")
        (base/"devinfo"/"1"/"error_stats").write_text("write_errs 1\nread_errs 2\nflush_errs 3\ncorruption_errs 4\ngeneration_errs 5\n")
        mod.HOST_SYS=Path(tmp)
        last,maxc,errors=mod.btrfs_stats()
        assert last == 0.25, last
        assert maxc == 0.9, maxc
        assert errors == {"write":1,"read":2,"flush":3,"corruption":4,"generation":5}, errors

    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)/"fs"/"btrfs"/"synthetic-fs"
        (base/"devices"/"1"/"stats").mkdir(parents=True)
        (base/"devices"/"1"/"stats"/"read").write_text("6\n")
        mod.HOST_SYS=Path(tmp)
        _,_,errors=mod.btrfs_stats()
        assert errors == {"read":6}, errors

print("observer Btrfs parser: PASS")
