print('hi')
import paramiko

HOST = "172.20.10.5"
USER = "pi"
PASSWORD = "password"

PI_CODE = r'''
import smbus2, struct, time
ADDR = 0x68
bus = smbus2.SMBus(1)
bus.write_byte_data(ADDR, 0x6B, 0)
while True:
    raw = bus.read_i2c_block_data(ADDR, 0x3B, 6)
    ax, ay, az = [v / 16384.0 for v in struct.unpack(">hhh", bytes(raw))]
    print(f"{ax:+.3f},{ay:+.3f},{az:+.3f}", flush=True)
    time.sleep(0.05)
'''

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD)

stdin, stdout, stderr = client.exec_command("python3 -u -")
stdin.write(PI_CODE)
stdin.channel.shutdown_write()

try:
    for line in stdout:
        ax, ay, az = map(float, line.strip().split(","))
        print(f"\rax={ax:+.3f}  ay={ay:+.3f}  az={az:+.3f} g", end="", flush=True)
except KeyboardInterrupt:
    pass

status = stdout.channel.recv_exit_status()
err = stderr.read().decode()
print(f"\nexit status: {status}")
if err:
    print("Pi error:\n", err)
client.close()