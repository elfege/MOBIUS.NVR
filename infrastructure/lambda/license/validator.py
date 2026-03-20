"""
NVR License Validator Lambda

Validates license keys on NVR startup. Called via Lambda Function URL.
No authentication on the endpoint — the license key itself is the credential.

Request: POST {"license_key": "uuid", "hardware_fingerprint": "sha256"}
Response: {"status": "valid|expired|invalid|revoked", "expires": "ISO8601", "demo_days_remaining": N}
"""

import json
import os
import boto3
from datetime import datetime, timedelta

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

# Also log to deployments table for phone-home tracking
deployments_table_name = os.environ.get("DEPLOYMENTS_TABLE_NAME", "nvr-deployments")
try:
    deployments_table = dynamodb.Table(deployments_table_name)
except Exception:
    deployments_table = None

DEMO_DAYS = 7


def lambda_handler(event, context):
    """Validate a license key and hardware fingerprint."""
    try:
        body = json.loads(event.get("body", "{}"))
        license_key = body.get("license_key", "").strip()
        fingerprint = body.get("hardware_fingerprint", "unknown")
        version = body.get("version", "unknown")
        hostname_hash = body.get("hostname_hash", "unknown")

        # Get source IP
        source_ip = (
            event.get("requestContext", {})
            .get("http", {})
            .get("sourceIp", "unknown")
        )

        now = datetime.utcnow()
        now_iso = now.isoformat() + "Z"

        # Log to deployments table (phone-home, regardless of license status)
        _log_deployment(fingerprint, now_iso, source_ip, version, hostname_hash)

        # No license key provided — demo mode
        if not license_key:
            return _response(
                "demo",
                message="No license key provided. Running in demo mode.",
                demo_days_remaining=DEMO_DAYS,
            )

        # Look up license in DynamoDB
        result = table.get_item(Key={"license_key": license_key})
        item = result.get("Item")

        if not item:
            return _response("invalid", message="License key not found.")

        # Check if revoked
        if not item.get("active", True):
            return _response("revoked", message="License has been revoked.")

        # Check expiry
        expires = item.get("expires", "")
        if expires:
            expires_dt = datetime.fromisoformat(expires.replace("Z", ""))
            if now > expires_dt:
                return _response(
                    "expired",
                    expires=expires,
                    message="License has expired. Renew at elfege.com",
                )

        # Check hardware fingerprint binding
        stored_fp = item.get("hardware_fingerprint")
        if stored_fp and stored_fp != fingerprint:
            return _response(
                "invalid",
                message="License is bound to a different machine.",
            )

        # First activation — bind fingerprint
        if not stored_fp:
            table.update_item(
                Key={"license_key": license_key},
                UpdateExpression=(
                    "SET hardware_fingerprint = :fp, activated_at = :at"
                ),
                ExpressionAttributeValues={
                    ":fp": fingerprint,
                    ":at": now_iso,
                },
            )

        # Update last_seen and ip
        table.update_item(
            Key={"license_key": license_key},
            UpdateExpression="SET last_seen = :ls, ip = :ip",
            ExpressionAttributeValues={
                ":ls": now_iso,
                ":ip": source_ip,
            },
        )

        return _response("valid", expires=expires)

    except Exception as e:
        # Don't expose internal errors
        return _response("error", message="Validation service temporarily unavailable.")


def _log_deployment(fingerprint, timestamp, ip, version, hostname_hash):
    """Log deployment heartbeat to the deployments tracking table."""
    if deployments_table is None:
        return
    try:
        deployments_table.put_item(
            Item={
                "hardware_fingerprint": fingerprint,
                "timestamp": timestamp,
                "ip": ip,
                "version": version,
                "hostname_hash": hostname_hash,
            }
        )
    except Exception:
        pass  # Non-critical — don't fail validation over logging


def _response(status, expires=None, message=None, demo_days_remaining=None):
    """Build a standardized response."""
    body = {"status": status}
    if expires:
        body["expires"] = expires
    if message:
        body["message"] = message
    if demo_days_remaining is not None:
        body["demo_days_remaining"] = demo_days_remaining
    return {"statusCode": 200, "body": json.dumps(body)}
