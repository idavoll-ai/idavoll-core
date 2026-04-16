import type { ReviewRecordOut } from '@/api/types'

export function ReviewRecordList({
  records,
  emptyTitle,
  emptyDesc,
  onOpenTopic,
}: {
  records: ReviewRecordOut[]
  emptyTitle: string
  emptyDesc: string
  onOpenTopic?: (topicId: string) => void
}) {
  if (records.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '36px 16px' }}>
        <div className="empty-state-icon">🧾</div>
        <h3>{emptyTitle}</h3>
        <p>{emptyDesc}</p>
      </div>
    )
  }

  return (
    <div className="review-record-list">
      {records.map((record) => (
        <div key={record.id} className="review-record-card">
          <div className="review-record-header">
            <div>
              <div className="review-record-title">
                <span>{record.agent_name}</span>
                <span className={`priority-badge ${record.growth_priority}`}>{record.growth_priority}</span>
              </div>
              <div className="review-record-meta">
                <span>{triggerLabel(record.trigger_type)}</span>
                <span>{targetLabel(record)}</span>
                <span>{formatDateTime(record.created_at)}</span>
              </div>
            </div>
            <div className="review-record-score">
              <strong>{record.quality_score.toFixed(1)}</strong>
              <span>{Math.round(record.confidence * 100)}%</span>
            </div>
          </div>

          <div className="review-record-summary">{record.summary}</div>

          {record.error_message && (
            <div className="review-record-error">
              <div className="review-section-title" style={{ marginBottom: 6 }}>Failure Reason</div>
              <div>{record.error_message}</div>
            </div>
          )}

          <div className="review-record-pills">
            <span className="metric-pill"><span>Topic</span><strong>{shortId(record.topic_id)}</strong></span>
            <span className="metric-pill"><span>Target</span><strong>{shortId(record.target_id)}</strong></span>
            <span className="metric-pill"><span>Strategies</span><strong>{record.strategy_results.length}</strong></span>
            <span className="metric-pill"><span>Directives</span><strong>{record.growth_directives.length}</strong></span>
          </div>

          {record.strategy_results.length > 0 && (
            <div className="review-section">
              <div className="review-section-title">Reviewer Outputs</div>
              <div className="review-strategy-grid">
                {record.strategy_results.map((item, index) => (
                  <div key={`${record.id}-strategy-${index}`} className="review-strategy-card">
                    <div className="review-strategy-header">
                      <span>{item.reviewer_name}</span>
                      <span className="metric-pill"><span>{item.dimension}</span><strong>{item.score.toFixed(1)}</strong></span>
                    </div>
                    <div className="review-strategy-summary">{item.summary || '无摘要'}</div>
                    {item.evidence.length > 0 && (
                      <ul className="review-list">
                        {item.evidence.slice(0, 3).map((evidence, evidenceIndex) => (
                          <li key={`${record.id}-evidence-${index}-${evidenceIndex}`}>{evidence}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {record.growth_directives.length > 0 && (
            <div className="review-section">
              <div className="review-section-title">Directives</div>
              <div className="directive-list">
                {record.growth_directives.map((directive, index) => (
                  <div key={`${record.id}-directive-${index}`} className="directive-card">
                    <div className="directive-card-header">
                      <span className={`directive-kind ${directive.kind}`}>{directive.kind}</span>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        {directive.agent_decision && (
                          <span className={`decision-badge ${directive.agent_decision}`}>
                            {decisionLabel(directive.agent_decision)}
                          </span>
                        )}
                        <span className={`priority-badge ${directive.priority}`}>{directive.priority}</span>
                      </div>
                    </div>
                    {directive.content && <div className="directive-content">{directive.content}</div>}
                    <div className="directive-rationale">{directive.rationale}</div>
                    {directive.final_content && directive.final_content !== directive.content && (
                      <div className="directive-final-content">
                        <div className="review-section-title" style={{ marginBottom: 6 }}>Final Content</div>
                        <div>{directive.final_content}</div>
                      </div>
                    )}
                    {directive.decision_rationale && (
                      <div className="directive-decision-note">
                        <div className="review-section-title" style={{ marginBottom: 6 }}>Agent Decision</div>
                        <div>{directive.decision_rationale}</div>
                        {directive.decided_at && (
                          <div className="directive-decision-time">{formatDateTime(directive.decided_at)}</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {onOpenTopic && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => onOpenTopic(record.topic_id)}>
                打开 Topic
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function shortId(value: string) {
  return value.length > 10 ? `${value.slice(0, 8)}…` : value
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function triggerLabel(triggerType: string) {
  const map: Record<string, string> = {
    topic_closed: 'Topic Close',
    hot_interaction: 'Hot Interaction',
  }
  return map[triggerType] ?? triggerType
}

function targetLabel(record: ReviewRecordOut) {
  const map: Record<string, string> = {
    agent_in_topic: 'Agent in Topic',
    post: 'Single Post',
    thread: 'Thread',
  }
  return map[record.target_type] ?? record.target_type
}

function decisionLabel(decision: string) {
  const map: Record<string, string> = {
    accept: 'Accepted',
    reject: 'Rejected',
    defer: 'Deferred',
  }
  return map[decision] ?? decision
}
