import frappe

from datetime import datetime, timezone
from upande_goodwe.goodwe.auth import authenticate, get_platform
from upande_goodwe.goodwe.client import GoodWeClient
from upande_goodwe.goodwe import health
from upande_goodwe.goodwe.sems_plus import SemsPlusClient, SemsPlusError

AUTH_ERROR_HINTS = ("authoriz", "expired", "log in again", "login", "token")

# SEMS+ factor codes -> GoodWe Reading fields: (candidate codes, scale).
# Only codes confirmed by pygoodwe are listed. Run sems_plus.inspect_station to see every
# code your inverters send and add more here (e.g. PV voltages and currents).
SEMS_PLUS_TELEMETRY_MAP = {
    "current_power_kw": (("pAc",), 1),            # kW
    "grid_voltage_v": (("Vac", "PHASE-A:Vac"), 1),
    "inverter_temperature_c": (("Temperature",), 1),
    "battery_soc": (("soc",), 1),
}
SEMS_PLUS_COUNTER_MAP = {
    "daily_energy_kwh": (("proPvStatsToday",), 1),   # PV generation today, kWh
    "total_energy_kwh": (("proPvStatsTotal",), 1),   # PV generation lifetime, kWh
}
# Device detail keys that may hold the time GoodWe last received data (format unconfirmed).
SEMS_PLUS_TIME_KEYS = ("lastRefreshTime", "lastUpdateTime", "updateTime", "dataTime", "reportTime")


def _fetch_monitor_detail(power_station_id):
    """Fetches station data, logging in again once if the session has expired."""
    try:
        return GoodWeClient().get_monitor_detail(power_station_id)
    except frappe.ValidationError as e:
        if not any(h in str(e).lower() for h in AUTH_ERROR_HINTS):
            raise
        frappe.clear_messages()
        authenticate()
        return GoodWeClient().get_monitor_detail(power_station_id)


@frappe.whitelist()
def sync_station(power_station_id):
    """Syncs one station from SEMS+ (default) or the classic SEMS Portal,
    depending on API Platform in GoodWe Credentials."""
    if get_platform() == "SEMS+":
        site, devices, readings = _sync_station_sems_plus(power_station_id)
    else:
        data = _fetch_monitor_detail(power_station_id)
        site = sync_site(data)
        devices = sync_devices(site.name, data)
        readings = sync_readings(site.name, devices, data)

    frappe.db.commit()

    # Health check must never break the sync itself.
    frozen = None
    try:
        frozen = health.check_frozen()
        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "GoodWe health check failed")

    return {
        "site": site.name,
        "devices": len(devices),
        "readings": len(readings),
        "frozen_inverters": [f["inverter"] for f in frozen] if frozen else [],
        "platform": get_platform(),
        "status": "Success",
    }


# ---------------------------------------------------------------------------
# SEMS+
# ---------------------------------------------------------------------------

def _num(value):
    try:
        return float(value) if value not in (None, "", "--") else None
    except (TypeError, ValueError):
        return None


def _pick(factors, mapping, target):
    codes, scale = mapping[target]
    for code in codes:
        value = _num(factors.get(code))
        if value is not None:
            return value * scale
    return None


def _sync_station_sems_plus(power_station_id):
    try:
        snapshot = SemsPlusClient().station_snapshot(power_station_id)
    except SemsPlusError as exc:
        frappe.throw(f"SEMS+ sync failed ({exc.kind}): {exc}")

    devices_ok = [d for d in snapshot["devices"] if d["telemetry"] or d["counters"]]
    if not devices_ok:
        errors = "; ".join(e for d in snapshot["devices"] for e in d["errors"])
        frappe.throw(f"SEMS+ returned no telemetry or counters for any inverter: {errors}")

    site = _sync_site_sems_plus(power_station_id, snapshot)
    devices = _sync_devices_sems_plus(site.name, snapshot)
    readings = _sync_readings_sems_plus(devices, snapshot)
    return site, devices, readings


