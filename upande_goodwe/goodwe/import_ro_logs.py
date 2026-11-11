"""Import RO Daily Log records from the efficiency tracking Excel workbook.

Supported sheets:
  VV    → site "VV":    permeate, reject, borehole meter, smart meter, units sold, tank levels
  Yamin → site "Yamin": units sold, borehole meter

Usage (bench):
    bench --site <site> execute upande_goodwe.goodwe.import_ro_logs.run \
        --kwargs "{'path': '/path/to/file.xlsx'}"

Usage (System Console):
    import upande_goodwe.goodwe.import_ro_logs as imp
    result = imp.run(path="/path/to/file.xlsx")
    print(result["summary"])

Options:
    overwrite   False (default) — skip fields already set; True — overwrite everything
    dry_run     True — log what would happen without saving anything
"""

import os

import frappe
from frappe.utils import flt, getdate


# ---------------------------------------------------------------------------
# Column indices in the Excel sheets (0-based)
# ---------------------------------------------------------------------------

VV_COLS = {
    "log_date":       0,
    "permeate_m3":    1,   # To the estate (booster pump)
    "reject_m3":      2,   # Reject water
    "borehole_meter": 5,   # Bore hole meter next morning (cumulative)
    "smart_meter":    6,   # Ubuntu Smart meter (cumulative)
    "units_sold":     13,  # Sold
    "rw_tank_level":  17,  # RW tank (0–1 fraction → converted to %)
    "tw_tank_level":  18,  # TW tank (0–1 fraction → converted to %)
    # kplc_kwh is not in this workbook — entered manually in the daily log
}

YAMIN_COLS = {
    "log_date":       0,
    "units_sold":     2,   # Sold
    "borehole_meter": 3,   # Bore hole meter (cumulative)
}


@frappe.whitelist()
def import_from_attached_file(file_url, overwrite=False, dry_run=False):
    """Called from the RO Log Import form. Resolves the attached file URL to a
    filesystem path and delegates to run()."""
    frappe.only_for("System Manager")
    path = _resolve_path(file_url)
    return run(path=path, overwrite=bool(int(overwrite or 0)), dry_run=bool(int(dry_run or 0)))


def _resolve_path(file_url):
    """Convert a Frappe file URL (/private/files/… or /files/…) to an absolute path."""
    if not file_url:
        frappe.throw("No file URL provided.")
    if file_url.startswith("/private/"):
        path = frappe.get_site_path() + file_url
    elif file_url.startswith("/files/"):
        path = frappe.get_site_path("public") + file_url
    else:
        frappe.throw(f"Cannot resolve file URL: {file_url}")
    if not os.path.exists(path):
        frappe.throw(f"File not found on disk: {path}")
    return path


def run(path, overwrite=False, dry_run=False):
    """Import RO Daily Logs from *path* (absolute path to the Excel workbook).

    Returns a dict with keys: created, updated, skipped, errors, summary.
    """
    try:
        import openpyxl
    except ImportError:
        frappe.throw("openpyxl is not installed. Run: /path/to/env/bin/pip install openpyxl")

    from datetime import datetime

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    for sheet_name, site, col_map in [
        ("VV",    "VV",    VV_COLS),
        ("Yamin", "Yamin", YAMIN_COLS),
    ]:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))

        for raw_row in rows:
            date_val = raw_row[col_map["log_date"]] if len(raw_row) > col_map["log_date"] else None
            if not isinstance(date_val, datetime):
                continue

            log_date = date_val.date()
            values = _extract(raw_row, col_map, sheet_name)

            try:
                _upsert(site, log_date, values, overwrite=overwrite, dry_run=dry_run, stats=stats)
            except Exception as exc:
                msg = f"{site} {log_date}: {exc}"
                stats["errors"].append(msg)
                frappe.log_error(frappe.get_traceback(), f"RO import: {msg}")
                frappe.db.rollback()

    stats["summary"] = (
        f"Import {'(dry run) ' if dry_run else ''}complete: "
        f"{stats['created']} created, {stats['updated']} updated, "
        f"{stats['skipped']} skipped, {len(stats['errors'])} errors."
    )
    if not dry_run:
        frappe.db.commit()
    return stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract(row, col_map, sheet_name):
    """Pull field values from a raw row using the column map."""
    values = {}
    for field, col in col_map.items():
        if field == "log_date":
            continue
        if col >= len(row) or row[col] is None:
            continue
        v = row[col]
        if field in ("rw_tank_level", "tw_tank_level"):
            # Sheet stores 0–1 fractions; doctype stores percent 0–100
            try:
                pct = flt(v) * 100
                if 0 <= pct <= 100:
                    values[field] = round(pct, 1)
            except (TypeError, ValueError):
                pass
        else:
            try:
                values[field] = flt(v)
            except (TypeError, ValueError):
                pass
    return values


def _upsert(site, log_date, values, overwrite, dry_run, stats):
    """Create or update an RO Daily Log record for (site, log_date)."""
    name = frappe.db.get_value("RO Daily Log", {"site": site, "log_date": getdate(log_date)})

    if not values:
        stats["skipped"] += 1
        return

    if name:
        doc = frappe.get_doc("RO Daily Log", name)
        changed = False
        for field, val in values.items():
            existing = doc.get(field)
            if overwrite or existing in (None, ""):
                if existing != val:
                    if not dry_run:
                        doc.set(field, val)
                    changed = True
        if changed:
            if not dry_run:
                doc.save(ignore_permissions=True)
            stats["updated"] += 1
        else:
            stats["skipped"] += 1
    else:
        if not dry_run:
            doc = frappe.new_doc("RO Daily Log")
            doc.site = site
            doc.log_date = getdate(log_date)
            for field, val in values.items():
                doc.set(field, val)
            doc.insert(ignore_permissions=True)
        stats["created"] += 1
