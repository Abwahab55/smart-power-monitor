"""
Smart Power Monitor — AWS Lambda Function
Triggered by AWS IoT Core Rule when a message arrives on 'power/monitor/data'.
Responsibilities:
  1. Validate and parse incoming sensor payload
  2. Detect power anomalies (overvoltage, overcurrent, undervoltage)
  3. Store reading to DynamoDB
  4. Publish alert to SNS if anomaly detected
  5. Archive raw payload to S3
"""

import json
import os
import uuid
import boto3
from datetime import datetime, timezone
from decimal import Decimal


# ── AWS Clients (initialized once per Lambda container) ──────────────────────

dynamodb = boto3.resource("dynamodb")
sns      = boto3.client("sns")
s3       = boto3.client("s3")

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "power-monitor-readings")
SNS_ARN    = os.environ.get("SNS_TOPIC_ARN", "")
S3_BUCKET  = os.environ.get("S3_BUCKET", "power-monitor-raw")

table = dynamodb.Table(TABLE_NAME)


# ── Thresholds ───────────────────────────────────────────────────────────────

THRESHOLDS = {
    "voltage_v":      {"min": 207.0,  "max": 253.0},   # ±10% of 230V
    "current_a":      {"min": 0.0,    "max": 16.0},     # 16A circuit breaker
    "power_factor":   {"min": 0.80,   "max": 1.0},
    "frequency_hz":   {"min": 49.5,   "max": 50.5},
    "temperature_c":  {"min": 0.0,    "max": 85.0},
}


# ── Helper: float → Decimal for DynamoDB ────────────────────────────────────

def to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(i) for i in obj]
    return obj


# ── Anomaly Detection ────────────────────────────────────────────────────────

def detect_anomalies(payload):
    alerts = []
    for field, limits in THRESHOLDS.items():
        value = payload.get(field)
        if value is None:
            continue
        if value < limits["min"]:
            alerts.append({
                "field":     field,
                "value":     value,
                "threshold": limits["min"],
                "type":      "BELOW_MIN",
                "severity":  "HIGH" if field in ["voltage_v", "current_a"] else "MEDIUM",
            })
        elif value > limits["max"]:
            alerts.append({
                "field":     field,
                "value":     value,
                "threshold": limits["max"],
                "type":      "ABOVE_MAX",
                "severity":  "HIGH" if field in ["voltage_v", "current_a"] else "MEDIUM",
            })

    if payload.get("anomaly") and payload.get("fault_type"):
        alerts.append({
            "field":    "device_reported",
            "value":    payload["fault_type"],
            "type":     "DEVICE_FAULT",
            "severity": "CRITICAL",
        })

    return alerts


# ── Store to DynamoDB ────────────────────────────────────────────────────────

def store_reading(payload, alerts):
    item = to_decimal({
        "device_id":    payload["device_id"],
        "timestamp":    payload["timestamp"],
        "reading_id":   str(uuid.uuid4()),
        "voltage_v":    payload.get("voltage_v"),
        "current_a":    payload.get("current_a"),
        "active_power_w":    payload.get("active_power_w"),
        "apparent_power_va": payload.get("apparent_power_va"),
        "reactive_power_var":payload.get("reactive_power_var"),
        "power_factor": payload.get("power_factor"),
        "frequency_hz": payload.get("frequency_hz"),
        "temperature_c":payload.get("temperature_c"),
        "has_alert":    len(alerts) > 0,
        "alert_count":  len(alerts),
        "alerts":       json.dumps(alerts),
        "ttl":          int(datetime.now(timezone.utc).timestamp()) + 30 * 24 * 3600,
    })
    table.put_item(Item=item)
    print(f"[DynamoDB] Stored reading for {payload['device_id']} at {payload['timestamp']}")


# ── Send SNS Alert ────────────────────────────────────────────────────────────

def send_alert(payload, alerts):
    if not SNS_ARN:
        print("[SNS] No SNS_TOPIC_ARN configured — skipping alert.")
        return

    critical = [a for a in alerts if a.get("severity") == "CRITICAL"]
    high      = [a for a in alerts if a.get("severity") == "HIGH"]

    subject = (
        f"[CRITICAL] Power fault on {payload['device_id']}" if critical
        else f"[ALERT] Power anomaly on {payload['device_id']}"
    )

    lines = [
        f"Smart Power Monitor — Anomaly Detected",
        f"{'='*45}",
        f"Device:    {payload['device_id']}",
        f"Timestamp: {payload['timestamp']}",
        f"",
        f"Current Readings:",
        f"  Voltage:      {payload.get('voltage_v')} V",
        f"  Current:      {payload.get('current_a')} A",
        f"  Active Power: {payload.get('active_power_w')} W",
        f"  Power Factor: {payload.get('power_factor')}",
        f"  Frequency:    {payload.get('frequency_hz')} Hz",
        f"  Temperature:  {payload.get('temperature_c')} °C",
        f"",
        f"Detected Anomalies ({len(alerts)}):",
    ]
    for a in alerts:
        lines.append(f"  [{a['severity']}] {a['field']}: {a['value']} — {a['type']}")

    sns.publish(
        TopicArn=SNS_ARN,
        Subject=subject,
        Message="\n".join(lines),
    )
    print(f"[SNS] Alert sent: {subject}")


# ── Archive to S3 ─────────────────────────────────────────────────────────────

def archive_to_s3(payload):
    ts   = payload.get("timestamp", datetime.now(timezone.utc).isoformat())
    date = ts[:10]
    key  = f"raw/{payload['device_id']}/{date}/{ts}.json"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(payload),
        ContentType="application/json",
    )
    print(f"[S3] Archived to s3://{S3_BUCKET}/{key}")


# ── Lambda Handler ────────────────────────────────────────────────────────────

def handler(event, context):
    """
    Entry point. AWS IoT Core Rule passes the MQTT payload as `event`.
    """
    print(f"[Lambda] Received event: {json.dumps(event)}")

    try:
        payload = event if isinstance(event, dict) else json.loads(event)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"[Lambda] ERROR: Invalid payload — {e}")
        return {"statusCode": 400, "body": "Invalid payload"}

    required = ["device_id", "timestamp", "voltage_v", "current_a"]
    missing  = [f for f in required if f not in payload]
    if missing:
        print(f"[Lambda] ERROR: Missing fields: {missing}")
        return {"statusCode": 400, "body": f"Missing fields: {missing}"}

    alerts = detect_anomalies(payload)
    print(f"[Lambda] Anomalies detected: {len(alerts)}")

    store_reading(payload, alerts)
    archive_to_s3(payload)

    if alerts:
        send_alert(payload, alerts)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message":      "Reading processed successfully",
            "device_id":    payload["device_id"],
            "timestamp":    payload["timestamp"],
            "alerts":       len(alerts),
        }),
    }
