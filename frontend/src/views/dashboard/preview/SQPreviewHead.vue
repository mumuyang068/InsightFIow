<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import icon_pc_outlined from '@/assets/svg/icon_pc_outlined.svg'
import icon_edit_outlined from '@/assets/svg/icon_edit_outlined.svg'

const { t } = useI18n()
const router = useRouter()
const props = defineProps<{ dashboardInfo?: Record<string, unknown> }>()
defineEmits<{ openDirectory: [] }>()
const directory = ref<{ $el: HTMLButtonElement }>()

function preview() {
  if (props.dashboardInfo) window.open('#/dashboard-preview?resourceId=' + encodeURIComponent(String(props.dashboardInfo.id)), '_blank')
}

function edit() {
  if (props.dashboardInfo) void router.push({ name: 'canvas', query: { resourceId: String(props.dashboardInfo.id) } })
}

defineExpose({ focusDirectory: () => directory.value?.$el.focus() })
</script>

<template>
  <header class="preview-head">
    <el-button ref="directory" secondary @click="$emit('openDirectory')">{{ t('dashboard.directory') }}</el-button>
    <h1 class="canvas-name">{{ dashboardInfo?.name || t('dashboard.dashboard') }}</h1>
    <div v-if="dashboardInfo" class="canvas-opt-button">
      <el-button secondary @click="preview">
        <template #icon><el-icon><icon_pc_outlined /></el-icon></template>
        {{ t('dashboard.preview') }}
      </el-button>
      <el-button type="primary" @click="edit">
        <template #icon><el-icon><icon_edit_outlined /></el-icon></template>
        {{ t('dashboard.edit') }}
      </el-button>
    </div>
  </header>
</template>

<style lang="less" scoped>
.preview-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  flex-shrink: 0;
  width: 100%;
  padding: 12px 20px;
  border-bottom: 1px solid #e1e4e9;
}
.canvas-name {
  flex: 1;
  min-width: min(100%, 12rem);
  margin: 4px 14px;
  font-size: 18px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.canvas-opt-button {
  display: flex;
  margin-left: auto;
}
</style>
