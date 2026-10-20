-- Run as the dedicated database owner; never in the Synapse schema.
CREATE SCHEMA IF NOT EXISTS yichat;
SET search_path TO yichat,public;
CREATE TABLE IF NOT EXISTS migrations(version integer PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events(
 event_id text PRIMARY KEY,room text NOT NULL,sequence bigint UNIQUE NOT NULL,
 received_ms bigint NOT NULL,day date NOT NULL,sender text NOT NULL,type text NOT NULL,
 state_key text,body bytea NOT NULL,body_sha256 text NOT NULL CHECK(length(body_sha256)=64));
CREATE INDEX IF NOT EXISTS events_room_day ON events(room,day,sequence);
CREATE TABLE IF NOT EXISTS memberships(
 event_id text PRIMARY KEY,room text NOT NULL,mxid text NOT NULL,sequence bigint UNIQUE NOT NULL,
 received_ms bigint NOT NULL,membership text NOT NULL);
CREATE INDEX IF NOT EXISTS memberships_history ON memberships(room,mxid,sequence);
CREATE TABLE IF NOT EXISTS attachments(
 id text PRIMARY KEY,room text NOT NULL,uploader text NOT NULL,received_ms bigint NOT NULL,day date NOT NULL,
 bytes bigint NOT NULL CHECK(bytes BETWEEN 0 AND 100000000),sha256 text NOT NULL CHECK(length(sha256)=64));
CREATE INDEX IF NOT EXISTS attachments_room_day ON attachments(room,day,id);
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS last_ref_ms bigint;
CREATE TABLE IF NOT EXISTS attachment_references(
 file text REFERENCES attachments(id),event_id text NOT NULL,room text NOT NULL,day date NOT NULL,
 received_ms bigint NOT NULL,PRIMARY KEY(file,event_id));
CREATE INDEX IF NOT EXISTS attachment_references_day ON attachment_references(room,day,file);
CREATE TABLE IF NOT EXISTS upload_reservations(id text PRIMARY KEY,created_ms bigint NOT NULL);
CREATE TABLE IF NOT EXISTS archive_gaps(id text PRIMARY KEY,room text NOT NULL,day date NOT NULL,reason text NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(
 source text PRIMARY KEY,sequence bigint NOT NULL,observed_ms bigint NOT NULL,healthy boolean NOT NULL,
 error text);
CREATE TABLE IF NOT EXISTS fences(day date PRIMARY KEY,sequence bigint NOT NULL,observed_ms bigint NOT NULL);
CREATE TABLE IF NOT EXISTS archives(
 room text NOT NULL,day date NOT NULL,version integer NOT NULL,spec text NOT NULL,
 sha256 text NOT NULL CHECK(length(sha256)=64),bytes bigint NOT NULL,event_count bigint NOT NULL,
 first_sequence bigint,last_sequence bigint,attachment_sha256 text NOT NULL,
 sealed_at timestamptz NOT NULL DEFAULT now(),state text NOT NULL CHECK(state='sealed'),
 permission_spec text NOT NULL,fence_sequence bigint NOT NULL,
 PRIMARY KEY(room,day,version,spec));
CREATE INDEX IF NOT EXISTS archives_last_sequence ON archives(last_sequence);
CREATE TABLE IF NOT EXISTS archive_audit(
 room text NOT NULL,day date NOT NULL,version integer NOT NULL,spec text NOT NULL,
 checked_at timestamptz,error text,cleaned_at timestamptz,history_task text,history_done boolean NOT NULL DEFAULT false,
 PRIMARY KEY(room,day,version,spec), FOREIGN KEY(room,day,version,spec) REFERENCES archives);
ALTER TABLE archive_audit ADD COLUMN IF NOT EXISTS cleanup_started_at timestamptz;
CREATE TABLE IF NOT EXISTS archive_jobs(room text,day date,version integer,spec text,status text NOT NULL,error text,updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(room,day,version,spec));
CREATE OR REPLACE FUNCTION immutable_summary() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'sealed archive metadata is immutable'; END; $$;
DROP TRIGGER IF EXISTS summaries_immutable ON archives;
CREATE TRIGGER summaries_immutable BEFORE UPDATE OR DELETE ON archives FOR EACH ROW EXECUTE FUNCTION immutable_summary();
INSERT INTO migrations VALUES(1) ON CONFLICT DO NOTHING;
REVOKE ALL ON SCHEMA yichat FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA yichat FROM PUBLIC;
GRANT USAGE ON SCHEMA yichat TO yc_ingest,yc_seal,yc_read,yc_cleanup;
GRANT SELECT ON ALL TABLES IN SCHEMA yichat TO yc_ingest,yc_seal,yc_read,yc_cleanup;
GRANT INSERT ON events,memberships,attachments,fences,archive_gaps TO yc_ingest;
GRANT INSERT ON attachment_references TO yc_ingest;
GRANT UPDATE(last_ref_ms) ON attachments TO yc_ingest;
GRANT UPDATE ON fences TO yc_ingest;
GRANT INSERT,DELETE ON upload_reservations TO yc_ingest;
GRANT INSERT,UPDATE ON checkpoints TO yc_ingest;
GRANT INSERT ON archives,archive_audit TO yc_seal;
GRANT INSERT,UPDATE ON archive_jobs TO yc_seal;
GRANT UPDATE ON archive_audit TO yc_seal,yc_cleanup;
GRANT DELETE ON events,attachments TO yc_cleanup;
