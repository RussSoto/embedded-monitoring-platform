"""
Embedded Monitoring Platform - Raspberry Pi

Version: V2.0
Distributed monitoring system using two Raspberry Pi devices.

Description
-----------
Runs a lightweight Flask dashboard on the monitoring node that polls a
second Raspberry Pi for system telemetry and displays the latest known status.

Features
--------
- Background polling of a monitored node over HTTP
- Local dashboard showing online/offline state
- Cached telemetry display for:
    -uptime
    -CPU load / usage
    -temperature
    -memory usage
    -disc usage
    -network I/O
    -latency
- Vision pipeline monitoring:
    -Tracks runtime health of a seperate computer vision system running on the monitored node
    -Allows the dashboard to confirm the system is actively processing frames and not stalled
    -Key signals:
        -vission process running
        -camera status
        -FPS ( frame rate)
        -vision runtime (Uptime for the vision pipeline)
- Event logging:
    - Records key state transitions and events (online/offline, failures, watchdog triggers)
    - Associates events with metric snapshot for context
    - Enable historical visibility beyond real time dashboard state
    - Accessible via API and deidcated log dashboard page 
- JSON API endpoint for machine-readable monitor status
- Local timestamp formatting for poll dashboard display

Notes
-----
V2 expands the original monitor into a more complete health dashboard with alerting/logging support,
visibility into the computer vision pipeline, and a reworked dashboardfor improved clarity and usability.

"""

from flask import Flask, jsonify, render_template
import time
import threading
from datetime import datetime
import requests
import json

app = Flask(__name__)

# Log file for  monitor events / state changes
LOG_FILE = "events.log"

#Monitoring Configuration
MONITORED_NODE_URL = "http://192.168.0.32:5001/status"
POLL_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5
STALE_THRESHOLD_SECONDS = POLL_INTERVAL_SECONDS * 3
WATCHDOG_THRESHOLD = 3


#Monitor Runtime
start_time = time.time()

#Shared runtime state for monitored node
monitored_state = {
    "monitor_node": "pi-zero",
    "monitored_node": "pi3",
    "online": False,
    "status": "offline",
    "last_poll_attempt": None,
    "last_successful_poll": None,
    "last_error": "Not polled yet",
    "poll_latency_ms": None,
    "data": {},
    "logs": [],
    "last_successful_poll_time": 0.0,
    "stale": False,
    "staleness_seconds": 0,
    "consecutive_failures": 0,
    "watchdog_triggered": False
}

#Lock protects shared monitor state between flask routes and poll thread
state_lock = threading.Lock()

#==================================================================

def log_event(node, event, message, severity="info", metrics=None):
    """
    Append a single monitoring event into the local log file.
    Used for important state transistions like online/offline changes &
    watchdog events so the dashboard can show a simple recent history 
    Args:
        node: Node name associated with the event
        event: Short event label
        severity: Event importance level
        metrics: Optional metric snapshot captured at time of event
    Returns:
        None
    """
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "node": node,
        "event": event,
        "message": message,
        "severity": severity,  
        "metrics": metrics or {}       
    }
    
    with open (LOG_FILE,"a") as f:
        f.write(json.dumps(entry) + "\n")

#==================================================================

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
    seconds = int(seconds)
    
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
    try:
        return round((celsius* 9/5) + 32, 1)
    except (TypeError, ValueError):    
        return "N/A"

    

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

def format_bytes_to_gb(value):
    """
    Format byte into GB when disk metrics are avaiable later"
    Args:
        Value: Memory value in bytes
    Return:
        Memory value in GB    
    """
    if value is None:
        return "N/A"
    return f"{value / (1024 ** 3):.2f} GB"

#==================================================================

def format_percent(value):
    """
    Format numeric percentage for display
    Args:
        value: Numeric value
    Return:
        Value: Percentage value %
    """
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"

#==================================================================

