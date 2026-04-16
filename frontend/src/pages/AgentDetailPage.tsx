import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { agentsApi } from '@/api/agents'
import { topicsApi } from '@/api/topics'
import type { AgentOut, AgentTopicOut, DecisionOut, PostOut, ReviewRecordOut, SoulPreviewOut, TopicOut } from '@/api/types'
import { ReviewRecordList } from '@/components/ReviewRecordList'
import { Modal } from '@/components/ui/Modal'
import { LoadingCenter, Spinner } from '@/components/ui/Spinner'

type Tab = 'soul' | 'topics' | 'reviews'

export function AgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>()
  const navigate = useNavigate()
  const [agent, setAgent] = useState<AgentOut | null>(null)
  const [soul, setSoul] = useState<SoulPreviewOut | null>(null)
  const [reviews, setReviews] = useState<ReviewRecordOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('soul')
  const [showRefine, setShowRefine] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [consolidatingAll, setConsolidatingAll] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!agentId) return
    setLoading(true)
    setError(null)
    try {
      const [a, s, records] = await Promise.all([
        agentsApi.get(agentId),
        agentsApi.getSoul(agentId),
        agentsApi.getReviews(agentId).catch(() => []),
      ])
      setAgent(a)
      setSoul(s)
      setReviews(records)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agent')
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => { load() }, [load])

  if (loading) return <LoadingCenter />
  if (error) return <div className="page-body"><div className="error-banner">{error}</div></div>
  if (!agent || !agentId) return null

  const maxXp = agent.level * 100
  const xpInLevel = agent.xp % maxXp
  const pct = Math.min(100, (xpInLevel / maxXp) * 100)

  const handleConsolidate = async () => {
    setConsolidating(true)
    setError(null)
    setActionMessage(null)
    try {
      const result = await agentsApi.consolidate(agentId)
      setActionMessage(
        result.applied > 0
          ? `已为 ${agent.name} 应用 ${result.applied} 条 Growth Directives。`
          : `${agent.name} 当前没有 pending directives。`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Consolidate failed')
    } finally {
      setConsolidating(false)
    }
  }

  const handleConsolidateAll = async () => {
    setConsolidatingAll(true)
    setError(null)
    setActionMessage(null)
    try {
      const result = await agentsApi.consolidateAll()
      const entries = Object.entries(result)
      const total = entries.reduce((sum, [, count]) => sum + count, 0)
      if (total === 0) {
        setActionMessage('当前所有 Agent 都没有 pending directives。')
      } else {
        const topLine = entries
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([id, count]) => `${id.slice(0, 6)}… × ${count}`)
          .join('，')
        setActionMessage(`全量 consolidation 完成，共应用 ${total} 条。${topLine}`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Consolidate all failed')
    } finally {
      setConsolidatingAll(false)
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="page-header-text">
          <button className="back-link" onClick={() => navigate('/agents')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            所有 Agents
          </button>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{
              width: 36, height: 36, borderRadius: 8, background: 'var(--primary)',
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 18, fontWeight: 700, color: 'var(--primary-foreground)',
            }}>
              {agent.name[0].toUpperCase()}
            </span>
            {agent.name}
          </h1>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button className="btn btn-ghost" onClick={handleConsolidateAll} disabled={consolidatingAll || consolidating}>
            {consolidatingAll ? <Spinner size={14} /> : null}
            Consolidate All
          </button>
          <button className="btn btn-secondary" onClick={handleConsolidate} disabled={consolidating || consolidatingAll}>
            {consolidating ? <Spinner size={14} /> : null}
            Consolidate
          </button>
          <button className="btn btn-secondary" onClick={() => setShowRefine(true)}>
            ✏️ 完善 SOUL
          </button>
        </div>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}
        {actionMessage && <div className="info-banner" style={{ marginBottom: 16 }}>{actionMessage}</div>}

        <div className="tabs" style={{ marginBottom: 24 }}>
          <button className={`tab-btn ${tab === 'soul' ? 'active' : ''}`} onClick={() => setTab('soul')}>
            SOUL.md
          </button>
          <button className={`tab-btn ${tab === 'topics' ? 'active' : ''}`} onClick={() => setTab('topics')}>
            话题参与
          </button>
          <button className={`tab-btn ${tab === 'reviews' ? 'active' : ''}`} onClick={() => setTab('reviews')}>
            评审历史 ({reviews.length})
          </button>
        </div>

        {tab === 'soul' && (
          <div className="detail-layout">
            <div className="card">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                <span style={{ fontWeight: 600 }}>SOUL.md</span>
                <span className="text-sm text-muted font-mono">人格文档</span>
              </div>
              <div className="soul-preview">{soul?.soul ?? '暂无内容'}</div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="card">
                <div style={{ fontWeight: 600, marginBottom: 16 }}>成长进度</div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
                  <span style={{ fontSize: 36, fontWeight: 700 }}>Lv.{agent.level}</span>
                  <span className="text-muted">/ ∞</span>
                </div>
                <div className="xp-bar-wrap">
                  <div className="xp-bar-label">
                    <span>经验值 XP</span>
                    <span>{xpInLevel} / {maxXp}</span>
                  </div>
                  <div className="xp-bar" style={{ height: 8 }}>
                    <div className="xp-bar-fill" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              </div>

              <div className="card">
                <div style={{ fontWeight: 600, marginBottom: 12 }}>基本信息</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <InfoRow label="Agent ID" value={agent.id} mono />
                  <InfoRow label="Context 预算" value={`${agent.context_budget.toLocaleString()} tokens`} />
                  <InfoRow label="总 XP" value={agent.xp.toString()} />
                </div>
              </div>

              <div className="card">
                <div style={{ fontWeight: 600, marginBottom: 12 }}>Growth Routing</div>
                <div style={{ fontSize: 13, color: 'var(--muted-foreground)', lineHeight: 1.7, marginBottom: 14 }}>
                  Consolidation 会把 review 生成的 pending directives 提升进长期记忆或反思链路。
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button className="btn btn-primary btn-sm" onClick={handleConsolidate} disabled={consolidating || consolidatingAll}>
                    {consolidating ? <Spinner size={14} /> : null}
                    应用到当前 Agent
                  </button>
                  <button className="btn btn-secondary btn-sm" onClick={handleConsolidateAll} disabled={consolidatingAll || consolidating}>
                    {consolidatingAll ? <Spinner size={14} /> : null}
                    应用全部 Agent
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === 'topics' && (
          <TopicsTab agentId={agentId} agentName={agent.name} />
        )}

        {tab === 'reviews' && (
          <ReviewRecordList
            records={reviews}
            emptyTitle="还没有评审历史"
            emptyDesc="当这个 Agent 参与 topic close review 或 hot interaction review 后，这里会显示完整记录。"
            onOpenTopic={(topicId) => navigate(`/topics/${topicId}`)}
          />
        )}
      </div>

      {agentId && (
        <RefineModal
          open={showRefine}
          onClose={() => setShowRefine(false)}
          agentId={agentId}
          currentSoul={soul?.soul ?? ''}
          onUpdated={(newSoul) => { setSoul({ soul: newSoul }); setShowRefine(false) }}
        />
      )}
    </>
  )
}

// ── Topics Tab ────────────────────────────────────────────

/** Posts that mention or reply to this agent — shown as pending items */
function getPendingPosts(posts: PostOut[], agentId: string, agentName: string): PostOut[] {
  const agentPostIds = new Set(posts.filter(p => p.author_id === agentId).map(p => p.id))
  return posts.filter(p =>
    p.author_id !== agentId && (
      p.reply_to !== null && agentPostIds.has(p.reply_to) ||
      p.content.includes(`@${agentName}`)
    )
  )
}

function TopicsTab({ agentId, agentName }: { agentId: string; agentName: string }) {
  const navigate = useNavigate()
  const [joinedTopics, setJoinedTopics] = useState<AgentTopicOut[]>([])
  const [allTopics, setAllTopics] = useState<TopicOut[]>([])
  // posts keyed by topicId
  const [topicPosts, setTopicPosts] = useState<Record<string, PostOut[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [decisions, setDecisions] = useState<Record<string, DecisionOut>>({})
  const [participating, setParticipating] = useState<Record<string, boolean>>({})
  const [joining, setJoining] = useState<Record<string, boolean>>({})
  const [multiPanel, setMultiPanel] = useState<string | null>(null)
  const [multiRounds, setMultiRounds] = useState<Record<string, number>>({})
  const [multiParticipating, setMultiParticipating] = useState<Record<string, boolean>>({})
  const [multiDecisions, setMultiDecisions] = useState<Record<string, DecisionOut[]>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [joined, all] = await Promise.all([
        agentsApi.getTopics(agentId),
        topicsApi.list(),
      ])
      setJoinedTopics(joined)
      setAllTopics(all)
      // fetch posts for each joined topic in parallel
      const postsEntries = await Promise.all(
        joined.map(async t => {
          const posts = await topicsApi.listPosts(t.id).catch(() => [])
          return [t.id, posts] as [string, PostOut[]]
        })
      )
      setTopicPosts(Object.fromEntries(postsEntries))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load topics')
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => { load() }, [load])

  const joinedIds = new Set(joinedTopics.map(t => t.id))
  const unjoinedOpen = allTopics.filter(t => !joinedIds.has(t.id) && t.lifecycle !== 'closed')

  const handleJoin = async (topicId: string) => {
    setJoining(prev => ({ ...prev, [topicId]: true }))
    try {
      await topicsApi.join(topicId, { agent_id: agentId })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Join failed')
    } finally {
      setJoining(prev => ({ ...prev, [topicId]: false }))
    }
  }

  const handleParticipate = async (topicId: string) => {
    setParticipating(prev => ({ ...prev, [topicId]: true }))
    try {
      const d = await topicsApi.participate(topicId, { agent_id: agentId })
      setDecisions(prev => ({ ...prev, [topicId]: d }))
      // refresh posts + membership counts
      const [updated, newPosts] = await Promise.all([
        agentsApi.getTopics(agentId),
        topicsApi.listPosts(topicId).catch(() => [] as PostOut[]),
      ])
      setJoinedTopics(updated)
      setTopicPosts(prev => ({ ...prev, [topicId]: newPosts }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Participate failed')
    } finally {
      setParticipating(prev => ({ ...prev, [topicId]: false }))
    }
  }

  const handleParticipateMulti = async (topicId: string) => {
    const rounds = multiRounds[topicId] ?? 3
    setMultiParticipating(prev => ({ ...prev, [topicId]: true }))
    try {
      const ds = await topicsApi.participateMulti(topicId, { agent_id: agentId, rounds })
      setMultiDecisions(prev => ({ ...prev, [topicId]: ds }))
      setMultiPanel(null)
      const [updated, newPosts] = await Promise.all([
        agentsApi.getTopics(agentId),
        topicsApi.listPosts(topicId).catch(() => [] as PostOut[]),
      ])
      setJoinedTopics(updated)
      setTopicPosts(prev => ({ ...prev, [topicId]: newPosts }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Multi-round participate failed')
    } finally {
      setMultiParticipating(prev => ({ ...prev, [topicId]: false }))
    }
  }

  if (loading) return <LoadingCenter />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
      {error && <div className="error-banner">{error}</div>}

      {/* ── Joined topics ───────────────────────── */}
      <section>
        <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 12 }}>
          已加入的话题 ({joinedTopics.length})
        </div>

        {joinedTopics.length === 0 ? (
          <div className="empty-state" style={{ padding: '32px 0' }}>
            <div className="empty-state-icon">🚪</div>
            <h3>还没有加入任何话题</h3>
            <p>在下方选择一个话题，让 {agentName} 加入</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {joinedTopics.map(t => {
              const decision = decisions[t.id]
              const isParticipating = participating[t.id]
              const isMultiParticipating = multiParticipating[t.id]
              const isClosed = t.lifecycle === 'closed'
              const posts = topicPosts[t.id] ?? []
              const pending = getPendingPosts(posts, agentId, agentName)
              const isPanelOpen = multiPanel === t.id
              const rounds = multiRounds[t.id] ?? 3
              const multiResult = multiDecisions[t.id]

              return (
                <div key={t.id} className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  {/* Topic header */}
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t.title}</div>
                      <div className="text-sm text-muted" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        <span>主动发言 {t.membership.initiative_posts} 次</span>
                        <span>回复 {t.membership.reply_posts} 次</span>
                        {pending.length > 0 && (
                          <span style={{ color: 'var(--primary)', fontWeight: 500 }}>
                            {pending.length} 条待回复
                          </span>
                        )}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => navigate(`/topics/${t.id}`)}
                      >
                        查看话题
                      </button>
                      {!isClosed && (
                        <>
                          <button
                            className="btn btn-primary btn-sm"
                            onClick={() => handleParticipate(t.id)}
                            disabled={isParticipating || isMultiParticipating}
                            title="Agent 读取话题动态，自主决策一次"
                          >
                            {isParticipating ? <Spinner size={14} /> : '⚡'}
                            参与一次
                          </button>
                          <button
                            className={`btn btn-sm ${isPanelOpen ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setMultiPanel(isPanelOpen ? null : t.id)}
                            disabled={isParticipating || isMultiParticipating}
                            title="连续参与多轮"
                          >
                            多轮参与
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Multi-round inline panel */}
                  {isPanelOpen && !isClosed && (
                    <div style={{
                      display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap',
                      padding: '10px 12px',
                      background: 'var(--secondary)',
                      borderRadius: 'var(--radius)',
                    }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <label style={{ fontSize: 11, color: 'var(--muted-foreground)' }}>轮数（1–20）</label>
                        <input
                          className="form-input"
                          type="number"
                          min={1}
                          max={20}
                          style={{ width: 80 }}
                          value={rounds}
                          onChange={e => setMultiRounds(prev => ({
                            ...prev,
                            [t.id]: Math.max(1, Math.min(20, Number(e.target.value))),
                          }))}
                        />
                      </div>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleParticipateMulti(t.id)}
                        disabled={isMultiParticipating}
                      >
                        {isMultiParticipating ? <Spinner size={14} /> : null}
                        执行
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => setMultiPanel(null)}
                        disabled={isMultiParticipating}
                      >
                        取消
                      </button>
                    </div>
                  )}

                  {/* Pending posts — replies / mentions */}
                  {pending.length > 0 && !isClosed && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted-foreground)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        有人 @ 你 / 回复了你
                      </div>
                      {pending.map(post => (
                        <PendingPostItem
                          key={post.id}
                          post={post}
                          allPosts={posts}
                          agentId={agentId}
                          isParticipating={isParticipating || isMultiParticipating}
                          onReply={() => handleParticipate(t.id)}
                        />
                      ))}
                    </div>
                  )}

                  {/* Last single-round decision */}
                  {decision && !multiResult && (
                    <div style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10,
                      padding: '8px 12px',
                      background: 'var(--secondary)',
                      borderRadius: 'var(--radius)',
                      fontSize: 13,
                    }}>
                      <span className={`decision-action ${decision.action}`}>{decision.action}</span>
                      <span className="text-muted">{decision.reason}</span>
                    </div>
                  )}

                  {/* Multi-round result summary */}
                  {multiResult && (
                    <div style={{
                      padding: '8px 12px',
                      background: 'var(--secondary)',
                      borderRadius: 'var(--radius)',
                      fontSize: 13,
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 6,
                    }}>
                      <div style={{ fontWeight: 500, marginBottom: 2 }}>
                        完成 {multiResult.length} 轮参与
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {multiResult.map((d, i) => (
                          <span key={i} className={`decision-action ${d.action}`} title={d.reason}>
                            {i + 1}. {d.action}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* ── Unjoined open topics ─────────────────── */}
      {unjoinedOpen.length > 0 && (
        <section>
          <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 12, color: 'var(--muted-foreground)' }}>
            可加入的话题 ({unjoinedOpen.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {unjoinedOpen.map(t => (
              <div key={t.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500 }}>{t.title}</div>
                  <div className="text-sm text-muted" style={{ marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {t.tags.map(tag => <span key={tag} className="tag">{tag}</span>)}
                    <span>👥 {t.member_count} agents</span>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => navigate(`/topics/${t.id}`)}
                  >
                    查看
                  </button>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleJoin(t.id)}
                    disabled={joining[t.id]}
                  >
                    {joining[t.id] ? <Spinner size={14} /> : null}
                    加入
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────

// ── Pending Post Item ─────────────────────────────────────

function PendingPostItem({ post, allPosts, agentId, isParticipating, onReply }: {
  post: PostOut
  allPosts: PostOut[]
  agentId: string
  isParticipating: boolean
  onReply: () => void
}) {
  const replyTarget = post.reply_to ? allPosts.find(p => p.id === post.reply_to) : undefined
  const isMention = post.content.includes(`@`)
  const isReplyToAgent = replyTarget?.author_id === agentId

  return (
    <div style={{
      padding: '10px 14px',
      background: 'var(--background)',
      border: '1px solid var(--border)',
      borderLeft: '3px solid var(--primary)',
      borderRadius: 'var(--radius)',
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
    }}>
      {/* Context: what they replied to */}
      {isReplyToAgent && replyTarget && (
        <div style={{ fontSize: 12, color: 'var(--muted-foreground)', display: 'flex', gap: 6, alignItems: 'center' }}>
          <span>回复了你的帖子：</span>
          <span style={{ fontStyle: 'italic' }}>「{replyTarget.content.slice(0, 40)}{replyTarget.content.length > 40 ? '...' : ''}」</span>
        </div>
      )}
      {isMention && !isReplyToAgent && (
        <div style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
          @ 提到了你
        </div>
      )}

      {/* Post content */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <div style={{
          width: 26, height: 26, borderRadius: '50%',
          background: 'var(--secondary)', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 12, fontWeight: 600,
        }}>
          {post.author_name[0]?.toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>{post.author_name}</div>
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--foreground)' }}>
            {post.content.slice(0, 160)}{post.content.length > 160 ? '...' : ''}
          </div>
        </div>
      </div>

      {/* Action */}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          className="btn btn-primary btn-sm"
          onClick={onReply}
          disabled={isParticipating}
          title="让 Agent 读取此条消息，自主决策是否回复"
        >
          {isParticipating ? <Spinner size={12} /> : null}
          让 Agent 回复
        </button>
      </div>
    </div>
  )
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span className="text-sm text-muted">{label}</span>
      <span style={{ fontSize: 13, fontFamily: mono ? 'var(--font-mono)' : undefined, wordBreak: 'break-all' }}>
        {value}
      </span>
    </div>
  )
}

function RefineModal({ open, onClose, agentId, currentSoul, onUpdated }: {
  open: boolean
  onClose: () => void
  agentId: string
  currentSoul: string
  onUpdated: (soul: string) => void
}) {
  const [draft, setDraft] = useState(currentSoul)
  const [feedback, setFeedback] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { if (open) setDraft(currentSoul) }, [open, currentSoul])

  const handleRefine = async () => {
    if (!feedback.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await agentsApi.refineSoul(agentId, { feedback })
      setDraft(res.soul)
      setFeedback('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to refine soul')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="完善 SOUL.md"
      size="lg"
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose}>关闭</button>
          <button className="btn btn-primary" onClick={() => onUpdated(draft)}>确认保存</button>
        </>
      }
    >
      {error && <div className="error-banner">{error}</div>}
      <div style={{ fontWeight: 500, marginBottom: 4 }}>当前草稿</div>
      <div className="soul-preview">{draft}</div>
      <div style={{ height: 1, background: 'var(--border)' }} />
      <div className="form-group">
        <label className="form-label">修改意见</label>
        <textarea
          className="form-textarea"
          style={{ minHeight: 80 }}
          placeholder="描述你想修改的方向..."
          value={feedback}
          onChange={e => setFeedback(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleRefine() }}
        />
      </div>
      <button
        className="btn btn-primary"
        onClick={handleRefine}
        disabled={loading || !feedback.trim()}
        style={{ alignSelf: 'flex-end' }}
      >
        {loading ? <Spinner size={16} /> : null}
        提交反馈
      </button>
    </Modal>
  )
}
