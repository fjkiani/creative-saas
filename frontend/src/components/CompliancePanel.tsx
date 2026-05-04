import React, { useState } from 'react'

interface ComplianceIssue {
  severity: string
  category: string
  description: string
  flagged_text?: string
}

interface ComplianceReport {
  passed: boolean
  issues: ComplianceIssue[]
  warnings: string[]
  errors: string[]
}

interface Props {
  preCompliance?: ComplianceReport | null
  postCompliance?: ComplianceReport | null
}

function ReportSection({ title, report }: { title: string; report: ComplianceReport | null | undefined }) {
  const [expanded, setExpanded] = useState(false)
  if (!report) return (
    <div style={styles.section}>
      <div style={styles.sectionHeader}>
        <span style={styles.sectionTitle}>{title}</span>
        <span style={{ ...styles.badge, background: '#1f2937', color: '#6b7280' }}>Pending</span>
      </div>
    </div>
  )

  const passed = report.passed
  const hasIssues = report.issues?.length > 0

  return (
    <div style={styles.section}>
      <div style={styles.sectionHeader} onClick={() => setExpanded(!expanded)}>
        <span style={styles.sectionTitle}>{title}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {report.warnings?.length > 0 && (
            <span style={{ ...styles.badge, background: '#78350f', color: '#fde68a' }}>
              {report.warnings.length} warning{report.warnings.length !== 1 ? 's' : ''}
            </span>
          )}
          {report.errors?.length > 0 && (
            <span style={{ ...styles.badge, background: '#7f1d1d', color: '#fca5a5' }}>
              {report.errors.length} error{report.errors.length !== 1 ? 's' : ''}
            </span>
          )}
          <span style={{
            ...styles.badge,
            background: passed ? '#14532d' : '#7f1d1d',
            color: passed ? '#86efac' : '#fca5a5',
          }}>
            {passed ? '✓ Passed' : '✗ Failed'}
          </span>
          <span style={styles.chevron}>{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {expanded && hasIssues && (
        <div style={styles.issueList}>
          {report.issues.map((issue, i) => (
            <div key={i} style={{
              ...styles.issue,
              borderLeft: `3px solid ${issue.severity === 'ERROR' ? '#ef4444' : '#f59e0b'}`,
            }}>
              <div style={styles.issueHeader}>
                <span style={{
                  ...styles.issueSeverity,
                  color: issue.severity === 'ERROR' ? '#fca5a5' : '#fde68a',
                }}>
                  {issue.severity}
                </span>
                <span style={styles.issueCategory}>{issue.category}</span>
              </div>
              <div style={styles.issueDesc}>{issue.description}</div>
              {issue.flagged_text && (
                <div style={styles.flaggedText}>"{issue.flagged_text}"</div>
              )}
            </div>
          ))}
        </div>
      )}

      {expanded && !hasIssues && (
        <div style={styles.allClear}>No issues found</div>
      )}
    </div>
  )
}

export function CompliancePanel({ preCompliance, postCompliance }: Props) {
  return (
    <div style={styles.container}>
      <div style={styles.header}>Compliance</div>
      <ReportSection title="Pre-generation Check" report={preCompliance} />
      <ReportSection title="Post-generation Check" report={postCompliance} />
      <div style={styles.disclaimer}>
        Compliance checks are LLM-based heuristics and pixel analysis.
        Not a substitute for legal review.
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: '#1a1a1a', border: '1px solid #2a2a2a',
    borderRadius: 12, padding: 20, display: 'flex', flexDirection: 'column', gap: 12,
  },
  header: {
    fontSize: 14, fontWeight: 600, color: '#e8e8e8',
    letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 4,
  },
  section: {
    background: '#111', borderRadius: 8, padding: 14,
    border: '1px solid #2a2a2a',
  },
  sectionHeader: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    cursor: 'pointer',
  },
  sectionTitle: { fontSize: 13, fontWeight: 500, color: '#ccc' },
  badge: {
    fontSize: 10, fontWeight: 700, padding: '2px 8px',
    borderRadius: 20, letterSpacing: '0.05em',
  },
  chevron: { fontSize: 10, color: '#555' },
  issueList: { marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 },
  issue: {
    background: '#1a1a1a', borderRadius: 6, padding: '10px 12px',
    display: 'flex', flexDirection: 'column', gap: 4,
  },
  issueHeader: { display: 'flex', gap: 8, alignItems: 'center' },
  issueSeverity: { fontSize: 10, fontWeight: 700, letterSpacing: '0.05em' },
  issueCategory: {
    fontSize: 10, color: '#666', background: '#2a2a2a',
    padding: '1px 6px', borderRadius: 4,
  },
  issueDesc: { fontSize: 12, color: '#aaa' },
  flaggedText: {
    fontSize: 11, color: '#666', fontStyle: 'italic',
    fontFamily: 'monospace',
  },
  allClear: { marginTop: 10, fontSize: 12, color: '#22c55e' },
  disclaimer: {
    fontSize: 10, color: '#444', fontStyle: 'italic',
    borderTop: '1px solid #2a2a2a', paddingTop: 10, marginTop: 4,
  },
}
