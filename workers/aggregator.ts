/**
 * BlackRoad OS - 30k Agent Aggregation Worker
 *
 * Receives 3,000 msg/sec from NATS, aggregates to ~100 updates/sec for UI
 * Deployed to Cloudflare Workers for edge processing
 */

export interface Env {
  AGENT_METRICS: D1Database;
  AGENT_REGISTRY: KVNamespace;
  AGGREGATOR: DurableObjectNamespace;
  NATS_URL: string;
  NATS_TOKEN: string;
}

interface AgentHeartbeat {
  agent_id: string;
  type: 'repository' | 'infrastructure' | 'ai' | 'deployment';
  core: 'alice' | 'aria' | 'octavia' | 'lucidia' | 'cloud';
  status: 'active' | 'idle' | 'error' | 'offline';
  metrics: {
    cpu: number;
    memory: number;
    tasks_completed: number;
    tasks_active: number;
  };
  hash: string;
  timestamp: number;
}

interface AggregatedMetrics {
  total_agents: number;
  active_agents: number;
  idle_agents: number;
  error_agents: number;
  offline_agents: number;
  avg_cpu: number;
  avg_memory: number;
  total_tasks: number;
  message_rate: number;
  by_core: {
    [core: string]: {
      count: number;
      avg_cpu: number;
      avg_memory: number;
    };
  };
  by_type: {
    [type: string]: number;
  };
  timestamp: number;
}

export class AgentAggregator implements DurableObject {
  private state: DurableObjectState;
  private env: Env;
  private agents: Map<string, AgentHeartbeat> = new Map();
  private sockets: Set<WebSocket> = new Set();
  private aggregationInterval: number | null = null;
  private messageCount: number = 0;
  private lastAggregation: number = Date.now();

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;

    // Load agents from storage on startup
    this.state.storage.get<Map<string, AgentHeartbeat>>('agents').then(stored => {
      if (stored) {
        this.agents = stored;
      }
    });

    // Start aggregation loop
    this.startAggregation();
  }

  async fetch(request: Request): Promise<Response> {
    // Handle WebSocket upgrades for real-time UI updates
    if (request.headers.get('Upgrade') === 'websocket') {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);

      await this.handleWebSocket(server);

      return new Response(null, {
        status: 101,
        webSocket: client,
      });
    }

    // Handle HTTP requests for agent heartbeats
    if (request.method === 'POST') {
      const heartbeat: AgentHeartbeat = await request.json();

      // Verify Roadchain hash
      if (!this.verifyHash(heartbeat)) {
        return new Response('Invalid hash', { status: 401 });
      }

      // Update agent status
      this.agents.set(heartbeat.agent_id, heartbeat);
      this.messageCount++;

      // Persist to storage periodically
      if (this.messageCount % 1000 === 0) {
        await this.state.storage.put('agents', this.agents);
      }

      return new Response('OK', { status: 200 });
    }

    // GET request returns current aggregated metrics
    const metrics = this.aggregate();
    return new Response(JSON.stringify(metrics), {
      headers: { 'Content-Type': 'application/json' }
    });
  }

  private async handleWebSocket(ws: WebSocket) {
    ws.accept();
    this.sockets.add(ws);

    // Send initial state
    const initialMetrics = this.aggregate();
    ws.send(JSON.stringify({ type: 'initial', data: initialMetrics }));

    ws.addEventListener('close', () => {
      this.sockets.delete(ws);
    });

    ws.addEventListener('error', () => {
      this.sockets.delete(ws);
    });
  }

  private startAggregation() {
    // Aggregate and broadcast every 1 second
    this.aggregationInterval = setInterval(() => {
      const metrics = this.aggregate();
      this.broadcast(metrics);

      // Calculate message rate
      const now = Date.now();
      const timeDiff = (now - this.lastAggregation) / 1000;
      metrics.message_rate = this.messageCount / timeDiff;

      this.lastAggregation = now;
      this.messageCount = 0;

      // Clean up old agents (offline > 5 minutes)
      const cutoff = Date.now() - 5 * 60 * 1000;
      for (const [id, agent] of this.agents.entries()) {
        if (agent.timestamp < cutoff) {
          this.agents.delete(id);
        }
      }
    }, 1000) as any;
  }

  private aggregate(): AggregatedMetrics {
    const metrics: AggregatedMetrics = {
      total_agents: this.agents.size,
      active_agents: 0,
      idle_agents: 0,
      error_agents: 0,
      offline_agents: 0,
      avg_cpu: 0,
      avg_memory: 0,
      total_tasks: 0,
      message_rate: 0,
      by_core: {},
      by_type: {},
      timestamp: Date.now()
    };

    let totalCpu = 0;
    let totalMemory = 0;

    for (const agent of this.agents.values()) {
      // Count by status
      metrics[`${agent.status}_agents` as keyof AggregatedMetrics] =
        (metrics[`${agent.status}_agents` as keyof AggregatedMetrics] as number) + 1;

      // Aggregate metrics
      totalCpu += agent.metrics.cpu;
      totalMemory += agent.metrics.memory;
      metrics.total_tasks += agent.metrics.tasks_completed;

      // By core
      if (!metrics.by_core[agent.core]) {
        metrics.by_core[agent.core] = { count: 0, avg_cpu: 0, avg_memory: 0 };
      }
      metrics.by_core[agent.core].count++;

      // By type
      metrics.by_type[agent.type] = (metrics.by_type[agent.type] || 0) + 1;
    }

    if (this.agents.size > 0) {
      metrics.avg_cpu = totalCpu / this.agents.size;
      metrics.avg_memory = totalMemory / this.agents.size;

      // Calculate core averages
      for (const core in metrics.by_core) {
        const coreAgents = Array.from(this.agents.values())
          .filter(a => a.core === core);

        if (coreAgents.length > 0) {
          metrics.by_core[core].avg_cpu =
            coreAgents.reduce((sum, a) => sum + a.metrics.cpu, 0) / coreAgents.length;
          metrics.by_core[core].avg_memory =
            coreAgents.reduce((sum, a) => sum + a.metrics.memory, 0) / coreAgents.length;
        }
      }
    }

    return metrics;
  }

  private broadcast(metrics: AggregatedMetrics) {
    const message = JSON.stringify({ type: 'update', data: metrics });

    for (const ws of this.sockets) {
      try {
        ws.send(message);
      } catch (error) {
        this.sockets.delete(ws);
      }
    }
  }

  private verifyHash(heartbeat: AgentHeartbeat): boolean {
    // TODO: Implement Roadchain SHA-256 hash verification
    // For now, just check that hash exists and is 8 chars
    return heartbeat.hash && heartbeat.hash.length === 8;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Route to appropriate Durable Object
    const url = new URL(request.url);

    // For now, use a single aggregator instance
    // In production, shard by agent_id hash for scalability
    const id = env.AGGREGATOR.idFromName('global');
    const aggregator = env.AGGREGATOR.get(id);

    return aggregator.fetch(request);
  },
};
