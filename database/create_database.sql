-- Ejecutar una sola vez conectado a la base postgres como superusuario.
-- Cambia la clave de banco_app y usa la misma en DATABASE_URL del archivo .env.
CREATE ROLE banco_app WITH LOGIN PASSWORD 'oscarqv28.';
CREATE DATABASE banco_ia OWNER banco_app ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE banco_ia TO banco_app;
