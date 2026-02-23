#!/usr/bin/env python3
"""
BlackRoad 30K Agent Monitoring — Health checks, dead-agent detection,
performance metrics, load distribution analysis, and alert escalation.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── ANSI ──────────────────────────────────────────────────────────────────────
R = "\033[0;31m";  G = "\033[0;32m";  Y = "\033[1;33m"
C = "\033[0;36m";  B = "\033[0;34m";  M = "\033[0;35m"
W = "\033[1;37m";  DIM = "\033[2m";   NC = "\033[0m";  BOLD = "\033[1m"

DB_PATH = Path(os.environ.get("MON_DB", Path.home() / ".blackroad" / "monitoring.db"))

TOTAL_AGENTS   = 30_000
DEAD_THRESHOLD = 300   # seconds since last heartbeat → dead
ALERT_LEVELS   = ["info", "warning", "critical", "resolved"]


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class HealthCheck:
    check_id: str
    agent_id: str
    node_id: str
    status: str          # healthy | degraded | dead | unreachable
    heartbeat_age_s: float
    response_time_ms: float
    cpu_pct: float
    mem_pct: float
    error_count: int
    checked_at: str

    def is_dead(self) -> bool:
        return self.heartbeat_age_s > DEAD_THRESHOLD or self.status == "dead"

    def severity(self) -> str:
        if self.is_dead():
            return "critical"
        if self.cpu_pct > 90 or self.mem_pct > 90 or self.response_time_ms > 2000:
            return "warning"
        return "info"


@dataclass
class DeadAgent:
    dead_id: str
    agent_id: str
    node_id: str
    agent_type: str
    last_seen: str
    dead_since: str
    reason: str          # timeout | crash | oom | network
    recovered: bool
    recovery_attempts: int


@dataclass
class PerformanceMetric:
    metric_id: str
    agent_id: str
    node_id: str
    timestamp: str
    tasks_per_min: float
    avg_task_ms: float
    p95_task_ms: float
    p99_task_ms: float
    error_rate_pct: float
    throughput_kbps: float

    def efficiency_score(self) -> float:
        """0–100 composite efficiency score."""
        speed  = max(0.0, 100 - self.avg_task_ms / 50)
        errors = max(0.0, 100 - self.error_rate_pct * 10)
        thru   = min(100.0, self.throughput_kbps / 10)
        return round((speed * 0.4 + errors * 0.4 + thru * 0.2), 2)


@dataclass
class LoadDistribution:
    dist_id: str
    node_id: str
    total_agents: int
    active_agents: int
    queued_tasks: int
    load_pct: float
    imbalance_score: float   # 0=perfectly balanced, 100=critically skewed
    recorded_at: str

    def is_overloaded(self) -> bool:
        return self.load_pct > 90 or self.imbalance_score > 70


@dataclass
class Alert:
    alert_id: str
    source_id: str      # agent_id or node_id
    source_type: str    # agent | node | fleet
    level: str          # info | warning | critical
    title: str
    description: str
    fired_at: str
    resolved_at: str    # "" if open
    escalated: bool
    ack: bool

    def is_open(self) -> bool:
        return not self.resolved_at and not self.ack

    def age_minutes(self) -> float:
        ts = datetime.fromisoformat(self.fired_at)
        return round((datetime.utcnow() - ts).total_seconds() / 60, 1)


# ── Database ──────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS health_checks (
        check_id         TEXT PRIMARY KEY,
        agent_id         TEXT NOT NULL,
        node_id          TEXT NOT NULL,
        status           TEXT NOT NULL,
        heartbeat_age_s  REAL NOT NULL DEFAULT 0,
        response_time_ms REAL NOT NULL DEFAULT 0,
        cpu_pct          REAL NOT NULL DEFAULT 0,
        mem_pct          REAL NOT NULL DEFAULT 0,
        error_count      INTEGER NOT NULL DEFAULT 0,
        checked_at       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS dead_agents (
        dead_id            TEXT PRIMARY KEY,
        agent_id           TEXT NOT NULL,
        node_id            TEXT NOT NULL,
        agent_type         TEXT NOT NULL,
        last_seen          TEXT NOT NULL,
        dead_since         TEXT NOT NULL,
        reason             TEXT NOT NULL DEFAULT 'timeout',
        recovered          INTEGER NOT NULL DEFAULT 0,
        recovery_attempts  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS performance_metrics (
        metric_id        TEXT PRIMARY KEY,
        agent_id         TEXT NOT NULL,
        node_id          TEXT NOT NULL,
        timestamp        TEXT NOT NULL,
        tasks_per_min    REAL NOT NULL DEFAULT 0,
        avg_task_ms      REAL NOT NULL DEFAULT 0,
        p95_task_ms      REAL NOT NULL DEFAULT 0,
        p99_task_ms      REAL NOT NULL DEFAULT 0,
        error_rate_pct   REAL NOT NULL DEFAULT 0,
        throughput_kbps  REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS load_distribution (
        dist_id         TEXT PRIMARY KEY,
        node_id         TEXT NOT NULL,
        total_agents    INTEGER NOT NULL DEFAULT 0,
        active_agents   INTEGER NOT NULL DEFAULT 0,
        queued_tasks    INTEGER NOT NULL DEFAULT 0,
        load_pct        REAL NOT NULL DEFAULT 0,
        imbalance_score REAL NOT NULL DEFAULT 0,
        recorded_at     TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS alerts (
        alert_id     TEXT PRIMARY KEY,
        source_id    TEXT NOT NULL,
        source_type  TEXT NOT NULL DEFAULT 'agent',
        level        TEXT NOT NULL DEFAULT 'info',
        title        TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        fired_at     TEXT NOT NULL,
        resolved_at  TEXT NOT NULL DEFAULT '',
        escalated    INTEGER NOT NULL DEFAULT 0,
        ack          INTEGER NOT NULL DEFAULT 0
    );
    """)
    conn.commit()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _uid(p: str = "") -> str:
    import hashlib
    return p + hashlib.sha1(f"{p}{time.time_ns()}{random.random()}".encode()).hexdigest()[:10]


