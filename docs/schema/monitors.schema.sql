CREATE TABLE monitor_runs (
	id INTEGER NOT NULL, 
	monitor_name VARCHAR(255) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	duration_seconds FLOAT, 
	message TEXT, 
	alert_count INTEGER NOT NULL, 
	report_count INTEGER NOT NULL, 
	error_count INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_monitor_runs_monitor_name ON monitor_runs (monitor_name);
CREATE TABLE monitor_test_results (
	id INTEGER NOT NULL, 
	run_id INTEGER NOT NULL, 
	monitor_name VARCHAR(255) NOT NULL, 
	test_key VARCHAR(512) NOT NULL, 
	test_type VARCHAR(128) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	message TEXT, 
	value TEXT, 
	error TEXT, 
	duration_seconds FLOAT, 
	result_json TEXT, 
	recorded_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_monitor_test_results_run_id ON monitor_test_results (run_id);
CREATE INDEX ix_monitor_test_results_monitor_name ON monitor_test_results (monitor_name);
CREATE INDEX ix_monitor_test_results_test_key ON monitor_test_results (test_key);
CREATE TABLE monitor_state (
	id INTEGER NOT NULL, 
	monitor_name VARCHAR(255) NOT NULL, 
	last_run_at DATETIME, 
	last_status VARCHAR(32), 
	last_run_id INTEGER, 
	next_run_at DATETIME, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_monitor_state_monitor_name UNIQUE (monitor_name)
);
CREATE TABLE email_send_log (
	id INTEGER NOT NULL, 
	recipient VARCHAR(320) NOT NULL, 
	monitor_name VARCHAR(255), 
	subject TEXT, 
	sent_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_email_send_log_sent_at ON email_send_log (sent_at);
CREATE INDEX ix_email_send_log_recipient ON email_send_log (recipient);
CREATE TABLE notification_state (
	id INTEGER NOT NULL, 
	monitor_name VARCHAR(255) NOT NULL, 
	contact_type VARCHAR(32) NOT NULL, 
	last_sent_at DATETIME NOT NULL, 
	last_status VARCHAR(32), 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_notification_state_monitor_type UNIQUE (monitor_name, contact_type)
);
CREATE INDEX ix_notification_state_monitor_name ON notification_state (monitor_name);
