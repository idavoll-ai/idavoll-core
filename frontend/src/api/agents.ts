import { api } from './client'
import type {
  AgentOut,
  AgentProgressOut,
  ReviewRecordOut,
  AgentTopicOut,
  BootstrapChatRequest,
  BootstrapChatResponse,
  CreateAgentRequest,
  RefineSoulRequest,
  RefineSoulTextRequest,
  SoulPreviewOut,
} from './types'

export const agentsApi = {
  list: () => api.get<AgentOut[]>('/agents'),
  get: (id: string) => api.get<AgentOut>(`/agents/${id}`),
  create: (body: CreateAgentRequest) => api.post<AgentOut>('/agents', body),
  remove: (id: string) => api.delete<{ ok: boolean }>(`/agents/${id}`),

  getSoul: (id: string) => api.get<SoulPreviewOut>(`/agents/${id}/soul`),
  refineSoul: (id: string, body: RefineSoulRequest) =>
    api.post<SoulPreviewOut>(`/agents/${id}/soul/refine`, body),
  // Stateless refine — no agent required, used in preview step before confirm
  refineSoulText: (body: RefineSoulTextRequest) =>
    api.post<SoulPreviewOut>('/agents/soul/refine', body),

  getProgress: (id: string) => api.get<AgentProgressOut>(`/agents/${id}/progress`),
  getTopics: (id: string) => api.get<AgentTopicOut[]>(`/agents/${id}/topics`),
  getReviews: (id: string) => api.get<ReviewRecordOut[]>(`/agents/${id}/reviews`),

  bootstrapChat: (body: BootstrapChatRequest) =>
    api.post<BootstrapChatResponse>('/agents/bootstrap/chat', body),

  consolidate: (id: string) =>
    api.post<{ applied: number }>(`/agents/${id}/consolidate`),
  consolidateAll: () =>
    api.post<Record<string, number>>('/agents/consolidate/all'),
}
