"""
Embedded Monitoring Platform - Monitored Node

Version: V2.0
Lightweight telemetry server for Raspberry Pi system monitoring.

Description
-----------
Runs a minimal Flask service on the monitored device that exposes system
telemetry over HTTP. This endpoint is polled by the monitoring node.

Features
--------
- System uptime retrieval from Linux kernel
- CPU load monitoring (1 minute average)
- CPU temperature reading from onboard thermal sensor
- Memeory and disk usage monitoring
- CPU usage and network I/O sampling via background thread
- Vision pipeline monitoring:
    - Reads status from external compter vision system
    - Exposes runtime health signals (process, camera, FPS, runtime)
- JSON telemetry endpoint for external monitoring systems

Notes
-----
This service is designed to stay lightweight and focused on data collection,
with all visualization and alerting handled on the monitoring node.
"""

from flask import Flask, jsonify
import os
import socket
import time
from datetime import datetime
import threading
import json 

app = Flask(__name__)

# Path to shared vision status file produced by external computer vision pipeline.
# This service reads (does not own) this file for vision health metrics
VISION_STATUS_FILE = "/home/russellsoto/BarBot-Vision/vision_status.json"
VISION_STALE_SECONDS = 10



#==================================================================

metrics_state = {
    "cpu_usage_percent":0.0,
    "network_io_kBps":[0.0,0.0], #[in,out]
}

# Protects shared metrics_state between Flask and sampler
metrics_lock = threading.Lock()
#==================================================================

def get_vision_status():
    """
    Retrieve latest vision pipeline status from shared JSON file
    Args:
        None
    Returns:
        dict: vision status including process state, camera status, FPS, and runtime
        or default values if file is missing, stale, or unreadable
    """
    default = {
        "vision_process_running": False,        
        "camera_status": "offline",
        "fps":0.0,
        "vision_runtime_seconds": 0.0
        
    }

    if not os.path.exists(VISION_STATUS_FILE):
        return default
    try:
        with open(VISION_STATUS_FILE,"r") as f:
            data = json.load(f)
        
        last_update = data.get("last_update", 0)
        age = time.time() - last_update
        
        # Treat vision data as invalid if not updated recently to prevent
        # reporting stale state if pipeline has stopped working
        if age > VISION_STALE_SECONDS:
            return default
        
        return {
        "vision_process_running": data.get("vision_process_running", False),
        "camera_status": data.get("camera_status", "offline"),
        "fps":data.get("fps",0.0),
        "vision_runtime_seconds": data.get("vision_runtime_seconds", 0.0)
        }
    
    except Exception as e:
        print(f"[VISION STATUS ERROR] {e}")
        return default
    
#==================================================================

def get_uptime_seconds():
  """
  Retrieve system uptime from Linux kernel uptime file
  Args:
      None
  Returns:
      uptime_seconds: System uptime in seconds since last boot
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
      load1: 1-minute CPU load average rounded to two decimal places
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
      temp_milli_c: CPU temperature in Celsius rounded to one decimal place
  """

  with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp_milli_c = int(f.read().strip())

  #Sensor reports temperature in millidegrees Celsius
  return round(temp_milli_c / 1000.0, 1)

#==================================================================    

def get_memory_used_percent():
    """
    Retrieve memory stats from Linux kernel file to calculate percent of system memory in use
    Args:
        None
    Returns:
        percent: Percentage of memory used in decimal form 
        
    """
    meminfo = {}
    
    with open("/proc/meminfo","r") as f:
        for line in f:
            parts = line.split()
            key = parts[0].rstrip(":")
            value = int(parts[1])
            meminfo[key] = value
            
    
    mem_total = meminfo.get("MemTotal")
    mem_available = meminfo.get("MemAvailable")
    
    if mem_total is None or mem_available is None:
        return None
    
    used = (mem_total - mem_available)
    percent = (used / mem_total)*100
    return round(percent,1)

#==================================================================

def get_disk_used_percent():
    """
    Retrieve disk space info using from virtual file system to calculate percent of disk space in use
    OS uses Disc memory block allocation to manage disc space
    Args:
        None
    Returns:
        percent: Percentage of disk usage in decimal form
    """
    try:
        disk = os.statvfs('/')
        
        # (f_block = number of blocks) x (f_frsize = bytes per block  )
        # Gives total disk size in blocks
        total_space = disk.f_blocks * disk.f_frsize
        # (f_bavail = usable free blocks)
        free_space = disk.f_bavail * disk.f_frsize
        
        if total_space == 0: 
            return None
        
        percent = (free_space / total_space) * 100
        
        return(round(percent,1))

    except OSError as e:
        return None 

#==================================================================

def read_cpu_time():
    """
    Retrieve CPU time stats from Linux kernel file to calculate total time & idle time
    Args:
        None
    Returns:
        total_time: sum of cpu line which aggregates total spent in various states across all cores
        idle_time: total time CPU has no active task, doing nothing (idle) and waiting for disk I/O (iowait)
    """
    with open("/proc/stat","r") as f:
        first_line = f.readline().split()
        
    #cpu = 0 : from here on numerical values -> user =1 nice =2 system=3 idle =4 iowait =5
    if first_line[0]!= "cpu":
        return None, None
    
    values = [int(value) for value in first_line[1:]]
    
    #idle time = idle + iowait
    idle_time = values[3] + values[4]
    total_time = sum(values)
        
    return idle_time,total_time

