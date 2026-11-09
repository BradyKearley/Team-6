import argparse, os, socket, time

COLS = "t_unix,ax,ay,az,gx,gy,gz"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi", default="B8:27:EB:5E:A0:AD")
    ap.add_argument("--channel", type=int, default=1)
    ap.add_argument("--tcp", type=int, default=0)
    ap.add_argument("--out", default=f"results/live_imu_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    ap.add_argument("--seconds", type=float, default=0)
    a = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    if a.tcp:
        s = socket.socket(); s.connect(("127.0.0.1", a.tcp))
    else:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        print(f"Connecting to {a.pi} channel {a.channel} ...")
        s.connect((a.pi, a.channel))
    print(f"Connected. Saving to {a.out} (Ctrl+C to stop)")

    start, n, buf = time.time(), 0, b""
    with open(a.out, "w") as fh:
        fh.write(COLS + "\n")
        try:
            while not a.seconds or time.time() - start < a.seconds:
                chunk = s.recv(4096)
                if not chunk:
                    print("Connection closed by the Pi."); break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    if ln.count(b",") == 6:
                        fh.write(ln.decode() + "\n"); n += 1
                fh.flush()
                if lines:
                    last = lines[-1].decode().split(",")
                    print(f"\r{n} samples  ax={float(last[1]):6.2f} ay={float(last[2]):6.2f } az={float(last[3]):6.2f}", end="")
        except KeyboardInterrupt:
            pass
    print(f"\nSaved {n} samples to {a.out}")

if __name__ == "__main__":
    main()