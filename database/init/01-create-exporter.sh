#!/bin/sh
set -eu

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS 'crm_metrics'@'%' IDENTIFIED BY '${DB_EXPORTER_PASSWORD}';
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'crm_metrics'@'%';
FLUSH PRIVILEGES;
SQL
