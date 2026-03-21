"""
Smart Power Monitor — API Lambda
Serves REST endpoints for the web dashboard via API Gateway.

Routes:
  GET /readings?device_id=X&limit=100   — latest N readings
  GET /readings/stats?device_id=X       — aggregated stats
  GET /alerts?device_id=X&limit=50      — recent alerts only
"""

import json
import os
import boto3
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr


dynamodb   = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "power-monitor-readings")
table      = dynamodb.Table(TABLE_NAME)


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=decimal_default),
    }


def get_readings(device_id, limit=100):
    result = table.query(
        KeyConditionExpression=Key("device_id").eq(device_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return result.get("Items", [])


def get_alerts(device_id, limit=50):
    result = table.query(
        KeyConditionExpression=Key("device_id").eq(device_id),
        FilterExpression=Attr("has_alert").eq(True),
        ScanIndexForward=False,
        Limit=200,
    )
    items = result.get("Items", [])
    return items[:limit]


def compute_stats(readings):
    if not readings:
        return {}

    def avg(field):
        vals = [float(r[field]) for r in readings if field in r]
        return round(sum(vals) / len(vals), 3) if vals else None

    def mx(field):
        vals = [float(r[field]) for r in readings if field in r]
        return round(max(vals), 3) if vals else None

    def mn(field):
        vals = [float(r[field]) for r in readings if field in r]
        return round(min(vals), 3) if vals else None

    alert_count = sum(1 for r in readings if r.get("has_alert"))

    return {
        "reading_count":      len(readings),
        "alert_count":        alert_count,
        "alert_rate_pct":     round(alert_count / len(readings) * 100, 1),
        "voltage": {"avg": avg("voltage_v"), "min": mn("voltage_v"), "max": mx("voltage_v")},
        "current": {"avg": avg("current_a"), "min": mn("current_a"), "max": mx("current_a")},
        "active_power": {"avg": avg("active_power_w"), "max": mx("active_power_w")},
        "power_factor": {"avg": avg("power_factor"), "min": mn("power_factor")},
        "temperature":  {"avg": avg("temperature_c"), "max": mx("temperature_c")},
    }


def handler(event, context):
    path   = event.get("path", "/")
    params = event.get("queryStringParameters") or {}
    device_id = params.get("device_id", "smart-power-monitor-device-01")

    try:
        if path == "/readings":
            limit    = int(params.get("limit", 100))
            readings = get_readings(device_id, limit)
            return respond(200, {"device_id": device_id, "count": len(readings), "readings": readings})

        elif path == "/readings/stats":
            readings = get_readings(device_id, 500)
            stats    = compute_stats(readings)
            return respond(200, {"device_id": device_id, "stats": stats})

        elif path == "/alerts":
            limit  = int(params.get("limit", 50))
            alerts = get_alerts(device_id, limit)
            return respond(200, {"device_id": device_id, "count": len(alerts), "alerts": alerts})

        else:
            return respond(404, {"error": f"Unknown path: {path}"})

    except Exception as e:
        print(f"[API] ERROR: {e}")
        return respond(500, {"error": str(e)})
