
-- ----------------------------------------------------------------------------
-- Tables for storing update information
-- ----------------------------------------------------------------------------

-- create db_version table
CREATE TABLE IF NOT EXISTS db_version (
  name TEXT NOT NULL,
  version INT NOT NULL,
  PRIMARY KEY (name)
) TABLESPACE pg_default;

-- db upgrade log
CREATE TABLE IF NOT EXISTS db_upgrade_log (
  id SERIAL,
  version INT NOT NULL,
  status TEXT NOT NULL,
  script TEXT,
  returncode INT,
  stdout TEXT,
  stderr TEXT,
  last_updated TIMESTAMP WITH TIME ZONE NOT NULL
) TABLESPACE pg_default;

-- set_last_updated
CREATE OR REPLACE FUNCTION set_last_updated()
  RETURNS TRIGGER AS
$set_last_updated$
  BEGIN
    IF (TG_OP = 'UPDATE') OR
       NEW.last_updated IS NULL THEN
      NEW.last_updated := CURRENT_TIMESTAMP;
    END IF;
    RETURN NEW;
  END;
$set_last_updated$
  LANGUAGE 'plpgsql';

CREATE TRIGGER db_upgrade_log_set_last_updated
  BEFORE INSERT OR UPDATE ON db_upgrade_log
  FOR EACH ROW EXECUTE PROCEDURE set_last_updated();


GRANT SELECT ON db_version to vmaas_reader;
GRANT SELECT ON db_upgrade_log to vmaas_reader;
GRANT USAGE, SELECT ON db_upgrade_log_id_seq TO vmaas_reader;

GRANT SELECT ON db_version to vmaas_writer;
GRANT SELECT ON db_upgrade_log to vmaas_writer;
GRANT USAGE, SELECT ON db_upgrade_log_id_seq TO vmaas_writer;
