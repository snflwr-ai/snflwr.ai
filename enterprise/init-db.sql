-- snflwr.ai - PostgreSQL Initialization Script
-- This file is mounted into the PostgreSQL Docker container at
-- /docker-entrypoint-initdb.d/init.sql and runs automatically on first start.
--
-- The database and user are created by the POSTGRES_USER / POSTGRES_DB env vars
-- in docker-compose. This script creates the application schema.

-- Enable UUID generation (if available)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- The full schema is applied by the application on first startup via
-- database/init_db.py. This file ensures the database is ready to accept
-- connections. Add any PostgreSQL-specific initialization here.