# ── Seed data ─────────────────────────────────────────────────────────────────

NODES  = ["octavia-pi", "lucidia-pi", "shellfish-droplet"]
ATYPES = ["worker", "reasoning", "security", "analytics", "memory"]
REASONS = ["timeout", "crash", "oom", "network"]


def seed_demo(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM health_checks").fetchone()[0] > 0:
        return
    now = _now()
    # Health checks for 50 sample agents
    for i in range(50):
        hb_age  = random.uniform(0, 600)
        status  = "dead" if hb_age > 300 else random.choice(["healthy", "degraded", "healthy"])
        node    = random.choice(NODES)
        conn.execute("INSERT INTO health_checks VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (_uid("hc"), f"agent-{i:05d}", node, status,
                      round(hb_age, 1), round(random.uniform(5, 3000), 1),
                      round(random.uniform(10, 95), 1), round(random.uniform(20, 95), 1),
                      random.randint(0, 20), now))

    # Dead agents
    for i in range(8):
        last_seen = (datetime.utcnow() - timedelta(minutes=random.randint(5, 120))).isoformat(timespec="seconds")
        conn.execute("INSERT INTO dead_agents VALUES (?,?,?,?,?,?,?,?,?)",
                     (_uid("da"), f"agent-dead-{i:03d}", random.choice(NODES),
                      random.choice(ATYPES), last_seen, now,
                      random.choice(REASONS), 0, random.randint(0, 3)))

    # Performance metrics
    for i in range(30):
        node = random.choice(NODES)
        avg_ms = random.uniform(50, 2000)
        conn.execute("INSERT INTO performance_metrics VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (_uid("pm"), f"agent-{i:05d}", node, now,
                      round(random.uniform(0.5, 100), 2),
                      round(avg_ms, 1),
                      round(avg_ms * 1.5, 1),
                      round(avg_ms * 2.2, 1),
                      round(random.uniform(0, 15), 2),
                      round(random.uniform(10, 5000), 1)))

    # Load distribution
    capacities = {"octavia-pi": 22500, "lucidia-pi": 7500, "shellfish-droplet": 0}
    for node, cap in capacities.items():
        active = int(cap * random.uniform(0.3, 0.9))
        load   = round(active / max(cap, 1) * 100, 2)
        conn.execute("INSERT INTO load_distribution VALUES (?,?,?,?,?,?,?,?)",
                     (_uid("ld"), node, cap, active, random.randint(0, 5000),
                      load, round(random.uniform(0, 60), 1), now))

    # Alerts
    for lvl, title, desc in [
        ("critical", "Dead agents detected",    "8 agents unresponsive on octavia-pi"),
        ("warning",  "High CPU on lucidia-pi",   "CPU sustained above 85% for 10 minutes"),
        ("warning",  "Task queue backlog",       "Queue depth >5000 on octavia-pi"),
        ("info",     "Routine health check",     "All primary nodes healthy"),
    ]:
        conn.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (_uid("al"), random.choice(NODES), "node", lvl, title, desc,
                      now, "", 0, 0))
    conn.commit()


