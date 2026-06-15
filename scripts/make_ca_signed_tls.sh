#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Generate a LOCAL CA + server certificate for the NVR.
#
# First run:  Creates a Root CA (ca.pem + ca-key.pem) and a server cert
#             signed by that CA.
# Subsequent: Reuses existing CA, regenerates only the server cert.
#
# The CA cert (ca.pem) is what users install on their devices to trust
# the NVR's HTTPS — the Flask app serves it at /install-cert.
#
# Output layout:
#   certs/dev/ca.pem          ← Root CA cert  (distribute to clients)
#   certs/dev/ca-key.pem      ← Root CA key   (KEEP SECRET)
#   certs/dev/fullchain.pem   ← Server cert   (nginx uses this)
#   certs/dev/privkey.pem     ← Server key    (nginx uses this)
#
# Customize hosts via TLS_HOSTS (comma-separated):
#   TLS_HOSTS="nvr.local,mobius.nvr,localhost,127.0.0.1,<LAN_IP>" ./0_MAINTENANCE_SCRIPTS/make_ca_signed_tls.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${ROOT_DIR}/certs/dev"
mkdir -p "${OUT_DIR}"

# -----------------------------------------------------------------------------
# Defensive guard (operator directive 2026-06-15: NEVER overwrite existing
# certs without explicit consent). If both the CA and server cert already
# exist, refuse to regenerate unless --force is passed. Existing browsers
# have trusted the current CA and re-generating would require every client
# to re-import.
# -----------------------------------------------------------------------------
_FORCE=0
for arg in "$@"; do
    [[ "$arg" == "--force" ]] && _FORCE=1
done

if [[ -f "${OUT_DIR}/ca.pem" && -f "${OUT_DIR}/ca-key.pem" \
   && -f "${OUT_DIR}/fullchain.pem" && -f "${OUT_DIR}/privkey.pem" \
   && "$_FORCE" -eq 0 ]]; then
    echo ""
    echo "Refusing to overwrite existing certs at ${OUT_DIR}."
    echo "  CA + server cert already exist. Browsers have likely imported"
    echo "  the current CA — regenerating would force every client to"
    echo "  re-import."
    echo ""
    echo "  If you really mean to regenerate (e.g., SAN list changed,"
    echo "  cert near expiry), pass --force:"
    echo "    $0 --force"
    echo ""
    echo "  Or to regenerate just the server cert while keeping the CA,"
    echo "  delete fullchain.pem + privkey.pem first and re-run without"
    echo "  --force."
    exit 0
fi

sudo chown "$USER":"$USER" "$OUT_DIR"

# ---------------------------------------------------------------------------
# Configurable SANs — include your LAN IP(s) here
# ---------------------------------------------------------------------------
# Auto-detect host IP if not overridden
_HOST_IP="${NVR_LOCAL_HOST_IP:-$(hostname -I | awk '{print $1}')}"
TLS_HOSTS_DEFAULT="nvr.local,mobius.nvr,localhost,127.0.0.1,${_HOST_IP}"
TLS_HOSTS="${TLS_HOSTS:-$TLS_HOSTS_DEFAULT}"

CA_CERT="${OUT_DIR}/ca.pem"
CA_KEY="${OUT_DIR}/ca-key.pem"
SERVER_CERT="${OUT_DIR}/fullchain.pem"
SERVER_KEY="${OUT_DIR}/privkey.pem"

# ---------------------------------------------------------------------------
# Step 1: Create Root CA (only if it doesn't already exist)
# ---------------------------------------------------------------------------
if [ ! -f "$CA_CERT" ] || [ ! -f "$CA_KEY" ]; then
    echo "=== Creating new Root CA ==="

    # Generate CA private key (ECDSA P-256)
    openssl ecparam -name prime256v1 -genkey -noout -out "$CA_KEY"
    chmod 600 "$CA_KEY"

    # Generate CA certificate (10 years)
    openssl req -x509 -new -nodes \
        -key "$CA_KEY" \
        -sha256 \
        -days 3650 \
        -out "$CA_CERT" \
        -subj "/CN=NVR Local CA/O=Home NVR/OU=Development"

    echo "  CA cert:  $CA_CERT"
    echo "  CA key:   $CA_KEY"
    echo ""
    echo "  *** Install ca.pem on your devices to trust the NVR ***"
    echo "  *** The NVR UI at /install-cert will help with this ***"
    echo ""
else
    echo "=== Reusing existing Root CA ==="
    echo "  CA cert:  $CA_CERT"
    # Show CA expiry for awareness
    EXPIRY=$(openssl x509 -enddate -noout -in "$CA_CERT" 2>/dev/null | cut -d= -f2)
    echo "  Expires:  $EXPIRY"
    echo ""
fi

# ---------------------------------------------------------------------------
# Step 2: Generate server certificate signed by our CA
# ---------------------------------------------------------------------------
echo "=== Generating server certificate ==="

# Build SAN list from TLS_HOSTS
IFS=',' read -r -a SAN_ARR <<< "$TLS_HOSTS"
SAN_LINES=()
IDX=1
for h in "${SAN_ARR[@]}"; do
    if [[ "$h" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        SAN_LINES+=("IP.$IDX = $h")
    else
        SAN_LINES+=("DNS.$IDX = $h")
    fi
    IDX=$((IDX + 1))
done

echo "  SANs: $TLS_HOSTS"

# Create temporary OpenSSL config
OPENSSL_CNF="$(mktemp)"
trap 'rm -f "$OPENSSL_CNF"' EXIT

cat > "$OPENSSL_CNF" <<EOF
[ req ]
default_bits       = 2048
default_md         = sha256
distinguished_name = dn
req_extensions     = req_ext
prompt             = no

[ dn ]
CN = ${SAN_ARR[0]}
O  = Home NVR
OU = Development

[ req_ext ]
subjectAltName = @alt_names

[ v3_server ]
authorityKeyIdentifier = keyid,issuer
basicConstraints       = CA:FALSE
keyUsage               = digitalSignature, keyEncipherment
extendedKeyUsage       = serverAuth
subjectAltName         = @alt_names

[ alt_names ]
$(printf '%s\n' "${SAN_LINES[@]}")
EOF

# Generate server private key
openssl ecparam -name prime256v1 -genkey -noout -out "$SERVER_KEY"
chmod 600 "$SERVER_KEY"

# Generate CSR
CSR="$(mktemp)"
openssl req -new -key "$SERVER_KEY" -out "$CSR" -config "$OPENSSL_CNF"

# Sign with our CA (825 days ≈ macOS trust limit for non-CA certs)
openssl x509 -req \
    -in "$CSR" \
    -CA "$CA_CERT" \
    -CAkey "$CA_KEY" \
    -CAcreateserial \
    -out "$SERVER_CERT" \
    -days 825 \
    -sha256 \
    -extfile "$OPENSSL_CNF" \
    -extensions v3_server

rm -f "$CSR"

echo ""
echo "=== Done ==="
echo "  Server cert: $SERVER_CERT  (nginx ssl_certificate)"
echo "  Server key:  $SERVER_KEY   (nginx ssl_certificate_key)"
echo "  CA cert:     $CA_CERT      (install on client devices)"
echo ""
echo "Restart the NVR container to pick up new certs."
