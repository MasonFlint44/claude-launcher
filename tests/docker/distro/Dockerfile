# One image per distro, with that distro's own Python, bash and fzf, plus
# pytest and pyte in a venv built on the system Python (tests only; the
# launcher itself never uses the venv). BASE is the knob: see DISTROS in run.sh.
ARG BASE=ubuntu:26.04
FROM ghcr.io/astral-sh/uv:0.11.26 AS uv
FROM ${BASE}
COPY --from=uv /uv /usr/local/bin/uv
COPY packages.sh /tmp/packages.sh
RUN sh /tmp/packages.sh && rm /tmp/packages.sh
# A fake claude: install.sh warns without one, and the tests bring their own.
RUN printf '#!/bin/sh\necho "fake claude $*"\n' > /usr/local/bin/claude && chmod 755 /usr/local/bin/claude \
 && { old=$(getent passwd 1000 | cut -d: -f1); [ -z "$old" ] || userdel -r "$old"; } \
 && useradd -m -u 1000 -s /bin/bash tester \
 && UV_PYTHON_DOWNLOADS=never uv venv -q --python /usr/bin/python3 /opt/venv \
 && uv pip install -q --python /opt/venv/bin/python 'pytest>=8' 'pyte>=0.8'
COPY check.sh /usr/local/bin/check-distro
ENV LANG=C.UTF-8
USER tester
WORKDIR /home/tester
ENTRYPOINT ["/usr/local/bin/check-distro"]
