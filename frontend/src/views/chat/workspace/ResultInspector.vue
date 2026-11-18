<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{ title: string }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()
</script>

<template>
  <el-drawer v-model="open" :title="title" :close-on-press-escape="true" append-to-body size="min(560px, 100vw)">
    <section class="result-inspector" :aria-label="title">
      <slot />
    </section>
    <template #footer>
      <el-button @click="open = false">{{ t('cbiWorkspace.close') }}</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.result-inspector { display: grid; gap: 16px; min-width: 0; overflow-wrap: anywhere; }
</style>
