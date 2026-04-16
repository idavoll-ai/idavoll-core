import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { topicsApi } from '@/api/topics'
import type { DecisionOut, PostOut, ReviewRecordOut, TopicOut, TopicReviewSummaryOut } from '@/api/types'
import { ReviewRecordList } from '@/components/ReviewRecordList'
import { LoadingCenter, Spinner } from '@/components/ui/Spinner'

export function TopicDetailPage() {
  const { topicId } = useParams<{ topicId: string }>()
  const navigate = useNavigate()

  const [topic, setTopic] = useState<TopicOut | null>(null)
  const [posts, setPosts] = useState<PostOut[]>([])
  const [review, setReview] = useState<TopicReviewSummaryOut | null>(null)
  const [reviewRecords, setReviewRecords] = useState<ReviewRecordOut[]>([])

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'posts' | 'review' | 'history'>('posts')
  const [closingLoading, setClosingLoading] = useState(false)
  const [reopeningLoading, setReopeningLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [roundLoading, setRoundLoading] = useState(false)
  const [multiPanel, setMultiPanel] = useState(false)
  const [multiAgentId, setMultiAgentId] = useState('')
  const [multiRounds, setMultiRounds] = useState(3)
  const [multiLoading, setMultiLoading] = useState(false)
  const [liking, setLiking] = useState<Record<string, boolean>>({})
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const bottomRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async (mode: 'full' | 'refresh' = 'full') => {
    if (!topicId) return
    if (mode === 'full') {
      setLoading(true)
    } else {
      setRefreshing(true)
    }
    setError(null)
    try {
      const [t, p, records] = await Promise.all([
        topicsApi.get(topicId),
        topicsApi.listPosts(topicId),
        topicsApi.getReviewRecords(topicId).catch(() => []),
      ])
      setTopic(t)
      setPosts(p)
      setReviewRecords(records)
      let nextReview: TopicReviewSummaryOut | null = null
      if (t.lifecycle === 'closed') {
        try {
          nextReview = await topicsApi.getReview(topicId)
        } catch {}
        if (mode === 'full') setTab('review')
      }
      setReview(nextReview)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load topic')
    } finally {
      if (mode === 'full') {
        setLoading(false)
      } else {
        setRefreshing(false)
      }
    }
  }, [topicId])

  useEffect(() => { load('full') }, [load])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [posts.length])

  if (loading) return <LoadingCenter />
  if (error) return <div className="page-body"><div className="error-banner">{error}</div></div>
  if (!topic || !topicId) return null

  const isClosed = topic.lifecycle === 'closed'
  const hotReviewByPost = buildHotReviewMap(reviewRecords)

  const lifecycleLabel: Record<string, string> = {
    open: '进行中', active: '活跃中', closed: '已关闭',
  }
  const lifecycleCls: Record<string, string> = {
    open: 'badge-open', active: 'badge-active', closed: 'badge-closed',
  }

  const handleClose = async () => {
    if (!confirm('确定关闭话题并触发 LLM 评审？此操作不可撤销。')) return
    setClosingLoading(true)
    try {
      const r = await topicsApi.close(topicId)
      setReview(r)
      setReviewRecords(await topicsApi.getReviewRecords(topicId).catch(() => []))
      setTopic(prev => prev ? { ...prev, lifecycle: 'closed' } : prev)
      setTab('review')
      setActionMessage('话题已关闭，正式评审结果已生成。')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Close failed')
    } finally {
      setClosingLoading(false)
    }
  }

  const handleReopen = async () => {
    setReopeningLoading(true)
    try {
      const reopened = await topicsApi.reopen(topicId)
      setTopic(reopened)
      setReview(null)
      setReviewRecords(await topicsApi.getReviewRecords(topicId).catch(() => []))
      setTab('posts')
      setPosts(await topicsApi.listPosts(topicId))
      setActionMessage('话题已重新开放，可以继续讨论。')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reopen failed')
    } finally {
      setReopeningLoading(false)
    }
  }

  const handleRefresh = async () => {
    await load('refresh')
  }

  const handleRunRound = async () => {
    if (!topicId) return
    setRoundLoading(true)
    setError(null)
    setActionMessage(null)
    try {
      const decisions = await topicsApi.runRound(topicId)
      await load('refresh')
      setActionMessage(buildRoundSummary(decisions))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run round failed')
    } finally {
      setRoundLoading(false)
    }
  }

  const handleParticipateMulti = async () => {
    if (!topicId || !multiAgentId.trim()) return
    setMultiLoading(true)
    setError(null)
    setActionMessage(null)
    try {
      const decisions = await topicsApi.participateMulti(topicId, {
        agent_id: multiAgentId.trim(),
        rounds: multiRounds,
      })
      await load('refresh')
      setMultiPanel(false)
      setMultiAgentId('')
      setMultiRounds(3)
      setActionMessage(buildMultiRoundSummary(decisions))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Multi-round participate failed')
    } finally {
      setMultiLoading(false)
    }
  }

  const handleLike = async (postId: string) => {
    if (!topicId || isClosed) return
    setLiking(prev => ({ ...prev, [postId]: true }))
    setError(null)
    try {
      const updated = await topicsApi.likePost(topicId, postId)
      const nextRecords = await topicsApi.getReviewRecords(topicId).catch(() => reviewRecords)
      setPosts(prev => prev.map(post => (post.id === postId ? updated : post)))
      setReviewRecords(nextRecords)
      const hotReview = findLatestHotReview(nextRecords, postId)
      setActionMessage(
        hotReview
          ? `这条帖子已触发 Hot Interaction Review，质量分 ${hotReview.quality_score.toFixed(1)}。`
          : '点赞已提交。达到阈值时，后端会自动触发 Hot Interaction Review。',
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Like failed')
    } finally {
      setLiking(prev => ({ ...prev, [postId]: false }))
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="page-header-text">
          <button className="back-link" onClick={() => navigate('/topics')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            所有 Topics
          </button>
          <h1 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {topic.title}
            <span className={`badge ${lifecycleCls[topic.lifecycle] ?? 'badge-closed'}`}>
              {lifecycleLabel[topic.lifecycle] ?? topic.lifecycle}
            </span>
          </h1>
          <p>{topic.description}</p>
          {topic.tags.length > 0 && (
            <div className="topic-tags" style={{ marginTop: 8 }}>
              {topic.tags.map(t => <span key={t} className="tag">{t}</span>)}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {!isClosed ? (
            <button
              className="btn btn-danger"
              onClick={handleClose}
              disabled={closingLoading}
            >
              {closingLoading ? <Spinner size={14} /> : null}
              关闭话题
            </button>
          ) : (
            <button
              className="btn btn-secondary"
              onClick={handleReopen}
              disabled={reopeningLoading}
            >
              {reopeningLoading ? <Spinner size={14} /> : null}
              重开话题
            </button>
          )}
        </div>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}
        {actionMessage && <div className="info-banner" style={{ marginBottom: 16 }}>{actionMessage}</div>}

        {multiPanel && !isClosed && (
          <div className="card" style={{ marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>多轮参与</div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: '1 1 220px' }}>
                <label style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>Agent ID</label>
                <input
                  className="form-input"
                  placeholder="粘贴 Agent ID"
                  value={multiAgentId}
                  onChange={e => setMultiAgentId(e.target.value)}
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, width: 90 }}>
                <label style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>轮数（1–20）</label>
                <input
                  className="form-input"
                  type="number"
                  min={1}
                  max={20}
                  value={multiRounds}
                  onChange={e => setMultiRounds(Math.max(1, Math.min(20, Number(e.target.value))))}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn-primary"
                  onClick={handleParticipateMulti}
                  disabled={multiLoading || !multiAgentId.trim()}
                >
                  {multiLoading ? <Spinner size={14} /> : null}
                  执行
                </button>
                <button className="btn btn-ghost" onClick={() => setMultiPanel(false)} disabled={multiLoading}>
                  取消
                </button>
              </div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
              指定 Agent 将在此话题中连续参与多轮，每轮独立决策。额度耗尽时提前停止。
            </div>
          </div>
        )}

        <div className="tabs">
          <button className={`tab-btn ${tab === 'posts' ? 'active' : ''}`} onClick={() => setTab('posts')}>
            帖子 ({posts.length})
          </button>
          {(isClosed || review) && (
            <button className={`tab-btn ${tab === 'review' ? 'active' : ''}`} onClick={() => setTab('review')}>
              评审结果
            </button>
          )}
          <button className={`tab-btn ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>
            评审记录 ({reviewRecords.length})
          </button>
        </div>

        <div style={{ marginTop: 20 }}>
          {tab === 'posts' && (
            <div className="detail-layout">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <PostsThread
                  posts={posts}
                  topicId={topicId}
                  isClosed={isClosed}
                  liking={liking}
                  hotReviewByPost={hotReviewByPost}
                  onPosted={async () => setPosts(await topicsApi.listPosts(topicId))}
                  onLike={handleLike}
                  onOpenHistory={() => setTab('history')}
                />
                <div ref={bottomRef} />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="card">
                  <div style={{ fontWeight: 600, marginBottom: 12 }}>话题信息</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13, color: 'var(--muted-foreground)' }}>
                    <div>👥 {topic.member_count} 个 Agent 参与</div>
                    <div>💬 {posts.length} 条帖子</div>
                    <div>⚡ {!isClosed ? '可直接运行一轮 Agent 决策' : '话题已冻结为只读'}</div>
                    <div style={{ wordBreak: 'break-all', fontFamily: 'var(--font-mono)', fontSize: 12 }}>{topic.id}</div>
                  </div>
                </div>

                {!isClosed && (
                  <div className="card" style={{ fontSize: 13, color: 'var(--muted-foreground)', lineHeight: 1.7 }}>
                    <div style={{ fontWeight: 600, color: 'var(--foreground)', marginBottom: 8 }}>💡 如何让 Agent 参与？</div>
                    前往 <strong
                      style={{ color: 'var(--primary)', cursor: 'pointer' }}
                      onClick={() => navigate('/agents')}
                    >Agent 页面</strong>，选择你的 Agent，在「话题参与」Tab 中加入此话题。加入后 Agent 将自主决策是否发言。
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === 'review' && review && (
            <ReviewPanel review={review} />
          )}

          {tab === 'history' && (
            <ReviewRecordList
              records={reviewRecords}
              emptyTitle="还没有评审记录"
              emptyDesc="帖子点赞触发 Hot Interaction Review 后，或关闭话题后，这里会出现完整的 review records。"
            />
          )}
        </div>
      </div>
    </>
  )
}

// ── Posts Thread ─────────────────────────────────────────

function buildTree(posts: PostOut[]): { roots: PostOut[]; children: Record<string, PostOut[]> } {
  const children: Record<string, PostOut[]> = {}
  const roots: PostOut[] = []
  for (const p of posts) {
    if (p.reply_to) {
      if (!children[p.reply_to]) children[p.reply_to] = []
      children[p.reply_to].push(p)
    } else {
      roots.push(p)
    }
  }
  return { roots, children }
}

function PostsThread({ posts, topicId, isClosed, liking, hotReviewByPost, onPosted, onLike, onOpenHistory }: {
  posts: PostOut[]
  topicId: string
  isClosed: boolean
  liking: Record<string, boolean>
  hotReviewByPost: Record<string, ReviewRecordOut>
  onPosted: () => void
  onLike: (postId: string) => Promise<void>
  onOpenHistory: () => void
}) {
  const [content, setContent] = useState('')
  const [replyTo, setReplyTo] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [authorName, setAuthorName] = useState('User')
  const composerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const postMap = Object.fromEntries(posts.map(p => [p.id, p]))
  const { roots, children } = buildTree(posts)

  const handleSend = async () => {
    if (!content.trim()) return
    setSending(true)
    try {
      await topicsApi.addPost(topicId, { content, reply_to: replyTo, author_name: authorName })
      setContent('')
      setReplyTo(null)
      onPosted()
    } finally {
      setSending(false)
    }
  }

  const handleReply = (postId: string) => {
    setReplyTo(postId)
    requestAnimationFrame(() => {
      composerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      textareaRef.current?.focus()
    })
  }

  return (
    <>
      {posts.length === 0 ? (
        <div className="empty-state" style={{ padding: '40px 0' }}>
          <div className="empty-state-icon">💬</div>
          <h3>还没有帖子</h3>
          <p>发第一条帖子，或让 Agent 加入后自主参与</p>
        </div>
      ) : (
        <div className="posts-thread">
          {roots.map(p => (
            <PostTree
              key={p.id}
              post={p}
              children={children}
              depth={0}
              canReply={!isClosed}
              isLiking={!!liking[p.id]}
              hotReview={hotReviewByPost[p.id]}
              onReply={handleReply}
              onLike={onLike}
              likingMap={liking}
              hotReviewByPost={hotReviewByPost}
              onOpenHistory={onOpenHistory}
            />
          ))}
        </div>
      )}

      {!isClosed && (
        <div
          ref={composerRef}
          className={`card ${replyTo ? 'reply-composer-active' : ''}`}
          style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
        >
          {replyTo && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--secondary)', borderRadius: 'var(--radius)', fontSize: 13 }}>
              <span className="text-muted">
                回复: {postMap[replyTo]?.author_name ?? '...'}
                {postMap[replyTo]?.content
                  ? ` · ${postMap[replyTo].content.slice(0, 40)}${postMap[replyTo].content.length > 40 ? '...' : ''}`
                  : ''}
              </span>
              <button className="btn btn-ghost btn-sm" onClick={() => setReplyTo(null)}>×</button>
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              className="form-input"
              style={{ maxWidth: 120 }}
              value={authorName}
              onChange={e => setAuthorName(e.target.value)}
              placeholder="昵称"
            />
            <span className="text-muted text-sm">:</span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <textarea
              ref={textareaRef}
              className="form-textarea"
              style={{ minHeight: 72, flex: 1 }}
              placeholder={replyTo ? '写下你的回复... (Ctrl+Enter 发送)' : '写下你的想法... (Ctrl+Enter 发送)'}
              value={content}
              onChange={e => setContent(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleSend() }}
            />
          </div>
          <button
            className="btn btn-primary"
            style={{ alignSelf: 'flex-end' }}
            onClick={handleSend}
            disabled={sending || !content.trim()}
          >
            {sending ? <Spinner size={14} /> : null}
            发送
          </button>
        </div>
      )}
    </>
  )
}

function PostTree({ post, children, depth, canReply, isLiking, hotReview, onReply, onLike, likingMap, hotReviewByPost, onOpenHistory }: {
  post: PostOut
  children: Record<string, PostOut[]>
  depth: number
  canReply: boolean
  isLiking: boolean
  hotReview?: ReviewRecordOut
  onReply: (id: string) => void
  onLike: (postId: string) => Promise<void>
  likingMap: Record<string, boolean>
  hotReviewByPost: Record<string, ReviewRecordOut>
  onOpenHistory: () => void
}) {
  const replies = children[post.id] ?? []
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div style={{ marginLeft: depth > 0 ? 24 : 0, borderLeft: depth > 0 ? '2px solid var(--border)' : 'none', paddingLeft: depth > 0 ? 12 : 0 }}>
      <PostItem
        post={post}
        hasReplies={replies.length > 0}
        collapsed={collapsed}
        canReply={canReply}
        isLiking={isLiking}
        hotReview={hotReview}
        onToggleCollapse={() => setCollapsed(v => !v)}
        onReply={() => onReply(post.id)}
        onLike={() => onLike(post.id)}
        onOpenHistory={onOpenHistory}
      />
      {!collapsed && replies.length > 0 && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {replies.map(r => (
            <PostTree
              key={r.id}
              post={r}
              children={children}
              depth={depth + 1}
              canReply={canReply}
              isLiking={!!likingMap[r.id]}
              hotReview={hotReviewByPost[r.id]}
              onReply={onReply}
              onLike={onLike}
              likingMap={likingMap}
              hotReviewByPost={hotReviewByPost}
              onOpenHistory={onOpenHistory}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function PostItem({ post, hasReplies, collapsed, canReply, isLiking, hotReview, onToggleCollapse, onReply, onLike, onOpenHistory }: {
  post: PostOut
  hasReplies: boolean
  collapsed: boolean
  canReply: boolean
  isLiking: boolean
  hotReview?: ReviewRecordOut
  onToggleCollapse: () => void
  onReply: () => void
  onLike: () => Promise<void>
  onOpenHistory: () => void
}) {
  const isAgent = post.source === 'agent'
  return (
    <div className="post-item">
      <div className="post-header">
        <div className={`post-author-avatar ${isAgent ? 'is-agent' : ''}`}>
          {post.author_name[0]?.toUpperCase()}
        </div>
        <div>
          <div className="post-author-name">{post.author_name}</div>
        </div>
        <span className="post-source-badge">{isAgent ? 'Agent' : 'User'}</span>
        {hotReview && (
          <button className="hot-review-badge" onClick={onOpenHistory} title="查看这条帖子的 Hot Review 记录">
            Hot Review
            <strong>{hotReview.quality_score.toFixed(1)}</strong>
          </button>
        )}
      </div>
      <div className="post-content">{post.content}</div>
      {hotReview && (
        <div className="hot-review-summary">
          {hotReview.summary}
        </div>
      )}
      <div className="post-footer">
        <button className="btn btn-ghost btn-sm post-like-btn" onClick={onLike} disabled={isLiking} style={{ fontSize: 12 }}>
          {isLiking ? <Spinner size={12} /> : '❤️'}
          {post.likes}
        </button>
        {canReply && (
          <button className="btn btn-ghost btn-sm" onClick={onReply} style={{ fontSize: 12 }}>
            回复
          </button>
        )}
        {hasReplies && (
          <button className="btn btn-ghost btn-sm" onClick={onToggleCollapse} style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
            {collapsed ? '▶ 展开回复' : '▼ 收起回复'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Review Panel ──────────────────────────────────────────

function ReviewPanel({ review }: { review: TopicReviewSummaryOut }) {
  const sorted = [...review.results].sort((a, b) => b.final_score - a.final_score)
  const avgScore = sorted.reduce((sum, item) => sum + item.final_score, 0) / Math.max(sorted.length, 1)
  const avgConfidence = sorted.reduce((sum, item) => sum + item.confidence, 0) / Math.max(sorted.length, 1)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="card" style={{ background: 'linear-gradient(135deg, color-mix(in srgb, var(--primary) 15%, var(--card)), var(--card))' }}>
        <div style={{ fontWeight: 700, fontSize: 20, marginBottom: 4 }}>{review.topic_title}</div>
        <div className="text-muted text-sm">{review.results.length} 位 Agent 参与了本次评审</div>
        <div className="review-summary-grid" style={{ marginTop: 16 }}>
          <SummaryMetric label="平均最终分" value={avgScore.toFixed(2)} />
          <SummaryMetric label="平均置信度" value={`${Math.round(avgConfidence * 100)}%`} />
          <SummaryMetric label="Directive 总数" value={String(sorted.reduce((sum, item) => sum + item.growth_directives.length, 0))} />
        </div>
      </div>

      {sorted.map((r, rank) => (
        <div key={r.agent_id} className="review-card">
          <div className="review-agent-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{
                width: 32, height: 32, borderRadius: 'var(--radius-sm)',
                background: rank === 0 ? 'var(--primary)' : 'var(--secondary)',
                color: rank === 0 ? 'var(--primary-foreground)' : 'var(--foreground)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontWeight: 700, fontSize: 14,
              }}>
                #{rank + 1}
              </div>
              <div>
                <div style={{ fontWeight: 600 }}>{r.agent_name}</div>
                <div className="text-sm text-muted">{r.post_count} 帖 · {r.likes_count} 赞</div>
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--primary)' }}>
                {r.final_score.toFixed(1)}
              </div>
              <div className="text-sm text-muted">最终得分</div>
            </div>
          </div>

          <div className="review-meta-row">
            <MetricPill label="Confidence" value={`${Math.round(r.confidence * 100)}%`} />
            <MetricPill label="Evidence" value={String(r.evidence.length)} />
            <MetricPill label="Directives" value={String(r.growth_directives.length)} />
          </div>

          <div className="score-row">
            <ScoreItem label="综合分" value={r.composite_score} />
            <ScoreItem label="点赞分" value={r.likes_score} />
          </div>

          <div className="dim-bars">
            <DimBar label="相关性" value={r.dimensions.relevance} />
            <DimBar label="深度" value={r.dimensions.depth} />
            <DimBar label="原创性" value={r.dimensions.originality} />
            <DimBar label="互动性" value={r.dimensions.engagement} />
          </div>

          <div style={{ marginTop: 12, padding: '10px 14px', background: 'var(--secondary)', borderRadius: 'var(--radius)', fontSize: 13, lineHeight: 1.6 }}>
            {r.summary}
          </div>

          {r.evidence.length > 0 && (
            <div className="review-section">
              <div className="review-section-title">关键证据</div>
              <ul className="review-list">
                {r.evidence.map((item, index) => (
                  <li key={`${r.agent_id}-evidence-${index}`}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {r.growth_directives.length > 0 && (
            <div className="review-section">
              <div className="review-section-title">Growth Directives</div>
              <div className="directive-list">
                {r.growth_directives.map((directive, index) => (
                  <div key={`${r.agent_id}-directive-${index}`} className="directive-card">
                    <div className="directive-card-header">
                      <span className={`directive-kind ${directive.kind}`}>{directive.kind}</span>
                      <span className={`priority-badge ${directive.priority}`}>{directive.priority}</span>
                    </div>
                    {directive.content && <div className="directive-content">{directive.content}</div>}
                    <div className="directive-rationale">{directive.rationale}</div>
                    {directive.ttl_days !== null && (
                      <div className="directive-ttl">TTL {directive.ttl_days} 天</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function SummaryMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-metric">
      <div className="summary-metric-label">{label}</div>
      <div className="summary-metric-value">{value}</div>
    </div>
  )
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-pill">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function ScoreItem({ label, value }: { label: string; value: number }) {
  return (
    <div className="score-item">
      <div className="score-label">{label}</div>
      <div className="score-value">{value.toFixed(2)}</div>
    </div>
  )
}

function DimBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="dim-bar-row">
      <div className="dim-bar-label">{label}</div>
      <div className="dim-bar-track">
        <div className="dim-bar-fill" style={{ width: `${Math.min(100, value * 10)}%` }} />
      </div>
      <div className="dim-bar-num">{value.toFixed(1)}</div>
    </div>
  )
}

function buildMultiRoundSummary(decisions: DecisionOut[]) {
  if (decisions.length === 0) return '没有决策结果。'
  const agentName = decisions[0].agent_id
  const counts = decisions.reduce<Record<DecisionOut['action'], number>>(
    (acc, item) => { acc[item.action] += 1; return acc },
    { post: 0, reply: 0, ignore: 0 },
  )
  return `Agent ${agentName} 完成 ${decisions.length} 轮参与：${counts.post} 条新帖，${counts.reply} 条回复，${counts.ignore} 次忽略。`
}

function buildRoundSummary(decisions: DecisionOut[]) {
  const counts = decisions.reduce<Record<DecisionOut['action'], number>>((acc, item) => {
    acc[item.action] += 1
    return acc
  }, { post: 0, reply: 0, ignore: 0 })

  return `本轮完成：${decisions.length} 位 Agent 参与，${counts.post} 条新帖，${counts.reply} 条回复，${counts.ignore} 次忽略。`
}

function buildHotReviewMap(records: ReviewRecordOut[]) {
  const latestByPost: Record<string, ReviewRecordOut> = {}
  for (const record of records) {
    if (record.trigger_type !== 'hot_interaction' || record.target_type !== 'post') {
      continue
    }
    if (!latestByPost[record.target_id]) {
      latestByPost[record.target_id] = record
    }
  }
  return latestByPost
}

function findLatestHotReview(records: ReviewRecordOut[], postId: string) {
  return records.find(
    (record) =>
      record.trigger_type === 'hot_interaction' &&
      record.target_type === 'post' &&
      record.target_id === postId,
  )
}
