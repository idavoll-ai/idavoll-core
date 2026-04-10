import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { agentsApi } from '@/api/agents'
import type { BootstrapMessage } from '@/api/types'
import { Spinner } from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────

type Step = 'name' | 'chat' | 'preview'

// ── Page ───────────────────────────────────────────────────────

export function CreateAgentPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('name')
  const [agentName, setAgentName] = useState('')
  const [messages, setMessages] = useState<BootstrapMessage[]>([])
  const [soul, setSoul] = useState('')

  return (
    <div className="create-agent-page">
      {/* Header */}
      <header className="create-agent-header">
        <button
          className="btn btn-ghost btn-sm create-agent-back"
          onClick={() => navigate('/agents')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          返回
        </button>
        <span className="create-agent-header-title">创建 Agent</span>
        <StepIndicator step={step} />
      </header>

      {/* Content */}
      <div className="create-agent-body">
        {step === 'name' && (
          <NameStep
            onConfirm={(name) => {
              setAgentName(name)
              setStep('chat')
            }}
          />
        )}

        {step === 'chat' && (
          <ChatStep
            agentName={agentName}
            messages={messages}
            onMessagesChange={setMessages}
            onSoulReady={(s) => {
              setSoul(s)
              setStep('preview')
            }}
          />
        )}

        {step === 'preview' && (
          <PreviewStep
            agentName={agentName}
            soul={soul}
            onSoulChange={setSoul}
            onConfirm={(agentId) => navigate(`/agents/${agentId}`)}
          />
        )}
      </div>
    </div>
  )
}

// ── Step Indicator ─────────────────────────────────────────────

function StepIndicator({ step }: { step: Step }) {
  const steps: { key: Step; label: string }[] = [
    { key: 'name', label: '起名' },
    { key: 'chat', label: '描述' },
    { key: 'preview', label: '确认' },
  ]
  const order: Record<Step, number> = { name: 0, chat: 1, preview: 2 }

  return (
    <div className="step-indicator">
      {steps.map((s, i) => {
        const current = order[step]
        const isDone = order[s.key] < current
        const isActive = s.key === step
        return (
          <div key={s.key} className="step-indicator-item">
            {i > 0 && <div className={`step-line ${isDone ? 'done' : ''}`} />}
            <div className={`step-dot ${isActive ? 'active' : isDone ? 'done' : ''}`}>
              {isDone ? (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              ) : (i + 1)}
            </div>
            <span className={`step-label ${isActive ? 'active' : ''}`}>{s.label}</span>
          </div>
        )
      })}
    </div>
  )
}

// ── Step 1: Name ───────────────────────────────────────────────

function NameStep({ onConfirm }: { onConfirm: (name: string) => void }) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const handleSubmit = () => {
    const name = value.trim()
    if (!name) return
    onConfirm(name)
  }

  return (
    <div className="name-step">
      <div className="name-step-icon">🤖</div>
      <h1 className="name-step-title">给你的 Agent 起个名字</h1>
      <p className="name-step-hint">名字会成为 Agent 的标识，之后可以在详情页修改人格</p>

      <div className="name-step-form">
        <input
          ref={inputRef}
          className="form-input name-step-input"
          placeholder="e.g. 苏格拉底、代码卫士、哲学教授..."
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSubmit() }}
          maxLength={40}
        />
        <button
          className="btn btn-primary name-step-btn"
          onClick={handleSubmit}
          disabled={!value.trim()}
        >
          继续
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  )
}

// ── Step 2: Chat ───────────────────────────────────────────────

const WELCOME_MESSAGE: BootstrapMessage = {
  role: 'assistant',
  content: '',  // filled dynamically with agent name
}

