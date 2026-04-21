#!/usr/bin/env bash
set -euo pipefail

KIND_VERSION="${KIND_VERSION:-v0.29.0}"
KUBECTL_VERSION="${KUBECTL_VERSION:-stable}"

log() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf '[ok] %s\n' "$1"
}

fail() {
  printf '\n%s\n' "$1" >&2
  exit 1
}

need_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    printf ''
  elif command -v sudo >/dev/null 2>&1; then
    printf 'sudo'
  else
    fail "sudo is required. Install sudo or run this script as root."
  fi
}

install_apt_packages() {
  local sudo_cmd="$1"

  command -v apt-get >/dev/null 2>&1 || fail "This installer currently supports Debian/Ubuntu apt-based Linux only."

  log "Installing base packages"
  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    python3 \
    openssl
}

install_docker() {
  local sudo_cmd="$1"

  if command -v docker >/dev/null 2>&1; then
    ok "Docker is already installed: $(command -v docker)"
    return 0
  fi

  log "Installing Docker Engine"
  $sudo_cmd install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $sudo_cmd gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $sudo_cmd chmod a+r /etc/apt/keyrings/docker.gpg

  . /etc/os-release
  local distro_id="${ID:-ubuntu}"
  local codename="${VERSION_CODENAME:-}"
  if [[ -z "$codename" ]]; then
    codename="$(lsb_release -cs)"
  fi

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$distro_id $codename stable" |
    $sudo_cmd tee /etc/apt/sources.list.d/docker.list >/dev/null

  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $sudo_cmd systemctl enable --now docker >/dev/null 2>&1 || true
}

install_kind() {
  local sudo_cmd="$1"

  if command -v kind >/dev/null 2>&1; then
    ok "kind is already installed: $(command -v kind)"
    return 0
  fi

  log "Installing kind $KIND_VERSION"
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) fail "Unsupported architecture for kind: $arch" ;;
  esac

  curl -fsSL -o /tmp/kind "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch"
  chmod +x /tmp/kind
  $sudo_cmd mv /tmp/kind /usr/local/bin/kind
}

install_kubectl() {
  local sudo_cmd="$1"

  if command -v kubectl >/dev/null 2>&1; then
    ok "kubectl is already installed: $(command -v kubectl)"
    return 0
  fi

  log "Installing kubectl"
  local version="$KUBECTL_VERSION"
  if [[ "$version" == "stable" ]]; then
    version="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
  fi

  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) fail "Unsupported architecture for kubectl: $arch" ;;
  esac

  curl -fsSL -o /tmp/kubectl "https://dl.k8s.io/release/$version/bin/linux/$arch/kubectl"
  chmod +x /tmp/kubectl
  $sudo_cmd mv /tmp/kubectl /usr/local/bin/kubectl
}

install_helm() {
  if command -v helm >/dev/null 2>&1; then
    ok "helm is already installed: $(command -v helm)"
    return 0
  fi

  log "Installing Helm"
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
}

configure_docker_group() {
  local sudo_cmd="$1"

  if [[ "$(id -u)" -eq 0 ]]; then
    return 0
  fi

  if groups "$USER" | grep -qw docker; then
    ok "User $USER is already in docker group"
    return 0
  fi

  log "Adding current user to docker group"
  $sudo_cmd usermod -aG docker "$USER"
  printf '\nDocker group permission was updated.\n'
  printf 'Log out and back in, or run this before quick start:\n'
  printf '  newgrp docker\n'
}

verify_tools() {
  log "Verifying installed tools"
  docker --version
  kind --version
  kubectl version --client=true
  helm version --short
  curl --version | head -n 1
  python3 --version
  openssl version
}

SUDO_CMD="$(need_sudo)"

install_apt_packages "$SUDO_CMD"
install_docker "$SUDO_CMD"
install_kind "$SUDO_CMD"
install_kubectl "$SUDO_CMD"
install_helm
configure_docker_group "$SUDO_CMD"
verify_tools

printf '\nLinux prerequisites installed.\n'
printf 'Next step:\n'
printf '  bash scripts/quick_start_all.sh\n'
