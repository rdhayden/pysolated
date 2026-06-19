FROM python:3.13-bookworm

# Install system dependencies.
# gawk is required because the codex installer's checksum lookup uses an
# interval regex (/^[0-9a-fA-F]{64}$/) that Debian's default mawk does not
# honor, which makes it fail to find the package digest in SHA256SUMS.
RUN apt-get update && apt-get install -y \
  git \
  curl \
  jq \
  gawk \
  && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update && apt-get install -y gh \
  && rm -rf /var/lib/apt/lists/*

# Build-args for UID/GID alignment: sandcastle docker build-image
# defaults these to the host user's UID/GID so image-built files
# and bind-mounted files share an owner without runtime chown.
ARG AGENT_UID=1000
ARG AGENT_GID=1000

# Add "agent" group
RUN addgroup --gid ${AGENT_GID} agent

# Add "agent" user and align UID/GID.
RUN adduser --uid ${AGENT_UID} --gid ${AGENT_GID} --home /home/agent agent

# Add agent to PATH
ENV PATH="/home/agent/.local/bin:$PATH"

# Install Claude Code CLI as the agent user so the binary lands in
# /home/agent/.local/bin (the installer targets $HOME/.local/bin).
USER agent
RUN curl -fsSL https://claude.ai/install.sh | bash
RUN curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh

WORKDIR /home/agent

# In worktree sandbox mode, Sandcastle bind-mounts the git worktree at /home/agent/workspace
# and overrides the working directory to /home/agent/workspace at container start.
# Structure your Dockerfile so that /home/agent/workspace can serve as the project root.
ENTRYPOINT ["sleep", "infinity"]
