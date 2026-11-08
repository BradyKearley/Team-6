import tetra3
from PIL import Image
import time
import os
from datetime import datetime, timezone
import math
import requests
import io
import numpy as np

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
    longitude = gmst - ra
    while longitude > 180:
        longitude -= 360
    while longitude < -180:
        longitude += 360
    return latitude, longitude


def render_hud(result, lat, lon):
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=========================================")
    print("           AIRCRAFT STAR NAV             ")
    print("=========================================")
    if result and result.get('RA') is not None:
        print(f" [STATUS] LOCK ACQUIRED ({result['Matches']} star matches)")
        print(f" -------------------------------------")
        print(f"  Right Acension (RA) : {float(result['RA']):.2f}°")
        print(f"  Declination (Dec)    : {float(result['Dec']):.2f}°")
        print(f"  Aircraft Bank (Roll) : {float(result['Roll']):.2f}°")
        print(f"  Field of View (FOV)  : {float(result['FOV']):.2f}°")
        print(f"  Solution Time        : {float(result['T_solve']):.2f}s")
        print("=========================================")
        print(f"  Latitude : {float(lat):.6f}")
        print(f"  Longitude : {float(lon):.6f}")
    else:
        print(" [STATUS] SEARCHING SKY... NO LOCK")
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
                
                # 1. Rotate 180° to correct for physical lens inversion
                image = image.rotate(180)
                
                # 2. Crop out the dark physical tube borders
                width, height = image.size
                crop_box = (width * 0.25, height * 0.25, width * 0.75, height * 0.75)
                cropped_image = image.crop(crop_box)
                
                # 3. Apply higher threshold to strip out monitor background glow
                threshold_val = 190
                cleaned_image = cropped_image.point(lambda p: 255 if p > threshold_val else 0)
                
                # 4. Extract centroids and check the new count
                detected_stars = tetra3.get_centroids_from_image(cleaned_image)
                detected_stars = np.array(detected_stars)
                print(f"Cleaned centroids count with threshold 190: {len(detected_stars)}")
                
                # 5. Solve using the filtered points
                top_stars = detected_stars[:25] if len(detected_stars) > 25 else detected_stars
                # 5. Test a tight bracket of FOV values around 7.4°
                # Test with relaxed distance tolerance to handle monitor pixel-grid geometry
                result = t3.solve_from_centroids(
                    top_stars, 
                    fov_estimate=7.4, 
                    size=cleaned_image.size,
                    match_radius=0.02  # Loosen geometric tolerance for screen rendering
                )
                # Convert celestial orientation to geographic position
                lat, lon = None, None
                if result.get('RA') is not None:
                    lat, lon = calculate_lat_long(float(result['RA']), float(result['Dec']), capture_time)
                render_hud(result, lat, lon)
            else:
                print(" [ERROR] Failed to fetch frame from Pi.")

        except requests.exceptions.RequestException:
            print(" [ERROR] connection to Pi lost. Retrying")

except KeyboardInterrupt:
    print("\nShutting down ground station...") 