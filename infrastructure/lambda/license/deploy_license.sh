#!/bin/bash
# =============================================================================
# Deploy NVR License System — DynamoDB + 2 Lambdas
# =============================================================================
# Creates (or updates):
#   - DynamoDB table: nvr-licenses
#   - Lambda: nvr-license-validator (public function URL)
#   - Lambda: nvr-license-issuer (admin-key protected function URL)
#   - IAM role: nvr-license-role
#
# Requirements: AWS CLI v2, profile 'personal'
# Usage: ./deploy_license.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REGION="us-east-1"
TABLE_NAME="nvr-licenses"
DEPLOYMENTS_TABLE="nvr-deployments"
ROLE_NAME="nvr-license-role"
VALIDATOR_NAME="nvr-license-validator"
ISSUER_NAME="nvr-license-issuer"
export AWS_PROFILE="${AWS_PROFILE:-nvr-deployer}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Generate admin API key if not set
if [[ -z "$NVR_LICENSE_ADMIN_KEY" ]]; then
    NVR_LICENSE_ADMIN_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
    echo -e "${YELLOW}Generated admin API key: $NVR_LICENSE_ADMIN_KEY${NC}"
    echo -e "${YELLOW}Save this! You need it to issue licenses.${NC}"
    echo ""
fi

# Check AWS
if ! aws sts get-caller-identity &>/dev/null; then
    echo -e "${RED}ERROR: AWS auth failed. Run: aws sso login --profile personal${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account: $ACCOUNT_ID | Region: $REGION"
echo ""

# =============================================================================
# DynamoDB: nvr-licenses
# =============================================================================
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &>/dev/null; then
    echo -e "${GREEN}Table '$TABLE_NAME' exists${NC}"
else
    echo "Creating table '$TABLE_NAME'..."
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=license_key,AttributeType=S \
        --key-schema AttributeName=license_key,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" >/dev/null
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$REGION"
    echo -e "${GREEN}Table created${NC}"
fi

# DynamoDB: nvr-deployments (phone-home, if not already created)
if aws dynamodb describe-table --table-name "$DEPLOYMENTS_TABLE" --region "$REGION" &>/dev/null; then
    echo -e "${GREEN}Table '$DEPLOYMENTS_TABLE' exists${NC}"
else
    echo "Creating table '$DEPLOYMENTS_TABLE'..."
    aws dynamodb create-table \
        --table-name "$DEPLOYMENTS_TABLE" \
        --attribute-definitions \
            AttributeName=hardware_fingerprint,AttributeType=S \
            AttributeName=timestamp,AttributeType=S \
        --key-schema \
            AttributeName=hardware_fingerprint,KeyType=HASH \
            AttributeName=timestamp,KeyType=RANGE \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" >/dev/null
    aws dynamodb wait table-exists --table-name "$DEPLOYMENTS_TABLE" --region "$REGION"
    echo -e "${GREEN}Table created${NC}"
fi
echo ""

# =============================================================================
# IAM role
# =============================================================================
TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
    echo -e "${GREEN}Role '$ROLE_NAME' exists${NC}"
    ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
else
    echo "Creating role '$ROLE_NAME'..."
    ROLE_ARN=$(aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --query 'Role.Arn' --output text)

    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    # DynamoDB access for both tables
    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "nvr-license-dynamodb" \
        --policy-document "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
                \"Effect\": \"Allow\",
                \"Action\": [\"dynamodb:GetItem\", \"dynamodb:PutItem\", \"dynamodb:UpdateItem\", \"dynamodb:Query\"],
                \"Resource\": [
                    \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE_NAME}\",
                    \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${DEPLOYMENTS_TABLE}\"
                ]
            }]
        }"

    echo -e "${GREEN}Role created: $ROLE_ARN${NC}"
    echo "Waiting 10s for IAM propagation..."
    sleep 10
fi
echo ""

