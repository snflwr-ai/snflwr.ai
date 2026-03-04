#!/bin/bash
# Generate Self-Signed SSL Certificate for Development
# WARNING: Do NOT use in production - use Let's Encrypt instead

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_FILE="$SCRIPT_DIR/fullchain.pem"
KEY_FILE="$SCRIPT_DIR/privkey.pem"
CHAIN_FILE="$SCRIPT_DIR/chain.pem"

echo "Generating self-signed SSL certificate for development..."
echo "WARNING: This is for DEVELOPMENT ONLY - use Let's Encrypt for production"
echo ""

# Generate private key and certificate
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -subj "/C=US/ST=State/L=City/O=snflwr.ai/OU=Development/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1"

# Set proper permissions
chmod 600 "$KEY_FILE"
chmod 644 "$CERT_FILE"

# chain.pem is the same as fullchain.pem for self-signed certs (no separate CA chain)
cp "$CERT_FILE" "$CHAIN_FILE"
chmod 644 "$CHAIN_FILE"

echo "Self-signed certificate generated:"
echo "   Certificate: $CERT_FILE"
echo "   Private Key: $KEY_FILE"
echo "   Chain:       $CHAIN_FILE"
echo ""
echo "Valid for: 365 days"
echo "Domain: localhost"
echo ""
echo "WARNING: Your browser will show a security warning - this is expected for self-signed certificates"
echo "WARNING: For production, use Let's Encrypt: ./scripts/setup-letsencrypt.sh"
