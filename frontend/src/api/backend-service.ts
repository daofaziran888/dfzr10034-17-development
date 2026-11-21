import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'
import type { ContributionView, LateMeasurePayload } from '@/data/contribution'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ── 治理评分贡献视图 ──────────────────────────────────────────

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error('接口返回非 2xx，数据未更新')
  return (await response.json()) as T
}

export async function fetchContributionView(params: {
  scoreId?: number
  tableCode?: string
  window?: string
}): Promise<ContributionView> {
  const query = new URLSearchParams()
  if (params.scoreId) query.set('score_id', String(params.scoreId))
  if (params.tableCode) query.set('table_code', params.tableCode)
  if (params.window) query.set('window', params.window)
  const response = await request(`/api/governance-scoring/contributions?${query.toString()}`)
  if (!response.ok) throw new Error('贡献视图读取失败')
  return await response.json()
}

export async function recomputeScore(scoreId: number): Promise<{
  ok: boolean
  compatible: boolean
  original_score: string
  recomputed_score: number | null
  basis: string
  late_registered_evidence: { check_code: string; entry_kind: string }[]
}> {
  return postJson(`/api/governance-scoring/scores/${scoreId}/recompute`, {})
}

export async function registerLateMeasure(payload: LateMeasurePayload): Promise<{
  ok: boolean
  error?: string
}> {
  return postJson('/api/governance-scoring/measures/late', payload)
}

export async function addSupplementalMeasure(payload: LateMeasurePayload): Promise<{
  ok: boolean
  error?: string
  measure?: { id: number }
}> {
  return postJson('/api/governance-scoring/measures/supplement', payload)
}

export async function retestMeasure(
  measureId: number,
  payload: { new_value: number; conclusion: string; reviewer: string },
): Promise<{ ok: boolean; error?: string }> {
  return postJson(`/api/governance-scoring/measures/${measureId}/retest`, payload)
}
