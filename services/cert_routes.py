"""
Certificate Installation Routes
================================
Flask Blueprint that serves the local CA certificate and provides
a guided installation page for all major platforms.

The NVR uses a local CA to sign its TLS certificate. Users install
the CA cert once on their device, and the browser trusts the NVR
HTTPS connection permanently — no more "Your connection is not private".

Endpoints:
    GET /install-cert           → Guided installation page (HTML)
    GET /install-cert/download  → Raw CA cert download (.crt)
    GET /install-cert/mobileconfig → iOS configuration profile (.mobileconfig)
    GET /api/cert/status        → JSON: cert info + whether CA exists

Integration:
    In app.py, add two lines:
        from services.cert_routes import cert_bp
        app.register_blueprint(cert_bp)
"""

import os
import uuid
import hashlib
import subprocess
import logging
from datetime import datetime

from flask import Blueprint, render_template, send_file, jsonify, request, Response

logger = logging.getLogger(__name__)

cert_bp = Blueprint('cert', __name__)

# ---------------------------------------------------------------------------
# Paths — relative to project root
# ---------------------------------------------------------------------------
CERTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'certs', 'dev')
CA_CERT_PATH = os.path.join(CERTS_DIR, 'ca.pem')
SERVER_CERT_PATH = os.path.join(CERTS_DIR, 'fullchain.pem')


def _ca_exists():
    """Check if the CA certificate file exists and is readable."""
    return os.path.isfile(CA_CERT_PATH) and os.access(CA_CERT_PATH, os.R_OK)


def _get_ca_fingerprint():
    """Return SHA-256 fingerprint of the CA cert for verification display."""
    if not _ca_exists():
        return None
    try:
        result = subprocess.run(
            ['openssl', 'x509', '-fingerprint', '-sha256', '-noout', '-in', CA_CERT_PATH],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Output: "sha256 Fingerprint=AA:BB:CC:..."
            return result.stdout.strip().split('=', 1)[-1]
    except Exception as e:
        logger.warning(f"Could not read CA fingerprint: {e}")
    return None


def _get_ca_expiry():
    """Return CA certificate expiry date as a human-readable string."""
    if not _ca_exists():
        return None
    try:
        result = subprocess.run(
            ['openssl', 'x509', '-enddate', '-noout', '-in', CA_CERT_PATH],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Output: "notAfter=Feb 19 15:00:00 2036 GMT"
            return result.stdout.strip().split('=', 1)[-1]
    except Exception as e:
        logger.warning(f"Could not read CA expiry: {e}")
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@cert_bp.route('/install-cert')
def cert_install_page():
    """
    Render the guided certificate installation page.
    Platform detection happens client-side (JS). This page works
    over both HTTP and HTTPS.
    """
    fingerprint = _get_ca_fingerprint()
    expiry = _get_ca_expiry()
    ca_available = _ca_exists()

    return render_template(
        'cert_install.html',
        ca_available=ca_available,
        fingerprint=fingerprint,
        expiry=expiry
    )


@cert_bp.route('/install-cert/download')
def cert_download():
    """
    Serve the CA certificate as a .crt file.
    Content-Type: application/x-x509-ca-cert triggers OS cert install
    dialogs on most platforms.
    """
    if not _ca_exists():
        return jsonify({'error': 'CA certificate not found. Run make_ca_signed_tls.sh first.'}), 404

    return send_file(
        CA_CERT_PATH,
        mimetype='application/x-x509-ca-cert',
        as_attachment=True,
        download_name='NVR_Local_CA.crt'
    )


@cert_bp.route('/install-cert/mobileconfig')
def cert_mobileconfig():
    """
    Serve an iOS/macOS configuration profile (.mobileconfig) that
    installs the CA certificate. This provides a more guided experience
    on Apple devices than a raw .crt download.

    The .mobileconfig is an XML plist that embeds the CA cert as
    base64 DER. When opened on iOS/macOS, it triggers the system
    profile installer.
    """
    if not _ca_exists():
        return jsonify({'error': 'CA certificate not found.'}), 404

    try:
        # Convert PEM to DER (base64-encoded binary cert)
        result = subprocess.run(
            ['openssl', 'x509', '-in', CA_CERT_PATH, '-outform', 'DER'],
            capture_output=True, timeout=5
        )
        if result.returncode != 0:
            return jsonify({'error': 'Failed to convert certificate.'}), 500

        import base64
        cert_der_b64 = base64.b64encode(result.stdout).decode('ascii')

    except Exception as e:
        logger.error(f"Failed to generate mobileconfig: {e}")
        return jsonify({'error': 'Certificate conversion failed.'}), 500

    # Generate a stable UUID from the cert content (same cert = same UUID)
    cert_hash = hashlib.sha256(result.stdout).hexdigest()
    profile_uuid = str(uuid.UUID(cert_hash[:32]))
    payload_uuid = str(uuid.UUID(cert_hash[32:64] if len(cert_hash) >= 64 else cert_hash[:32]))

    # Build the .mobileconfig XML plist
    mobileconfig = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>PayloadCertificateFileName</key>
            <string>NVR_Local_CA.cer</string>
            <key>PayloadContent</key>
            <data>{cert_der_b64}</data>
            <key>PayloadDescription</key>
            <string>Installs the NVR Local CA certificate so your device trusts the NVR HTTPS connection.</string>
            <key>PayloadDisplayName</key>
            <string>NVR Local CA</string>
            <key>PayloadIdentifier</key>
            <string>com.home-nvr.cert.{payload_uuid}</string>
            <key>PayloadType</key>
            <string>com.apple.security.root</string>
            <key>PayloadUUID</key>
            <string>{payload_uuid}</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
        </dict>
    </array>
    <key>PayloadDescription</key>
    <string>Trust the Home NVR HTTPS certificate. After installing, go to Settings → General → About → Certificate Trust Settings and enable full trust for "NVR Local CA".</string>
    <key>PayloadDisplayName</key>
    <string>Home NVR — Trust Certificate</string>
    <key>PayloadIdentifier</key>
    <string>com.home-nvr.profile.{profile_uuid}</string>
    <key>PayloadOrganization</key>
    <string>Home NVR</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>"""

    return Response(
        mobileconfig,
        mimetype='application/x-apple-aspen-config',
        headers={
            'Content-Disposition': 'attachment; filename="NVR_Trust_Certificate.mobileconfig"'
        }
    )


@cert_bp.route('/api/cert/status')
def cert_status():
    """
    JSON endpoint returning certificate status info.
    Used by the frontend banner to decide whether to show the
    "install certificate" prompt.
    """
    return jsonify({
        'ca_available': _ca_exists(),
        'fingerprint': _get_ca_fingerprint(),
        'expiry': _get_ca_expiry(),
        'download_url': '/install-cert/download',
        'mobileconfig_url': '/install-cert/mobileconfig',
        'install_page_url': '/install-cert'
    })
