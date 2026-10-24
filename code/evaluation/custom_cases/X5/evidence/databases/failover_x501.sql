-- Fixture point-in-time database snapshot
-- Captured at: 2026-09-10T12:00:00Z
-- Database topology: failover_replica
BEGIN TRANSACTION;
CREATE TABLE customer_profiles_failover_replica (resource_id TEXT, user_id TEXT, full_name TEXT, email TEXT, phone TEXT, account_status TEXT, updated_at TEXT);
CREATE TABLE customer_profiles_primary (resource_id TEXT, user_id TEXT, full_name TEXT, email TEXT, phone TEXT, account_status TEXT, updated_at TEXT);
INSERT INTO customer_profiles_primary VALUES('account_990777', 'user_9906', 'Rhea Valentine', 'rhea.valentine@example.test', '+44 7700 990006', 'ACTIVE', '2026-09-10T12:00:00Z');
INSERT INTO customer_profiles_failover_replica VALUES('account_990777', 'user_9906', 'Rhea Valentine', 'rhea.valentine@example.test', '+44 7700 990006', 'ACTIVE', '2026-09-10T12:00:00Z');
COMMIT;
