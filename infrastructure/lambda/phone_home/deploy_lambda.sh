#!/bin/bash
# =============================================================================
# Deploy NVR Phone-Home Lambda + DynamoDB
# =============================================================================
# Creates (or updates) the AWS infrastructure for deployment tracking:
#   - DynamoDB table: nvr-deployments
#   - IAM role: nvr-phone-home-role
#   - Lambda function: nvr-phone-home (Python 3.12, arm64)
#   - Lambda function URL (public, no auth)
#
# Requirements: AWS CLI v2, configured with profile 'personal'
# Usage:
#   ./deploy_lambda.sh            # Create everything
#   ./deploy_lambda.sh --update   # Update Lambda code only
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REGION="us-east-1"
FUNCTION_NAME="nvr-phone-home"
TABLE_NAME="nvr-deployments"
ROLE_NAME="nvr-phone-home-role"
export AWS_PROFILE="personal"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check AWS CLI
if ! command -v aws &>/dev/null; then
    echo -e "${RED}ERROR: AWS CLI not found. Install it first.${NC}"
    exit 1
fi

# Check auth
if ! aws sts get-caller-identity &>/dev/null; then
    echo -e "${RED}ERROR: AWS authentication failed. Run 'aws sso login --profile personal'${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account: $ACCOUNT_ID"
echo "Region: $REGION"
echo ""

# =============================================================================
# Step 1: DynamoDB table
# =============================================================================
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &>/dev/null; then
    echo -e "${GREEN}DynamoDB table '$TABLE_NAME' already exists${NC}"
else
    echo "Creating DynamoDB table '$TABLE_NAME'..."
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions \
            AttributeName=hardware_fingerprint,AttributeType=S \
            AttributeName=timestamp,AttributeType=S \
        --key-schema \
            AttributeName=hardware_fingerprint,KeyType=HASH \
            AttributeName=timestamp,KeyType=RANGE \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" >/dev/null
    echo "Waiting for table to be active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$REGION"
    echo -e "${GREEN}DynamoDB table created${NC}"
fi
echo ""

# =============================================================================
# Step 2: IAM role
# =============================================================================
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
    echo -e "${GREEN}IAM role '$ROLE_NAME' already exists${NC}"
    ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
else
    echo "Creating IAM role '$ROLE_NAME'..."
    ROLE_ARN=$(aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file://trust_policy.json \
        --query 'Role.Arn' --output text)

    # Attach basic Lambda execution policy (CloudWatch Logs)
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    # Inline policy for DynamoDB write access
    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "nvr-dynamodb-write" \
        --policy-document "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [{
                \"Effect\": \"Allow\",
                \"Action\": [\"dynamodb:PutItem\"],
                \"Resource\": \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE_NAME}\"
            }]
        }"

    echo -e "${GREEN}IAM role created: $ROLE_ARN${NC}"
    echo "Waiting 10s for IAM propagation..."
    sleep 10
fi
echo ""

# =============================================================================
# Step 3: Lambda function
# =============================================================================
# Package the code
echo "Packaging Lambda function..."
cd "$SCRIPT_DIR"
rm -f function.zip
zip -j function.zip lambda_function.py >/dev/null

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" &>/dev/null; then
    echo "Updating Lambda function code..."
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file fileb://function.zip \
        --region "$REGION" >/dev/null
    echo -e "${GREEN}Lambda function updated${NC}"
else
    echo "Creating Lambda function..."
    aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --runtime python3.12 \
        --architectures arm64 \
        --handler lambda_function.lambda_handler \
        --role "$ROLE_ARN" \
        --zip-file fileb://function.zip \
        --environment "Variables={TABLE_NAME=${TABLE_NAME}}" \
        --timeout 10 \
        --memory-size 128 \
        --region "$REGION" >/dev/null
    echo -e "${GREEN}Lambda function created${NC}"

    echo "Waiting for function to be active..."
    aws lambda wait function-active-v2 --function-name "$FUNCTION_NAME" --region "$REGION"
fi
rm -f function.zip
echo ""

# =============================================================================
# Step 4: Function URL
# =============================================================================
FUNC_URL=$(aws lambda get-function-url-config \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'FunctionUrl' --output text 2>/dev/null || echo "")

if [[ -n "$FUNC_URL" && "$FUNC_URL" != "None" ]]; then
    echo -e "${GREEN}Function URL already exists${NC}"
else
    echo "Creating function URL..."
    FUNC_URL=$(aws lambda create-function-url-config \
        --function-name "$FUNCTION_NAME" \
        --auth-type NONE \
        --region "$REGION" \
        --query 'FunctionUrl' --output text)

    # Allow public invoke
    aws lambda add-permission \
        --function-name "$FUNCTION_NAME" \
        --statement-id "FunctionURLAllowPublicAccess" \
        --action "lambda:InvokeFunctionUrl" \
        --principal "*" \
        --function-url-auth-type NONE \
        --region "$REGION" >/dev/null 2>&1 || true

    echo -e "${GREEN}Function URL created${NC}"
fi

echo ""
echo "=========================================="
echo -e "  ${GREEN}Deployment complete!${NC}"
echo "=========================================="
echo ""
echo "  Function URL: $FUNC_URL"
echo ""
echo "  Next step: Update scripts/phone_home.sh with:"
echo "    NVR_PHONE_HOME_URL=\"$FUNC_URL\""
echo ""
echo "  Test with:"
echo "    curl -X POST $FUNC_URL -H 'Content-Type: application/json' -d '{\"fingerprint\":\"test\"}'"
echo ""
echo "  Query deployments:"
echo "    aws dynamodb scan --table-name $TABLE_NAME --profile personal --region $REGION"
echo ""