def format_rate(value):
    """
    Format network throughput value in kB/s
    Args:
        value: Numeric value
    Return:
        Value: rounded throughput kB/s
    """
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f} kB/s"
    except (TypeError, ValueError):
        return "N/A"

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
            prev_online = monitored_state.get("online",False)
            last_good_poll_time = time.time()
            now_iso = datetime.utcnow().isoformat() + "Z"
            
            poll_start = time.time()
            
            #Time the request so latency can be displayed on the dashboard 
            response = requests.get(MONITORED_NODE_URL, timeout = REQUEST_TIMEOUT_SECONDS)
            poll_latency_ms = int((time.time() - poll_start) * 1000)

            #Raise exception for HTTP error responses (404,500,etc) if request fails
            response.raise_for_status()

            
            #Only update cached telemetry if the monitored node retrun valid data
            data = response.json()

            with state_lock:
                monitored_state["online"] = True
                monitored_state["status"] = "online"
                monitored_state["last_poll_attempt"] = now_iso
                monitored_state["last_successful_poll"] = now_iso
                
                monitored_state["last_successful_poll_time"] = last_good_poll_time
                monitored_state["stale"] = False
                monitored_state["staleness_seconds"] = 0
                
                monitored_state["consecutive_failures"] = 0
                monitored_state["watchdog_triggered"] = False
                
                monitored_state["last_error"] = None
                monitored_state["poll_latency_ms"] = poll_latency_ms
                monitored_state["monitored_node"] = data.get("node", monitored_state["monitored_node"])
                monitored_state["data"] = data
                
                #Lightweight health metrics snapshot for event logging 
                metrics_snapshot = {
                    "cpu_usage_percent": data.get("cpu_usage_percent"),               
                    "memory_used_percent": data.get("memory_used_percent"),
                    "disk_used_percent": data.get("disk_used_percent")
                    }
                
                #only log recovery when the node actually transistioned back online
                if not prev_online:
                    log_event(
                        node = monitored_state["monitored_node"],
                        event = "online",
                        message = "Node back online",
                        metrics = metrics_snapshot
                        )

        except Exception as e:
            #Any failure during the request/response/parsing flow land here.
            #(Timeouts, connection failures, HTTP errors, invalid JSON response)
            current_time = time.time()
            now_iso = datetime.utcnow().isoformat() + "Z"
            
            with state_lock:
                prev_online = monitored_state.get("online",False)
                data_snapshot = monitored_state.get("data", {})
                
                # Use latest cached metrics so logs still carry useful context on failures
                metrics_snapshot = {
                    "cpu_usage_percent": data_snapshot.get("cpu_usage_percent"),               
                    "memory_used_percent": data_snapshot.get("memory_used_percent"),
                    "disk_used_percent": data_snapshot.get("disk_used_percent")
                    }
                
                last_successful_time = monitored_state.get("last_successful_poll_time",0.0)
                
                age=0
                if last_successful_time > 0:
                    age = int(current_time - last_successful_time)
                
                monitored_state["online"] = False
                monitored_state["status"] = "offline"
                monitored_state["last_poll_attempt"] = now_iso
                monitored_state["last_error"] = str(e)
                
                monitored_state["stale"] = age > STALE_THRESHOLD_SECONDS
                monitored_state["staleness_seconds"] = age 
                
                monitored_state["consecutive_failures"] = monitored_state.get("consecutive_failures",0) + 1
                monitored_state["watchdog_triggered"] = monitored_state.get("watchdog_triggered",False)
                
                if (monitored_state["consecutive_failures"] >= WATCHDOG_THRESHOLD
                    and not monitored_state["watchdog_triggered"]
                ):
                    monitored_state["watchdog_triggered"] = True
                    log_event(
                        node = monitored_state["monitored_node"],
                        event = "watchdog triggered",
                        message = "Node failed multiple consecutive attempts",
                        severity = "critical",
                        metrics = metrics_snapshot
                        )
                
                
                #Only log offline event on state transition to avoid noisy repeats 
                if prev_online:
                    log_event(
                        node = monitored_state["monitored_node"],
                        event = "offline",
                        message = f"Node unreachable: {e}",
                        metrics = metrics_snapshot
                        )

        #Keeps polling steady without hammering the monitored node
        time.sleep(POLL_INTERVAL_SECONDS)

