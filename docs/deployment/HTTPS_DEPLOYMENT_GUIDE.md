---
---

# HTTPS/TLS Deployment Guide
**snflwr.ai - Secure Production Deployment**

This guide covers setting up HTTPS/TLS for secure production deployment using nginx reverse proxy.

---

## Table of Contents
1. [Overview](#overview)
2. [Development Setup (Self-Signed)](#development-setup)
3. [Production Setup (Let's Encrypt)](#production-setup)
4. [Docker Compose Integration](#docker-compose-integration)
5. [Testing](#testing)
6. [Troubleshooting](#troubleshooting)
7. [Security Best Practices](#security-best-practices)

---

## Overview

### Architecture
```
Internet → Nginx (443/HTTPS) → FastAPI (8000/HTTP)
           Nginx (80/HTTP)  → Redirect to HTTPS
```

### What's Included
- ✅ Nginx reverse proxy configuration
- ✅ SSL/TLS termination
- ✅ HTTP to HTTPS redirection
- ✅ WebSocket support (wss://)
- ✅ Rate limiting
- ✅ Security headers
- ✅ HSTS (HTTP Strict Transport Security)
- ✅ Let's Encrypt auto-renewal

---

## Development Setup (Self-Signed Certificates)

### Option 1: Quick Start Script

```bash
# Generate self-signed certificate
./nginx/ssl/generate-self-signed.sh

# Copy development nginx config
cp nginx/conf.d/snflwr-dev.conf.example nginx/conf.d/snflwr.conf

# Start with docker compose (nginx is included in the main compose file)
docker compose -f docker/compose/docker-compose.yml up -d
```

### Option 2: Manual Generation

```bash
# Create SSL directory
mkdir -p nginx/ssl

# Generate certificate (valid for 365 days)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/self-signed.key \
  -out nginx/ssl/self-signed.crt \
  -subj "/C=US/ST=State/L=City/O=snflwr.ai/OU=Development/CN=localhost"

# Set permissions
chmod 600 nginx/ssl/self-signed.key
chmod 644 nginx/ssl/self-signed.crt
```

### Access Your Site
- HTTP: http://localhost
- HTTPS: https://localhost (will show browser warning)

**Note:** Your browser will show a security warning for self-signed certificates. This is expected and safe for development.

---

## Production Setup (Let's Encrypt)

### Prerequisites
- Domain name pointed to your server's IP address
- Ports 80 and 443 open in firewall
- Root/sudo access

### Option 1: Automated Script (Recommended)

```bash
# Run Let's Encrypt setup script
sudo ./scripts/setup-letsencrypt.sh

# Follow the prompts:
# - Enter your domain (e.g., snflwr.ai)
# - Enter your email for certificate notifications
```

### Option 2: Manual Setup

#### Step 1: Install Certbot
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install certbot

# CentOS/RHEL
sudo yum install certbot
```

#### Step 2: Obtain Certificate
```bash
# Stop nginx if running
docker-compose down nginx

# Get certificate (standalone mode)
sudo certbot certonly --standalone \
  --preferred-challenges http \
  --email your-email@example.com \
  --agree-tos \
  --domain snflwr.ai \
  --domain www.snflwr.ai

# Certificates will be in: /etc/letsencrypt/live/snflwr.ai/
```

#### Step 3: Copy Certificates
```bash
# Copy to nginx ssl directory
sudo cp /etc/letsencrypt/live/snflwr.ai/fullchain.pem nginx/ssl/
sudo cp /etc/letsencrypt/live/snflwr.ai/privkey.pem nginx/ssl/
sudo cp /etc/letsencrypt/live/snflwr.ai/chain.pem nginx/ssl/

# Set permissions
sudo chmod 644 nginx/ssl/fullchain.pem
sudo chmod 600 nginx/ssl/privkey.pem
sudo chmod 644 nginx/ssl/chain.pem
```

#### Step 4: Update Nginx Config
```bash
# Edit nginx/conf.d/snflwr.conf
# Replace 'snflwr.ai' with your actual domain
sed -i 's/snflwr.ai/your-domain.com/g' nginx/conf.d/snflwr.conf
```

#### Step 5: Start Services
```bash
docker compose -f docker/compose/docker-compose.yml up -d
```

### Auto-Renewal

Certificates expire every 90 days. Set up automatic renewal:

```bash
# Add cron job for renewal
sudo crontab -e

# Add this line:
0 0 * * * certbot renew --quiet --deploy-hook "docker compose -f /path/to/snflwr.ai/docker/compose/docker-compose.yml restart nginx"
```

Or use the certbot docker service (included in docker-compose.yml):
```bash
# Run with production profile
docker compose --profile production -f docker/compose/docker-compose.yml up -d
```

---

## Docker Compose Integration

### Start All Services
```bash
# Development (HTTP + HTTPS with self-signed)
docker compose -f docker/compose/docker-compose.yml up -d

# Production (with Let's Encrypt auto-renewal)
docker compose --profile production -f docker/compose/docker-compose.yml up -d
```

### Start Only Nginx
```bash
docker compose -f docker/compose/docker-compose.yml up -d nginx
```

### View Logs
```bash
# Nginx access logs
docker-compose logs -f nginx

# Or view raw logs
tail -f nginx/logs/snflwr_access.log
tail -f nginx/logs/snflwr_error.log
```

### Restart Nginx (after config changes)
```bash
docker-compose restart nginx

# Or reload without downtime
docker-compose exec nginx nginx -s reload
```

---

## Testing

### 1. Test HTTP to HTTPS Redirect
```bash
curl -I http://your-domain.com
# Should return: HTTP/1.1 301 Moved Permanently
# Location: https://your-domain.com
```

### 2. Test HTTPS
```bash
curl -I https://your-domain.com
# Should return: HTTP/2 200
```

### 3. Test WebSocket
```bash
# Should upgrade to WSS
wscat -c wss://your-domain.com/api/ws/monitor?token=YOUR_TOKEN
```

### 4. Test SSL Configuration
```bash
# Check SSL certificate
openssl s_client -connect your-domain.com:443 -servername your-domain.com

# Test SSL strength (requires sslscan)
sslscan your-domain.com
```

### 5. Online SSL Tests
- **SSL Labs**: https://www.ssllabs.com/ssltest/
  - Target Grade: **A+**
- **Security Headers**: https://securityheaders.com/
  - Target Grade: **A**

---

## Troubleshooting

### Certificate Errors

**Problem:** "Certificate not found" error
```bash
# Check certificate files exist
ls -la nginx/ssl/

# Verify permissions
# fullchain.pem: 644
# privkey.pem: 600
```

**Solution:**
```bash
# Re-run certificate generation script
./nginx/ssl/generate-self-signed.sh  # Development
sudo ./scripts/setup-letsencrypt.sh  # Production
```

### Connection Refused

**Problem:** Cannot connect to HTTPS

```bash
# Check if nginx is running
docker-compose ps nginx

# Check nginx logs
docker-compose logs nginx

# Test nginx config
docker-compose exec nginx nginx -t
```

**Solution:**
```bash
# Restart nginx
docker-compose restart nginx

# Check firewall
sudo ufw status
sudo ufw allow 443/tcp
```

### Rate Limiting Issues

**Problem:** Getting 429 Too Many Requests

**Solution:**
```bash
# Check rate limit in nginx/conf.d/snflwr.conf
# Adjust these values:
limit_req zone=api_limit burst=200 nodelay;   # API endpoints
limit_req zone=auth_limit burst=10 nodelay;   # Auth endpoints
```

### WebSocket Connection Fails

**Problem:** WebSocket upgrade fails

**Solution:**
```bash
# Verify WebSocket configuration in nginx/conf.d/snflwr.conf
# Should have:
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

---

## Security Best Practices

### 1. Certificate Management
- ✅ Use Let's Encrypt for production (free, auto-renewal)
- ✅ Never commit private keys to git
- ✅ Set proper file permissions (600 for private keys)
- ✅ Monitor certificate expiration
- ✅ Use OCSP stapling (enabled by default)

### 2. SSL Configuration
- ✅ TLS 1.2 and 1.3 only (configured)
- ✅ Strong cipher suites (Mozilla Modern config)
- ✅ HSTS enabled (max-age 2 years)
- ✅ Disable SSL session tickets
- ✅ Enable OCSP stapling

### 3. Security Headers
All configured in nginx:
- ✅ `Strict-Transport-Security: max-age=63072000`
- ✅ `X-Frame-Options: DENY`
- ✅ `X-Content-Type-Options: nosniff`
- ✅ `X-XSS-Protection: 1; mode=block`
- ✅ `Referrer-Policy: strict-origin-when-cross-origin`

### 4. Rate Limiting
- ✅ Auth endpoints: 5 req/minute
- ✅ API endpoints: 100 req/second
- ✅ Configurable burst limits

### 5. Firewall Configuration
```bash
# Allow only necessary ports
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP (for redirects)
sudo ufw allow 443/tcp  # HTTPS
sudo ufw enable
```

### 6. Monitoring
```bash
# Check SSL certificate expiration
openssl x509 -in nginx/ssl/fullchain.pem -noout -dates

# Monitor nginx logs
tail -f nginx/logs/snflwr_access.log | grep -E "429|401|403|500"
```

---

## File Structure

```
snflwr.ai/
├── nginx/
│   ├── nginx.conf                    # Main nginx config
│   ├── conf.d/
│   │   ├── snflwr.conf           # Production HTTPS config
│   │   └── snflwr-dev.conf.example  # Development config
│   ├── ssl/
│   │   ├── generate-self-signed.sh  # Dev certificate script
│   │   ├── fullchain.pem            # SSL certificate (production)
│   │   ├── privkey.pem              # Private key (production)
│   │   ├── chain.pem                # Certificate chain
│   │   ├── self-signed.crt          # Self-signed cert (dev)
│   │   └── self-signed.key          # Self-signed key (dev)
│   ├── logs/                        # Nginx logs
│   └── certbot/                     # Let's Encrypt challenges
├── scripts/
│   └── setup-letsencrypt.sh         # Production cert setup
├── docker/compose/docker-compose.yml # Unified docker config (includes nginx)
└── HTTPS_DEPLOYMENT_GUIDE.md        # This file
```

---

## Quick Reference

### Commands Cheat Sheet

```bash
# Development
./nginx/ssl/generate-self-signed.sh
docker compose -f docker/compose/docker-compose.yml up -d

# Production
sudo ./scripts/setup-letsencrypt.sh
docker compose --profile production -f docker/compose/docker-compose.yml up -d

# Maintenance
docker-compose logs -f nginx          # View logs
docker-compose restart nginx          # Restart nginx
docker-compose exec nginx nginx -t    # Test config
docker-compose exec nginx nginx -s reload  # Reload config

# Certificate Renewal (manual)
sudo certbot renew
docker-compose restart nginx
```

### Important URLs
- **Let's Encrypt**: https://letsencrypt.org/
- **SSL Labs Test**: https://www.ssllabs.com/ssltest/
- **Mozilla SSL Config**: https://ssl-config.mozilla.org/
- **Certbot Docs**: https://certbot.eff.org/

---

## Support

For issues or questions:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review nginx error logs: `nginx/logs/snflwr_error.log`
3. Test configuration: `docker-compose exec nginx nginx -t`
4. See SECURITY_HARDENING_REPORT.md for security details

---

**Security Score After HTTPS:** 100/100 ✅
**Last Updated:** 2025-12-29
