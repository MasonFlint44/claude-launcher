# systemd as PID 1, with logind and a user manager, on each distro. BASE is
# the knob: see DISTROS in run.sh.
ARG BASE=ubuntu:24.04
FROM ${BASE}
COPY packages.sh /tmp/packages.sh
RUN sh /tmp/packages.sh && rm /tmp/packages.sh
# Whether the distro keeps the journal across boots on its own (a directory
# /var/log/journal), noted before the one below is made.
RUN if [ -d /var/log/journal ]; then echo yes; else echo no; fi > /etc/journal-dir-from-package \
 && mkdir -p /var/log/journal \
 && { old=$(getent passwd 1000 | cut -d: -f1); [ -z "$old" ] || userdel -r "$old"; } \
 && useradd -m -u 1000 -s /bin/bash tester \
 && rm -f /etc/machine-id && systemd-machine-id-setup \
 && ln -sf "$(readlink -f /usr/bin/python3)" /usr/local/bin/claude \
 && systemctl mask systemd-firstboot.service systemd-resolved.service systemd-timesyncd.service \
      systemd-networkd.service console-getty.service getty@tty1.service systemd-homed.service 2>/dev/null || true
# A login session's PAM stack: logind's pam_systemd plus pam_loginuid, which
# gives the session its audit ID (what the hook reads from /proc/<pid>/sessionid).
COPY pam-claude-test /etc/pam.d/claude-test
COPY boot.sh /usr/local/sbin/boot
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/bin/sh", "/usr/local/sbin/boot"]
