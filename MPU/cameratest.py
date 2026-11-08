import paramiko
from datetime import datetime
from pathlib import Path

HOST = "172.20.10.5"
USER = "pi"
PASSWORD = "password"
CMD = "rpicam-still -n -t 500 -o -"

photos_dir = Path(__file__).parent / "photos"
photos_dir.mkdir(exist_ok=True)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=10)

stdin, stdout, stderr = client.exec_command(CMD)
data = stdout.read()
client.close()

filepath = photos_dir / datetime.now().strftime("capture_%Y%m%d_%H%M%S.jpg")
filepath.write_bytes(data)
print("Saved", filepath)