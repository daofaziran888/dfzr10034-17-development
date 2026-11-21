<template>
  <section class="page" data-module="score-contribution">
    <header class="page-head">
      <div>
        <h2>治理评分贡献视图</h2>
        <p class="page-desc">
          按一次评估的质量指标检查记录和权重计算每项贡献，列出影响总分的原始检查；
          无证据（未采集）与实际零分（检查越界）严格区分，同一检查只归属一个评估窗口。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="recompute" @click="onRecompute">按原口径重算</button>
        <button class="btn primary" type="button" data-testid="open-late" @click="openLate">补登记质量测量</button>
        <button class="btn primary" type="button" data-testid="open-supplement" @click="openSupplement">补录质量检查</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadView">
      <label class="filter-item">
        <span>评分对象（表编号）</span>
        <input v-model="tableCode" data-testid="filter-table" placeholder="如 survey_week" />
      </label>
      <label class="filter-item">
        <span>评估窗口</span>
        <input v-model="assessWindow" data-testid="filter-window" placeholder="如 2026-W46" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询贡献</button>
      <button class="btn ghost" type="button" @click="usePreset('survey_week')">反馈表 W46</button>
      <button class="btn ghost" type="button" @click="usePreset('payout_week')">结算表 W46</button>
      <button class="btn ghost" type="button" @click="usePreset('retention_week', '2026-W45')">补登记样例 W45</button>
    </form>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="view">
      <!-- 评分头：总分、证据基础、排名冲突 -->
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">总分（{{ view.score.task_code }}）</span>
          <strong class="stat-value">{{ view.score.overall ?? '无证据' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">评分口径 / 窗口</span>
          <strong class="stat-value">{{ basisLabel }} · {{ view.score.window }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">历史排名 / 本周排查次序</span>
          <strong class="stat-value">
            {{ view.ranking.historical_rank ?? '—' }} / {{ view.ranking.inspection_order ?? '—' }}
            <em v-if="view.ranking.order_conflict" class="conflict-tag">次序冲突</em>
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">质量证据</span>
          <strong class="stat-value">{{ view.quality_evidence_summary.evidence }}</strong>
        </article>
      </div>

      <!-- 评审归因：无证据 vs 实际零分 -->
      <div class="analysis-summary">
        <article class="analysis-card">
          <span class="stat-label">缺采集证据（不贡献权重，非零分）</span>
          <p v-if="!view.quality_evidence_summary.no_evidence_checks.length" class="muted">无</p>
          <ul v-else class="check-list">
            <li v-for="c in view.quality_evidence_summary.no_evidence_checks" :key="c.check_code">
              <strong>{{ c.check_code }}</strong>（{{ c.check_type }}，权重 {{ c.weight }}）未采集实测值
            </li>
          </ul>
        </article>
        <article class="analysis-card">
          <span class="stat-label">检查实际零分（执行了但越界，得 0 分）</span>
          <p v-if="!view.quality_evidence_summary.actual_zero_checks.length" class="muted">无</p>
          <ul v-else class="check-list">
            <li v-for="c in view.quality_evidence_summary.actual_zero_checks" :key="c.check_code">
              <strong>{{ c.check_code }}</strong>（{{ c.check_type }}）实测 {{ c.measured_value }}
              超出 [{{ c.threshold[0] }}, {{ c.threshold[1] }}]
            </li>
          </ul>
        </article>
      </div>

      <!-- 维度贡献与原始检查 -->
      <h3>各维度贡献与原始检查</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>评分维度</th><th>权重</th><th>维度分</th><th>加权贡献</th><th>证据</th><th>关联原始检查</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="dim in view.breakdown.dimensions" :key="dim.dimension">
            <td>{{ dim.dimension }}</td>
            <td>{{ dim.weight }}</td>
            <td>{{ dim.score ?? '无证据（不计权重）' }}</td>
            <td>{{ dim.weighted_contribution }}</td>
            <td>
              <span :class="evidenceClass(dim.evidence)">{{ dim.evidence }}</span>
            </td>
            <td>
              <span v-if="!dim.contributors.length" class="muted">—</span>
              <ul class="check-list compact" v-else>
                <li v-for="c in dim.contributors" :key="c.check_code">
                  <span :class="evidenceClass(c.evidence_state)">{{ c.evidence_state }}</span>
                  {{ c.check_code }} · {{ c.check_type }} · 权重{{ c.weight }}
                  · 检查分{{ c.check_score ?? '不计' }} · 实测「{{ c.measured_value || '空' }}」
                  <em v-if="c.entry_kind !== '常规登记'" class="entry-tag">{{ c.entry_kind }}</em>
                </li>
              </ul>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="page-foot">
        已用权重 {{ view.breakdown.weight_used }}（无证据维度不参与重归一化）；
        证据快照签发于 {{ view.score.issued_at }}，复测与补录不会改写该分项依据。
      </p>

      <!-- 重算结果 -->
      <div v-if="view.last_recompute" class="analysis-card recompute-box">
        <h3>原口径重算结果（{{ view.last_recompute.recomputed_at }}）</h3>
        <p>
          口径 {{ view.last_recompute.basis }}：原分 {{ view.last_recompute.original_score }}
          → 重算 {{ view.last_recompute.recomputed_score }}
          <strong :class="view.last_recompute.compatible ? 'trend-up' : 'trend-down'">
            {{ view.last_recompute.compatible ? '兼容一致' : '存在偏差' }}
          </strong>
        </p>
        <p v-if="view.last_recompute.late_registered_evidence.length">
          签发后补登记证据（未计入原始评分）：
          <span v-for="d in view.last_recompute.late_registered_evidence" :key="d.check_code" class="entry-tag">
            {{ d.check_code }}（{{ d.entry_kind }}）
          </span>
        </p>
      </div>

      <!-- 下一轮复测项目 -->
      <h3>下一轮复测项目（检查编号交质量规则复测，结论回写巡检待办）</h3>
      <table class="data-table">
        <thead>
          <tr><th>检查编号</th><th>检查类型</th><th>证据状态</th><th>录入类型</th><th>当前实测值</th><th>复测</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in view.next_retest_items" :key="item.measure_id">
            <td>{{ item.check_code }}</td>
            <td>{{ item.check_type }}</td>
            <td><span :class="evidenceClass(item.evidence_state)">{{ item.evidence_state }}</span></td>
            <td>{{ item.entry_kind }}</td>
            <td>{{ item.measured_value || '空（无证据）' }}</td>
            <td>
              <button class="link" type="button" :data-testid="`retest-${item.check_code}`"
                      @click="openRetest(item.measure_id, item.check_code)">录入复测结论</button>
            </td>
          </tr>
          <tr v-if="!view.next_retest_items.length">
            <td colspan="6" class="empty-state">本评分没有待复测检查</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 补登记 / 补录弹窗 -->
    <div v-if="showMeasureModal" class="modal-backdrop" data-testid="measure-modal">
      <form class="modal" @submit.prevent="submitMeasure">
        <h3>{{ measureMode === 'late' ? '评分签发后补登记质量测量' : '补录一次质量检查' }}</h3>
        <label class="create-field"><span>所属表</span><input v-model="measureForm.table_code" required /></label>
        <label class="create-field"><span>评估窗口</span><input v-model="measureForm.window" required /></label>
        <label class="create-field"><span>检查编号</span><input v-model="measureForm.check_code" required /></label>
        <label class="create-field"><span>检查类型</span><input v-model="measureForm.check_type" required /></label>
        <label class="create-field"><span>阈值下限</span><input v-model.number="measureForm.threshold_low" type="number" required /></label>
        <label class="create-field"><span>阈值上限</span><input v-model.number="measureForm.threshold_high" type="number" required /></label>
        <label class="create-field"><span>权重（同窗口合计≤1）</span><input v-model.number="measureForm.weight" type="number" step="0.1" min="0" max="1" required /></label>
        <label class="create-field"><span>实测值（留空=无证据）</span><input v-model="measureForm.measured_value" /></label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showMeasureModal = false">取消</button>
          <button class="btn primary" data-testid="submit-measure" type="submit">确认</button>
        </div>
      </form>
    </div>

    <!-- 复测弹窗 -->
    <div v-if="showRetestModal" class="modal-backdrop" data-testid="retest-modal">
      <form class="modal" @submit.prevent="submitRetest">
        <h3>质量规则复测 · {{ retestCheckCode }}</h3>
        <label class="create-field"><span>复测值</span><input v-model.number="retestForm.new_value" type="number" required /></label>
        <label class="create-field"><span>复测结论（回写巡检待办）</span><input v-model="retestForm.conclusion" required /></label>
        <label class="create-field"><span>复测人</span><input v-model="retestForm.reviewer" required /></label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showRetestModal = false">取消</button>
          <button class="btn primary" data-testid="submit-retest" type="submit">提交复测</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>反馈表 survey_week：质量维度弱、元数据齐；结算表 payout_week：证据分布相反。</span>
      <span v-if="feedback" class="trend-up">{{ feedback }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  addSupplementalMeasure,
  fetchContributionView,
  recomputeScore,
  registerLateMeasure,
  retestMeasure,
} from '@/api/backend-service'
import type { ContributionView, LateMeasurePayload } from '@/data/contribution'

const tableCode = ref('survey_week')
const assessWindow = ref('2026-W46')
const view = ref<ContributionView | null>(null)
const errorMessage = ref('')
const feedback = ref('')

const basisLabel = computed(() =>
  view.value?.score.scoring_basis === 'evidence_v1' ? '证据化评分' : '旧口径评分')

function evidenceClass(state: string): string {
  if (state === '实际零分' || state.includes('零分')) return 'badge-zero'
  if (state === '无证据' || state.includes('无证据')) return 'badge-none'
  return 'badge-ok'
}

async function loadView() {
  errorMessage.value = ''
  try {
    view.value = await fetchContributionView({
      tableCode: tableCode.value,
      window: assessWindow.value,
    })
    if (!view.value.ok) errorMessage.value = view.value.error ?? '未找到评分'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '贡献视图读取失败'
  }
}

function usePreset(table: string, win = '2026-W46') {
  tableCode.value = table
  assessWindow.value = win
  void loadView()
}

async function onRecompute() {
  if (!view.value) return
  try {
    await recomputeScore(view.value.score.score_id)
    feedback.value = '重算完成'
    await loadView()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重算失败'
  }
}

// ── 补登记 / 补录 ──
const showMeasureModal = ref(false)
const measureMode = ref<'late' | 'supplement'>('late')
const measureForm = ref<LateMeasurePayload>(emptyMeasureForm())

function emptyMeasureForm(): LateMeasurePayload {
  return {
    table_code: '', window: '', check_code: '', check_type: '完整性',
    threshold_low: 95, threshold_high: 100, weight: 0.2, measured_value: '',
  }
}

function openLate() {
  measureMode.value = 'late'
  measureForm.value = { ...emptyMeasureForm(), table_code: tableCode.value, window: assessWindow.value }
  showMeasureModal.value = true
}

function openSupplement() {
  measureMode.value = 'supplement'
  measureForm.value = { ...emptyMeasureForm(), table_code: tableCode.value, window: assessWindow.value }
  showMeasureModal.value = true
}

async function submitMeasure() {
  try {
    const payload = { ...measureForm.value }
    if (payload.measured_value === '') delete payload.measured_value
    const result = measureMode.value === 'late'
      ? await registerLateMeasure(payload)
      : await addSupplementalMeasure(payload)
    if (!result.ok) throw new Error(result.error ?? '提交失败')
    showMeasureModal.value = false
    feedback.value = measureMode.value === 'late' ? '补登记完成：原始评分不变' : '补录检查已进入复测清单'
    await loadView()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交失败'
  }
}

// ── 复测 ──
const showRetestModal = ref(false)
const retestMeasureId = ref(0)
const retestCheckCode = ref('')
const retestForm = ref({ new_value: 95, conclusion: '', reviewer: '' })

function openRetest(measureId: number, checkCode: string) {
  retestMeasureId.value = measureId
  retestCheckCode.value = checkCode
  retestForm.value = { new_value: 95, conclusion: '', reviewer: '' }
  showRetestModal.value = true
}

async function submitRetest() {
  try {
    const result = await retestMeasure(retestMeasureId.value, { ...retestForm.value })
    if (!result.ok) throw new Error(result.error ?? '复测失败')
    showRetestModal.value = false
    feedback.value = '复测结论已回写巡检待办，原始分项依据保留'
    await loadView()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复测失败'
  }
}

onMounted(loadView)
</script>

<style scoped>
.conflict-tag {
  font-style: normal;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #fef3c7;
  color: #b45309;
  font-size: 11px;
}
.entry-tag {
  font-style: normal;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 4px;
  background: #e0e7ff;
  color: #3730a3;
  font-size: 11px;
}
.check-list { margin: 4px 0; padding-left: 16px; font-size: 13px; }
.check-list.compact { padding-left: 0; list-style: none; }
.check-list li { margin-bottom: 4px; }
.muted { color: var(--muted); }
.badge-ok { color: #15803d; font-weight: 600; }
.badge-zero { color: #b42318; font-weight: 600; }
.badge-none { color: #b45309; font-weight: 600; }
.recompute-box { margin: 12px 0; }
h3 { margin: 16px 0 8px; font-size: 15px; }
</style>
