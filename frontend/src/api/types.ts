// ── Agents ──────────────────────────────────────────────

export interface AgentOut {
  id: string
  name: string
  description: string
  level: number
  xp: number
  context_budget: number
}

export interface AgentProgressOut {
  agent_id: string
  xp: number
  level: number
}

export interface SoulPreviewOut {
  soul: string
}

export interface CreateAgentRequest {
  name: string
  description: string
  soul?: string
}

export interface RefineSoulRequest {
  feedback: string
}

export interface RefineSoulTextRequest {
  name: string
  current_soul: string
  feedback: string
}

export interface BootstrapMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface BootstrapChatRequest {
  name: string
  messages: BootstrapMessage[]
}

export interface BootstrapChatResponse {
  reply: string
  soul: string | null
}

export type BootstrapStreamEvent =
  | { type: 'token'; delta: string }
  | { type: 'soul'; text: string }
  | { type: 'error'; message: string }
  | { type: 'done' }

// ── Topics ───────────────────────────────────────────────

export interface TopicOut {
  id: string
  title: string
  description: string
  tags: string[]
  lifecycle: 'open' | 'closed' | string
  member_count: number
}

export interface PostOut {
  id: string
  topic_id: string
  author_id: string
  author_name: string
  content: string
  source: string
  reply_to: string | null
  likes: number
}

export interface DecisionOut {
  topic_id: string
  agent_id: string
  action: 'ignore' | 'reply' | 'post'
  reason: string
  post_id: string | null
}

export interface CreateTopicRequest {
  title: string
  description: string
  tags: string[]
}

export interface MembershipOut {
  joined_at: string
  initiative_posts: number
  reply_posts: number
  last_post_at: string | null
}

export interface AgentTopicOut extends TopicOut {
  membership: MembershipOut
}

export interface JoinTopicRequest {
  agent_id: string
}

export interface AddUserPostRequest {
  author_name?: string
  content: string
  reply_to?: string | null
}

export interface ParticipateRequest {
  agent_id: string
}

export interface MultiParticipateRequest {
  agent_id: string
  rounds: number
}

// ── Review ───────────────────────────────────────────────

export interface DimensionScoresOut {
  relevance: number
  depth: number
  originality: number
  engagement: number
  average: number
}

export interface GrowthDirectiveOut {
  kind: 'memory_candidate' | 'reflection_candidate' | 'no_action' | 'policy_candidate'
  priority: 'low' | 'medium' | 'high'
  content: string
  rationale: string
  agent_decision: 'accept' | 'reject' | 'defer' | string | null
  decision_rationale: string | null
  final_content: string | null
  decided_at: string | null
  ttl_days: number | null
}

export interface ReviewStrategyResultOut {
  reviewer_name: string
  status: 'ok' | 'timeout' | 'failed' | string
  dimension: string
  score: number
  confidence: number
  evidence: string[]
  concerns: string[]
  parse_failed: boolean
  summary: string
  raw_output: string
}

export interface AgentReviewResultOut {
  agent_id: string
  agent_name: string
  post_count: number
  likes_count: number
  composite_score: number
  likes_score: number
  final_score: number
  dimensions: DimensionScoresOut
  summary: string
  // Phase 2/3
  confidence: number
  evidence: string[]
  growth_directives: GrowthDirectiveOut[]
}

export interface TopicReviewSummaryOut {
  topic_id: string
  topic_title: string
  results: AgentReviewResultOut[]
}

export interface ReviewRecordOut {
  id: string
  trigger_type: string
  topic_id: string
  session_id: string | null
  target_type: 'agent_in_topic' | 'post' | 'thread' | string
  target_id: string
  agent_id: string
  agent_name: string
  quality_score: number
  confidence: number
  summary: string
  growth_priority: 'low' | 'medium' | 'high' | string
  status: string
  error_message: string | null
  created_at: string
  strategy_results: ReviewStrategyResultOut[]
  growth_directives: GrowthDirectiveOut[]
}
