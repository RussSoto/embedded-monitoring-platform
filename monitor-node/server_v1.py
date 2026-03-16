"""
Embedded Monitoring Platform - Raspberry Pi

Version: V1.0
Simple distributed monitoring system using two Raspberry Pi devices.

Description
-----------
Runs a lightweight Flask dashboard on a monitoring node that polls a
second Raspberry Pi for system telemetry and displays the latest known status.

Features
--------
- Background polling of a monitored node over HTTP
- Local dashboard showing online/offline state
- Cached telemetry display for uptime, CPU load, temperature, and latency
- JSON API endpoint for machine-readable monitor status
- Local timestamp formatting for poll history display

"""

from flask import Flask, jsonify
import time
import threading
from datetime import datetime
import requests

#Monitoring Configuration
MONITORED_NODE_URL = "http://192.168.0.32:5001/status"
POLL_INTERVAL_SECONDS = 5

app = Flask(__name__)

#Monitor Runtime
start_time = time.time()

#Runtime State Dictionary
monitored_state = {
    "monitor_node": "pi-zero",
    "monitored_node": "pi3",
    "online": False,
    "last_successful_poll": None,
    "last_error": "Not polled yet",
    "data": None
}

def format_uptime(seconds):
    """
    Format uptime in seconds into HH:MM:SS display format
    Args:
        seconds: Uptime value in seconds or None
    Returns:
        Formatted uptime string in HH:MM:SS format
        otherwise "N/A"
    """
    if seconds is None:
        return "N/A"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds %60

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

#==================================================================

def c_to_f(celsius):
    """
    Convert temperature from Celsius to Fahrenheit
    Args:
        celsius: Temperature in Celsius or None
    Returns:
        Converted Fahrenheit value rounded to 1 decimal place
        otherwise "N/A"
    """
    if celsius is None:
        return "N/A"

    return round ((celsius* 9/5) + 32, 1)

#==================================================================

def format_timestamp(ts):
    """
    Convert monitored-node UTC timestamp into local display time
    Args:
        ts: ISO-format UTC timestamp string or None
    Returns:
        Local timestamp formatted as MM-DD-YYYY HH:MM:SS AM/PM
        otherwise "Never"
    """
    if ts is None:
        return "Never"

    from datetime import datetime, timezone

    #Parse timestamp from monitored node and convert to local time
    dt_utc = datetime.fromisoformat(ts.replace("Z","")).replace(tzinfo=timezone.utc)
    dt_local = dt_utc.astimezone()
    return dt_local.strftime("%m-%d-%Y %I:%M:%S %p")

#==================================================================

def poll_monitored_node():
    """
    Background polling loop.
    Repeatedly requests status data from monitored node endpoint & updates
    the shared monitoring dictionary with either:
        -the latest successful response data
        -the latest failure condition / error message
    Args:
        None

    Returns:
        None
    """
    global monitored_state

    while True:
        try:
            poll_start = time.time()
            #Send a GET request to the monitored node
            #timeout=2 prevents monitoring thread from hanging too long
            response = requests.get(MONITORED_NODE_URL, timeout=2)
            poll_latency_ms = int((time.time() - poll_start) * 1000)

            #Raise exception for HTTP error responses (404,500,etc) if request fails
            response.raise_for_status()

            #Parsing of the JSON body and updating monitored_state dictionary will
            #only execute if the status code was 2xx (successful)
            data = response.json()

            monitored_state["online"] = True
            monitored_state["last_successful_poll"] = datetime.utcnow().isoformat() + "Z"
            monitored_state["last_error"] = None
            monitored_state["poll_latency_ms"] = poll_latency_ms
            monitored_state["data"] = data

        except Exception as e:
            #Any failure during the request/response/parsing flow land here.
            #(Timeouts, connection failures, HTTP errors, invalid JSON response)
            monitored_state["online"] = False
            monitored_state["last_error"] = str(e)

        #Gives poll time to cycle to avoid continously hammering the monitored node.
        time.sleep(POLL_INTERVAL_SECONDS)

#==================================================================