# =============================================================================
# Helper: deploy a Lambda function
# =============================================================================
deploy_lambda() {
    local func_name="$1"
    local handler="$2"
    local source_file="$3"
    local env_vars="$4"
    local zip_file="${func_name}.zip"

    echo "Packaging $func_name..."
    rm -f "$zip_file"
    zip -j "$zip_file" "$source_file" >/dev/null

    if aws lambda get-function --function-name "$func_name" --region "$REGION" &>/dev/null; then
        echo "Updating $func_name..."
        aws lambda update-function-code \
            --function-name "$func_name" \
            --zip-file "fileb://$zip_file" \
            --region "$REGION" >/dev/null

        # Wait for update to complete before updating config
        aws lambda wait function-updated-v2 --function-name "$func_name" --region "$REGION" 2>/dev/null || sleep 5

        aws lambda update-function-configuration \
            --function-name "$func_name" \
            --environment "Variables={$env_vars}" \
            --region "$REGION" >/dev/null 2>&1 || true
        echo -e "${GREEN}$func_name updated${NC}"
    else
        echo "Creating $func_name..."
        aws lambda create-function \
            --function-name "$func_name" \
            --runtime python3.12 \
            --architectures arm64 \
            --handler "$handler" \
            --role "$ROLE_ARN" \
            --zip-file "fileb://$zip_file" \
            --environment "Variables={$env_vars}" \
            --timeout 10 \
            --memory-size 128 \
            --region "$REGION" >/dev/null
        aws lambda wait function-active-v2 --function-name "$func_name" --region "$REGION"
        echo -e "${GREEN}$func_name created${NC}"
    fi

    rm -f "$zip_file"

    # Function URL
    local func_url
    func_url=$(aws lambda get-function-url-config \
        --function-name "$func_name" --region "$REGION" \
        --query 'FunctionUrl' --output text 2>/dev/null || echo "")

    if [[ -z "$func_url" || "$func_url" == "None" ]]; then
        func_url=$(aws lambda create-function-url-config \
            --function-name "$func_name" \
            --auth-type NONE \
            --region "$REGION" \
            --query 'FunctionUrl' --output text)

        aws lambda add-permission \
            --function-name "$func_name" \
            --statement-id "FunctionURLPublicAccess" \
            --action "lambda:InvokeFunctionUrl" \
            --principal "*" \
            --function-url-auth-type NONE \
            --region "$REGION" >/dev/null 2>&1 || true
    fi

    echo "  URL: $func_url"
    echo ""
}

# =============================================================================
# Deploy both Lambdas
# =============================================================================
deploy_lambda "$VALIDATOR_NAME" "validator.lambda_handler" "validator.py" \
    "TABLE_NAME=${TABLE_NAME},DEPLOYMENTS_TABLE_NAME=${DEPLOYMENTS_TABLE}"

deploy_lambda "$ISSUER_NAME" "issuer.lambda_handler" "issuer.py" \
    "TABLE_NAME=${TABLE_NAME},ADMIN_API_KEY=${NVR_LICENSE_ADMIN_KEY}"

# =============================================================================
# Summary
# =============================================================================
VALIDATOR_URL=$(aws lambda get-function-url-config \
    --function-name "$VALIDATOR_NAME" --region "$REGION" \
    --query 'FunctionUrl' --output text 2>/dev/null)
ISSUER_URL=$(aws lambda get-function-url-config \
    --function-name "$ISSUER_NAME" --region "$REGION" \
    --query 'FunctionUrl' --output text 2>/dev/null)

echo ""
echo "=========================================="
echo -e "  ${GREEN}License system deployed!${NC}"
echo "=========================================="
echo ""
echo "  Validator URL: $VALIDATOR_URL"
echo "  Issuer URL:    $ISSUER_URL"
echo "  Admin Key:     $NVR_LICENSE_ADMIN_KEY"
echo ""
echo "  Issue a license:"
echo "    curl -X POST $ISSUER_URL \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -H 'X-Admin-Key: $NVR_LICENSE_ADMIN_KEY' \\"
echo "      -d '{\"email\": \"customer@example.com\", \"plan\": \"yearly\"}'"
echo ""
echo "  Validate a license:"
echo "    curl -X POST $VALIDATOR_URL \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"license_key\": \"THE-KEY\", \"hardware_fingerprint\": \"test\"}'"
echo ""
echo "  IMPORTANT: Save the admin key securely!"
echo "  Store it in AWS Secrets Manager:"
echo "    aws secretsmanager create-secret --name nvr-license-admin-key \\"
echo "      --secret-string '$NVR_LICENSE_ADMIN_KEY' --profile personal"
echo ""
