# Docker checks

The test suite runs on this machine's Ubuntu, Python, fzf and terminal. These run it, and the parts it can only fake, on other distros, every terminal, and every systemd version the distros ship. Each needs Docker, and the exit code is the result.

```sh
./run.sh distro [IMAGE...]      # install.sh and the test suite, on each distro
./run.sh terminals [NAME...]    # every terminal, live on a virtual display
./run.sh systemd [IMAGE...]     # logout, restart and power-off detection under real systemd
```

Logs go to `.work/` (gitignored). CI (`.github/workflows/test.yml`) runs all three.

**Distros.** One image per distro: Ubuntu 22.04, 24.04 and 26.04, Debian 12 and 13, Fedora 42 and 43, and Arch. They span Python 3.10 to 3.14, bash 5.1 to 5.3, and fzf 0.29 to the newest. Ubuntu 26.04 ships 0.67.0, the oldest the launcher takes. Each runs `install.sh` as a new user with the distro's own Python, bash and fzf, and checks:

- the links, the hook in settings.json, and the desktop entry (`desktop-file-validate`);
- that fzf is usable after install: the distro's, or the launcher's own download where the distro's is too old;
- a second install;
- the whole test suite on the distro's Python, with PyGObject and a comma-decimal locale so nothing is skipped but the Ghostty theme check;
- `--uninstall`, and that everything it set up is gone.

pytest and pyte are in a venv built on the system Python; the launcher never uses it. About 35 s a distro once the images are built.

**Terminals.** This one image, on Arch at 3.6 GB, runs every terminal claude-launcher sets up, plus xterm, live on a virtual X display: Ghostty, kitty, WezTerm, Ptyxis, GNOME Terminal, Konsole and Guake. A run takes about a minute. For each terminal it:

- runs the launcher in a plain tab, and checks the background turns Claude Dark and the colors come back exactly on exit;
- installs with `--terminal NAME`, and checks the profile is there and the desktop entry validates;
- opens the entry, and checks the picker shows in Claude Dark;
- opens a new tab with Ctrl+Shift+T (Guake: a second launch);
- checks `--uninstall` takes the profile out.

Colors are read off screenshots and compared with `themes/claude-dark`. A failed check saves a screenshot to `.work/terminals-logs/`. It can't check anything that needs a real desktop: Wayland-only behavior, the dock icon, Guake's hotkey, or `--terminal default`. Ptyxis needs `systemd-run` for its tabs, and there's no systemd here, so `terminals/systemd-run` stands in.

**systemd.** This one boots real systemd, with logind and a user manager, in one container per systemd version the distros ship (249 to 262). It logs out three kinds of session, each with a stand-in Claude running the real hook:

- a desktop session;
- a desktop that only logind's session type marks as graphical;
- an SSH-style session.

Then it reboots the container, leaves another Claude open, and powers the container off and starts it again. After each logout, the reboot and the power-off, it checks the ↻ group (a power-off counts as a restart, from logind's "System is powering down.", the same message ID as "System is rebooting."), with and without the systemd-journal group. Each container start is a new boot: `systemd/boot.sh` mounts a fresh boot ID over the kernel's, as systemd-nspawn does.

It can't test what a real desktop closes first, or a power cut. On systemd 257 and later, logind in a container doesn't name sessions after their audit IDs, so the SSH check is skipped there.

It needs `--cgroupns=private --cap-add SYS_ADMIN --security-opt apparmor=unconfined --tmpfs /run --tmpfs /run/lock`, not `--privileged`; override with `RUN_OPTS=`. `KEEP=1` leaves the containers to look at. A run takes about 4 minutes, with the distros in parallel.

Images stay after a run. Remove them with `docker images 'claude-launcher-*' -q | xargs docker rmi`.