def _sync_site_sems_plus(station_id, snapshot):
    if frappe.db.exists("Solar Site", station_id):
        doc = frappe.get_doc("Solar Site", station_id)
    else:
        doc = frappe.new_doc("Solar Site")
        doc.name = station_id
        doc.station_id = station_id

    statuses = [normalize_status(d.get("status")) for d in snapshot["devices"]]
    producing = any((_num(d["telemetry"].get("pAc")) or 0) > 0 for d in snapshot["devices"])
    if "Online" in statuses or producing:
        doc.status = "Running"
    else:
        doc.status = "Fault" if "Fault" in statuses else "Offline"
    doc.last_sync = frappe.utils.now()
    doc.notes = frappe.as_json({
        "platform": "SEMS+",
        "flow": snapshot["flow"],
        "devices": [{"sn": d["sn"], "status": d["status"], "deviceType": d["deviceType"]} for d in snapshot["devices"]],
    })
    doc.save(ignore_permissions=True)
    return doc


def _sync_devices_sems_plus(site_name, snapshot):
    devices = []
    for d in snapshot["devices"]:
        serial = d["sn"]
        if frappe.db.exists("GoodWe Device", serial):
            doc = frappe.get_doc("GoodWe Device", serial)
        else:
            doc = frappe.new_doc("GoodWe Device")
            doc.device_id = serial
            doc.serial_number = serial
        detail = d.get("detail") or {}
        doc.solar_site = site_name
        doc.device_name = detail.get("name") or doc.device_name or serial
        doc.device_type = "Inverter"
        doc.model = detail.get("model") or detail.get("modelType") or doc.model
        doc.status = normalize_device_status(d.get("status"))
        if normalize_status(d.get("status")) == "Unknown" and (d["telemetry"] or d["counters"]):
            doc.status = "Online"  # unrecognised SEMS+ status text, but data is arriving
        doc.save(ignore_permissions=True)
        devices.append(doc)
    return devices


def _sync_readings_sems_plus(devices, snapshot):
    """One GoodWe Reading per inverter. Station-level power flow (grid, load, battery) isn't
    available per inverter in SEMS+, so it is split equally between the inverters: totals
    across inverters stay correct and per-inverter averages stay comparable with before."""
    flow = snapshot["flow"]
    by_serial = {d.serial_number: d for d in devices}
    count = len(snapshot["devices"]) or 1
    now = frappe.utils.now_datetime()
    readings = []

    for d in snapshot["devices"]:
        device = by_serial.get(d["sn"])
        if not device:
            continue
        telemetry, counters, detail = d["telemetry"], d["counters"], d.get("detail") or {}

        reading = frappe.new_doc("GoodWe Reading")
        reading.inverter = device.name
        reading.reading_time = now

        for field in SEMS_PLUS_TELEMETRY_MAP:
            reading.set(field, _pick(telemetry, SEMS_PLUS_TELEMETRY_MAP, field))
        for field in SEMS_PLUS_COUNTER_MAP:
            reading.set(field, _pick(counters, SEMS_PLUS_COUNTER_MAP, field))

        if reading.battery_soc is None:
            reading.battery_soc = _num(flow.get("soc"))
        for field, code in (("grid_power_kw", "pGrid"), ("load_power_kw", "pConsum"), ("battery_power_kw", "pBat")):
            value = _num(flow.get(code))
            if value is not None:
                reading.set(field, value / count)

        data_time = next((detail[k] for k in SEMS_PLUS_TIME_KEYS if isinstance(detail.get(k), str)), None)
        if not data_time and isinstance(flow.get("refreshTime"), str):
            data_time = flow["refreshTime"]  # station-level, e.g. "2026-08-12T15:56:02.755"
        reading.goodwe_time = data_time[:140] if data_time else None
        parsed = parse_goodwe_datetime(data_time)
        if parsed and frappe.utils.time_diff_in_hours(now, parsed) > health.DATA_AGE_HOURS:
            reading.is_stale = 1

        reading.status = normalize_status(d.get("status"))
        reading.error_code = "; ".join(d["errors"])[:140] or None
        reading.api_timestamp = frappe.utils.now()
        reading.sync_time = frappe.utils.now()
        reading.error_message = frappe.as_json({  # field labelled "Raw Response"
            "platform": "SEMS+",
            "device": d,
            "station_flow": flow,
            "flow_split_between": count,
        })
        reading.insert(ignore_permissions=True)
        readings.append(reading)

    return readings