function ChatStep({ agentName, messages, onMessagesChange, onSoulReady }: {
  agentName: string
  messages: BootstrapMessage[]
  onMessagesChange: (msgs: BootstrapMessage[]) => void
  onSoulReady: (soul: string) => void
}) {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const initialized = useRef(false)

  // Send a welcome message on mount
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    if (messages.length === 0) {
      const welcome: BootstrapMessage = {
        role: 'assistant',
        content: `你好！我来帮你设计「${agentName}」的人格。\n\n描述一下它的背景和性格吧，随意说就行 ✍️`,
      }
      onMessagesChange([welcome])
    }
  }, [agentName, messages.length, onMessagesChange])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (!loading) textareaRef.current?.focus()
  }, [loading])

  const sendMessage = useCallback(async (text: string) => {
    const userMsg: BootstrapMessage = { role: 'user', content: text }
    const next = [...messages, userMsg]
    onMessagesChange(next)
    setInput('')
    setLoading(true)
    setError(null)

    try {
      const res = await agentsApi.bootstrapChat({ name: agentName, messages: next })
      const assistantMsg: BootstrapMessage = { role: 'assistant', content: res.reply }
      onMessagesChange([...next, assistantMsg])

      if (res.soul) {
        // short delay so user sees the last reply before transition
        setTimeout(() => onSoulReady(res.soul!), 600)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '请求失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [agentName, messages, onMessagesChange, onSoulReady])

  const handleSend = () => {
    const text = input.trim()
    if (!text || loading) return
    sendMessage(text)
  }

  return (
    <div className="chat-step">
      {/* Messages */}
      <div className="chat-messages">
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            {m.role === 'assistant' && (
              <div className="chat-msg-avatar">🤖</div>
            )}
            <div className="chat-msg-bubble">
              {m.content.split('\n').map((line, j) => (
                <span key={j}>{line}{j < m.content.split('\n').length - 1 && <br />}</span>
              ))}
            </div>
          </div>
        ))}

        {loading && (
          <div className="chat-msg chat-msg-assistant">
            <div className="chat-msg-avatar">🤖</div>
            <div className="chat-msg-bubble chat-msg-thinking">
              <span /><span /><span />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="chat-input-area">
        {error && <div className="error-banner" style={{ marginBottom: 10 }}>{error}</div>}
        <div className="chat-input-row">
          <textarea
            ref={textareaRef}
            className="form-textarea chat-textarea"
            placeholder="描述性格、背景、说话风格... (Enter 发送，Shift+Enter 换行)"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSend()
              }
            }}
            rows={3}
            disabled={loading}
          />
          <button
            className="btn btn-primary chat-send-btn"
            onClick={handleSend}
            disabled={!input.trim() || loading}
          >
            {loading ? <Spinner size={16} /> : (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
              </svg>
            )}
          </button>
        </div>
        <div className="chat-input-hint">
          AI 会根据你的描述自动生成 SOUL.md，你可以自由聊天，信息足够后会自动进入预览
        </div>
      </div>
    </div>
  )
}

// ── Step 3: Preview ────────────────────────────────────────────

function PreviewStep({ agentName, soul, onSoulChange, onConfirm }: {
  agentName: string
  soul: string
  onSoulChange: (s: string) => void
  onConfirm: (agentId: string) => void
}) {
  const [feedback, setFeedback] = useState('')
  const [refining, setRefining] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Stateless soul refinement — no agent created until the user clicks confirm.
  const handleRefine = async () => {
    const text = feedback.trim()
    if (!text || refining) return
    setRefining(true)
    setError(null)
    try {
      const res = await agentsApi.refineSoulText({
        name: agentName,
        current_soul: soul,
        feedback: text,
      })
      onSoulChange(res.soul)
      setFeedback('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '调整失败，请重试')
    } finally {
      setRefining(false)
    }
  }

  // Agent is created exactly once — when the user explicitly confirms.
  const handleConfirm = async () => {
    setCreating(true)
    setError(null)
    try {
      const agent = await agentsApi.create({ name: agentName, description: agentName, soul })
      onConfirm(agent.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败，请重试')
      setCreating(false)
    }
  }

  return (
    <div className="preview-step">
      {/* Left: SOUL.md */}
      <div className="preview-soul-panel">
        <div className="preview-soul-header">
          <span className="preview-soul-title">SOUL.md</span>
          <span className="text-sm text-muted font-mono">「{agentName}」的人格草稿</span>
        </div>
        <pre className="soul-preview preview-soul-content">{soul}</pre>
      </div>

      {/* Right: Refine + Confirm */}
      <div className="preview-actions-panel">
        <div className="preview-actions-inner">
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>满意了吗？</div>
            <div className="text-sm text-muted" style={{ lineHeight: 1.7 }}>
              可以直接确认创建，也可以告诉 AI 你想调整的方向，它会重新生成。
            </div>
          </div>

          {error && <div className="error-banner" style={{ marginBottom: 12 }}>{error}</div>}

          <div className="form-group" style={{ marginBottom: 12 }}>
            <textarea
              className="form-textarea"
              rows={4}
              placeholder="例如：让它更幽默一些，减少正式感；增加对科幻文学的热情..."
              value={feedback}
              onChange={e => setFeedback(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleRefine()
                }
              }}
              disabled={refining || creating}
            />
          </div>

          <button
            className="btn btn-secondary"
            style={{ width: '100%', marginBottom: 10 }}
            onClick={handleRefine}
            disabled={refining || creating || !feedback.trim()}
          >
            {refining ? <><Spinner size={14} /> 调整中...</> : '✏️ 调整 SOUL.md'}
          </button>

          <button
            className="btn btn-primary"
            style={{ width: '100%', padding: '12px', fontSize: 15 }}
            onClick={handleConfirm}
            disabled={creating || refining}
          >
            {creating ? <><Spinner size={16} /> 创建中...</> : '创建 Agent'}
          </button>
        </div>
      </div>
    </div>
  )
}
