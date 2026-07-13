#!/usr/bin/env bash
set -euo pipefail

KIND_VERSION="${KIND_VERSION:-v0.29.0}"
KUBECTL_VERSION="${KUBECTL_VERSION:-v1.32.2}"
HELM_VERSION="${HELM_VERSION:-v3.21.3}"

DOWNLOAD_DIR="$(mktemp -d)"
cleanup() {
  rm -rf -- "$DOWNLOAD_DIR"
}
trap cleanup EXIT

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

verify_sha256() {
  local artifact="$1"
  local checksum_file="$2"
  local expected
  local actual

  expected="$(awk 'NR == 1 { print $1 }' "$checksum_file" | tr '[:upper:]' '[:lower:]')"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || fail "Invalid SHA256 checksum file: $checksum_file"
  actual="$(sha256sum "$artifact" | awk '{ print $1 }')"
  [[ "$actual" == "$expected" ]] || fail "SHA256 verification failed for $artifact"
  ok "SHA256 verified: $(basename "$artifact")"
}

kind_version() {
  local binary="${1:-kind}"
  "$binary" version 2>/dev/null | awk 'NR == 1 { print $2 }'
}

kubectl_version() {
  local binary="${1:-kubectl}"
  "$binary" version --client=true --output=yaml 2>/dev/null |
    awk '$1 == "gitVersion:" { print $2; exit }'
}

helm_version() {
  local binary="${1:-helm}"
  "$binary" version --short 2>/dev/null | awk -F'+' 'NR == 1 { print $1 }'
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
    openssl \
    tar \
    coreutils
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

  # shellcheck source=/dev/null
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
  local installed_version=""

  [[ "$KIND_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "KIND_VERSION must be pinned as vMAJOR.MINOR.PATCH."

  if command -v kind >/dev/null 2>&1; then
    installed_version="$(kind_version "$(command -v kind)" || true)"
    if [[ "$installed_version" == "$KIND_VERSION" ]]; then
      ok "kind $KIND_VERSION is already installed: $(command -v kind)"
      return 0
    fi
    log "Replacing kind ${installed_version:-unknown} with pinned $KIND_VERSION"
  fi

  log "Installing kind $KIND_VERSION"
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) fail "Unsupported architecture for kind: $arch" ;;
  esac

  local artifact="$DOWNLOAD_DIR/kind-linux-$arch"
  local checksum="$artifact.sha256"
  local url="https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$arch"
  curl -fsSL -o "$artifact" "$url"
  curl -fsSL -o "$checksum" "$url.sha256"
  verify_sha256 "$artifact" "$checksum"
  $sudo_cmd install -o root -g root -m 0755 "$artifact" /usr/local/bin/kind
  hash -r
  [[ "$(kind_version "$(command -v kind)" || true)" == "$KIND_VERSION" ]] ||
    fail "kind $KIND_VERSION was installed, but PATH does not resolve to that version. Put /usr/local/bin before the existing kind path."
}

install_kubectl() {
  local sudo_cmd="$1"
  local installed_version=""

  [[ "$KUBECTL_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "KUBECTL_VERSION must be pinned as vMAJOR.MINOR.PATCH."

  if command -v kubectl >/dev/null 2>&1; then
    installed_version="$(kubectl_version "$(command -v kubectl)" || true)"
    if [[ "$installed_version" == "$KUBECTL_VERSION" ]]; then
      ok "kubectl $KUBECTL_VERSION is already installed: $(command -v kubectl)"
      return 0
    fi
    log "Replacing kubectl ${installed_version:-unknown} with pinned $KUBECTL_VERSION"
  fi

  log "Installing kubectl $KUBECTL_VERSION"

  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) fail "Unsupported architecture for kubectl: $arch" ;;
  esac

  local artifact="$DOWNLOAD_DIR/kubectl-linux-$arch"
  local checksum="$artifact.sha256"
  local url="https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl"
  curl -fsSL -o "$artifact" "$url"
  curl -fsSL -o "$checksum" "$url.sha256"
  verify_sha256 "$artifact" "$checksum"
  $sudo_cmd install -o root -g root -m 0755 "$artifact" /usr/local/bin/kubectl
  hash -r
  [[ "$(kubectl_version "$(command -v kubectl)" || true)" == "$KUBECTL_VERSION" ]] ||
    fail "kubectl $KUBECTL_VERSION was installed, but PATH does not resolve to that version. Put /usr/local/bin before the existing kubectl path."
}

install_helm() {
  local sudo_cmd="$1"
  local installed_version=""

  [[ "$HELM_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "HELM_VERSION must be pinned as vMAJOR.MINOR.PATCH."

  if command -v helm >/dev/null 2>&1; then
    installed_version="$(helm_version "$(command -v helm)" || true)"
    if [[ "$installed_version" == "$HELM_VERSION" ]]; then
      ok "Helm $HELM_VERSION is already installed: $(command -v helm)"
      return 0
    fi
    log "Replacing Helm ${installed_version:-unknown} with pinned $HELM_VERSION"
  fi

  log "Installing Helm $HELM_VERSION"
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) fail "Unsupported architecture for Helm: $arch" ;;
  esac

  local archive="$DOWNLOAD_DIR/helm-$HELM_VERSION-linux-$arch.tar.gz"
  local checksum="$archive.sha256sum"
  local url="https://get.helm.sh/helm-$HELM_VERSION-linux-$arch.tar.gz"
  curl -fsSL -o "$archive" "$url"
  curl -fsSL -o "$checksum" "$url.sha256sum"
  verify_sha256 "$archive" "$checksum"
  tar -xzf "$archive" -C "$DOWNLOAD_DIR"
  $sudo_cmd install -o root -g root -m 0755 "$DOWNLOAD_DIR/linux-$arch/helm" /usr/local/bin/helm
  hash -r
  [[ "$(helm_version "$(command -v helm)" || true)" == "$HELM_VERSION" ]] ||
    fail "Helm $HELM_VERSION was installed, but PATH does not resolve to that version. Put /usr/local/bin before the existing Helm path."
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
install_helm "$SUDO_CMD"
configure_docker_group "$SUDO_CMD"
verify_tools

printf '\nLinux prerequisites installed.\n'
printf 'Next step:\n'
printf '  bash scripts/quick_start_all.sh\n'