#==================================================================

@app.route("/")
def home():
    """
    Render the main monitoring dashboard.
    
    Displays the latest known status of the monitored node using
    cached values from monitored_state. This route does not poll,
    it only renders the most recently cached data maintained by the background thread.
    Args:
        None
    Returns:
        HTML dashboard page as a string
    """
       
    alerts = []
    
    #Dashboard warning thresholds
    LATENCY_WARNING_MS = 3000
    CPU_USAGE_WARNING_PERCENT = 80
    CPU_TEMP_WARNING_C = 75
    MEMORY_USED_WARNING_PERCENT = 85
    DISK_USED_WARNING_PERCENT = 90
    
    
    
    with state_lock:
        online = monitored_state["online"]
        poll_latency_ms = monitored_state["poll_latency_ms"]
        data = monitored_state["data"] or {}
        stale = monitored_state.get("stale", False)
        last_success_time = monitored_state.get("last_successful_poll_time", 0.0)
        watchdog_triggered = monitored_state.get("watchdog_triggered")
    
    status_text = "ONLINE" if online else "OFFLINE"
    status_class = "status-online" if online else "status-offline"
   

    
    #Current v1 metrics from cached pi3 payload
    node_name = data.get("node", monitored_state["monitored_node"])
    uptime = format_uptime(data.get("uptime_seconds"))
    
    cpu_load = data.get("cpu_load_1min", "N/A")
    
    
    cpu_temp_c = data.get("cpu_temp_c", "N/A")
    cpu_temp_f = c_to_f(cpu_temp_c)
    
    #V2 metrics expansion
    memory_used_percent_raw = data.get("memory_used_percent")
    memory_used_percent = format_percent(memory_used_percent_raw)
    disk_used_percent_raw = data.get("disk_used_percent")
    disk_used_percent = format_percent(disk_used_percent_raw)
    cpu_usage_percent_raw = data.get("cpu_usage_percent")
    cpu_usage_percent = format_percent(cpu_usage_percent_raw)
    
    #Network I/O arrives as [rx,tx]
    network_io = data.get("network_io_kBps")
    if isinstance(network_io, list) and len(network_io)==2:
        network_rx_kBps = format_rate(network_io[0])
        network_tx_kBps = format_rate(network_io[1])
    else:
        network_rx_kBps = "N/A"
        network_tx_kBps = "N/A"
    
    #Recalc staleness from the last known good poll time for display / alerts 
    if last_success_time >0:
        staleness_seconds = int(time.time()-last_success_time)
    else:
        staleness_seconds = 0
    stale = staleness_seconds > STALE_THRESHOLD_SECONDS
    
    # V2 vision expansion
    process_status = "ONLINE" if data.get("vision_process_running") else "OFFLINE"    
    camera_status = "ONLINE" if data.get("camera_status") else "OFFLINE" 
    
    frame_rate = data.get("fps")
    if frame_rate is None:
        frame_rate = "N/A"
    
    vision_runtime_seconds_raw = data.get("vision_runtime_seconds")
    vision_runtime_seconds = format_uptime(vision_runtime_seconds_raw)
    if vision_runtime_seconds_raw is None:
        vision_runtime_seconds = "N/A"
        
        
    last_poll = format_timestamp(monitored_state["last_successful_poll"])

    latency = monitored_state.get("poll_latency_ms")
    if latency is None:
        latency = "N/A"

    # Build alert list from from current cached state and threshold checks.
    # System alerts only happen when monitored node online (exception for watchdog trigger)
    if not online:
        alerts.append({
        "level": "critical",
        "message":"Monitored node is offline"        
        })
    else:
        if poll_latency_ms is not None and poll_latency_ms > LATENCY_WARNING_MS:
            alerts.append({
                "level": "warning",
                "message": f"High poll latency: {poll_latency_ms:.0f} ms"
                })
        if cpu_usage_percent_raw is not None and cpu_usage_percent_raw > CPU_USAGE_WARNING_PERCENT:
            alerts.append({
                "level": "warning",
                "message": f"High CPU usage: {cpu_usage_percent_raw:.1f}%"
                })
        if cpu_temp_c is not None and cpu_temp_c > CPU_TEMP_WARNING_C:
            alerts.append({
                "level": "warning",
                "message": f"High CPU temperature: {cpu_temp_c:.1f}C"
                })
        if memory_used_percent_raw is not None and memory_used_percent_raw > MEMORY_USED_WARNING_PERCENT:
            alerts.append({
                "level": "warning",
                "message": f"High memory usage: {memory_used_percent_raw:.1f}%"
                })
        if disk_used_percent_raw is not None and disk_used_percent_raw > DISK_USED_WARNING_PERCENT:
            alerts.append({
                "level": "warning",
                "message": f"High memory usage: {disk_used_percent_raw:.1f}%"
                })
        if stale:
            alerts.append({
                "level": "warning",
                "message": f"Data stale: Last good update  {staleness_seconds}s ago"
                })
    if watchdog_triggered:
        alerts.append({
            "level": "critical",
            "message": "Watchdog Triggered"
            })

    
    
    return  render_template(
        "dashboard.html",
        alerts=alerts,
        online=online,
        monitor_node=monitored_state["monitor_node"],
        monitored_node=node_name,
        status_text=status_text,
        status_class=status_class,
        uptime=uptime,
        cpu_load=cpu_load,
        cpu_usage_percent=cpu_usage_percent,
        cpu_temp_c=cpu_temp_c,
        cpu_temp_f=cpu_temp_f,
        latency=latency,
        last_poll=last_poll,
        last_error=monitored_state["last_error"],
        memory_used_percent=memory_used_percent,
        disk_used_percent=disk_used_percent,
        network_rx_kBps=network_rx_kBps,
        network_tx_kBps=network_tx_kBps,
        process_status=process_status,
        camera_status=camera_status,
        frame_rate=frame_rate,
        vision_runtime_seconds=vision_runtime_seconds,
    )

