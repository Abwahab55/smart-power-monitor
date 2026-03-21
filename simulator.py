"""
Smart Power Monitor — Edge Device Simulator
Simulates a power sensor (voltage, current, power factor) and
publishes readings to AWS IoT Core via MQTT over TLS.
"""

import json
import time
import random
import math
import argparse
from datetime import datetime, timezone

try:
    from awsiot import mqtt_connection_builder
    from awscrt import mqtt
    AWS_IOT_AVAILABLE = True
except ImportError:
    AWS_IOT_AVAILABLE = False
    print("[WARNING] awsiotsdk not installed — running in LOCAL mode (print only)")


# ── Configuration ────────────────────────────────────────────────────────────

TOPIC        = "power/monitor/data"
ALERT_TOPIC  = "power/monitor/alerts"
CLIENT_ID    = "smart-power-monitor-device-01"
PUBLISH_INTERVAL = 5  # seconds between readings


# ── Sensor Simulation ────────────────────────────────────────────────────────

class PowerSensorSimulator:
    """Simulates a 3-phase power monitoring sensor."""

    def __init__(self, nominal_voltage=230.0, nominal_current=10.0):
        self.nominal_voltage = nominal_voltage
        self.nominal_current = nominal_current
        self.t = 0

    def _add_noise(self, value, noise_pct=0.02):
        return value * (1 + random.uniform(-noise_pct, noise_pct))

    def _inject_anomaly(self):
        """Randomly inject a fault event (5% probability)."""
        return random.random() < 0.05

    def read(self):
        self.t += 1
        anomaly = self._inject_anomaly()

        voltage = self._add_noise(self.nominal_voltage)
        current = self._add_noise(self.nominal_current)

        if anomaly:
            fault_type = random.choice(["overvoltage", "overcurrent", "undervoltage"])
            if fault_type == "overvoltage":
                voltage *= random.uniform(1.15, 1.30)
            elif fault_type == "overcurrent":
                current *= random.uniform(1.20, 1.50)
            elif fault_type == "undervoltage":
                voltage *= random.uniform(0.70, 0.85)
        else:
            fault_type = None

        power_factor = self._add_noise(0.92, noise_pct=0.03)
        apparent_power = voltage * current
        active_power   = apparent_power * power_factor
        reactive_power = math.sqrt(max(apparent_power**2 - active_power**2, 0))
        frequency      = self._add_noise(50.0, noise_pct=0.005)
        temperature    = self._add_noise(45.0 + current * 0.5)

        return {
            "device_id":       CLIENT_ID,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "voltage_v":       round(voltage, 3),
            "current_a":       round(current, 3),
            "active_power_w":  round(active_power, 2),
            "apparent_power_va": round(apparent_power, 2),
            "reactive_power_var": round(reactive_power, 2),
            "power_factor":    round(power_factor, 4),
            "frequency_hz":    round(frequency, 3),
            "temperature_c":   round(temperature, 2),
            "anomaly":         anomaly,
            "fault_type":      fault_type,
        }


# ── MQTT Publisher ───────────────────────────────────────────────────────────

class IoTPublisher:

    def __init__(self, endpoint, cert, key, ca, client_id):
        self.connection = mqtt_connection_builder.mtls_from_path(
            endpoint=endpoint,
            cert_filepath=cert,
            pri_key_filepath=key,
            ca_filepath=ca,
            client_id=client_id,
            clean_session=False,
            keep_alive_secs=30,
        )

    def connect(self):
        print("[IoT] Connecting to AWS IoT Core...")
        future = self.connection.connect()
        future.result()
        print("[IoT] Connected.")

    def publish(self, topic, payload):
        self.connection.publish(
            topic=topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )

    def disconnect(self):
        self.connection.disconnect().result()


# ── Main Loop ────────────────────────────────────────────────────────────────

def run_local(interval):
    """Run without AWS — prints readings to console."""
    sensor = PowerSensorSimulator()
    print(f"[LOCAL] Publishing every {interval}s. Press Ctrl+C to stop.\n")
    try:
        while True:
            reading = sensor.read()
            print(json.dumps(reading, indent=2))
            if reading["anomaly"]:
                print(f"  *** ALERT: {reading['fault_type'].upper()} detected! ***")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[LOCAL] Stopped.")


def run_aws(endpoint, cert, key, ca, interval):
    """Publish to AWS IoT Core via MQTT."""
    sensor    = PowerSensorSimulator()
    publisher = IoTPublisher(endpoint, cert, key, ca, CLIENT_ID)
    publisher.connect()

    print(f"[IoT] Publishing to '{TOPIC}' every {interval}s. Ctrl+C to stop.\n")
    try:
        while True:
            reading = sensor.read()
            publisher.publish(TOPIC, reading)
            print(f"[{reading['timestamp']}] V={reading['voltage_v']}V "
                  f"I={reading['current_a']}A P={reading['active_power_w']}W "
                  f"{'*** FAULT: ' + str(reading['fault_type']) + ' ***' if reading['anomaly'] else ''}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[IoT] Stopping...")
        publisher.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Smart Power Monitor — Device Simulator")
    parser.add_argument("--endpoint",  help="AWS IoT Core endpoint")
    parser.add_argument("--cert",      help="Path to device certificate (.pem)")
    parser.add_argument("--key",       help="Path to private key (.pem)")
    parser.add_argument("--ca",        help="Path to root CA certificate")
    parser.add_argument("--interval",  type=int, default=PUBLISH_INTERVAL)
    parser.add_argument("--local",     action="store_true", help="Run in local mode (no AWS)")
    args = parser.parse_args()

    if args.local or not AWS_IOT_AVAILABLE:
        run_local(args.interval)
    else:
        if not all([args.endpoint, args.cert, args.key, args.ca]):
            print("ERROR: Provide --endpoint, --cert, --key, --ca for AWS mode.")
            print("       Or run with --local for offline testing.")
            return
        run_aws(args.endpoint, args.cert, args.key, args.ca, args.interval)


if __name__ == "__main__":
    main()
