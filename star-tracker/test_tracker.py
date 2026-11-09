import tetra3
from PIL import Image
import time
import os
from datetime import datetime, timezone
import math
import requests
import io
import numpy as np
import csv

last_lock = None
log_file = "lock_log.csv"


#from picamera2 import Picamera2
PI_IP = "172.20.10.5"
URL = f"http://{PI_IP}:5000/frame"

def calculate_lat_long(ra, dec, timestamp):
    """
    Converts celestial coordinates and timestamp into geographic lat/long.
    """
    latitude = dec

    # Calculate Greenwich Mean Sidereal Time (GMST) based on J2000 epoch
    d = timestamp - datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)
    days = d.total_seconds() / 86400.0
    gmst = (280.46061837 + 360.98564736629 * days)  % 360.0


    # Longitude is the difference between sidereal time and right ascension
    longitude = ra - gmst
    while longitude > 180:
        longitude -= 360
    while longitude < -180:
        longitude += 360
    return latitude, longitude

if not os.path.exists(log_file):
    with open(log_file, "w", newline="") as f:
        csv.writer(f).writerow(["utc_time", "ra", "dec", "roll", "fov", "matches", "lat", "lon"])


def render_hud(result, lat, lon, last_lock):
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=========================================")
    print("           AIRCRAFT STAR NAV             ")
    print("=========================================")
    if result and result.get('RA') is not None:
        print(f" [STATUS] LOCK ACQUIRED ({result['Matches']} star matches)")
    else:
        print(" [STATUS] SEARCHING SKY... NO LOCK")
    print("=========================================")
    if last_lock:
        age = (datetime.now(timezone.utc) - last_lock['time']).total_seconds()
        print(f" LAST LOCK ({age:.1f}s ago)")
        print(f"  RA   : {last_lock['ra']:.2f}°")
        print(f"  Dec  : {last_lock['dec']:.2f}°")
        print(f"  Roll : {last_lock['roll']:.2f}°")
        print(f"  FOV  : {last_lock['fov']:.2f}°")
        print(f"  Matches : {last_lock['matches']}")
        print(f"  Latitude  : {last_lock['lat']:.6f}")
        print(f"  Longitude : {last_lock['lon']:.6f}")
    else:
        print(" No lock yet")
    print("=========================================")


# Initialize Tetra3 star tracker solver
t3 = tetra3.Tetra3()
print("Tetra3 initialized. Connecting to Pi camera...")


try:
    while True:
        capture_time = datetime.now(timezone.utc)

        try:
            response = requests.get(URL, timeout=5)
            if response.status_code ==200:
                image_stream = io.BytesIO(response.content)
                image = Image.open(image_stream).convert('L')

                w, h = image.size
                roi = (int(w*0.22), int(h*0.43), int(w*0.92), int(h*0.96))  # sky only, below toolbar
                crop = image.crop(roi)
                arr = np.array(crop)
                crop.save("debug_crop.png")

                cents = tetra3.get_centroids_from_image(arr, sigma=3, min_area=3,
                                                        max_area=200, max_returned=25)
                print("centroids:", len(cents))
                if len(cents) >= 4:
                    result = t3.solve_from_centroids(cents, size=arr.shape[:2])  # (height, width)
                else:
                    result = {'RA': None}
                
                # Convert celestial orientation to geographic position
                lat, lon = None, None
                if result.get('RA') is not None:
                    lat, lon = calculate_lat_long(float(result['RA']), float(result['Dec']), capture_time)
                    last_lock = {
                        'time': capture_time, 'ra': float(result['RA']), 'dec': float(result['Dec']),
                        'roll': float(result['Roll']), 'fov': float(result['FOV']),
                        'matches': result['Matches'], 'lat': lat, 'lon': lon,
                    }
                    with open(log_file, "a", newline="") as f:
                        csv.writer(f).writerow([capture_time.isoformat(), last_lock['ra'], last_lock['dec'],
                                                last_lock['roll'], last_lock['fov'], last_lock['matches'], lat, lon])

                render_hud(result, lat, lon, last_lock)
            else:
                print(" [ERROR] Failed to fetch frame from Pi.")

        except requests.exceptions.RequestException:
            print(" [ERROR] connection to Pi lost. Retrying")

except KeyboardInterrupt:
    print("\nShutting down ground station...") 
