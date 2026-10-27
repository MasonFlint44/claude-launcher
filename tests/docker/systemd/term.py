"""A terminal window: runs the fake Claude on a pseudo-terminal it controls,
and on SIGTERM closes the terminal, so Claude gets SIGHUP as it does when a
desktop closes its windows. usage: term.py SESSION_ID"""

import os
import pty
import signal
import sys
import time

pid, master = pty.fork()
if pid == 0:
    os.execv("/usr/local/bin/claude", ["/usr/local/bin/claude", "/check/fake_claude.py", sys.argv[1]])


def close(*_):
    os.close(master)  # the window goes: the kernel hangs up Claude's terminal
    for _ in range(100):
        if os.waitpid(pid, os.WNOHANG)[0]:
            break
        time.sleep(0.05)
    os._exit(0)


signal.signal(signal.SIGTERM, close)
while True:
    try:
        os.read(master, 4096)  # keep the terminal drained
    except OSError:
        time.sleep(1)