#==================================================================

@app.route("/api/status")
def api_status():
    """
    Returns the Pi Zero monitor's current view of system state as JSON.
    
    Machine readable state includes:
        -monitoring node identity
        -monitored node identity
        -monitored service uptime
        -online/Offline state
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
        "last_successful_poll_time": monitored_state["last_successful_poll_time"],
        "stale": monitored_state["stale"],
        "staleness_seconds": monitored_state["staleness_seconds"],
        "last_error": monitored_state["last_error"],
        "data": monitored_state["data"]
    })


#==================================================================

@app.route("/logging")
def get_logs():
    """
    Returns recent monitor logs as JSON.
    Log output includes:
        -monitored node state 
        -monitored node identity
        -event timestamp
        -message & severity
        -cached metric snapshot at event time
    Args:
        None
    Returns:
        JSON response containing logs
    """
    logs =[]
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
    except FileNotFoundError:
        return jsonify([])

    #Return newest 50 ogs first for easier dashbaord / API consumption
    return jsonify(logs[-50:][::-1])
#==================================================================

#==================================================================

@app.route("/logging/view")
def logging_view():
    """
    Render recent monitor logs on the log dashboard page.
    Log display includes:
        -event name
        -timestamp 
        -message & severity
        -cached metric snapshot at event time
    Args:
        None
    Returns:
        HTML response containing logs
    """
    logs =[]
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
    except FileNotFoundError:
        logs = []

    return render_template("logging.html", logs=logs[-50:][::-1])

#==================================================================

if __name__ == "__main__":
    #Run polling loop in a daemon thread so Flask can serve requests
    #while the monitor continues collecting status data in the background
    poller = threading.Thread(target=poll_monitored_node, daemon=True)
    poller.start()

    app.run(host="0.0.0.0", port=5000)