@app.route("/")
def home():
    """
    Landing page of the monitoring dashboard.
    Displays the latest known status of monitored node using
    contents of monitored_state. This route does not poll , it only renders the most recently cached data
    maintained by the background thread.
    Args:
        None
    Returns:
        HTML dashboard page as a string
    """
    status_text = "ONLINE" if monitored_state["online"] else "OFFLINE"
    status_class = "status-online" if monitored_state["online"] else "status-offline"
    data = monitored_state["data"]

    if data:
        #dict.get() keeps the dashboard stable if one or more expected fields
        #are missing from the monitored node payload
        uptime = format_uptime(data.get("uptime_seconds"))
        cpu_load = data.get("cpu_load_1min", "N/A")

        cpu_temp_c = data.get("cpu_temp_c", "N/A")
        cpu_temp_f = c_to_f(cpu_temp_c)

        node_name = data.get("node", "unknown")

    else:
        uptime = "N/A"
        cpu_load = "N/A"
        cpu_temp_c = "N/A"
        cpu_temp_f = "N/A"
        node_name = monitored_state["monitored_node"]

    last_poll = format_timestamp(monitored_state["last_successful_poll"])

    latency = monitored_state.get("poll_latency_ms")
    if latency is None:
        latency = "N/A"

#======================= DASHBOARD HTML (Part of home) ==================================
    return f"""
    <html>

    <head>
    <meta http-equiv="refresh" content="5">
    <title>Embedded Monitoring Platform</title>

   <style>

   body{{
       font-family: Arial, sans-serif;
       background-color: #111;
       color: #eee;
       margin: 40px;
   }}

   h1{{
       color: #64B5F6;
       margin-bottom: 25px;
   }}

   h2{{
       margin-top: 30px;
       border-bottom: 1px solid #444;
       padding-bottom: 5px;
       color: #FFC107
   }}

   ul{{
       list-style:None;
       padding-left:0;
   }}

   li{{
       margin:6px 0;
   }}

   strong{{
       color: #BB86FC;
   }}

   .api-link a{{
       color: #03A9F4;
       text-decoration: none;
       font-weight: bold;
   }}

   .api-link a:hover{{
       text-decoration: underline;
   }}

   .status-online{{
       color: #4CAF50;
       font-weight: bold;
   }}

   .status-offline{{
       color: #F44336;
       font-weight: bold;
   }}

   </style>

   </head>

    <body>
    <h1>Embedded Monitoring Platform</h1>
    <p><strong>Monitoring Node:</strong> Pi Zero</p>
    <p><strong>Monitored Node:</strong> {node_name}</p>

    <p><strong>Status:</strong><span class="{status_class}">{status_text}</span></p>

    <h2>System Stats</h2>
    <ul>
    <li>Uptime: {uptime}</li>
    <li>CPU Load (1 min): {cpu_load}</li>
    <li>CPU Temp (C): {cpu_temp_c}&deg;C</li>
    <li>CPU Temp (F): {cpu_temp_f}&deg;F</li>
    <li>Latency: {latency} ms</li>
    </ul>

    <h2>Monitoring</h2>



    <ul>
    <li>Last Successful Poll: {last_poll}</li>
    <li>Last Error: {monitored_state["last_error"]}</li>
    </ul>

    <p class="api-link">
    <a href="/api/status">View raw JSON status</a></p>

    </body>
    </html>
    """

#==================================================================

@app.route("/api/status")
def api_status():
    """
    Returns the Pi Zero monitor's current view of system state as JSON making it
    machine readable. System view includes:
        -monitoring node identity
        -monitored node identity
        -monitored service uptime
        -mnline/Offline state
        -last successful poll time
        -last recorded error
        -last cached telemetry payload from monitored node
    Args:
        None
    Returns:
        JSON response containing current monitoring state
    """
    monitor_uptime = int(time.time() - start_time)

    return jsonify({
        "monitor_node": monitored_state["monitor_node"],
        "monitored_node": monitored_state["monitored_node"],
        "monitor_uptime_seconds": monitor_uptime,
        "online": monitored_state["online"],
        "last_successful_poll": monitored_state["last_successful_poll"],
        "last_error": monitored_state["last_error"],
        "data": monitored_state["data"]
    })

#==================================================================

if __name__ == "__main__":
    #Run polling loop in a daemon thread so Flask can serve requests
    #while the monitor continues collecting status data in the background
    poller = threading.Thread(target=poll_monitored_node, daemon=True)
    poller.start()

    app.run(host="0.0.0.0", port=5000)
