<template>
  <div class="reader">
    <aside class="side">
      <h3 class="book-title">{{ book?.book_title || '加载中…' }}</h3>
      <el-menu :default-active="current" @select="select">
        <el-menu-item v-for="c in chapters" :key="c.no" :index="c.filename || ''"
                      :disabled="!c.filename">
          第{{ c.no }}章 {{ c.title }}
        </el-menu-item>
        <el-menu-item v-if="glossaryTerms.length" index="__glossary">
          附录：术语表
        </el-menu-item>
      </el-menu>
    </aside>
    <main class="content">
      <div v-if="current === '__glossary'">
        <h1>术语表</h1>
        <el-table :data="glossaryTerms" size="small">
          <el-table-column prop="term" label="英文" width="220" />
          <el-table-column prop="zh" label="推荐译名" width="200" />
          <el-table-column prop="note" label="说明" />
        </el-table>
      </div>
      <article v-else class="markdown" v-html="html" />
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import { api } from '../api'

const route = useRoute()
const id = route.params.id
const book = ref(null)
const current = ref('')
const markdown = ref('')

const md = new MarkdownIt({
  html: true, linkify: true,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return `<pre><code class="hljs">${hljs.highlight(code, { language: lang }).value}</code></pre>`
    }
    return `<pre><code class="hljs">${md.utils.escapeHtml(code)}</code></pre>`
  },
})

const chapters = computed(() => book.value?.chapters || [])
const glossaryTerms = computed(() =>
  Object.entries(book.value?.glossary?.terms || {})
    .map(([term, e]) => ({ term, zh: e.keep_english ? '（保留英文）' : e.translation,
                           note: e.note || '' }))
    .sort((a, b) => a.term.localeCompare(b.term)))
const html = computed(() => md.render(markdown.value))

async function select(filename) {
  if (!filename) return
  current.value = filename
  if (filename === '__glossary') return
  const r = await api.getChapter(id, filename)
  markdown.value = r.markdown
}

onMounted(async () => {
  book.value = await api.getBook(id)
  const first = chapters.value.find((c) => c.filename)
  if (first) await select(first.filename)
})
</script>

<style scoped>
.reader { display: flex; gap: 20px; align-items: flex-start; }
.side { width: 300px; flex-shrink: 0; background: #fff; border-radius: 8px;
        position: sticky; top: 16px; max-height: 85vh; overflow: auto; }
.book-title { padding: 12px 16px 0; font-size: 15px; }
.content { flex: 1; background: #fff; border-radius: 8px; padding: 24px 32px; }
.markdown :deep(pre) { background: #f6f8fa; padding: 12px; border-radius: 6px;
                       overflow: auto; }
.markdown :deep(table) { border-collapse: collapse; }
.markdown :deep(th), .markdown :deep(td) { border: 1px solid #dcdfe6;
                                           padding: 6px 12px; }
.markdown :deep(img) { max-width: 100%; }
</style>