def sync_site(data):
    info = data.get("info", {})
    kpi = data.get("kpi", {})

    name = info.get("powerstation_id")

    if not name:
        frappe.throw("GoodWe response does not contain a powerstation ID")

    if frappe.db.exists("Solar Site", name):
        doc = frappe.get_doc("Solar Site", name)
    else:
        doc = frappe.new_doc("Solar Site")
        doc.name = name
        doc.station_id = name

    doc.station_name = info.get("stationname")
    doc.organization = info.get("org_name")
    doc.address = info.get("address")

    doc.latitude = info.get("latitude")
    doc.longitude = info.get("longitude")

    doc.capacity_kw = info.get("capacity")
    doc.battery_capacity_kwh = info.get("battery_capacity")

    doc.station_type = info.get("powerstation_type")

    doc.status = (
        "Running"
        if info.get("status") == 1
        else "Offline"
    )

    doc.currency = kpi.get("currency")

    images = data.get("images") or []
    doc.image_url = images[0] if images else None

    doc.last_sync = frappe.utils.now()
    doc.raw_json = frappe.as_json(data)

    doc.save(ignore_permissions=True)

    return doc

def sync_devices(site_name, data):
    devices = []

    for d in data.get("inverter", []):
        serial = d.get("sn")

        if not serial:
            continue

        if frappe.db.exists("GoodWe Device", serial):
            doc = frappe.get_doc("GoodWe Device", serial)
        else:
            doc = frappe.new_doc("GoodWe Device")

            doc.device_id = serial
            doc.serial_number = serial

        doc.solar_site = site_name
        doc.device_name = d.get("name")
        doc.device_type = "Inverter"
        doc.model = d.get("type")
        doc.status = normalize_device_status(d.get("status"))

        doc.save(ignore_permissions=True)

        devices.append(doc)

    return devices

def sync_readings(site_name, devices, data):
    readings = []

    devices_by_serial = {
        device.serial_number: device
        for device in devices
    }

    for inverter_data in data.get("inverter", []):
        serial = inverter_data.get("sn")

        if not serial:
            continue

        device = devices_by_serial.get(serial)
        if not device:
            continue

        runtime = inverter_data.get("d") or {}
        invert_full = inverter_data.get("invert_full") or {}

        reading = frappe.new_doc("GoodWe Reading")

        # Device Link
        reading.inverter = device.name

        # Reading Time: always the time we fetched it. GoodWe's own time can be hours or
        # days old when the inverter stops reporting, and its date order is ambiguous,
        # so it is kept separately in goodwe_time for checking.
        reading.reading_time = frappe.utils.now_datetime()
        reading.goodwe_time = str(
            inverter_data.get("time") or inverter_data.get("last_refresh_time") or ""
        )[:140] or None

        # Output / AC Power (Convert W string "9443W" to kW float 9.443)
        output_power = runtime.get("output_power") or inverter_data.get("output_power")
        if isinstance(output_power, str):
            output_power = output_power.replace("W", "").strip()
        if output_power is not None:
            reading.current_power_kw = float(output_power) / 1000

        #Calls load power calculation function to determine load power in kW
        reading.load_power_kw = get_load_power_kw(inverter_data, runtime)

        # Daily & Total Energy Generation (Stored at top level of inverter dict)
        reading.daily_energy_kwh = inverter_data.get("eday")
        reading.total_energy_kwh = inverter_data.get("etotal")

        # Grid Power & Metrics
        pac = runtime.get("pac")
        if pac is not None:
            reading.grid_power_kw = float(pac) / 1000

        reading.grid_voltage_v = runtime.get("vac1")
        reading.grid_frequency_hz = runtime.get("fac1")
        reading.grid_current_a = runtime.get("iac1")

        # PV Inputs
        reading.pv1_voltage_v = runtime.get("vpv1")
        reading.pv1_current_a = runtime.get("ipv1")
        reading.pv2_voltage_v = runtime.get("vpv2")
        reading.pv2_current_a = runtime.get("ipv2")

        # Battery State of Charge (SOC)
        battery_soc = inverter_data.get("soc") or invert_full.get("soc")
        if isinstance(battery_soc, str):
            battery_soc = battery_soc.replace("%", "").strip()
        if battery_soc is not None:
            reading.battery_soc = float(battery_soc)

        # Battery Power (Convert W to kW)
        battery_power = inverter_data.get("battery_power")
        if battery_power is not None:
            reading.battery_power_kw = float(battery_power) / 1000

        # Inverter Temperature & Power Factor
        reading.inverter_temperature_c = (
            invert_full.get("tempperature") or inverter_data.get("tempperature")
        )
        reading.power_factor = invert_full.get("pf")

        # Status & Modes
        raw_status = invert_full.get("grid_conn_status") or inverter_data.get("status")
        reading.status = normalize_status(raw_status)
        reading.work_mode = inverter_data.get("bms_status") or runtime.get("workmode")

        # Error / Warning Codes
        reading.error_code = (
            inverter_data.get("warning_code")
            or runtime.get("warning")
        )

        # Sync Information
        reading.api_timestamp = frappe.utils.now()
        reading.sync_time = frappe.utils.now()
        reading.error_message = frappe.as_json(inverter_data)  # field labelled "Raw Response"

        reading.insert(ignore_permissions=True)
        readings.append(reading)

    return readings

