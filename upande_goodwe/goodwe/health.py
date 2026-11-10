"""Data health checks for the GoodWe sync, with email alerts.

Three problems are detected:
  * frozen data   - an inverter's lifetime energy counter hasn't moved for STALE_HOURS
                    during daylight (what happened when the plant moved to SEMS+)
  * sync failing  - sync_station raised an error FAILURES_BEFORE_ALERT times in a row
  * sync stopped  - no new GoodWe Reading for NO_SYNC_MINUTES (scheduler not running)

Each problem emails ALERT_RECIPIENTS once, reminds every REMIND_HOURS while it lasts,
and sends a "resolved" email when it clears.

Manual testing (System Manager), from the browser console or bench:
  frappe.call("upande_goodwe.goodwe.health.send_test_alert")
  frappe.call("upande_goodwe.goodwe.health.check_now", {send_email: 1})
  bench --site <site> execute upande_goodwe.goodwe.health.check_now --kwargs "{'send_email': 1}"
"""

import json

import frappe
from frappe.utils import add_to_date, flt, get_datetime, get_url, now_datetime, time_diff_in_seconds

ALERT_RECIPIENTS = ["edwin@upande.com", "wycliffe@upande.com"]

DAYLIGHT_HOURS = (9, 16)          # local hours when the counter must be moving
STALE_HOURS = 2                   # how long the counter may stay flat in daylight
MIN_READINGS_IN_WINDOW = 6        # need this many readings in the window to judge
FAILURES_BEFORE_ALERT = 2         # consecutive failed syncs (15 min apart) before alerting
NO_SYNC_MINUTES = 45              # no readings for this long = sync stopped
REMIND_HOURS = 12                 # repeat an open alert this often

STATE_KEY = "goodwe_alert_state"


# ----------------------------------------------------------------------------
# Alert state and email
# ----------------------------------------------------------------------------

def get_recipients():
    """Site config `goodwe_alert_recipients` (list) overrides the default list."""
    return frappe.conf.get("goodwe_alert_recipients") or ALERT_RECIPIENTS


def _load_state():
    try:
        return json.loads(frappe.db.get_default(STATE_KEY) or "{}")
    except ValueError:
        return {}


def _save_state(state):
    frappe.db.set_default(STATE_KEY, json.dumps(state))


def _send(subject, body_html):
    try:
        frappe.sendmail(
            recipients=get_recipients(),
            subject=subject,
            message=body_html,
            now=True,
        )
        return True
    except Exception:
        frappe.log_error(frappe.get_traceback(), "GoodWe alert email failed")
        return False


def notify(key, subject, body_html):
    """Open (or keep open) an alert. Emails on first occurrence and every REMIND_HOURS."""
    state = _load_state()
    now = now_datetime()
    entry = state.get(key)
    due = entry is None or time_diff_in_seconds(now, get_datetime(entry["last_sent"])) >= REMIND_HOURS * 3600
    if due:
        prefix = "" if entry is None else "[Still happening] "
        if _send(f"{prefix}{subject}", body_html):
            state[key] = {
                "first_seen": entry["first_seen"] if entry else str(now),
                "last_sent": str(now),
                "subject": subject,
            }
            _save_state(state)
    return due


def resolve(key, body_html=None):
    """Close an open alert and send one 'resolved' email."""
    state = _load_state()
    entry = state.pop(key, None)
    if not entry:
        return False
    _save_state(state)
    _send(
        f"Resolved: {entry.get('subject', key)}",
        body_html or f"<p>This GoodWe alert has cleared. It was first seen at {entry.get('first_seen')}.</p>"
        + _footer(),
    )
    return True


def _footer():
    return (
        f'<p style="color:#888;font-size:12px">Sent by the Upande GoodWe integration on '
        f'<a href="{get_url()}">{get_url()}</a>. Readings: '
        f'<a href="{get_url("/app/goodwe-reading")}">GoodWe Reading</a>.</p>'
    )


# ----------------------------------------------------------------------------
# Frozen data
# ----------------------------------------------------------------------------

