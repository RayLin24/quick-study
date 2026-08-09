<template>
  <div>
    <el-card class="new-job">
      <el-form @submit.prevent="submit">
        <el-input v-model="url" size="large" :disabled="hasActive"
                  placeholder="粘贴官方文档站地址，如 https://fastapi.tiangolo.com">
          <template #append>
            <el-checkbox v-model="withDemos" :disabled="hasActive">生成 Demo</el-checkbox>
          </template>
        </el-input>
        <el-button class="submit" type="primary" size="large" :loading="submitting"
                   :disabled="hasActive || !url.trim()" @click="submit">
          {{ hasActive ? '有任务正在进行（单任务串行）' : '开始生成' }}
        </el-button>
        <el-alert v-if="error" :title="error" type="error" show-icon class="error" />
      </el-form>
    </el-card>

    <h3>历史任务</h3>
    <el-empty v-if="!jobs.length" description="还没有任务" />
    <el-card v-for="j in jobs" :key="j.id" class="job-card" shadow="hover"
             @click="open(j)">
      <div class="row">
        <span class="url">{{ j.url }}</span>
        <el-tag :type="tagType(j.status)">{{ statusText(j) }}</el-tag>
      </div>
      <div class="meta">创建于 {{ fmt(j.created_at) }}<span v-if="j.error">｜{{ j.error }}</span></div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const url = ref('')
const withDemos = ref(true)
const jobs = ref([])
const submitting = ref(false)
const error = ref('')

const ACTIVE = ['queued', 'crawling', 'organizing', 'demoing', 'outlining', 'writing']
const hasActive = computed(() => jobs.value.some(j => ACTIVE.includes(j.status)))

const STATUS_TEXT = {
  queued: '排队中', crawling: '爬取中', organizing: '知识组织', demoing: 'Demo 重构',
  outlining: '生成大纲', awaiting_confirm: '待确认写书', writing: '写作中',
  done: '已完成', failed: '失败', cancelled: '已取消',
}
const statusText = (j) => STATUS_TEXT[j.status] || j.status
const tagType = (s) => (s === 'done' ? 'success'
  : s === 'failed' ? 'danger' : s === 'awaiting_confirm' ? 'warning' : 'info')
const fmt = (ts) => new Date(ts * 1000).toLocaleString('zh-CN')

async function load() { jobs.value = await api.listJobs() }

async function submit() {
  submitting.value = true
  error.value = ''
  try {
    const job = await api.createJob(url.value.trim(), withDemos.value)
    router.push(`/job/${job.id}`)
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

function open(j) {
  router.push(j.status === 'done' ? `/job/${j.id}/read` : `/job/${j.id}`)
}

onMounted(load)
</script>

<style scoped>
.new-job { margin-bottom: 16px; }
.submit { margin-top: 12px; width: 100%; }
.error { margin-top: 12px; }
.job-card { margin-bottom: 10px; cursor: pointer; }
.row { display: flex; justify-content: space-between; align-items: center; }
.url { font-weight: 600; }
.meta { color: #909399; font-size: 13px; margin-top: 6px; }
</style>