# ── Core operations ───────────────────────────────────────────────────────────

def run_health_checks(conn: sqlite3.Connection) -> list[HealthCheck]:
    rows = conn.execute("""
        SELECT * FROM health_checks
        WHERE checked_at = (SELECT MAX(checked_at) FROM health_checks)
        ORDER BY heartbeat_age_s DESC
    """).fetchall()
    return [HealthCheck(**dict(r)) for r in rows]


def detect_dead_agents(conn: sqlite3.Connection, include_recovered: bool = False) -> list[DeadAgent]:
    q = "SELECT * FROM dead_agents"
    if not include_recovered:
        q += " WHERE recovered=0"
    q += " ORDER BY dead_since DESC"
    rows = conn.execute(q).fetchall()
    return [DeadAgent(**dict(r)) for r in rows]


def get_performance(conn: sqlite3.Connection, node_id: Optional[str] = None,
                    limit: int = 20) -> list[PerformanceMetric]:
    q = "SELECT * FROM performance_metrics"
    params: list = []
    if node_id:
        q += " WHERE node_id=?"; params.append(node_id)
    q += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    return [PerformanceMetric(**dict(r)) for r in rows]


def get_load_distribution(conn: sqlite3.Connection) -> list[LoadDistribution]:
    rows = conn.execute("""
        SELECT * FROM load_distribution
        WHERE recorded_at = (SELECT MAX(recorded_at) FROM load_distribution)
        ORDER BY load_pct DESC
    """).fetchall()
    return [LoadDistribution(**dict(r)) for r in rows]


def fire_alert(conn: sqlite3.Connection, source_id: str, source_type: str,
               level: str, title: str, description: str = "") -> Alert:
    now = _now()
    a = Alert(alert_id=_uid("al"), source_id=source_id, source_type=source_type,
              level=level, title=title, description=description,
              fired_at=now, resolved_at="", escalated=False, ack=False)
    conn.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (a.alert_id, a.source_id, a.source_type, a.level,
                  a.title, a.description, a.fired_at, a.resolved_at,
                  int(a.escalated), int(a.ack)))
    conn.commit()
    return a


def escalate_alerts(conn: sqlite3.Connection, age_minutes: float = 15.0) -> list[Alert]:
    """Escalate unacknowledged critical alerts older than age_minutes."""
    cutoff = (datetime.utcnow() - timedelta(minutes=age_minutes)).isoformat(timespec="seconds")
    rows = conn.execute("""
        SELECT * FROM alerts
        WHERE level='critical' AND resolved_at='' AND ack=0
          AND escalated=0 AND fired_at <= ?
    """, (cutoff,)).fetchall()
    escalated = []
    for row in rows:
        conn.execute("UPDATE alerts SET escalated=1 WHERE alert_id=?", (row["alert_id"],))
        escalated.append(Alert(**dict(row)))
    conn.commit()
    return escalated


def get_open_alerts(conn: sqlite3.Connection, level: Optional[str] = None) -> list[Alert]:
    q = "SELECT * FROM alerts WHERE resolved_at='' AND ack=0"
    params: list = []
    if level:
        q += " AND level=?"; params.append(level)
    q += " ORDER BY fired_at DESC"
    rows = conn.execute(q, params).fetchall()
    return [Alert(**dict(r)) for r in rows]