def _frozen_since(inverter, lifetime):
    """First reading time at the current lifetime value, i.e. when the counter stopped."""
    row = frappe.db.sql(
        """
        SELECT MIN(reading_time) FROM `tabGoodWe Reading`
        WHERE inverter = %s AND total_energy_kwh = %s
          AND reading_time > IFNULL(
              (SELECT MAX(reading_time) FROM `tabGoodWe Reading`
               WHERE inverter = %s AND total_energy_kwh > 0 AND total_energy_kwh < %s), '1900-01-01')
        """,
        (inverter, lifetime, inverter, lifetime),
    )
    return row[0][0] if row and row[0][0] else None


def _last_daylight_moment(now=None):
    """The latest moment, at or before now, that falls inside DAYLIGHT_HOURS."""
    now = now or now_datetime()
    if DAYLIGHT_HOURS[0] <= now.hour < DAYLIGHT_HOURS[1]:
        return now
    end_of_daylight = now.replace(hour=DAYLIGHT_HOURS[1] - 1, minute=59, second=0, microsecond=0)
    if now.hour >= DAYLIGHT_HOURS[1]:
        return end_of_daylight
    return add_to_date(end_of_daylight, days=-1)


def _last_reading_saved():
    row = frappe.db.sql("SELECT MAX(creation) FROM `tabGoodWe Reading`")
    return row[0][0] if row and row[0][0] else None


def find_frozen_inverters(inverters=None, now=None):
    """Inverters whose lifetime counter didn't move in the STALE_HOURS before `now`.
    Returns None outside daylight (the counter legitimately stops at night)."""
    now = now or now_datetime()
    if not DAYLIGHT_HOURS[0] <= now.hour < DAYLIGHT_HOURS[1]:
        return None

    since = add_to_date(now, hours=-STALE_HOURS)
    if inverters is None:
        inverters = frappe.get_all("GoodWe Device", pluck="name")

    frozen = []
    for inv in inverters:
        rows = frappe.get_all(
            "GoodWe Reading",
            filters={"inverter": inv, "reading_time": ["between", [since, now]], "total_energy_kwh": [">", 0]},
            fields=["reading_time", "total_energy_kwh", "current_power_kw"],
            order_by="reading_time asc",
        )
        if len(rows) < MIN_READINGS_IN_WINDOW:
            continue
        span = time_diff_in_seconds(rows[-1].reading_time, rows[0].reading_time)
        if span < (STALE_HOURS * 3600) * 0.75:
            continue
        values = [flt(r.total_energy_kwh) for r in rows]
        if max(values) - min(values) < 0.05:
            frozen.append({
                "inverter": inv,
                "lifetime_kwh": values[-1],
                "frozen_since": _frozen_since(inv, values[-1]),
                "readings_checked": len(rows),
            })
    return frozen


def check_frozen(now=None, mark_readings=True):
    """Run the frozen-data check over all inverters, flag readings and email if needed."""
    frozen = find_frozen_inverters(now=now)
    if frozen is None:
        return None
    _apply_frozen(frozen, mark_readings)
    return frozen


def _apply_frozen(frozen, mark_readings=True):
    key = "frozen"
    if frozen:
        if mark_readings:
            since = add_to_date(now_datetime(), hours=-STALE_HOURS)
            for f in frozen:
                frappe.db.sql(
                    """UPDATE `tabGoodWe Reading` SET is_stale = 1
                       WHERE inverter = %s AND reading_time >= %s""",
                    (f["inverter"], f["frozen_since"] or since),
                )
        rows = "".join(
            f"<tr><td>{f['inverter']}</td><td>{f['lifetime_kwh']}</td>"
            f"<td>{f['frozen_since'] or 'more than ' + str(STALE_HOURS) + ' h ago'}</td></tr>"
            for f in frozen
        )
        notify(
            key,
            "GoodWe data frozen: readings are not updating",
            f"""
            <p>The GoodWe sync is running, but the data it receives isn't changing.
            The lifetime energy counter of {len(frozen)} inverter(s) hasn't moved for at least
            {STALE_HOURS} hours of daylight, so readings show 0 kWh and 0 kW even if the plant is producing.</p>
            <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
            <tr><th>Inverter</th><th>Lifetime kWh (stuck at)</th><th>Not changing since</th></tr>{rows}</table>
            <p><b>Check:</b> compare with SEMS+ (semsplus / plus.goodwe.com). If SEMS+ shows live data, the
            plant has likely been moved off the old SEMS Portal API that this integration reads.</p>
            {_footer()}
            """,
        )
    else:
        resolve(key)


