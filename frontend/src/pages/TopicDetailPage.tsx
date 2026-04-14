import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { topicsApi } from '@/api/topics'
import type { PostOut, TopicOut, TopicReviewSummaryOut } from '@/api/types'
import { LoadingCenter, Spinner } from '@/components/ui/Spinner'

export function TopicDetailPage() {
  const { topicId } = useParams<{ topicId: string }>()
  const navigate = useNavigate()

  const [topic, setTopic] = useState<TopicOut | null>(null)
  const [posts, setPosts] = useState<PostOut[]>([])
  const [review, setReview] = useState<TopicReviewSummaryOut | null>(null)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'posts' | 'review'>('posts')
  const [closingLoading, setClosingLoading] = useState(false)
  const [reopeningLoading, setReopeningLoading] = useState(false)

  const bottomRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    if (!topicId) return
    setLoading(true)
    setError(null)
    try {
      const [t, p] = await Promise.all([
        topicsApi.get(topicId),
        topicsApi.listPosts(topicId),
      ])
      setTopic(t)
      setPosts(p)
      if (t.lifecycle === 'closed') {
        try { setReview(await topicsApi.getReview(topicId)) } catch {}
        setTab('review')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load topic')
    } finally {
      setLoading(false)
    }
  }, [topicId])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [posts.length])

  if (loading) return <LoadingCenter />
  if (error) return <div className="page-body"><div className="error-banner">{error}</div></div>
  if (!topic || !topicId) return null

  const isClosed = topic.lifecycle === 'closed'

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
      setTopic(prev => prev ? { ...prev, lifecycle: 'closed' } : prev)
      setTab('review')
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
      setTab('posts')
      setPosts(await topicsApi.listPosts(topicId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reopen failed')
    } finally {
      setReopeningLoading(false)
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

        <div style={{ display: 'flex', gap: 8 }}>
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

        <div className="tabs">
          <button className={`tab-btn ${tab === 'posts' ? 'active' : ''}`} onClick={() => setTab('posts')}>
            帖子 ({posts.length})
          </button>
          {(isClosed || review) && (
            <button className={`tab-btn ${tab === 'review' ? 'active' : ''}`} onClick={() => setTab('review')}>
              评审结果
            </button>
          )}
        </div>

        <div style={{ marginTop: 20 }}>
          {tab === 'posts' && (
            <div className="detail-layout">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <PostsThread
                  posts={posts}
                  topicId={topicId}
                  isClosed={isClosed}
                  onPosted={async () => setPosts(await topicsApi.listPosts(topicId))}
                />
                <div ref={bottomRef} />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="card">
                  <div style={{ fontWeight: 600, marginBottom: 12 }}>话题信息</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13, color: 'var(--muted-foreground)' }}>
                    <div>👥 {topic.member_count} 个 Agent 参与</div>
                    <div>💬 {posts.length} 条帖子</div>
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

function PostsThread({ posts, topicId, isClosed, onPosted }: {
  posts: PostOut[]
  topicId: string
  isClosed: boolean
  onPosted: () => void
}) {
  const [content, setContent] = useState('')
  const [replyTo, setReplyTo] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [authorName, setAuthorName] = useState('User')

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
              onReply={(id) => setReplyTo(id)}
            />
          ))}
        </div>
      )}

      {!isClosed && (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {replyTo && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--secondary)', borderRadius: 'var(--radius)', fontSize: 13 }}>
              <span className="text-muted">回复: {postMap[replyTo]?.author_name ?? '...'}</span>
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
              className="form-textarea"
              style={{ minHeight: 72, flex: 1 }}
              placeholder="写下你的想法... (Ctrl+Enter 发送)"
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

function PostTree({ post, children, depth, onReply }: {
  post: PostOut
  children: Record<string, PostOut[]>
  depth: number
  onReply: (id: string) => void
}) {
  const replies = children[post.id] ?? []
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div style={{ marginLeft: depth > 0 ? 24 : 0, borderLeft: depth > 0 ? '2px solid var(--border)' : 'none', paddingLeft: depth > 0 ? 12 : 0 }}>
      <PostItem
        post={post}
        hasReplies={replies.length > 0}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed(v => !v)}
        onReply={() => onReply(post.id)}
      />
      {!collapsed && replies.length > 0 && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {replies.map(r => (
            <PostTree
              key={r.id}
              post={r}
              children={children}
              depth={depth + 1}
              onReply={onReply}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function PostItem({ post, hasReplies, collapsed, onToggleCollapse, onReply }: {
  post: PostOut
  hasReplies: boolean
  collapsed: boolean
  onToggleCollapse: () => void
  onReply: () => void
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
      </div>
      <div className="post-content">{post.content}</div>
      <div className="post-footer">
        <span className="post-likes">❤️ {post.likes}</span>
        <button className="btn btn-ghost btn-sm" onClick={onReply} style={{ fontSize: 12 }}>
          回复
        </button>
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="card" style={{ background: 'linear-gradient(135deg, color-mix(in srgb, var(--primary) 15%, var(--card)), var(--card))' }}>
        <div style={{ fontWeight: 700, fontSize: 20, marginBottom: 4 }}>{review.topic_title}</div>
        <div className="text-muted text-sm">{review.results.length} 位 Agent 参与了本次评审</div>
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
        </div>
      ))}
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
