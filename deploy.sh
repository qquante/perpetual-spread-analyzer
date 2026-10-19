#!/bin/bash
# Deploy script for AWS
# Usage: ./deploy.sh user@your-aws-ip

set -e

# AWS connection
AWS_HOST="${1:-ubuntu@your-aws-ip}"
REMOTE_DIR="/home/ubuntu/arb-bot"

echo "=== DEPLOY TO AWS ==="
echo "Target: $AWS_HOST:$REMOTE_DIR"

# Remove old bot on AWS
echo ""
echo "[1] Removing old bot on AWS..."
ssh $AWS_HOST "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"

# Files to deploy (only essentials)
echo ""
echo "[2] Uploading essential files..."

# Upload bot module
scp -r bot/ $AWS_HOST:$REMOTE_DIR/bot/

# Upload clients module
scp -r clients/ $AWS_HOST:$REMOTE_DIR/clients/

# Upload main files
scp arb_executor.py $AWS_HOST:$REMOTE_DIR/
scp requirements.txt $AWS_HOST:$REMOTE_DIR/
scp .env $AWS_HOST:$REMOTE_DIR/

# Install dependencies
echo ""
echo "[3] Installing dependencies..."
ssh $AWS_HOST "cd $REMOTE_DIR && pip install -r requirements.txt"

echo ""
echo "=== DEPLOY COMPLETE ==="
echo ""
echo "To run the bot:"
echo "  ssh $AWS_HOST"
echo "  cd $REMOTE_DIR"
echo "  python arb_executor.py"
echo ""
echo "Or run in background:"
echo "  nohup python arb_executor.py > bot.log 2>&1 &"
