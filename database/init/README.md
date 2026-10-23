# MySQL initialization

The MySQL entrypoint runs `01-create-exporter.sh` only when initializing a new
data volume. It creates the separate, read-only `crm_metrics` account used by
mysqld-exporter. If the database volume already existed before this script was
added, create the account once using the SQL in the script (substitute your
local exporter password) or deliberately migrate the data volume after a
backup. Do not delete a volume containing data just to apply this change.
