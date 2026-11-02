"""Frontend contract: the AG Grid reporting view is really wired in.

The grid runs in the browser, so these tests pin the integration
points in the shipped HTML: pinned AG Grid community build, grid
initialisation, CSV export, the server-report hookup, and the offline
fallback — a CDN failure must never hide the books.
"""
from pathlib import Path

HTML = (
    Path(__file__).resolve().parent.parent / "static" / "index.html"
).read_text(encoding="utf-8")


def test_ag_grid_community_pinned_cdn():
    assert "ag-grid-community@31.3.4" in HTML
    assert "ag-grid-community.min.js" in HTML
    assert "ag-theme-quartz" in HTML


def test_grid_initialised_with_books_columns():
    assert "agGrid.createGrid" in HTML
    assert "booksGrid" in HTML
    for field in ("number", "client", "status", "total", "days_overdue"):
        assert f"field:'{field}'" in HTML


def test_grid_csv_export_and_search_wired():
    assert "exportDataAsCsv" in HTML
    assert "setQuickFilter" in HTML
    assert "/api/reports/export.csv" in HTML


def test_aging_buckets_rendered_from_api():
    assert "/api/reports/aging" in HTML
    assert "agingRow" in HTML


def test_offline_fallback_present():
    assert "gridFallback" in HTML
    assert "fallback-table" in HTML
