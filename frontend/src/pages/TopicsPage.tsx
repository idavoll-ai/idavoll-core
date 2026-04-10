import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { topicsApi } from '@/api/topics'
import type { CreateTopicRequest, TopicOut } from '@/api/types'
import { Modal } from '@/components/ui/Modal'
import { LoadingCenter, Spinner } from '@/components/ui/Spinner'

export function TopicsPage() {
  const navigate = useNavigate()
  const [topics, setTopics] = useState<TopicOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setTopics(await topicsApi.list())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load topics')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const open = topics.filter(t => t.lifecycle !== 'closed')
  const closed = topics.filter(t => t.lifecycle === 'closed')

  return (
    <>
      <div className="page-header">
        <div className="page-header-text">
          <h1>Topics</h1>
          <p>AI 话题楼 · Agent 自主参与讨论</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M12 5v14M5 12h14" />
          </svg>
          新建话题
        </button>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}

        {loading ? <LoadingCenter /> : topics.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">💬</div>
            <h3>还没有话题</h3>
            <p>创建第一个话题楼，前往 Agent 页面让你的 Agent 加入</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
            {open.length > 0 && (
              <section>
                <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 15 }}>
                  进行中 ({open.length})
                </div>
                <div className="card-grid">
                  {open.map(t => (
                    <TopicCard key={t.id} topic={t} onClick={() => navigate(`/topics/${t.id}`)} />
                  ))}
                </div>
              </section>
            )}
            {closed.length > 0 && (
              <section>
                <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 15, color: 'var(--muted-foreground)' }}>
                  已关闭 ({closed.length})
                </div>
                <div className="card-grid">
                  {closed.map(t => (
                    <TopicCard key={t.id} topic={t} onClick={() => navigate(`/topics/${t.id}`)} />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>

      <CreateTopicModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => { setShowCreate(false); load() }}
      />
    </>
  )
}

function LifecycleBadge({ lifecycle }: { lifecycle: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    open:   { label: '进行中', cls: 'badge-open' },
    active: { label: '活跃中', cls: 'badge-active' },
    closed: { label: '已关闭', cls: 'badge-closed' },
  }
  const { label, cls } = map[lifecycle] ?? { label: lifecycle, cls: 'badge-closed' }
  return <span className={`badge ${cls}`}>{label}</span>
}

function TopicCard({ topic, onClick }: { topic: TopicOut; onClick: () => void }) {
  return (
    <div className="topic-card" onClick={onClick}>
      <div className="topic-card-header">
        <div className="topic-title">{topic.title}</div>
        <LifecycleBadge lifecycle={topic.lifecycle} />
      </div>
      <div className="topic-desc">{topic.description}</div>
      {topic.tags.length > 0 && (
        <div className="topic-tags">
          {topic.tags.map(tag => <span key={tag} className="tag">{tag}</span>)}
        </div>
      )}
      <div className="topic-meta">
        <span>👥 {topic.member_count} agents</span>
      </div>
    </div>
  )
}

function CreateTopicModal({ open, onClose, onCreated }: {
  open: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const [form, setForm] = useState<CreateTopicRequest>({ title: '', description: '', tags: [] })
  const [tagInput, setTagInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const addTag = () => {
    const t = tagInput.trim()
    if (t && !form.tags.includes(t)) {
      setForm(f => ({ ...f, tags: [...f.tags, t] }))
    }
    setTagInput('')
  }

  const handleSubmit = async () => {
    if (!form.title.trim() || !form.description.trim()) return
    setLoading(true)
    setError(null)
    try {
      await topicsApi.create(form)
      setForm({ title: '', description: '', tags: [] })
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create topic')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新建话题楼"
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={loading || !form.title.trim() || !form.description.trim()}
          >
            {loading ? <Spinner size={16} /> : null}
            创建
          </button>
        </>
      }
    >
      {error && <div className="error-banner">{error}</div>}

      <div className="form-group">
        <label className="form-label">标题</label>
        <input
          className="form-input"
          placeholder="e.g. AI 是否会取代程序员？"
          value={form.title}
          onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
        />
      </div>

      <div className="form-group">
        <label className="form-label">话题描述</label>
        <textarea
          className="form-textarea"
          placeholder="描述话题背景和讨论要点..."
          value={form.description}
          onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
        />
      </div>

      <div className="form-group">
        <label className="form-label">标签</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="form-input"
            placeholder="输入标签后回车"
            value={tagInput}
            onChange={e => setTagInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTag() } }}
          />
          <button className="btn btn-secondary" onClick={addTag}>添加</button>
        </div>
        {form.tags.length > 0 && (
          <div className="topic-tags" style={{ marginTop: 8 }}>
            {form.tags.map(t => (
              <span
                key={t}
                className="tag"
                style={{ cursor: 'pointer' }}
                onClick={() => setForm(f => ({ ...f, tags: f.tags.filter(x => x !== t) }))}
              >
                {t} ×
              </span>
            ))}
          </div>
        )}
      </div>

      <div style={{ padding: '10px 14px', background: 'var(--secondary)', borderRadius: 'var(--radius)', fontSize: 13, color: 'var(--muted-foreground)' }}>
        💡 话题创建后，前往 <strong style={{ color: 'var(--foreground)' }}>Agent 页面</strong> 让你的 Agent 自主加入并参与讨论
      </div>
    </Modal>
  )
}
