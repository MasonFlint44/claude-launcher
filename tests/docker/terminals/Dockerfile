# Every terminal claude-launcher sets up, on one Arch image (Arch packages all
# of them), with a virtual X display to run them on. See ../README.md.
FROM archlinux:latest
RUN set -eux; \
    pacman -Syu --noconfirm --needed \
      bash python fzf procps-ng desktop-file-utils gtk-update-icon-cache \
      xorg-server-xvfb xorg-xwininfo xorg-xprop openbox xdotool imagemagick \
      dbus dconf glib2 mesa ttf-dejavu \
      ghostty kitty wezterm ptyxis gnome-terminal konsole guake xterm; \
    pacman -Scc --noconfirm; \
    rm -rf /usr/share/doc /usr/share/man; \
    useradd -m -u 1000 -s /bin/bash tester; \
    mkdir -p /run/user/1000 && chown tester /run/user/1000 && chmod 700 /run/user/1000
# A fake claude: the launcher only has to find one.
RUN printf '#!/bin/sh\necho "fake claude $*"\nsleep 1\n' > /usr/local/bin/claude && chmod 755 /usr/local/bin/claude
# Ptyxis starts each tab through `systemd-run --user --scope`, and there's no
# systemd user manager here.
COPY systemd-run /usr/local/bin/systemd-run
COPY entrypoint.sh /usr/local/bin/terminals-entrypoint
COPY check.py /usr/local/lib/terminals-check.py
ENV LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/1000 LIBGL_ALWAYS_SOFTWARE=1 GDK_BACKEND=x11 QT_QPA_PLATFORM=xcb
USER tester
WORKDIR /home/tester
ENTRYPOINT ["/usr/local/bin/terminals-entrypoint"]