# ----------------------------------------------------------------------------
# Sync failures and stopped sync
# ----------------------------------------------------------------------------

def record_sync_result(station, ok, error=None):
    """Called by sync_all_stations after each station. Alerts on repeated failures."""
    state = _load_state()
    counts = state.setdefault("_failures", {})
    key = f"sync_failed:{station}"
    if ok:
        if counts.pop(station, None) is not None:
            _save_state(state)
        resolve(key)
        return
    counts[station] = counts.get(station, 0) + 1
    _save_state(state)
    if counts[station] >= FAILURES_BEFORE_ALERT:
        notify(
            key,
            f"GoodWe sync failing for station {station}",
            f"""
            <p>The GoodWe sync for station <b>{station}</b> has failed {counts[station]} times in a row
            (every 15 minutes). No new readings are being saved.</p>
            <p><b>Last error:</b></p><pre style="white-space:pre-wrap">{frappe.utils.escape_html((error or '')[-2000:])}</pre>
            <p>Common causes: GoodWe login expired or password changed (see GoodWe Credentials),
            GoodWe API down, or the plant moved to SEMS+.</p>
            {_footer()}
            """,
        )


def check_sync_running():
    """Alerts if no GoodWe Reading has been saved for NO_SYNC_MINUTES."""
    last = _last_reading_saved()
    key = "sync_stopped"
    if not last or time_diff_in_seconds(now_datetime(), get_datetime(last)) > NO_SYNC_MINUTES * 60:
        notify(
            key,
            "GoodWe sync has stopped",
            f"""
            <p>No GoodWe readings have been saved since <b>{last or 'ever'}</b>.
            The 15-minute sync isn't running or every station is failing.</p>
            <p><b>Check:</b> the scheduler is enabled for the site (<code>bench --site &lt;site&gt; scheduler status</code>),
            the Scheduled Job Log for <code>sync_all_stations</code>, and the Error Log.</p>
            {_footer()}
            """,
        )
        return False
    resolve(key)
    return True


def run_scheduled_checks():
    """Hourly scheduler job."""
    check_sync_running()
    check_frozen()


# ----------------------------------------------------------------------------
# Manual testing
# ----------------------------------------------------------------------------

@frappe.whitelist()
def send_test_alert():
    """Sends a test email to the alert recipients so delivery can be checked."""
    frappe.only_for("System Manager")
    ok = _send(
        "Test: GoodWe alert email",
        "<p>This is a test of the GoodWe data alerts. If you received it, alerts will reach you.</p>" + _footer(),
    )
    return {"sent": ok, "recipients": get_recipients(),
            "note": None if ok else "Sending failed. Check Email Account (outgoing) and the Error Log."}


@frappe.whitelist()
def check_now(send_email=0):
    """Runs every check without syncing and returns what it found.
    Outside daylight the frozen-data check looks at the most recent daylight window,
    so it can be tested at any time. With send_email=1 alerts are sent/resolved exactly
    as the scheduler would."""
    frappe.only_for("System Manager")
    send_email = frappe.utils.cint(send_email)

    last = _last_reading_saved()
    minutes_since = round(time_diff_in_seconds(now_datetime(), get_datetime(last)) / 60) if last else None
    judged_at = _last_daylight_moment()
    frozen = find_frozen_inverters(now=judged_at)

    if send_email:
        check_sync_running()
        if frozen is not None:
            _apply_frozen(frozen)

    state = _load_state()
    return {
        "last_reading_saved": str(last) if last else None,
        "minutes_since_last_reading": minutes_since,
        "sync_stopped": minutes_since is None or minutes_since > NO_SYNC_MINUTES,
        "frozen_check_window": f"{add_to_date(judged_at, hours=-STALE_HOURS)} to {judged_at}",
        "frozen_inverters": frozen,
        "consecutive_failures": state.get("_failures", {}),
        "open_alerts": {k: v for k, v in state.items() if not k.startswith("_")},
        "recipients": get_recipients(),
        "emails_sent": bool(send_email),
    }


@frappe.whitelist()
def reset_alerts():
    """Clears alert state (no emails), e.g. after testing."""
    frappe.only_for("System Manager")
    _save_state({})
    return "Alert state cleared"
