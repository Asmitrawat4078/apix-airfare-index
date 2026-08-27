"""Checkpoint-and-resume, tested.

The reason this exists: a collection run that dies at cell ninety must keep ninety cells.
A fare cannot be re-scraped tomorrow, so the difference between checkpointing and
accumulating in memory is the difference between losing a few cells and losing a day.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal

from collector.run import already_collected
from collector.schema import Quote
from collector.storage import CSV_COLUMNS, write_csv


def a_quote(source="easemytrip", origin="DEL", destination="BOM", lt=7, fare="6324"):
    return Quote(
        collection_ts_utc=datetime.now(UTC),
        source=source,
        url="https://example.invalid/search",
        origin=origin,
        destination=destination,
        lead_time_days=lt,
        dep_date="2026-09-02",
        is_available=True,
        total_fare=Decimal(fare),
        carrier="SG",
    )


def test_nothing_collected_yet_is_an_empty_set(tmp_path):
    assert already_collected("2026-08-26", tmp_path) == set()


def test_a_checkpointed_cell_is_recognised_on_resume(tmp_path):
    write_csv([a_quote()], "2026-08-26", tmp_path)
    done = already_collected("2026-08-26", tmp_path)
    assert ("easemytrip", "DEL", "BOM", 7) in done


def test_resume_distinguishes_cells_that_differ_only_by_lead_time(tmp_path):
    """T+7 and T+30 on the same route are different strata. Treating them as one cell on
    resume would silently skip a stratum and leave a permanent hole in it."""
    write_csv([a_quote(lt=7), a_quote(lt=30)], "2026-08-26", tmp_path)
    done = already_collected("2026-08-26", tmp_path)
    assert ("easemytrip", "DEL", "BOM", 7) in done
    assert ("easemytrip", "DEL", "BOM", 30) in done
    assert ("easemytrip", "DEL", "BOM", 45) not in done


def test_resume_distinguishes_sources():
    """Two sources on the same cell are two observations, not one."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        write_csv([a_quote(source="easemytrip")], "2026-08-26", tmp)
        done = already_collected("2026-08-26", tmp)
        assert ("easemytrip", "DEL", "BOM", 7) in done
        assert ("ixigo", "DEL", "BOM", 7) not in done


def test_checkpointing_appends_rather_than_overwriting(tmp_path):
    """Each cell is written the moment it completes. Later writes must not erase earlier
    ones, or checkpointing achieves nothing."""
    write_csv([a_quote(destination="BOM")], "2026-08-26", tmp_path)
    write_csv([a_quote(destination="BLR")], "2026-08-26", tmp_path)

    rows = list(csv.DictReader((tmp_path / "2026-08-26.csv").open(encoding="utf-8")))
    assert len(rows) == 2
    assert {r["destination"] for r in rows} == {"BOM", "BLR"}
    assert list(rows[0]) == CSV_COLUMNS


def test_a_malformed_checkpoint_row_does_not_break_resume(tmp_path):
    """A truncated final line is exactly what a killed process leaves behind. Resume must
    read what it can rather than refusing to start."""
    write_csv([a_quote()], "2026-08-26", tmp_path)
    with (tmp_path / "2026-08-26.csv").open("a", encoding="utf-8") as fh:
        fh.write("easemytrip,DEL,BOM,not-a-number\n")

    done = already_collected("2026-08-26", tmp_path)
    assert ("easemytrip", "DEL", "BOM", 7) in done
