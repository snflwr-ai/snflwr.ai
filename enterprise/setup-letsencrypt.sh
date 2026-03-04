#!/bin/bash
# Setup Let's Encrypt SSL Certificates for Production
# Uses Certbot with DNS or HTTP challenge

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}snflwr.ai - Let's Encrypt SSL Setup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root${NC}"
   echo "Usage: sudo ./scripts/setup-letsencrypt.sh"
   exit 1
fi

# Configuration
read -p "Enter your domain name (e.g., snflwr.ai): " DOMAIN
read -p "Enter your email address for Let's Encrypt notifications: " EMAIL

if [[ -z "$DOMAIN" ]] || [[ -z "$EMAIL" ]]; then
    echo -e "${RED}Error: Domain and email are required${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "  Domain: $DOMAIN"
echo "  Email: $EMAIL"
echo ""
read -p "Is this correct? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Install Certbot if not already installed
if ! command -v certbot &> /dev/null; then
    echo -e "${YELLOW}Installing Certbot...${NC}"

    if command -v apt-get &> /dev/null; then
        # Debian/Ubuntu
        apt-get update
        apt-get install -y certbot
    elif command -v yum &> /dev/null; then
        # CentOS/RHEL
        yum install -y certbot
    else
        echo -e "${RED}Error: Could not detect package manager${NC}"
        echo "Please install certbot manually: https://certbot.eff.org/"
        exit 1
    fi
fi

echo ""
echo -e "${YELLOW}Obtaining SSL certificate from Let's Encrypt...${NC}"
echo "This may take a few minutes..."
echo ""

# Use certbot standalone mode (requires port 80 to be available)
certbot certonly \
    --standalone \
    --preferred-challenges http \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --domain "$DOMAIN" \
    --domain "www.$DOMAIN"

# Copy certificates to nginx ssl directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSL_DIR="$SCRIPT_DIR/nginx/ssl"
mkdir -p "$SSL_DIR"

CERT_PATH="/etc/letsencrypt/live/$DOMAIN"

if [ -d "$CERT_PATH" ]; then
    echo ""
    echo -e "${YELLOW}Copying certificates to nginx directory...${NC}"

    cp "$CERT_PATH/fullchain.pem" "$SSL_DIR/fullchain.pem"
    cp "$CERT_PATH/privkey.pem" "$SSL_DIR/privkey.pem"
    cp "$CERT_PATH/chain.pem" "$SSL_DIR/chain.pem"

    chmod 644 "$SSL_DIR/fullchain.pem"
    chmod 600 "$SSL_DIR/privkey.pem"
    chmod 644 "$SSL_DIR/chain.pem"

    echo -e "${GREEN}✅ Certificates installed successfully!${NC}"
else
    echo -e "${RED}Error: Certificate directory not found${NC}"
    exit 1
fi

# Set up auto-renewal
echo ""
echo -e "${YELLOW}Setting up automatic renewal...${NC}"

# Create renewal script
COMPOSE_FILE="$SCRIPT_DIR/../docker/compose/docker-compose.yml"
cat > /etc/cron.daily/certbot-renew << EOF
#!/bin/bash
# Renew Let's Encrypt certificates

certbot renew --quiet --pre-hook "docker compose -f $COMPOSE_FILE stop nginx" --post-hook "docker compose -f $COMPOSE_FILE start nginx"
EOF

chmod +x /etc/cron.daily/certbot-renew

echo -e "${GREEN}✅ Auto-renewal configured!${NC}"
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}SSL Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Certificate Information:"
echo "  Domain: $DOMAIN"
echo "  Valid for: 90 days"
echo "  Auto-renewal: Enabled (daily check)"
echo ""
echo "Next Steps:"
echo "  1. Update nginx/conf.d/snflwr.conf with your domain"
echo "  2. Restart nginx: docker-compose restart nginx"
echo "  3. Test your site: https://$DOMAIN"
echo ""
echo "Certificate Locations:"
echo "  Fullchain: $SSL_DIR/fullchain.pem"
echo "  Private Key: $SSL_DIR/privkey.pem"
echo "  Chain: $SSL_DIR/chain.pem"
echo ""
echo -e "${YELLOW}⚠️  Important: Update the cron job path in /etc/cron.daily/certbot-renew${NC}"
