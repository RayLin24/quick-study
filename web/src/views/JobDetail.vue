<template>
  <div v-if="job">
    <el-card>
      <template #header>
        <div class="row">
          <span class="url">{{ job.url }}</span>
          <el-tag :type="job.status === 'failed' ? 'danger' : 'info'">
            {{ statusText[job.status] || job.status }}
          </el-tag>
        </div>
      </template>

      <el-steps :active="activeStep" align-center finish-status="success">
        <el-step title="爬取解析" />
        <el-step title="知识组织" />
        <el-step :title="job.skipped_demos ? 'Demo（已跳过）' : 'Demo 重构'" />
        <el-step title="生成大纲" />
        <el-step title="分章写作" />
      </el-steps>

      <el-descriptions :column="3" border class="progress">
        <el-descriptions-item v-if="d.crawl" label="页面">
          解析 {{ d.crawl.parsed }}/{{ d.crawl.discovered }}
        </el-descriptions-item>
        <el-descriptions-item v-if="d.organize" label="概念/边">
          {{ d.organize.concepts }}/{{ d.organize.edges }}
        </el-descriptions-item>
        <el-descriptions-item v-if="d.demos" label="Demo">
          {{ d.demos.passed }}/{{ d.demos.done }} 通过
        </el-descriptions-item>
        <el-descriptions-item v-if="d.writing" label="章节">
          {{ d.writing.written }}/{{ d.writing.total }}
        </el-descriptions-item>
        <el-descriptions-item label="token 消耗">
          {{ fmtK(cost.tokens_in) }} 入 / {{ fmtK(cost.tokens_out) }} 出
        </el-descriptions-item>
      </el-descriptions>

      <el-alert v-if="job.error" :title="job.error" type="error" show-icon class="block" />

      <div v-if="job.status === 'awaiting_confirm'" class="gate block">
        <el-alert type="warning" show-icon :closable="false"
                  title="大纲已生成，确认后才消耗写作 token" />
        <h3 v-if="outline">《{{ outline.book_title }}》</h3>
        <el-tree v-if="outline" :data="outlineTree" default-expand-all class="tree" />
        <el-button type="primary" size="large" :loading="acting" @click="doConfirm">
          确认写书
        </el-button>
        <el-button size="large" :loading="acting" @click="doCancel">放弃</el-button>
      </div>

      <div v-if="job.status === 'done'" class="block">
        <el-button type="success" size="large"
                   @click="$router.push(`/job/${job.id}/read`)">开始阅读</el-button>
      </div>
      <div v-if="isActive" class="block">
        <el-button type="danger" plain @click="doCancel">取消任务</el-button>
      </div>
    </el-card>

    <el-card v-if="recentLog.length" class="block">
      <template #header>最近日志</template>
      <pre class="log">{{ recentLog.join('\n') }}</pre>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const id = route.params.id
const job = ref(null)
const outline = ref(null)
const acting = ref(false)
let timer = null

const STATUS_TEXT = {
  queued: '排队中', crawling: '爬取中', organizing: '知识组织', demoing: 'Demo 重构',
  outlining: '生成大纲', awaiting_confirm: '待确认写书', writing: '写作中',
  done: '已完成', failed: '失败', cancelled: '已取消',
}
const STEP_OF = { queued: 0, crawling: 0, organizing: 1, demoing: 2, outlining: 3,
  awaiting_confirm: 4, writing: 4, done: 5, failed: 0, cancelled: 0 }

const d = computed(() => job.value?.progress?.detail || {})
const cost = computed(() => job.value?.progress?.cost || { tokens_in: 0, tokens_out: 0 })
const recentLog = computed(() => job.value?.progress?.recent_log || [])
const activeStep = computed(() => STEP_OF[job.value?.status] ?? 0)
const isActive = computed(() =>
  ['queued', 'crawling', 'organizing', 'demoing', 'outlining', 'writing']
    .includes(job.value?.status))
const outlineTree = computed(() => (outline.value?.chapters || []).map((c) => ({
  label: `第${c.no}章 ${c.title}（${'★'.repeat(c.difficulty)} ~${c.est_hours}h）`,
  children: [{ label: c.summary }],
})))
const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`)

async function refresh() {
  job.value = await api.getJob(id)
  if (job.value.status === 'awaiting_confirm' && !outline.value) {
    outline.value = await api.getOutline(id)
  }
  if (!isActive.value && job.value.status !== 'awaiting_confirm' && timer) {
    clearInterval(timer)
    timer = null
  }
}

async function doConfirm() {
  acting.value = true
  try { await api.confirm(id); await refresh() } finally { acting.value = false }
}

async function doCancel() {
  acting.value = true
  try { await api.cancel(id); await refresh(); router.push('/') }
  finally { acting.value = false }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 2000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
.row { display: flex; justify-content: space-between; align-items: center; }
.url { font-weight: 600; }
.progress { margin-top: 16px; }
.block { margin-top: 16px; }
.tree { margin: 12px 0; }
.log { max-height: 300px; overflow: auto; font-size: 12px; margin: 0; }
</style>
