const base = '/api'

async function req(path, opts = {}) {
  const r = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export const api = {
  listJobs: () => req('/jobs'),
  createJob: (url, withDemos) =>
    req('/jobs', { method: 'POST', body: JSON.stringify({ url, with_demos: withDemos }) }),
  getJob: (id) => req(`/jobs/${id}`),
  getOutline: (id) => req(`/jobs/${id}/outline`),
  confirm: (id) => req(`/jobs/${id}/confirm`, { method: 'POST' }),
  cancel: (id) => req(`/jobs/${id}/cancel`, { method: 'POST' }),
  getBook: (id) => req(`/jobs/${id}/book`),
  getChapter: (id, filename) =>
    req(`/jobs/${id}/chapters/${encodeURIComponent(filename)}`),
}
