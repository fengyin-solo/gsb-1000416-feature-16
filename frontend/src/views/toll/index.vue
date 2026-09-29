<template>
  <section class="page" data-module="toll">
    <header class="page-head">
      <div>
        <h2>通行费用管理</h2>
        <p class="page-desc">
          围绕过路记录、收费站、通行方向与收费金额统一判定：金额阈值、例外路线集中成一份口径，
          车辆列表、过路明细、费用汇总读取同一结论。当前生效口径：<strong>{{ policyVersion }}</strong>
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">补录过路记录</button>
        <button class="btn" type="button" @click="showPolicy = !showPolicy">
          {{ showPolicy ? '收起判定口径' : '查看判定口径' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出通行费用清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in cards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ formatMoney(item.value) }}</strong>
      </article>
    </div>

    <!-- 统一口径面板：阈值与例外路线都在这里，判定依据不再散落 -->
    <details v-if="showPolicy" class="policy-panel" open>
      <summary>统一判定口径（{{ policyVersion }}）</summary>
      <p class="policy-rule">{{ policyResolution }}</p>
      <div v-for="version in policyVersions" :key="version.version" class="policy-version">
        <h4>
          {{ version.version }}
          <span class="muted">生效：{{ version.effective_from }} 起{{ version.effective_to ? ` 至 ${version.effective_to}` : '，持续生效' }}</span>
        </h4>
        <p class="muted">{{ version.changelog }}</p>
        <div class="policy-grid">
          <div>
            <h5>金额阈值</h5>
            <table class="data-table">
              <thead><tr><th>收费站</th><th>方向</th><th>标准(元)</th><th>允许区间(元)</th></tr></thead>
              <tbody>
                <tr v-for="t in version.thresholds" :key="`${t.station}-${t.direction}`">
                  <td>{{ t.station }}</td><td>{{ t.direction }}</td><td>{{ t.standard }}</td>
                  <td>[{{ t.lower }}, {{ t.upper }}]</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div>
            <h5>例外路线（数字越小优先级越高）</h5>
            <table class="data-table">
              <thead><tr><th>编码</th><th>收费站/方向</th><th>优先级</th><th>口径</th></tr></thead>
              <tbody>
                <tr v-for="e in version.exceptions" :key="e.code">
                  <td>{{ e.code }}<div class="muted">{{ e.name }}</div></td>
                  <td>{{ e.station }} / {{ e.direction }}</td>
                  <td>{{ e.priority }}</td>
                  <td>{{ exceptionText(e) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </details>

    <!-- 费用汇总：与车辆列表同一结论 -->
    <section class="summary-panel">
      <h3>车辆费用汇总</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>车辆编号</th><th>过路记录数</th><th>正常记录</th><th>待核记录</th>
            <th>已确认金额</th><th>待核金额</th><th>冲销金额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in byVehicle" :key="item['车辆编号']">
            <td>{{ item['车辆编号'] }}</td>
            <td>{{ item['过路记录数'] }}</td>
            <td>{{ item['正常记录'] }}</td>
            <td :class="{ 'warn-cell': item['待核记录'] > 0 }">{{ item['待核记录'] }}</td>
            <td>{{ formatMoney(item['已确认金额']) }}</td>
            <td :class="{ 'warn-cell': item['待核金额'] > 0 }">{{ formatMoney(item['待核金额']) }}</td>
            <td>{{ formatMoney(item['冲销金额']) }}</td>
          </tr>
          <tr v-if="!byVehicle.length"><td colspan="7" class="empty-state">暂无汇总数据</td></tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号/车辆编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号或车辆编号检索" />
      </label>
      <label class="filter-item">
        <span>收费站</span>
        <input v-model="filters.station" placeholder="按收费站检索" />
      </label>
      <label class="filter-item">
        <span>记录状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>费用待核</span>
        <select v-model="filters.review">
          <option value="">全部</option>
          <option value="true">仅看待核</option>
          <option value="false">排除待核</option>
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
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'review-row': row.abnormal, 'final-row': row.final }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '判定结论'">
              <span :class="row.abnormal ? 'tag tag-review' : 'tag tag-ok'">{{ row[column] }}</span>
              <div v-if="row['异常原因']" class="muted">{{ row['异常原因'] }}</div>
            </template>
            <template v-else-if="column === '收费金额' || column === '应收金额'">{{ formatMoney(row[column]) }}</template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button v-if="canRun(row, '确认费用')" class="link" type="button" @click="runAction('确认费用', row)">确认费用</button>
            <button v-if="canRun(row, '凭证确认')" class="link" type="button" @click="openVoucher(row)">凭证确认</button>
            <button v-if="canRun(row, '冲销费用')" class="link danger" type="button" @click="openWriteOff(row)">冲销费用</button>
            <span v-if="row.final" class="muted">已锁定</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无过路记录，可先补录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条过路记录（历史记录按当时规则版本留痕，不随新口径重算）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 补录弹窗 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>补录过路记录</h3>
        <p class="muted">并发补录按「车辆+收费站+方向+日期+金额」去重，同一笔过路事实只允许一个最终结论。</p>
        <label v-for="field in createFields" :key="field" class="form-item">
          <span>{{ field }}</span>
          <input v-model="createForm[field]" :type="field === '收费金额' ? 'number' : 'text'" :placeholder="`请输入${field}`" />
        </label>
        <label class="form-item">
          <span>凭证编号（选填）</span>
          <input v-model="createForm['凭证编号']" placeholder="有业务确认凭证时填写" />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="createOpen = false">取消</button>
          <button class="btn primary" type="submit">提交补录</button>
        </div>
      </form>
    </div>

    <!-- 凭证确认弹窗：多规则冲突/金额异常的最终裁决 -->
    <div v-if="voucherOpen" class="modal-mask" @click.self="voucherOpen = false">
      <form class="modal" @submit.prevent="submitVoucher">
        <h3>业务确认凭证裁决</h3>
        <p class="muted">
          记录 {{ voucherTarget?.['记录编号'] }}（{{ voucherTarget?.['异常原因'] }}）。
          凭证结论优先且提交后锁定，是唯一最终结论。
        </p>
        <label class="form-item">
          <span>凭证编号（必填）</span>
          <input v-model="voucherForm['凭证编号']" placeholder="如 BIZ-V-0101" />
        </label>
        <label class="form-item">
          <span>确认入账金额（元）</span>
          <input v-model="voucherForm['确认金额']" type="number" :placeholder="`默认按实付 ${voucherTarget?.['收费金额']} 元`" />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="voucherOpen = false">取消</button>
          <button class="btn primary" type="submit">确认最终结论</button>
        </div>
      </form>
    </div>

    <!-- 冲销弹窗 -->
    <div v-if="writeOffOpen" class="modal-mask" @click.self="writeOffOpen = false">
      <form class="modal" @submit.prevent="submitWriteOff">
        <h3>冲销费用</h3>
        <label class="form-item">
          <span>冲销原因（选填）</span>
          <input v-model="writeOffReason" placeholder="如重复扣费、误入站" />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="writeOffOpen = false">取消</button>
          <button class="btn primary" type="submit">确认冲销并锁定</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | string[]>

const ENDPOINT = '/api/toll'
const columns = ['记录编号', '车辆编号', '收费站名称', '通行方向', '收费金额', '应收金额', '通行日期', '凭证编号', '判定结论', '记录状态']
const statuses = ['待确认', '待核', '已确认', '待冲销']
const createFields = ['车辆编号', '收费站名称', '收费金额', '通行方向', '通行日期']

interface SummaryItem {
  车辆编号: string
  过路记录数: number
  正常记录: number
  待核记录: number
  已确认金额: number
  待核金额: number
  冲销金额: number
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', station: '', status: '', review: '' })

const cards = ref<{ label: string; value: number }[]>([])
const byVehicle = ref<SummaryItem[]>([])
const policyVersion = ref('')
const policyResolution = ref('')
const policyVersions = ref<any[]>([])
const showPolicy = ref(false)

const createOpen = ref(false)
const createForm = reactive<Record<string, string | number>>({})
const voucherOpen = ref(false)
const voucherTarget = ref<Row | null>(null)
const voucherForm = reactive<Record<string, string | number>>({ 凭证编号: '', 确认金额: '' })
const writeOffOpen = ref(false)
const writeOffTarget = ref<Row | null>(null)
const writeOffReason = ref('')

const createPayload = computed(() => {
  const values: Record<string, string | number> = {}
  createFields.forEach((field) => {
    const raw = createForm[field]
    values[field] = field === '收费金额' && raw !== '' ? Number(raw) : raw
  })
  if (createForm['凭证编号']) values['凭证编号'] = createForm['凭证编号']
  return JSON.stringify({ values })
})

function formatMoney(value: unknown): string {
  const num = Number(value ?? 0)
  return `¥${num.toFixed(2)}`
}

function exceptionText(item: { mode: string; amount: number }): string {
  if (item.mode === 'exempt') return '全免'
  if (item.mode === 'fixed') return `固定 ${item.amount} 元`
  return `标准金额 × ${item.amount}`
}

function resetFilters() {
  filters.value = { keyword: '', station: '', status: '', review: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function canRun(row: Row, action: string): boolean {
  if (row.final) return false
  const status = String(row.status ?? '')
  if (action === '确认费用') return status === '待确认'
  if (action === '凭证确认') return status === '待核'
  if (action === '冲销费用') return status === '待确认' || status === '待核'
  return false
}

function openCreate() {
  createFields.forEach((field) => { createForm[field] = '' })
  createForm['凭证编号'] = ''
  createOpen.value = true
}

async function submitCreate() {
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: createPayload.value })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    createOpen.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补录失败'
  }
}

function openVoucher(row: Row) {
  voucherTarget.value = row
  voucherForm['凭证编号'] = String(row['凭证编号'] ?? '')
  voucherForm['确认金额'] = ''
  voucherOpen.value = true
}

async function submitVoucher() {
  if (!voucherTarget.value) return
  const values: Record<string, string | number> = {
    action: '凭证确认',
    凭证编号: voucherForm['凭证编号'],
  }
  if (voucherForm['确认金额'] !== '') values['确认金额'] = Number(voucherForm['确认金额'])
  try {
    const response = await request(`${ENDPOINT}/${voucherTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    voucherOpen.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '凭证确认失败'
  }
}

function openWriteOff(row: Row) {
  writeOffTarget.value = row
  writeOffReason.value = ''
  writeOffOpen.value = true
}

async function submitWriteOff() {
  if (!writeOffTarget.value) return
  try {
    const response = await request(`${ENDPOINT}/${writeOffTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '冲销费用', 冲销原因: writeOffReason.value } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    writeOffOpen.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '冲销失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '通行费用动作未生效'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  Object.entries(filters.value).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  try {
    const [listResp, summaryResp, policyResp] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}/summary`),
      request(`${ENDPOINT}/policy`),
    ])
    if (!listResp.ok) throw new Error('过路记录列表读取失败')
    const payload = await listResp.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length

    if (summaryResp.ok) {
      const summary = await summaryResp.json()
      cards.value = summary.cards ?? []
      byVehicle.value = summary.by_vehicle ?? []
    }
    if (policyResp.ok) {
      const policy = await policyResp.json()
      policyVersion.value = policy.current_version
      policyResolution.value = policy.resolution
      policyVersions.value = policy.versions ?? []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '通行费用列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.policy-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
}
.policy-panel summary { cursor: pointer; font-weight: 600; }
.policy-rule { font-size: 13px; color: #344054; margin: 8px 0; }
.policy-version { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; }
.policy-version h4 { margin: 4px 0; font-size: 14px; }
.policy-version h5 { font-size: 13px; margin: 6px 0; }
.policy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.muted { color: var(--muted); font-size: 12px; font-weight: normal; }
.summary-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
.summary-panel h3 { margin: 0 0 8px; font-size: 15px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag-ok { background: #ecfdf3; color: #027a48; }
.tag-review { background: #fffaeb; color: #b54708; }
.warn-cell { color: #b54708; font-weight: 600; }
.review-row { background: #fffcf5; }
.final-row { color: var(--muted); }
.link.danger { color: #b42318; }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 380px; max-width: 90vw; }
.modal h3 { margin: 0 0 8px; font-size: 16px; }
.form-item { display: block; margin-bottom: 10px; font-size: 13px; }
.form-item span { display: block; color: var(--muted); margin-bottom: 4px; }
.form-item input { width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.filter-item select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
</style>