def generate_report(conn: sqlite3.Connection) -> dict:
    checks  = run_health_checks(conn)
    dead    = detect_dead_agents(conn)
    perf    = get_performance(conn, limit=100)
    load    = get_load_distribution(conn)
    alerts  = get_open_alerts(conn)
    healthy = sum(1 for c in checks if c.status == "healthy")
    avg_eff = round(sum(m.efficiency_score() for m in perf) / max(len(perf), 1), 2)
    return {
        "generated_at":    _now(),
        "total_checked":   len(checks),
        "healthy":         healthy,
        "dead":            len(dead),
        "open_alerts":     len(alerts),
        "critical_alerts": sum(1 for a in alerts if a.level == "critical"),
        "avg_efficiency":  avg_eff,
        "nodes":           [{"node_id": l.node_id, "load_pct": l.load_pct,
                             "overloaded": l.is_overloaded()} for l in load],
    }


# ── Rendering helpers ─────────────────────────────────────────────────────────

LEVEL_COL  = {"info": C,   "warning": Y,   "critical": R, "resolved": G}
STATUS_COL = {"healthy": G, "degraded": Y, "dead": R, "unreachable": M}


def _bar(v: float, width: int = 20) -> str:
    filled = int(width * min(v / 100.0, 1.0))
    col    = G if v < 70 else Y if v < 90 else R
    return f"{col}{'█' * filled}{'░' * (width - filled)}{NC} {v:.1f}%"


def _header(title: str) -> None:
    print(f"\n{B}{'─' * 64}{NC}")
    print(f"{W}{BOLD}  {title}{NC}")
    print(f"{B}{'─' * 64}{NC}")


# ── CLI handlers ──────────────────────────────────────────────────────────────

def cmd_health(args: argparse.Namespace) -> None:
    conn = get_conn(); seed_demo(conn)
    checks = run_health_checks(conn)
    if args.only_problems:
        checks = [c for c in checks if c.status != "healthy"]
    _header(f"Health Checks  [{len(checks)} agents]")
    for c in checks[:args.limit]:
        sc = STATUS_COL.get(c.status, NC)
        sv = LEVEL_COL.get(c.severity(), NC)
        print(f"  {c.agent_id:<16}  {sc}{c.status:<12}{NC}  "
              f"HB:{c.heartbeat_age_s:>6.0f}s  "
              f"RT:{c.response_time_ms:>6.0f}ms  "
              f"CPU:{_bar(c.cpu_pct, 12)}  {sv}{c.severity()}{NC}")
    print()


def cmd_dead(args: argparse.Namespace) -> None:
    conn = get_conn(); seed_demo(conn)
    dead = detect_dead_agents(conn, include_recovered=args.all)
    _header(f"Dead Agents  [{len(dead)} detected]")
    if not dead:
        print(f"  {G}✓ No dead agents.{NC}\n")
        return
    for d in dead:
        rec = f"{G}recovered{NC}" if d.recovered else f"{R}dead{NC}"
        print(f"  {R}✗{NC} {d.agent_id:<18}  node:{d.node_id:<20}  "
              f"reason:{Y}{d.reason}{NC}  attempts:{d.recovery_attempts}  {rec}")
    print()


def cmd_metrics(args: argparse.Namespace) -> None:
    conn = get_conn(); seed_demo(conn)
    metrics = get_performance(conn, node_id=args.node, limit=args.limit)
    _header(f"Performance Metrics  [{len(metrics)} records]")
    print(f"  {DIM}{'Agent':<16} {'Eff%':>5} {'TPM':>7} {'AvgMs':>7} {'p95Ms':>7} {'ErrRt%':>7}{NC}")
    print(f"  {'─' * 58}")
    for m in metrics:
        eff  = m.efficiency_score()
        ecol = G if eff >= 80 else Y if eff >= 60 else R
        print(f"  {m.agent_id:<16} {ecol}{eff:>5.1f}{NC} "
              f"{m.tasks_per_min:>7.1f} {m.avg_task_ms:>7.0f} "
              f"{m.p95_task_ms:>7.0f} {m.error_rate_pct:>7.2f}")
    print()


