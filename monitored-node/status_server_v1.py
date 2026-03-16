"""
Embedded Monitoring Platform - Monitored Node

Version: V1.0
Lightweight telemetry server for Raspberry Pi system monitoring.

Description
-----------
Runs a minimal Flask service on the monitored device that exposes
basic system telemetry over HTTP. This endpoint is polled by the
monitoring node dashboard.

Features
--------
- System uptime retrieval from Linux kernel
- CPU load monitoring (1 minute average)
- CPU temperature reading from onboard thermal sensor
- JSON telemetry endpoint for external monitoring systems
"""

from flask import Flask, jsonify
import os
import socket
import time
from datetime import datetime

app = Flask(__name__)

#==================================================================

def get_uptime_seconds():
  """
  Retrieve system uptime from Linux kernel uptime file
  Args:
      None
  Returns:
      System uptime in seconds since last boot
  """
  with open("/proc/uptime", "r") as f:
        uptime_seconds = float(f.readline().split()[0])

  return int(uptime_seconds)

#==================================================================

def get_cpu_load():
  """
  Retrieve CPU load average for the last 1 minute
  Args:
      None
  Returns:
      1-minute CPU load average rounded to two decimal places
  """
  #os.getloadavg() returns a tuple of (1min, 5min, 15min) averages
  load1, load5, load15 = os.getloadavg()

  return round(load1, 2)

#==================================================================

def get_cpu_temp_c():
  """
  Read CPU temperature from Raspberry Pi thermal sensor
  Args:
      None
  Returns:
      CPU temperature in Celsius rounded to one decimal place
  """

  with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp_milli_c = int(f.read().strip())

  #Sensor reports temperature in millidegrees Celsius
  return round(temp_milli_c / 1000.0, 1)

#==================================================================

@app.route("/status")
def status():
  """
  Telemetry endpoint for monitoring systems
  Returns current system status and metrics in JSON format
  Args:
    None
  Returns:
    JSON payload containing system telemetry
  """
  return jsonify({
        "node": socket.gethostname(),
        "status": "online",
        "uptime_seconds": get_uptime_seconds(),
        "cpu_load_1min": get_cpu_load(),
        "cpu_temp_c": get_cpu_temp_c(),
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })

#==================================================================

@app.route("/")
def home():
  """
  Basic landing page for the monitored node service
  Args:
    None
  Returns:
    Simple HTML message describing available endpoint
  """
  return "<h1>Pi 3 Status Server</h1><p>Use /status for JSON telemetry.</p>"

#==================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)