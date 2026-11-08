"""Make the server's physics importable from here.

The sensor and filter models (WGS-84 geometry, Iridium propagation, the EKF) live in
../server/app. They are imported rather than copied so there is one source of truth -
fix the physics there and this folder inherits it.
"""
import os
import sys

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server")
if SERVER not in sys.path:
    sys.path.insert(0, os.path.abspath(SERVER))
