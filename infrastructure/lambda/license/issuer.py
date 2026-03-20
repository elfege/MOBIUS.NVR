"""
NVR License Issuer Lambda

Creates new license keys. Called by PayPal webhook or admin manually.
Protected by admin API key in header (X-Admin-Key).

Request: POST {"email": "customer@example.com", "plan": "yearly"}
Response: {"license_key": "uuid", "expires": "ISO8601", "email": "..."}
"""

import json
import os
import uuid
import boto3
from datetime import datetime, timedelta

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")

# Plan durations in days
PLANS = {
    "yearly": 365,
    "monthly": 30,  # Future use
    "trial": 14,    # Future use
}


def lambda_handler(event, context):
    """Issue a new license key."""
    try:
        # Authenticate admin
        headers = event.get("headers", {})
        provided_key = headers.get("x-admin-key", "")
        if not ADMIN_KEY or provided_key != ADMIN_KEY:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Unauthorized"}),
            }

        body = json.loads(event.get("body", "{}"))
        email = body.get("email", "").strip().lower()
        plan = body.get("plan", "yearly")

        if not email:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Email is required"}),
            }

        if plan not in PLANS:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {"error": f"Invalid plan. Valid: {list(PLANS.keys())}"}
                ),
            }

        # Generate license
        license_key = str(uuid.uuid4())
        now = datetime.utcnow()
        expires = now + timedelta(days=PLANS[plan])

        item = {
            "license_key": license_key,
            "email": email,
            "plan": plan,
            "created": now.isoformat() + "Z",
            "expires": expires.isoformat() + "Z",
            "hardware_fingerprint": None,
            "active": True,
            "activated_at": None,
            "last_seen": None,
            "ip": None,
        }

        # DynamoDB doesn't accept None for non-key attributes in some contexts.
        # Remove None values — they simply won't exist in the item.
        item = {k: v for k, v in item.items() if v is not None}

        table.put_item(Item=item)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "license_key": license_key,
                    "email": email,
                    "plan": plan,
                    "expires": expires.isoformat() + "Z",
                    "message": "License created successfully.",
                }
            ),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to create license."}),
        }
