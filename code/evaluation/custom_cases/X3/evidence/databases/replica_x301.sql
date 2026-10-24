-- Fixture point-in-time database snapshot
-- Captured at: 2026-09-14T10:00:00Z
-- Database topology: read_replica
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO customer_data_primary VALUES('audit_record_990003','security_service','security_audit_events','{"resource_id": "audit_record_990003", "subject_reference": "user_9903", "event_name": "account_deletion_reviewed"}','2026-09-14T10:00:00Z');
INSERT INTO customer_data_primary VALUES('account_990013','account_service','customer_profiles','{"resource_id": "account_990013", "customer_reference": "user_9913", "full_name": "Tomas Brandt", "email": "t.brandt@example.test", "phone": "+49 151 990013", "account_status": "ACTIVE"}','2026-09-14T10:00:00Z');
INSERT INTO customer_data_read_replica VALUES('account_990003','account_service','customer_profiles','{"resource_id": "account_990003", "customer_reference": "user_9903", "full_name": "Mira Sol", "email": "mira.sol@example.test", "phone": "+1 (202) 555-0903", "account_status": "ACTIVE"}','2026-09-14T10:00:00Z');
INSERT INTO customer_data_read_replica VALUES('account_990013','account_service','customer_profiles','{"resource_id": "account_990013", "customer_reference": "user_9913", "full_name": "Tomas Brandt", "email": "t.brandt@example.test", "phone": "+49 151 990013", "account_status": "ACTIVE"}','2026-09-14T10:00:00Z');
INSERT INTO customer_data_read_replica VALUES('audit_record_990003','security_service','security_audit_events','{"resource_id": "audit_record_990003", "subject_reference": "user_9903", "event_name": "account_deletion_reviewed"}','2026-09-14T10:00:00Z');
COMMIT;
