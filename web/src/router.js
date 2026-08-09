import { createRouter, createWebHashHistory } from 'vue-router'
import Home from './views/Home.vue'
import JobDetail from './views/JobDetail.vue'
import Reader from './views/Reader.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/job/:id', component: JobDetail },
    { path: '/job/:id/read', component: Reader },
  ],
})
