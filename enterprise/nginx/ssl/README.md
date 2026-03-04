# SSL/TLS Certificates Directory

This directory contains SSL/TLS certificates for HTTPS.

## ⚠️ SECURITY WARNING

**NEVER commit certificate files to version control!**

Certificate files are automatically excluded by `.gitignore`:
- `*.key` - Private keys
- `*.pem` - Certificate files
- `*.crt` - Certificate files

## Development (Self-Signed Certificates)

Generate development certificates:
```bash
./generate-self-signed.sh
```

This creates:
- `fullchain.pem` - Certificate (valid 365 days)
- `privkey.pem` - Private key
- `chain.pem` - Copy of fullchain.pem (for nginx compatibility)

## Production (Let's Encrypt)

Use the automated setup script:
```bash
sudo ../scripts/setup-letsencrypt.sh
```

Or manually copy from Let's Encrypt:
```bash
sudo cp /etc/letsencrypt/live/your-domain/fullchain.pem .
sudo cp /etc/letsencrypt/live/your-domain/privkey.pem .
sudo cp /etc/letsencrypt/live/your-domain/chain.pem .
```

## Required Files

For nginx to start with HTTPS, you need:

**Production:**
- `fullchain.pem` - Full certificate chain
- `privkey.pem` - Private key
- `chain.pem` - CA certificate chain

**Development:**
- `fullchain.pem` - Self-signed certificate (same name as production for drop-in compatibility)
- `privkey.pem` - Self-signed private key (same name as production for drop-in compatibility)
- `chain.pem` - Copy of fullchain.pem

## Permissions

Set correct permissions:
```bash
chmod 644 *.crt *.pem chain.pem fullchain.pem
chmod 600 *.key privkey.pem
```

## Certificate Expiration

**Let's Encrypt:** 90 days (auto-renewal configured)
**Self-Signed:** 365 days (regenerate annually)

Check expiration:
```bash
openssl x509 -in fullchain.pem -noout -dates
```

## Troubleshooting

**Certificate not found:**
```bash
ls -la
# Should see: fullchain.pem, privkey.pem, chain.pem
```

**Permission denied:**
```bash
chmod 600 *.key privkey.pem
chmod 644 *.crt *.pem
```

**Invalid certificate:**
```bash
# Verify certificate
openssl x509 -in fullchain.pem -text -noout
```

## Documentation

See `HTTPS_DEPLOYMENT_GUIDE.md` for complete setup instructions.
