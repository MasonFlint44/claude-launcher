"""A login session's leader, started by check.py with systemd-run and the
claude-test PAM stack, so logind registers the session. It waits for
/tmp/logout-NAME, then logs out the way its kind does. usage: session.py NAME KIND SESSION_ID

  desktop:   start fake-desktop.target (which pulls in graphical-session.target)
             and a terminal with Claude in the user manager, as a desktop's apps
             run. Logout: stop the desktop (graphical-session.target stops),
             close the terminal, exit.
  bare-gui:  the same, without graphical-session.target: a desktop session
             only logind's session type can tell apart.
  tty:       Claude runs in the session itself, like over SSH. Logout: the
             connection drops: Claude's terminal hangs up, the leader exits.
"""

import os
import pty
import subprocess
import sys
import time

name, kind, session_id = sys.argv[1:4]
user_env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}


def user_ctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], env=user_env, check=True)


trigger = f"/tmp/logout-{name}"
if kind in ("desktop", "bare-gui"):
    if kind == "desktop":
        user_ctl("start", "fake-desktop.target")
    subprocess.run(
        [
            "systemd-run",
            "--user",
            "-q",
            f"--unit=term-{name}",
            "-p",
            "KillMode=mixed",
            "-E",
            f"HOME={os.environ['HOME']}",
            "/usr/bin/python3",
            "/check/term.py",
            session_id,
        ],
        env=user_env,
        check=True,
    )
    while not os.path.exists(trigger):
        time.sleep(0.1)
    if kind == "desktop":
        user_ctl("stop", "fake-desktop.target")
    user_ctl("stop", f"term-{name}.service")
    sys.exit(0)

pid, master = pty.fork()
if pid == 0:
    os.execv("/usr/local/bin/claude", ["/usr/local/bin/claude", "/check/fake_claude.py", session_id])
while not os.path.exists(trigger):
    try:
        os.set_blocking(master, False)
        os.read(master, 4096)
    except OSError:
        pass
    time.sleep(0.1)
os.close(master)
for _ in range(100):
    if os.waitpid(pid, os.WNOHANG)[0]:
        break
    time.sleep(0.05)
