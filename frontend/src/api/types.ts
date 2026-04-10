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

// ── Review ───────────────────────────────────────────────

export interface DimensionScoresOut {
  relevance: number
  depth: number
  originality: number
  engagement: number
  average: number
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
}

export interface TopicReviewSummaryOut {
  topic_id: string
  topic_title: string
  results: AgentReviewResultOut[]
}
