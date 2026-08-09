<template>
  <el-config-provider>
    <div class="page">
      <header class="topbar" @click="$router.push('/')">
        <h2>quickstudy · 文档站 → 中文学习手册</h2>
      </header>
      <router-view />
    </div>
  </el-config-provider>
</template>

<style>
body { margin: 0; background: #f5f7fa; }
.page { max-width: 1080px; margin: 0 auto; padding: 0 16px 40px; }
.topbar { cursor: pointer; padding: 12px 0; }
.topbar h2 { margin: 0; color: #303133; }
</style>
