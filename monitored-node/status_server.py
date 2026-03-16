from flask import Flask, jsonify
import os
import socket
import time
from datetime import datetime

app = Flask(__name__)

#Return uptime since boot, file also contains idle time. 
def get_uptime_seconds():
    with open("/proc/uptime", "r") as f:
        uptime_seconds = float(f.readline().split()[0])
    return int(uptime_seconds)

#Load average returns tuple of average CPU load over last 1 minute, 5 minutes, & 15 minutes. Updated Values continuously updated.
def get_cpu_load():
    load1, load5, load15 = os.getloadavg()
    return round(load1, 2)

#Return value from CPU temperature sensor in millidegree's that has been rounded and converted to Celsius 
def get_cpu_temp_c():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp_milli_c = int(f.read().strip())
    return round(temp_milli_c / 1000.0, 1)


@app.route("/status")
def status():
    return jsonify({
        "node": socket.gethostname(),
        "status": "online",
        "uptime_seconds": get_uptime_seconds(),
        "cpu_load_1min": get_cpu_load(),
        "cpu_temp_c": get_cpu_temp_c(),
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


@app.route("/")
def home():
    return "<h1>Pi 3 Status Server</h1><p>Use /status for JSON telemetry.</p>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
