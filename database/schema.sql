-- Sistema académico de banca simulada (PostgreSQL)
-- La app Flask crea el esquema automáticamente al iniciar.
CREATE TABLE users (
 id SERIAL PRIMARY KEY, username VARCHAR(40) UNIQUE NOT NULL, full_name VARCHAR(100) NOT NULL,
 password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'CLIENTE', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE accounts (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), account_type VARCHAR(40) NOT NULL DEFAULT 'Ahorros',
 number VARCHAR(24) UNIQUE NOT NULL, currency VARCHAR(3) NOT NULL DEFAULT 'PEN', balance NUMERIC(12,2) NOT NULL DEFAULT 0, status VARCHAR(20) NOT NULL DEFAULT 'ACTIVA'
);
CREATE TABLE beneficiaries (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name VARCHAR(100) NOT NULL, account_number VARCHAR(24) NOT NULL, bank_name VARCHAR(80) NOT NULL DEFAULT 'Banco Simulado'
);
CREATE TABLE cards (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), account_id INTEGER NOT NULL REFERENCES accounts(id), last4 VARCHAR(4) NOT NULL,
 card_type VARCHAR(30) NOT NULL DEFAULT 'Débito', status VARCHAR(20) NOT NULL DEFAULT 'Activa', limit_amount NUMERIC(12,2) NOT NULL DEFAULT 3000
);
CREATE TABLE sessions (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), operation_type VARCHAR(30) NOT NULL, started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 ended_at TIMESTAMP, duration_seconds INTEGER NOT NULL DEFAULT 0, steps INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
 back_actions INTEGER NOT NULL DEFAULT 0, face_detected BOOLEAN NOT NULL DEFAULT FALSE, consent_given BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE transactions (
 id SERIAL PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), session_id INTEGER REFERENCES sessions(id), operation_type VARCHAR(30) NOT NULL,
 description VARCHAR(180) NOT NULL, amount NUMERIC(12,2) NOT NULL, balance_after NUMERIC(12,2) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'COMPLETADA', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE ml_results (
 id SERIAL PRIMARY KEY, session_id INTEGER UNIQUE NOT NULL REFERENCES sessions(id), cluster_id INTEGER, label VARCHAR(80) NOT NULL,
 method VARCHAR(80) NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE operation_risk_assessments (
 id SERIAL PRIMARY KEY, session_id INTEGER UNIQUE NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 score INTEGER, level VARCHAR(40) NOT NULL, reasons JSON NOT NULL,
 method VARCHAR(80) NOT NULL DEFAULT 'Reglas transparentes (no probabilidad)',
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE audit_logs (
 id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), action VARCHAR(80) NOT NULL, module VARCHAR(50) NOT NULL,
 result VARCHAR(30) NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_transactions_account_date ON transactions(account_id, created_at DESC);
CREATE INDEX idx_sessions_user_date ON sessions(user_id, started_at DESC);
