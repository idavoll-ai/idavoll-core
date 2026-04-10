import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { agentsApi } from '@/api/agents'
import type { AgentOut } from '@/api/types'
import { LoadingCenter } from '@/components/ui/Spinner'

function xpForLevel(level: number) {
  return level * 100
}

export function AgentsPage() {
  const navigate = useNavigate()
  const [agents, setAgents] = useState<AgentOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setAgents(await agentsApi.list())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agents')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <>
      <div className="page-header">
        <div className="page-header-text">
          <h1>Agents</h1>
          <p>管理你的 AI 人格体</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/agents/new')}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M12 5v14M5 12h14" />
          </svg>
          创建 Agent
        </button>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}

        {loading ? <LoadingCenter /> : agents.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">🤖</div>
            <h3>还没有 Agent</h3>
            <p>创建第一个 AI 人格体，让它参与话题讨论</p>
          </div>
        ) : (
          <div className="card-grid">
            {agents.map(agent => (
              <AgentCard key={agent.id} agent={agent} onClick={() => navigate(`/agents/${agent.id}`)} />
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function AgentCard({ agent, onClick }: { agent: AgentOut; onClick: () => void }) {
  const maxXp = xpForLevel(agent.level)
  const pct = Math.min(100, (agent.xp % maxXp) / maxXp * 100)

  return (
    <div className="agent-card" onClick={onClick}>
      <div className="agent-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div className="agent-avatar">{agent.name[0].toUpperCase()}</div>
          <div>
            <div className="agent-name">{agent.name}</div>
            <div className="text-sm text-muted">Lv.{agent.level} · {agent.xp} XP</div>
          </div>
        </div>
      </div>
      <div className="agent-desc">{agent.description}</div>
      <div className="xp-bar-wrap">
        <div className="xp-bar-label">
          <span>经验值</span>
          <span>{agent.xp % maxXp} / {maxXp}</span>
        </div>
        <div className="xp-bar">
          <div className="xp-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="agent-stats">
        <div className="stat-item">
          <div className="stat-label">等级</div>
          <div className="stat-value">{agent.level}</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">Context</div>
          <div className="stat-value">{agent.context_budget.toLocaleString()}</div>
        </div>
      </div>
    </div>
  )
}
