#!/usr/bin/env bash
set -euo pipefail

# The official MySQL image runs /docker-entrypoint-initdb.d only when
# /var/lib/mysql is empty. Keep this hook limited to database-level bootstrap;
# application tables and seed data are initialized by backend-init from the
# current ORM models and JSON seed files.
database="${MYSQL_DATABASE:-fastapiadmin}"

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS \`${database}\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER DATABASE \`${database}\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
SQL
