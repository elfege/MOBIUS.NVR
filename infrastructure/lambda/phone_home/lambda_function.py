"""
NVR Phone-Home Lambda Function

Receives heartbeat POSTs from NVR deployments and logs them to DynamoDB.
Deployed with a Lambda Function URL (no API Gateway needed).

Each heartbeat includes:
  - fingerprint: SHA-256 of hardware identifiers (MACs + machine-id)
  - version: git describe output
  - hostname_hash: SHA-256 of hostname (privacy-preserving)

The source IP is captured from the Lambda function URL request context.
"""

import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])


def lambda_handler(event, context):
    """Process a heartbeat from an NVR deployment."""
    try:
        body = json.loads(event.get("body", "{}"))
        fingerprint = body.get("fingerprint", "unknown")
        now = datetime.utcnow().isoformat() + "Z"

        # Extract source IP from Lambda function URL request context
        source_ip = (
            event.get("requestContext", {})
            .get("http", {})
            .get("sourceIp", "unknown")
        )

        table.put_item(
            Item={
                "hardware_fingerprint": fingerprint,
                "timestamp": now,
                "ip": source_ip,
                "version": body.get("version", "unknown"),
                "hostname_hash": body.get("hostname_hash", "unknown"),
            }
        )

        return {"statusCode": 200, "body": json.dumps({"status": "ok"})}

    except Exception:
        # Always return 200 — don't leak errors to clients
        return {"statusCode": 200, "body": json.dumps({"status": "received"})}
