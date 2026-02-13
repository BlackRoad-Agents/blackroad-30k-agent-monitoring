-- BlackRoad OS - 30k Agent Monitoring Database Schema
-- Cloudflare D1 (SQLite)

-- Agent Registry
CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('repository', 'infrastructure', 'ai', 'deployment')),
  core TEXT NOT NULL CHECK(core IN ('alice', 'aria', 'octavia', 'lucidia', 'cloud')),
  capabilities TEXT, -- JSON array of capabilities
  hash TEXT NOT NULL, -- Roadchain identity hash
  created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active', 'idle', 'error', 'offline')) DEFAULT 'offline'
);

CREATE INDEX idx_agents_type ON agents(type);
CREATE INDEX idx_agents_core ON agents(core);
CREATE INDEX idx_agents_status ON agents(status);
CREATE INDEX idx_agents_last_seen ON agents(last_seen DESC);

-- Real-time Metrics (last 1 hour, then archived to R2)
CREATE TABLE IF NOT EXISTS agent_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  cpu_usage REAL NOT NULL,
  memory_usage INTEGER NOT NULL,
  tasks_completed INTEGER NOT NULL DEFAULT 0,
  tasks_active INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
);

CREATE INDEX idx_metrics_agent_time ON agent_metrics(agent_id, timestamp DESC);
CREATE INDEX idx_metrics_timestamp ON agent_metrics(timestamp DESC);
CREATE INDEX idx_metrics_status ON agent_metrics(status, timestamp DESC);

-- Agent Events (activity log)
CREATE TABLE IF NOT EXISTS agent_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  event_type TEXT NOT NULL CHECK(event_type IN ('started', 'stopped', 'error', 'task_completed', 'alert')),
  message TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'error', 'critical')) DEFAULT 'info',
  metadata TEXT, -- JSON object with additional context
  FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
);

CREATE INDEX idx_events_agent_time ON agent_events(agent_id, timestamp DESC);
CREATE INDEX idx_events_timestamp ON agent_events(timestamp DESC);
CREATE INDEX idx_events_severity ON agent_events(severity, timestamp DESC);

-- Alerts (critical issues requiring attention)
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_type TEXT NOT NULL,
  message TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('warning', 'critical')),
  agent_ids TEXT, -- JSON array of affected agent IDs
  triggered_at INTEGER NOT NULL,
  resolved_at INTEGER,
  resolved_by TEXT,
  status TEXT NOT NULL CHECK(status IN ('active', 'acknowledged', 'resolved')) DEFAULT 'active'
);

CREATE INDEX idx_alerts_status ON alerts(status, triggered_at DESC);
CREATE INDEX idx_alerts_severity ON alerts(severity, triggered_at DESC);

-- Aggregate Stats (pre-computed for performance)
CREATE TABLE IF NOT EXISTS aggregate_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL,
  window TEXT NOT NULL CHECK(window IN ('1min', '5min', '1hour', '1day')),
  total_agents INTEGER NOT NULL,
  active_agents INTEGER NOT NULL,
  idle_agents INTEGER NOT NULL,
  error_agents INTEGER NOT NULL,
  offline_agents INTEGER NOT NULL,
  avg_cpu REAL NOT NULL,
  avg_memory REAL NOT NULL,
  total_tasks INTEGER NOT NULL,
  message_rate REAL NOT NULL,
  by_core TEXT NOT NULL, -- JSON object: { alice: {...}, aria: {...}, ... }
  by_type TEXT NOT NULL  -- JSON object: { repository: N, infrastructure: N, ... }
);

CREATE UNIQUE INDEX idx_stats_time_window ON aggregate_stats(timestamp DESC, window);

-- Cleanup old data (retention policy)
-- Metrics: Keep 1 hour in D1, archive hourly to R2
-- Events: Keep 24 hours in D1, archive daily to R2
-- Alerts: Keep all (until resolved), archive resolved after 30 days
-- Aggregate stats: Keep 1 day of 1min, 7 days of 5min, 30 days of 1hour, 1 year of 1day

-- Sample query: Get current status of all agents
-- SELECT * FROM agents WHERE status = 'active' ORDER BY last_seen DESC;

-- Sample query: Get agent metrics for last 5 minutes
-- SELECT * FROM agent_metrics
-- WHERE agent_id = ? AND timestamp > (strftime('%s', 'now') - 300)
-- ORDER BY timestamp DESC;

-- Sample query: Get recent errors
-- SELECT e.*, a.name, a.core
-- FROM agent_events e
-- JOIN agents a ON e.agent_id = a.agent_id
-- WHERE e.severity IN ('error', 'critical')
-- AND e.timestamp > (strftime('%s', 'now') - 3600)
-- ORDER BY e.timestamp DESC;

-- Sample query: Get aggregate stats for dashboard
-- SELECT * FROM aggregate_stats
-- WHERE window = '1min'
-- ORDER BY timestamp DESC
-- LIMIT 60; -- Last hour