def normalize_status(status):
    status_map = {
        1: "Online",
        0: "Offline",
    }

    if status in status_map:
        return status_map[status]

    if isinstance(status, str):
        status = status.strip().lower()

        string_map = {
            "disconnected": "Offline",
            "disconnect": "Offline",
            "waiting": "Online",
            "alarm": "Warning",
            "1": "Online",
            "0": "Online",
            "-1": "Offline",
            "online": "Online",
            "running": "Online",
            "generating_on_grid": "Online",
            "generating": "Online",
            "normal": "Online",
            "offline": "Offline",
            "fault": "Fault",
            "error": "Fault",
            "standby": "Online",
            "wait": "Online",
            "warning": "Warning",
        }

        return string_map.get(status, "Unknown")

    return "Unknown"

def normalize_device_status(status):
    status_map = {
        -1: "Offline",
        0: "Standby",
        1: "Running",
        2: "Fault",
        3: "Standby",
    }

    try:
        return status_map.get(int(status), "Offline")
    except (TypeError, ValueError):
        # SEMS+ sends text statuses
        return {"Online": "Running", "Fault": "Fault", "Warning": "Running"}.get(
            normalize_status(status), "Offline"
        )

@frappe.whitelist()
def sync_all_stations():
    stations = frappe.get_all(
        "Solar Site",
        filters={
            "station_id": ["is", "set"]
        },
        fields=["name", "station_id"]
    )

    for station in stations:
        try:
            sync_station(station.station_id)
            health.record_sync_result(station.name, ok=True)

        except Exception:
            frappe.db.rollback()
            error = frappe.get_traceback()
            frappe.log_error(error, f"GoodWe sync failed: {station.name}")
            try:
                health.record_sync_result(station.name, ok=False, error=error)
                frappe.db.commit()
            except Exception:
                frappe.log_error(frappe.get_traceback(), "GoodWe alert failed")

def parse_goodwe_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    value = str(value).strip()
    if "T" in value:  # ISO 8601 from SEMS+, e.g. 2026-08-12T15:56:02.755
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is not None:  # convert to the site's timezone, then drop tz
                parsed = frappe.utils.convert_utc_to_system_timezone(
                    parsed.astimezone(timezone.utc).replace(tzinfo=None)
                ).replace(tzinfo=None)
            return parsed

    for fmt in (
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None

def get_load_power_kw(inverter_data, runtime):
    """
    Calculates Load Power in kW from GoodWe SEMS telemetry payload.
    Falls back to energy balance (PV + Battery) if explicit load key is missing/zero.
    """
    explicit_load = runtime.get("load_power") or runtime.get("pload")
    if explicit_load is not None and float(explicit_load) > 0:
        return float(explicit_load) / 1000

    vpv1 = float(runtime.get("vpv1") or 0)
    ipv1 = float(runtime.get("ipv1") or 0)
    vpv2 = float(runtime.get("vpv2") or 0)
    ipv2 = float(runtime.get("ipv2") or 0)
    pv_power_w = (vpv1 * ipv1) + (vpv2 * ipv2)

    battery_power_w = float(inverter_data.get("battery_power") or 0)

    raw_output = runtime.get("output_power") or inverter_data.get("output_power") or "0"
    if isinstance(raw_output, str):
        raw_output = raw_output.replace("W", "").strip()
    output_power_w = float(raw_output or 0)

    computed_load_w = pv_power_w + battery_power_w

    if computed_load_w <= 0 and output_power_w > 0:
        computed_load_w = output_power_w

    return max(0.0, float(computed_load_w) / 1000)
