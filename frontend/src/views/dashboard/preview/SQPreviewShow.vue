<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import ResourceTree from '@/views/dashboard/common/ResourceTree.vue'
import EmptyBackgroundSvg from '@/views/dashboard/common/EmptyBackgroundSvg.vue'
import SQPreview from '@/views/dashboard/preview/SQPreview.vue'
import SQPreviewHead from '@/views/dashboard/preview/SQPreviewHead.vue'
import { load_resource_prepare } from '@/views/dashboard/utils/canvasUtils'
import { dashboardStoreWithOut } from '@/stores/dashboard/dashboard.ts'
import { useUserStore } from '@/stores/user'
import type { CanvasItem } from '@/utils/canvas'
import type { SQTreeNode } from '@/views/dashboard/utils/treeNode'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const user = useUserStore()
const tree = ref<InstanceType<typeof ResourceTree>>()
const header = ref<InstanceType<typeof SQPreviewHead>>()
const board = ref<{
  dashboardInfo: Record<string, unknown>
  canvasDataResult: CanvasItem[]
  canvasStyleResult: Record<string, unknown>
  canvasViewInfoPreview: Record<string, unknown>
}>()
const selectedId = computed(() => {
  const id = route.query.dashboardId ?? route.query.resourceId
  return typeof id === 'string' ? id : undefined
})
const directoryOpen = ref(!selectedId.value)
const loading = ref(false)
const unavailable = ref(false)
let loadNumber = 0

dashboardStoreWithOut().canvasDataInit()

function loadBoard(id?: string) {
  const request = ++loadNumber
  board.value = undefined
  unavailable.value = false
  loading.value = !!id
  if (!id) {
    directoryOpen.value = true
    return
  }
  load_resource_prepare({ id }, (result) => {
    if (request !== loadNumber) return
    loading.value = false
    if (result.dashboardInfo) {
      board.value = result
    } else {
      unavailable.value = true
    }
  })
}

watch([selectedId, () => user.getUid, () => user.getOid], ([id]) => loadBoard(id), { immediate: true })
onBeforeUnmount(() => { loadNumber++ })

function openDirectory() {
  void tree.value?.getTree()
  directoryOpen.value = true
}

function chooseBoard(node: SQTreeNode) {
  directoryOpen.value = false
  if (String(node.id) === selectedId.value) {
    loadBoard(selectedId.value)
  } else {
    void router.push({ name: 'dashboard', query: { dashboardId: String(node.id) } })
  }
}

function onDelete(id: string) {
  if (String(id) === selectedId.value) void router.replace({ name: 'dashboard' })
}
</script>

<template>
  <main class="dv-preview" v-loading="loading">
    <SQPreviewHead ref="header" :dashboard-info="board?.dashboardInfo" @open-directory="openDirectory" />
    <div v-if="board" id="sq-preview-content" class="dashboard-content">
      <SQPreview
        :key="selectedId"
        :dashboard-info="board.dashboardInfo"
        :component-data="board.canvasDataResult"
        :canvas-style-data="board.canvasStyleResult"
        :canvas-view-info="board.canvasViewInfoPreview"
        reading-layout
      />
    </div>
    <EmptyBackgroundSvg
      v-else-if="!loading"
      :description="t(unavailable ? 'cbiWorkspace.resultUnavailable' : 'dashboard.select_dashboard_tips')"
    />
    <el-drawer
      v-model="directoryOpen"
      :title="t('dashboard.directory')"
      direction="ltr"
      size="min(440px, 100vw)"
      :close-on-press-escape="true"
      append-to-body
      @opened="tree?.focusSearch()"
      @closed="header?.focusDirectory()"
    >
      <ResourceTree
        ref="tree"
        :key="user.getUid + ':' + user.getOid"
        :selected-id="selectedId"
        @node-click="chooseBoard"
        @delete-cur-resource="onDelete"
        @refresh-selection="loadBoard(selectedId)"
      />
    </el-drawer>
  </main>
</template>

<style lang="less" scoped>
.dv-preview {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
}
.dashboard-content {
  flex: 1;
  min-height: 0;
  min-width: 0;
}
</style>
