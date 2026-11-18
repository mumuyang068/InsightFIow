<script setup lang="ts">
defineProps<{ title: string; scope?: string; status?: string }>()
</script>

<template>
  <article class="result-card">
    <header class="result-head">
      <div class="result-title">
        <h3>{{ title }}</h3>
        <p v-if="scope">{{ scope }}</p>
      </div>
      <slot name="badge">
        <span v-if="status" class="result-state" role="status">{{ status }}</span>
      </slot>
    </header>
    <div class="result-content"><slot /></div>
    <footer v-if="$slots.actions" class="result-actions"><slot name="actions" /></footer>
  </article>
</template>

<style scoped>
.result-card { border: 1px solid #dfe5e2; border-radius: 12px; background: #fff; color: #1f2329; min-width: 0; overflow-wrap: anywhere; }
.result-head { display: flex; justify-content: space-between; align-items: start; flex-wrap: wrap; gap: 8px 16px; padding: 16px 24px; border-bottom: 1px solid #ecf0ee; }
.result-title { min-width: 0; }
.result-title h3 { margin: 0; font-size: 17px; font-weight: 650; line-height: 1.4; }
.result-title p { color: #535b63; font-size: 13px; line-height: 1.5; margin: 4px 0 0; }
.result-state { font-size: 12px; line-height: 1.5; color: #405b52; padding: 3px 8px; background: #edf5f1; border-radius: 6px; }
.result-content { padding: 16px 24px; }
.result-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px 24px; border-top: 1px solid #ecf0ee; }
.result-actions :deep(.ed-button + .ed-button) { margin-left: 0; }
@media (max-width: 800px) {
  .result-head,.result-content,.result-actions { padding-inline: 16px; }
}
</style>