#==================================================================

def read_network_io():
    """
    Retrieve network I/O readings from Linux kernel file to save receiving bytes and transmitting bytes snapshot
    Args:
        None
    Returns:
        network_io: [bytes_in,bytes_out] receiving bytes and transmitting bytes
    """
    network_byte = {}
    with open("/proc/net/dev","r") as f:
        
        #Skip header, index range would not match with rest of file when converted to list
        #Skip second header to avoid strings allowing values to convert to int 
        next(f)
        next(f)
        
        for line in f:
            
            parts = line.split()
            key = parts[0].rstrip(":")
            bytes_in = int(parts[1])               
            bytes_out = int(parts[9])
           
            
            network_byte[key] = [bytes_in,bytes_out]
            
        network_io = network_byte.get("wlan0")
        if network_io is None:
            return None, None
        
        return (network_io)

#====================BACKGROUND THREAD#===========================#
#==================================================================

def metrics_sampler():
    """
    Background loop that samples CPU usage and network I/O rates
    
    Computes deltas between sucessive reads to derive:
    - CPU usage percent
    - Network throughput (kB/s)
    Args:
        None
    Returns:
        None
    """
    previous_idle_time = None
    previous_total_time = None
    previous_rx_bytes = None
    previous_tx_bytes = None
    previous_time = None
    
    while True :
        current_time = time.time()
        
        current_idle_time, current_total_time = read_cpu_time()
        current_rx_bytes, current_tx_bytes = read_network_io()
        
        cpu_usage_percent = 0.0
        rx_kBps = 0.0
        tx_kBps = 0.0
        
        # CPU usage percent math
        if (
            previous_idle_time is not None
            and previous_total_time is not None
            and current_idle_time is not None
            and current_total_time is not None
        ):

            idle_delta = current_idle_time - previous_idle_time
            total_delta = current_total_time - previous_total_time
        
            if total_delta >0:
                cpu_usage_percent = ((total_delta - idle_delta) / total_delta ) * 100

        # Network IO math
        if (
            previous_rx_bytes is not None
            and previous_tx_bytes is not None
            and current_rx_bytes is not None
            and current_tx_bytes is not None
            and previous_time is not None
        ):
            delta_time = current_time - previous_time
            
            if delta_time > 0:
                rx_kBps = (current_rx_bytes - previous_rx_bytes) / delta_time / 1024
                tx_kBps = (current_tx_bytes - previous_tx_bytes) / delta_time / 1024
            
        # SAFELY save values 
        #-----------------------         
        with metrics_lock:
            metrics_state["cpu_usage_percent"] = round(cpu_usage_percent,2)
            metrics_state["network_io_kBps"] = [round(rx_kBps,2), round(tx_kBps,2)]
            
        #update
        previous_idle_time = current_idle_time
        previous_total_time = current_total_time
        previous_rx_bytes = current_rx_bytes
        previous_tx_bytes = current_tx_bytes
        previous_time = current_time
        
        time.sleep(1)
#==================================================================
        
@app.route("/status")
def status():
  """
  Telemetry endpoint that returns current system status and vision status
  Args:
    None
  Returns:
    JSON: system telemetry payload including CPU, memory, disk, network, and vision metrics
  """
  #SAFELY read from shared state
  with metrics_lock:
      cpu_usage_percent = metrics_state["cpu_usage_percent"]
      network_io_kBps = metrics_state["network_io_kBps"]
  
  vision = get_vision_status()
  
  return jsonify({
        "node": socket.gethostname(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "uptime_seconds": get_uptime_seconds(),
        "cpu_load_1min": get_cpu_load(),
        "cpu_temp_c": get_cpu_temp_c(),
        
        "memory_used_percent": get_memory_used_percent(),
        "cpu_usage_percent": cpu_usage_percent,
        "disk_used_percent": get_disk_used_percent(),
        "network_io_kBps": network_io_kBps,
        
        "vision_process_running": vision["vision_process_running"],
        "vision_runtime_seconds": vision["vision_runtime_seconds"],
        "camera_status": vision["camera_status"],
        "fps":vision["fps"] 
        
    })


#==================================================================

@app.route("/")
def home():
  """
  Basic landing page for the monitored node service
  Args:
    None
  Returns:
    HTML: message with refrence to /status endpoint
  """
  
  return "<h1>Pi 3 Status Server</h1><p>Use /status for JSON telemetry.</p>"

#==================================================================

if __name__ == "__main__":
    
    # Run background sampler so CPU/Network metrics are continously updated
    # without blocking Flask reqeust handling
    threading.Thread(target=metrics_sampler,daemon=True).start()
    app.run(host="0.0.0.0", port=5001)