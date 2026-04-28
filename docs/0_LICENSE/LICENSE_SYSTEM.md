# NVR License System Documentation

## Architecture

```
Customer NVR Instance                    AWS (us-east-1)
┌──────────────┐                ┌────────────────────────┐
│ app.py       │  HTTPS POST    │  API Gateway           │
│ license_     ├───────────────►│  /prod/validate        │
│ service.py   │                │    → nvr-license-      │
│              │                │      validator Lambda   │
│              │                │                        │
│ NVR_LICENSE_ │                │  /prod/issue           │
│ KEY env var  │                │    → nvr-license-      │
│              │                │      issuer Lambda     │
└──────────────┘                │                        │
                                │  DynamoDB              │
                                │  ├ nvr-licenses        │
                                │  └ nvr-deployments     │
                                └────────────────────────┘
```

## Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/prod/validate` | POST | None (license key is auth) | Validate license on NVR startup |
| `/prod/issue` | POST | `X-Admin-Key` header | Create new license for customer |

**Base URL:** `https://imodm0mn53.execute-api.us-east-1.amazonaws.com`

## Issuing a License

```bash
curl -X POST https://imodm0mn53.execute-api.us-east-1.amazonaws.com/prod/issue \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Key: <ADMIN_KEY>' \
  -d '{"email": "customer@example.com", "plan": "yearly"}'
```

Response:
```json
{
  "license_key": "6d185477-7371-4a58-b7ce-0393684d0b12",
  "email": "customer@example.com",
  "plan": "yearly",
  "expires": "2027-03-20T05:26:51.276802Z",
  "message": "License created successfully."
}
```

The admin key is stored in AWS Secrets Manager: `nvr-license-admin-key`

Retrieve it:
```bash
aws secretsmanager get-secret-value --secret-id nvr-license-admin-key \
  --profile personal --region us-east-1 --query SecretString --output text
```

## Validating a License

```bash
curl -X POST https://imodm0mn53.execute-api.us-east-1.amazonaws.com/prod/validate \
  -H 'Content-Type: application/json' \
  -d '{"license_key": "THE-KEY", "hardware_fingerprint": "sha256..."}'
```

Responses:
- `{"status": "valid", "expires": "2027-03-20T..."}` — license is active
- `{"status": "invalid", "message": "License key not found."}` — key doesn't exist
- `{"status": "invalid", "message": "License is bound to a different machine."}` — hardware mismatch
- `{"status": "expired", "expires": "...", "message": "..."}` — license expired
- `{"status": "revoked", "message": "..."}` — manually deactivated
- `{"status": "demo", "message": "...", "demo_days_remaining": 7}` — no key provided

## Hardware Binding

- On first validation, the hardware fingerprint is permanently bound to the license key
- Fingerprint = SHA-256 of (sorted MAC addresses + /etc/machine-id)
- Same key on a different machine → rejected
- Customer cannot transfer license without admin reset

## Demo Mode

When no valid license is present:
- **7-day trial** from first startup
- Max 2 cameras
- No recording
- Watermark on streams
- "Purchase license" banner
- After 7 days: app refuses to start

Demo timer is stored locally in `config/.license_cache.json` and tied to hardware fingerprint (can't be reset by reinstall).

## NVR Integration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NVR_LICENSE_KEY` | No | License key UUID. Demo mode if absent. |
| `NVR_LICENSE_VALIDATOR_URL` | No | API endpoint. Defaults to production URL. |

### App Startup Flow

1. `app.py` imports `services/license_service.py`
2. `validate_license()` called during Flask app initialization
3. Sends POST to validator with license key + hardware fingerprint
4. Sets global `license` object with status, limits, watermark flag
5. All components check `license.is_demo`, `license.max_cameras`, etc.
6. `/api/license` endpoint exposes status to frontend

### Offline Grace Period

- Successful validation cached in `config/.license_cache.json`
- If validator is unreachable: cached result valid for 7 days
- After 7 days offline: demo mode

## AWS Resources

| Resource | Name | Region | Notes |
|----------|------|--------|-------|
| API Gateway | nvr-license-api | us-east-1 | REST API, `prod` stage |
| Lambda | nvr-license-validator | us-east-1 | Python 3.12, arm64, 128MB |
| Lambda | nvr-license-issuer | us-east-1 | Python 3.12, arm64, 128MB |
| DynamoDB | nvr-licenses | us-east-1 | PAY_PER_REQUEST, PK: license_key |
| DynamoDB | nvr-deployments | us-east-1 | PAY_PER_REQUEST, PK: hardware_fingerprint, SK: timestamp |
| IAM Role | nvr-license-role | global | Lambda execution + DynamoDB access |
| Secret | nvr-license-admin-key | us-east-1 | Admin API key for issuing licenses |
| Secret | nvr-git-crypt-key | us-east-1 | Git-crypt symmetric key (base64) |

## Deployment

### First-time setup
```bash
cd infrastructure/lambda/license/
export AWS_PROFILE=nvr-deployer
bash deploy_license.sh
```

### Update Lambda code only
```bash
deploy_nvr_license_lambdas  # bash_utils function
```

### AWS Profile: nvr-deployer
- IAM user with AdministratorAccess on account 032397977825
- Access keys stored in ELFEGE-secrets (DEPLOY_AWS_ACCESS_KEY, DEPLOY_AWS_SECRET_KEY)
- Configured as AWS CLI profile `nvr-deployer`

## Cost

All resources are within AWS Free Tier:
- Lambda: 1M requests/month free (forever)
- DynamoDB: 25GB storage, 25 WCU/RCU free (forever)
- API Gateway: 1M requests/month free (12 months), then ~$3.50/million
- Secrets Manager: $0.40/secret/month (~$1.20/month for 3 secrets)

**Estimated monthly cost after free tier: ~$1.60**
