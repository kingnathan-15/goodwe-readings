import frappe

from datetime import datetime
from hydrax_goodwe.goodwe.client import GoodWeClient

@frappe.whitelist()
def sync_station(power_station_id):
    client = GoodWeClient()

    data = client.get_monitor_detail(power_station_id)

    site = sync_site(data)

    devices = sync_devices(site.name, data)

    readings = sync_readings(site.name, devices, data)

    frappe.db.commit()

    return {
        "site": site.name,
        "devices": len(devices),
        "readings": len(readings),
        "status": "Success",
    }

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

        reading = frappe.new_doc("GoodWe Reading")

        # Device
        reading.inverter = device.name

        # Reading time
        reading.reading_time = (
            parse_goodwe_datetime(inverter_data.get("time"))
            or parse_goodwe_datetime(
                inverter_data.get("last_refresh_time")
            )
            or frappe.utils.now_datetime()
        )

        # Current power
        output_power = runtime.get("outputpower")

        if output_power is None:
            output_power = inverter_data.get("output_power")

        if isinstance(output_power, str):
            output_power = output_power.replace("W", "").strip()

        if output_power is not None:
            reading.current_power_kw = float(output_power) / 1000

        # Energy
        reading.daily_energy_kwh = runtime.get("eDay")
        reading.total_energy_kwh = runtime.get("eTotal")

        # Grid
        pac = runtime.get("pac")

        if pac is not None:
            reading.grid_power_kw = float(pac) / 1000

        reading.grid_voltage_v = runtime.get("vac1")
        reading.grid_frequency_hz = runtime.get("fac1")
        reading.grid_current_a = runtime.get("iac1")

        # PV
        reading.pv1_voltage_v = runtime.get("vpv1")
        reading.pv1_current_a = runtime.get("ipv1")

        reading.pv2_voltage_v = runtime.get("vpv2")
        reading.pv2_current_a = runtime.get("ipv2")

        # Battery
        battery_soc = (
            inverter_data.get("invert_full", {}).get("soc")
            if inverter_data.get("invert_full")
            else None
        )

        if battery_soc is None:
            battery_soc = inverter_data.get("soc")

        if isinstance(battery_soc, str):
            battery_soc = battery_soc.replace("%", "").strip()

        if battery_soc is not None:
            reading.battery_soc = float(battery_soc)

        battery_power = inverter_data.get("battery_power")

        if battery_power is not None:
            reading.battery_power_kw = float(battery_power) / 1000

        # Inverter
        reading.inverter_temperature_c = (
            inverter_data.get("tempperature")
        )

        reading.status = normalize_status(
            inverter_data.get("status")
        )

        reading.work_mode = (
            runtime.get("workmode")
            or runtime.get("work_mode")
        )

        # Warning / error
        reading.error_code = (
            inverter_data.get("warning_code")
            or runtime.get("warning")
        )

        # Sync information
        reading.api_timestamp = frappe.utils.now()
        reading.sync_time = frappe.utils.now()

        # Preserve raw inverter response
        reading.raw_response = frappe.as_json(
            inverter_data
        )

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
            "1": "Online",
            "0": "Offline",
            "online": "Online",
            "running": "Running",
            "offline": "Offline",
            "fault": "Fault",
            "standby": "Standby",
            "warning": "Warning",
        }

        return string_map.get(status, "Unknown")

    return "Unknown"

def normalize_device_status(status):
    status_map = {
        0: "Offline",
        1: "Running",
        2: "Fault",
        3: "Standby",
    }

    try:
        return status_map.get(int(status), "Offline")
    except (TypeError, ValueError):
        return "Offline"

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

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"GoodWe sync failed: {station.name}"
            )

def parse_goodwe_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

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