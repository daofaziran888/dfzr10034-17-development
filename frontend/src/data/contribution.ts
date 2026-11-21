/** 治理评分贡献视图接口类型：与后端 /api/governance-scoring 返回结构保持一致。 */

export type EvidenceState = '有证据' | '实际零分' | '无证据'

export type RawCheck = {
  check_code: string
  measure_id: number
  check_type: string
  weight: number
  measured_value: string
  threshold: [number, number]
  check_score: number | null
  evidence_state: EvidenceState
  retest_state: string
  entry_kind: string
}

export type DimensionBreakdown = {
  dimension: string
  weight: number
  score: number | null
  evidence: string
  weighted_contribution: number
  contributors: RawCheck[]
  weight_covered?: number
}

export type RetestItem = {
  measure_id: number
  check_code: string
  check_type: string
  evidence_state: EvidenceState
  entry_kind: string
  measured_value: string
}

export type ContributionView = {
  ok: boolean
  error?: string
  score: {
    score_id: number
    task_code: string
    table_code: string
    window: string
    overall: number | null
    issued_at: string
    scoring_basis: string
    warning_level: string
  }
  breakdown: {
    window: string
    table_code: string
    issued_at: string
    overall: number | null
    weight_used: number
    dimensions: DimensionBreakdown[]
  }
  quality_evidence_summary: {
    evidence: string
    quality_score: number | null
    no_evidence_checks: RawCheck[]
    actual_zero_checks: RawCheck[]
    raw_check_count: number
  }
  next_retest_items: RetestItem[]
  ranking: {
    historical_rank?: number
    inspection_order?: number
    order_conflict?: boolean
  }
  last_recompute: {
    recomputed_at: string
    basis: string
    original_score: string
    recomputed_score: number | null
    compatible: boolean
    late_registered_evidence: { check_code: string; entry_kind: string; evidence_state: string; note: string }[]
  } | null
}

export type LateMeasurePayload = {
  table_code: string
  window: string
  check_type: string
  threshold_low: number
  threshold_high: number
  weight: number
  measured_value?: string | number
  check_code?: string
  note?: string
}