def cmd_load(args: argparse.Namespace) -> None:
    conn = get_conn(); seed_demo(conn)
    dists = get_load_distribution(conn)
    _header("Load Distribution")
    for d in dists:
        ol  = f" {R}[OVERLOADED]{NC}" if d.is_overloaded() else ""
        print(f"  {W}{d.node_id:<25}{NC}  {_bar(d.load_pct)}  "
              f"agents:{d.active_agents:>6,}/{d.total_agents:>6,}  "
              f"queue:{Y}{d.queued_tasks:>5,}{NC}{ol}")
    print()


def cmd_alert(args: argparse.Namespace) -> None:
    conn = get_conn(); seed_demo(conn)
    if args.fire:
        level, title = args.fire[0], " ".join(args.fire[1:])
        a = fire_alert(conn, args.source or "cli", "fleet", level, title, args.desc or "")
        _header("Alert Fired")
        print(f"  {LEVEL_COL.get(level, NC)}● {level.upper()}{NC}  {a.title}")
        print(f"  ID: {a.alert_id}  at {a.fired_at}")
    else:
        alerts = get_open_alerts(conn, level=args.level)
        _header(f"Open Alerts  [{len(alerts)}]")
        for a in alerts:
            col = LEVEL_COL.get(a.level, NC)
            esc = f" {M}[ESCALATED]{NC}" if a.escalated else ""
            print(f"  {col}●{NC} {a.level:<10} {a.title:<40} {DIM}{a.age_minutes():.1f}m{NC}{esc}")
    print()


def cmd_report(args: argparse.Namespace) -> None:
    conn = get_conn(); seed_demo(conn)
    rep = generate_report(conn)
    _header("Monitoring Report")
    hpct = round(rep["healthy"] / max(rep["total_checked"], 1) * 100, 1)
    hcol = G if hpct >= 90 else Y if hpct >= 70 else R
    print(f"  Generated    : {DIM}{rep['generated_at']}{NC}")
    print(f"  Checked      : {rep['total_checked']}")
    print(f"  Healthy      : {hcol}{rep['healthy']} ({hpct}%){NC}")
    print(f"  Dead         : {R}{rep['dead']}{NC}")
    print(f"  Open Alerts  : {Y}{rep['open_alerts']}{NC}  (critical: {R}{rep['critical_alerts']}{NC})")
    print(f"  Avg Eff Score: {G}{rep['avg_efficiency']}{NC}")
    print()
    for node in rep["nodes"]:
        ol = f" {R}⚠ OVERLOADED{NC}" if node["overloaded"] else ""
        print(f"  {node['node_id']:<25}  load {_bar(node['load_pct'], 15)}{ol}")
    print()


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="monitoring",
                                description=f"{W}BlackRoad 30K Agent Monitoring{NC}")
    sub = p.add_subparsers(dest="command", required=True)

    ph = sub.add_parser("health", help="Run health checks")
    ph.add_argument("--only-problems", action="store_true")
    ph.add_argument("--limit", type=int, default=50)
    ph.set_defaults(func=cmd_health)

    pd = sub.add_parser("dead", help="List dead agents")
    pd.add_argument("--all", action="store_true", help="Include recovered")
    pd.set_defaults(func=cmd_dead)

    pm = sub.add_parser("metrics", help="Show performance metrics")
    pm.add_argument("--node")
    pm.add_argument("--limit", type=int, default=20)
    pm.set_defaults(func=cmd_metrics)

    sub.add_parser("load", help="Show load distribution").set_defaults(func=cmd_load)

    pa = sub.add_parser("alert", help="Fire or list alerts")
    pa.add_argument("--fire", nargs="+", metavar="LEVEL TITLE")
    pa.add_argument("--source")
    pa.add_argument("--desc")
    pa.add_argument("--level", choices=ALERT_LEVELS)
    pa.set_defaults(func=cmd_alert)

    sub.add_parser("report", help="Generate monitoring report").set_defaults(func=cmd_report)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
