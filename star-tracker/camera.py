from flask import Flask, Response
from picamera2 import Picamera2
import cv2

app = Flask(__name__)

picam2 = Picamera2()
config = picam2.create_preview_configuration(main={"size": (1024, 1024), "format": "RGB888"})
picam2.configure(config)
picam2.start()

@app.route('/frame')
def get_frame():
    frame_array = picam2.capture_array()
    success, encoded_image = cv2.imencode('.jpg', frame_array)
    if not success:
        return "Capture failed", 500
    return Response(encoded_image.tobytes(), mimetype='image/jpeg')

if __name__ == '__main__':
    # Run server on the Pi's local network IP
    app.run(host='0.0.0.0', port=5000)