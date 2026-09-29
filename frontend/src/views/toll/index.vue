<template>
  <section class="page" data-module="toll">
    <header class="page-head">
      <div>
        <h2>通行费用管理</h2>
        <p class="page-desc">
          过路记录、收费站、通行方向、收费金额统一判定：金额阈值、例外路线、判定依据集中在同一口径，
          车辆列表、过路明细、费用汇总读取同一结论。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记/补录过路记录</button>
        <button class="btn" type="button" @click="showRules = true">查看统一口径</button>
        <button class="btn" type="button" @click="exportRows">导出费用清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
      </label>
      <label class="filter-item">
        <span>车辆编号</span>
        <input v-model="filters.vehicle_id" placeholder="如 VEHI-0001" />
      </label>
      <label class="filter-item">
        <span>收费站</span>
        <input v-model="filters.station" placeholder="按收费站名称检索" />
      </label>
      <label class="filter-item">
        <span>通行方向</span>
        <select v-model="filters.direction">
          <option value="">全部</option>
          <option value="进京">进京</option>
          <option value="出京">出京</option>
        </select>
      </label>
      <label class="filter-item">
        <span>记录状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option value="待确认">待确认</option>
          <option value="已确认">已确认</option>
          <option value="已冲销">已冲销</option>
        </select>
      </label>
      <label class="filter-item">
        <span>费用结论</span>
        <select v-model="filters.abnormal">
          <option value="">全部</option>
          <option value="true">仅异常待核</option>
          <option value="false">非待核</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['记录编号'] }}</td>
          <td>{{ row['车辆编号'] }}</td>
          <td>{{ row['收费站名称'] }}</td>
          <td>¥{{ formatAmount(row['收费金额']) }}</td>
          <td>{{ row['通行方向'] }}</td>
          <td>{{ row['通行日期'] }}</td>
          <td>
            <span :class="badgeClass(row)">{{ row['判定结论'] }}</span>
            <div v-if="row['异常类型']" class="muted-cell">{{ row['异常类型'] }}</div>
          </td>
          <td>{{ row['适用规则版本'] }}</td>
          <td>{{ row['状态'] }}</td>
          <td>{{ row['凭证编号'] || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">明细/依据</button>
            <button
              v-if="row['状态'] === '待确认' && !row['最终结论']"
              class="link"
              type="button"
              @click="openConfirm(row)"
            >
              业务确认
            </button>
            <button
              v-if="row['状态'] !== '已冲销'"
              class="link"
              type="button"
              @click="openReverse(row)"
            >
              冲销费用
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的过路记录，可先登记/补录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条过路记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记/补录 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <div class="modal">
        <h3>登记 / 补录过路记录</h3>
        <div class="tip-box">
          提交后立即按统一口径给出结论；同一「车辆 + 收费站 + 方向 + 日期」的并发补录只允许一条最终结论。
        </div>
        <div class="form-grid">
          <label v-for="field in createFields" :key="field.key" :class="{ full: field.full }">
            <span>{{ field.label }}{{ field.required ? ' *' : '' }}</span>
            <input v-if="field.type !== 'select'" v-model="createForm[field.key]" :placeholder="field.placeholder" />
            <select v-else v-model="createForm[field.key]">
              <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
            </select>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createOpen = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">提交并判定</button>
        </div>
      </div>
    </div>

    <!-- 业务确认 -->
    <div v-if="confirmTarget" class="modal-mask" @click.self="confirmTarget = null">
      <div class="modal">
        <h3>业务确认：{{ confirmTarget['记录编号'] }}</h3>
        <div class="tip-box">
          规则口径：<strong>{{ confirmTarget['规则结论'] }}</strong>（{{ confirmTarget['适用规则版本'] }}）。
          多规则冲突或金额判异时，必须填写业务确认凭证，凭证结论作为唯一最终结论。
        </div>
        <p class="muted-cell">{{ confirmTarget['规则依据'] }}</p>
        <div class="form-grid">
          <label class="full">
            <span>最终结论 *</span>
            <select v-model="confirmForm['最终结论']">
              <option value="正常">正常</option>
              <option value="例外放行">例外放行</option>
            </select>
          </label>
          <label class="full">
            <span>业务确认凭证编号{{ confirmTarget['规则结论'] === '异常' ? ' *（判异记录必填）' : ''}}</span>
            <input v-model="confirmForm['凭证编号']" placeholder="如 PZ-20260915-01" />
          </label>
          <label class="full">
            <span>确认说明</span>
            <textarea v-model="confirmForm['备注']" rows="3" placeholder="说明业务确认的事实依据"></textarea>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="confirmTarget = null">取消</button>
          <button class="btn primary" type="button" @click="submitConfirm">落最终结论</button>
        </div>
      </div>
    </div>

    <!-- 冲销 -->
    <div v-if="reverseTarget" class="modal-mask" @click.self="reverseTarget = null">
      <div class="modal">
        <h3>冲销费用：{{ reverseTarget['记录编号'] }}</h3>
        <p class="muted-cell">冲销后金额不计入费用汇总，记录与判定依据保留留痕，不可重复冲销。</p>
        <div class="form-grid">
          <label class="full">
            <span>冲销原因 *</span>
            <textarea v-model="reverseReason" rows="3" placeholder="如：收费站重复扣费，已退回"></textarea>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="reverseTarget = null">取消</button>
          <button class="btn primary" type="button" @click="submitReverse">确认冲销</button>
        </div>
      </div>
    </div>

    <!-- 明细/判定依据 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <h3>过路明细：{{ detail['记录编号'] }}</h3>
        <table class="data-table">
          <tbody>
            <tr v-for="kv in detailPairs" :key="kv[0]">
              <th style="width: 130px">{{ kv[0] }}</th>
              <td>{{ kv[1] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <h4>判定历史（留痕，不可篡改）</h4>
        <ol class="timeline">
          <li v-for="(item, index) in detail['判定历史']" :key="index">
            <span class="tl-time">{{ item['时间'] }}</span>
            <span :class="historyBadgeClass(item['判定结论'])">{{ item['动作'] }} · {{ item['判定结论'] }}</span>
            <div>{{ item['判定依据'] }}</div>
            <div v-if="item['凭证编号']" class="muted-cell">凭证：{{ item['凭证编号'] }}（{{ item['适用规则版本'] }}）</div>
          </li>
        </ol>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 统一口径 -->
    <div v-if="showRules" class="modal-mask" @click.self="showRules = false">
      <div class="modal">
        <h3>通行费用统一判定口径</h3>
        <h4>金额阈值（按生效日期版本化，历史费用按当时规则留痕）</h4>
        <table class="data-table">
          <thead>
            <tr><th>版本</th><th>下限</th><th>上限</th><th>生效日期</th><th>失效日期</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="v in rules.阈值版本" :key="v.版本">
              <td>{{ v.版本 }}</td>
              <td>¥{{ formatAmount(v.金额下限) }}</td>
              <td>¥{{ formatAmount(v.金额上限) }}</td>
              <td>{{ v.生效日期 }}</td>
              <td>{{ v.失效日期 || '长期有效' }}</td>
              <td>{{ v.说明 }}</td>
            </tr>
          </tbody>
        </table>
        <h4>例外路线（数字越大优先级越高；同优先级结论冲突 → 待核，以业务凭证为准）</h4>
        <table class="data-table">
          <thead>
            <tr><th>优先级</th><th>收费站</th><th>方向</th><th>处置</th><th>适用版本</th><th>依据说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rules.例外路线" :key="r.id">
              <td>{{ r.优先级 }}</td>
              <td>{{ r.收费站名称 }}</td>
              <td>{{ r.通行方向 }}</td>
              <td>{{ r.处置结论 }}</td>
              <td>{{ r.适用版本 }}</td>
              <td>{{ r.依据说明 }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="showRules = false">我知道了</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/toll'
const columns = [
  '记录编号', '车辆编号', '收费站名称', '收费金额', '通行方向',
  '通行日期', '判定结论', '适用规则版本', '状态', '凭证编号',
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({
  keyword: '', vehicle_id: '', station: '', direction: '', status: '', abnormal: '',
})

const summary = ref<Record<string, any>>({})
const statCards = computed(() => [
  { label: `${summary.value['月份'] ?? ''} 已确认费用`, value: `¥${formatAmount(summary.value['已确认费用'])}` },
  { label: '已确认笔数', value: `${summary.value['已确认笔数'] ?? 0} 笔` },
  { label: '异常待核（金额）', value: `${summary.value['待核异常笔数'] ?? 0} 笔 / ¥${formatAmount(summary.value['待核异常金额'])}` },
  { label: '例外放行 / 已冲销', value: `${summary.value['例外放行笔数'] ?? 0} / ${summary.value['已冲销笔数'] ?? 0} 笔` },
])

function formatAmount(value: unknown): string {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num.toFixed(2) : '0.00'
}

function badgeClass(row: Row): string {
  const conclusion = String(row['判定结论'] ?? '')
  if (conclusion === '正常') return 'badge badge-normal'
  if (conclusion === '例外放行') return 'badge badge-exception'
  if (conclusion === '冲销') return 'badge badge-reversed'
  return 'badge badge-abnormal'
}

function historyBadgeClass(conclusion: string): string {
  if (conclusion === '正常') return 'badge badge-normal'
  if (conclusion === '例外放行') return 'badge badge-exception'
  if (conclusion === '冲销') return 'badge badge-reversed'
  return 'badge badge-abnormal'
}

function resetFilters() {
  filters.value = { keyword: '', vehicle_id: '', station: '', direction: '', status: '', abnormal: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) params.set(key, value)
  }
  try {
    const [listResp, summaryResp] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}/summary`),
    ])
    if (!listResp.ok) throw new Error('过路记录列表读取失败')
    if (!summaryResp.ok) throw new Error('费用汇总读取失败')
    const payload = await listResp.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    summary.value = await summaryResp.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '通行费用数据读取失败'
  }
}

// ---------- 登记/补录 ----------

const createOpen = ref(false)
interface CreateField {
  key: string
  label: string
  required?: boolean
  placeholder?: string
  type?: 'select'
  options?: string[]
  full?: boolean
}
const createFields: CreateField[] = [
  { key: '车辆编号', label: '车辆编号', required: true, placeholder: '如 VEHI-0001' },
  { key: '收费站名称', label: '收费站名称', required: true, placeholder: '如 京哈高速-白鹿' },
  { key: '通行方向', label: '通行方向', required: true, type: 'select', options: ['进京', '出京'] },
  { key: '收费金额', label: '收费金额（元）', required: true, placeholder: '0.00' },
  { key: '通行日期', label: '通行日期', required: true, placeholder: '2026-09-20' },
  { key: '凭证编号', label: '凭证编号', placeholder: '选填' },
]
const createForm = reactive<Record<string, string>>({
  车辆编号: '', 收费站名称: '', 通行方向: '进京', 收费金额: '', 通行日期: '', 凭证编号: '',
})

function openCreate() {
  Object.assign(createForm, {
    车辆编号: '', 收费站名称: '', 通行方向: '进京', 收费金额: '', 通行日期: '', 凭证编号: '',
  })
  errorMessage.value = ''
  createOpen.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '登记失败')
    createOpen.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '过路记录登记失败'
  }
}

// ---------- 业务确认 ----------

const confirmTarget = ref<Row | null>(null)
const confirmForm = reactive<Record<string, string>>({ 最终结论: '正常', 凭证编号: '', 备注: '' })

function openConfirm(row: Row) {
  confirmTarget.value = row
  Object.assign(confirmForm, { 最终结论: '正常', 凭证编号: '', 备注: '' })
  errorMessage.value = ''
}

async function submitConfirm() {
  if (!confirmTarget.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${confirmTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: { action: '业务确认', ...confirmForm },
      }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '业务确认未生效')
    confirmTarget.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务确认失败'
  }
}

// ---------- 冲销 ----------

const reverseTarget = ref<Row | null>(null)
const reverseReason = ref('')

function openReverse(row: Row) {
  reverseTarget.value = row
  reverseReason.value = ''
  errorMessage.value = ''
}

async function submitReverse() {
  if (!reverseTarget.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${reverseTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: { action: '冲销费用', 冲销原因: reverseReason.value },
      }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '冲销未生效')
    reverseTarget.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '冲销失败'
  }
}

// ---------- 明细 ----------

const detail = ref<Row | null>(null)
const detailPairs = computed<Array<[string, unknown]>>(() => {
  if (!detail.value) return []
  const d = detail.value
  return [
    ['记录编号', d['记录编号']],
    ['车辆编号', d['车辆编号']],
    ['收费站名称', d['收费站名称']],
    ['收费金额', `¥${formatAmount(d['收费金额'])}`],
    ['通行方向', d['通行方向']],
    ['通行日期', d['通行日期']],
    ['凭证编号', d['凭证编号']],
    ['记录状态', d['状态']],
    ['规则结论', d['规则结论']],
    ['最终结论', d['最终结论'] || '（尚未业务确认）'],
    ['异常类型', d['异常类型']],
    ['命中例外', d['命中例外']],
    ['适用规则版本', d['适用规则版本']],
    ['判定依据', d['规则依据']],
    ['确认说明', d['确认说明']],
  ]
})

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('过路明细读取失败')
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '过路明细读取失败'
  }
}

// ---------- 统一口径 ----------

const showRules = ref(false)
const rules = ref<Record<string, any>>({ 阈值版本: [], 例外路线: [], 当前生效版本: null })

async function loadRules() {
  try {
    const response = await request(`${ENDPOINT}/rules`)
    if (response.ok) rules.value = await response.json()
  } catch {
    // 口径加载失败不阻塞列表
  }
}

onMounted(() => {
  void reload()
  void loadRules()
})
</script>
