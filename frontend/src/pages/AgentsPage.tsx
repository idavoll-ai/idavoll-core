import { useState, useEffect, useCallback, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { agentsApi } from '@/api/agents'
import type { AgentOut } from '@/api/types'
import { LoadingCenter, Spinner } from '@/components/ui/Spinner'

function xpForLevel(level: number) {
  return level * 100
}

export function AgentsPage() {
  const navigate = useNavigate()
  const [agents, setAgents] = useState<AgentOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Record<string, boolean>>({})

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

  const handleDelete = async (agent: AgentOut) => {
    if (!confirm(`确定删除 Agent「${agent.name}」？这会删除 workspace、progress，并将其从已加入的话题中移除。`)) {
      return
    }
    setDeleting(prev => ({ ...prev, [agent.id]: true }))
    setError(null)
    try {
      await agentsApi.remove(agent.id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeleting(prev => ({ ...prev, [agent.id]: false }))
    }
  }

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
              <AgentCard
                key={agent.id}
                agent={agent}
                onClick={() => navigate(`/agents/${agent.id}`)}
                onDelete={() => handleDelete(agent)}
                deleting={!!deleting[agent.id]}
              />
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function AgentCard({
  agent,
  onClick,
  onDelete,
  deleting = false,
}: {
  agent: AgentOut
  onClick: () => void
  onDelete: () => void
  deleting?: boolean
}) {
  const maxXp = xpForLevel(agent.level)
  const pct = Math.min(100, (agent.xp % maxXp) / maxXp * 100)
  const stop = (e: MouseEvent) => e.stopPropagation()

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
        <button
          className="btn btn-ghost btn-icon"
          onClick={(e) => {
            stop(e)
            onDelete()
          }}
          disabled={deleting}
          aria-label="删除 Agent"
          title="删除 Agent"
          style={{ color: '#dc2626' }}
        >
          {deleting ? (
            <Spinner size={14} />
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 6h18" />
              <path d="M8 6V4.8C8 3.8 8.8 3 9.8 3h4.4C15.2 3 16 3.8 16 4.8V6" />
              <path d="M18 6l-1 13a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L6 6" />
              <path d="M10 11v6" />
              <path d="M14 11v6" />
            </svg>
          )}
        </button>
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
