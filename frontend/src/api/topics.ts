import { api } from './client'
import type {
  AddUserPostRequest,
  CreateTopicRequest,
  DecisionOut,
  JoinTopicRequest,
  ParticipateRequest,
  PostOut,
  TopicOut,
  TopicReviewSummaryOut,
} from './types'

export const topicsApi = {
  list: () => api.get<TopicOut[]>('/topics'),
  get: (id: string) => api.get<TopicOut>(`/topics/${id}`),
  create: (body: CreateTopicRequest) => api.post<TopicOut>('/topics', body),

  join: (topicId: string, body: JoinTopicRequest) =>
    api.post<TopicOut>(`/topics/${topicId}/join`, body),

  listPosts: (topicId: string) => api.get<PostOut[]>(`/topics/${topicId}/posts`),
  addPost: (topicId: string, body: AddUserPostRequest) =>
    api.post<PostOut>(`/topics/${topicId}/posts`, body),

  participate: (topicId: string, body: ParticipateRequest) =>
    api.post<DecisionOut>(`/topics/${topicId}/participate`, body),
  runRound: (topicId: string) => api.post<DecisionOut[]>(`/topics/${topicId}/round`),

  close: (topicId: string) => api.post<TopicReviewSummaryOut>(`/topics/${topicId}/close`),
  reopen: (topicId: string) => api.post<TopicOut>(`/topics/${topicId}/reopen`),
  remove: (topicId: string) => api.delete<{ ok: boolean }>(`/topics/${topicId}`),
  getReview: (topicId: string) => api.get<TopicReviewSummaryOut>(`/topics/${topicId}/review`),
}
