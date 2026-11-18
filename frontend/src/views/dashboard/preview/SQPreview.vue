<script setup lang="ts">
const dashboardStore = dashboardStoreWithOut()
const { curComponent } = storeToRefs(dashboardStore)

import { onMounted, onBeforeUnmount, toRefs, ref, computed, reactive } from 'vue'
import { dashboardStoreWithOut } from '@/stores/dashboard/dashboard.ts'
import { storeToRefs } from 'pinia'
import SQComponentWrapper from '@/views/dashboard/preview/SQComponentWrapper.vue'
import type { CanvasItem } from '@/utils/canvas.ts'
import { useEmittLazy } from '@/utils/useEmitt.ts'
import { hasSavedRunVisual } from '@/views/dashboard/components/sq-view/savedRun'

const props = defineProps({
  canvasStyleData: {
    type: Object,
    required: false,
    default: () => {},
  },
  componentData: {
    type: Object,
    required: true,
  },
  canvasViewInfo: {
    type: Object,
    required: true,
  },
  dashboardInfo: {
    type: Object,
    required: false,
    default: () => {},
  },
  baseMatrixCount: {
    type: Object,
    default: () => {
      return {
        x: 72,
        y: 36,
      }
    },
  },
  canvasId: {
    type: String,
    required: false,
    default: 'canvas-main',
  },
  readingLayout: Boolean,
  showPosition: {
    required: false,
    type: String,
    default: 'preview',
  },
})

const { componentData, showPosition, canvasId } = toRefs(props)
const domId = 'preview-' + canvasId.value
const previewCanvas = ref<HTMLElement>()
const renderReady = ref(true)
const state = reactive({
  initState: true,
  scrollMain: 0,
})

const cellWidth = ref(0)
const cellHeight = ref(0)
const baseWidth = ref(0)
const baseHeight = ref(0)
const baseMarginLeft = ref(0)
const baseMarginTop = ref(0)
function fitsSavedResult(item: CanvasItem) {
  const view = props.canvasViewInfo[item.id]
  return hasSavedRunVisual(view?.analysis_state, view?.method_result)
}

const readingItems = computed(() => {
  const items = componentData.value as CanvasItem[]
  const first = items[0]
  if (!props.readingLayout || !first || !items.every(item =>
    item.component === 'SQView' && fitsSavedResult(item) &&
    item.x === first.x && item.sizeX === first.sizeX
  )) return undefined

  // Only a vertical sequence of saved cards has an unambiguous reading order.
  const ordered = [...items].sort((a, b) => a.y - b.y)
  return ordered.every((item, index) => {
    const previous = ordered[index - 1]
    return !previous || previous.y + previous.sizeY <= item.y
  }) ? ordered : undefined
})

const fitsGridContent = computed(() => (componentData.value as CanvasItem[]).some(fitsSavedResult))
const comparingInReadingLayout = computed(() =>
  readingItems.value?.some(item => props.canvasViewInfo[item.id]?.analysis_state?.presentation?.decomposition)
)
const gridStyle = computed(() => fitsGridContent.value && !readingItems.value ? {
  gridTemplateColumns: 'repeat(' + props.baseMatrixCount.x + ', minmax(0, 1fr))',
  gridAutoRows: 'minmax(' + Math.max(0, cellHeight.value) + 'px, auto)',
  paddingRight: baseMarginLeft.value + 'px',
} : undefined)

const restore = () => {}

function nowItemStyle(item: CanvasItem) {
  if (readingItems.value) return { position: 'relative' as const, minWidth: 0 }
  if (fitsGridContent.value) {
    return {
      position: 'relative' as const,
      gridColumn: item.x + ' / span ' + item.sizeX,
      gridRow: item.y + ' / span ' + item.sizeY,
      marginLeft: baseMarginLeft.value + 'px',
      marginTop: baseMarginTop.value + 'px',
      contain: fitsSavedResult(item) ? undefined : 'size',
    }
  }
  return {
    width: cellWidth.value * item.sizeX - baseMarginLeft.value + 'px',
    height: cellHeight.value * item.sizeY - baseMarginTop.value + 'px',
    left: cellWidth.value * (item.x - 1) + baseMarginLeft.value + 'px',
    top: cellHeight.value * (item.y - 1) + baseMarginTop.value + 'px',
  }
}

const sizeInit = () => {
  if (previewCanvas.value) {
    const screenWidth = previewCanvas.value.offsetWidth
    const screenHeight = previewCanvas.value.offsetHeight
    baseMarginLeft.value = 10
    baseMarginTop.value = 10
    baseWidth.value =
      (screenWidth - baseMarginLeft.value) / props.baseMatrixCount.x - baseMarginLeft.value
    baseHeight.value =
      (screenHeight - baseMarginTop.value) / props.baseMatrixCount.y - baseMarginTop.value
    cellWidth.value = baseWidth.value + baseMarginLeft.value
    cellHeight.value = baseHeight.value + baseMarginTop.value
  }
  useEmittLazy('view-render-all')
}

let resizeObserver: ResizeObserver | undefined
onMounted(() => {
  sizeInit()
  if (!previewCanvas.value) return
  resizeObserver = new ResizeObserver(sizeInit)
  resizeObserver.observe(previewCanvas.value)
})
onBeforeUnmount(() => resizeObserver?.disconnect())

defineExpose({
  restore,
})
</script>

<template>
  <div
    v-if="state.initState"
    :id="domId"
    ref="previewCanvas"
    class="canvas-container"
  >
    <div
      v-if="renderReady"
      class="preview-layout"
      :class="{ 'reading-grid': readingItems, 'comparison-reading': comparingInReadingLayout, 'content-aware-grid': fitsGridContent && !readingItems }"
      :style="gridStyle"
    >
      <SQComponentWrapper
        v-for="(item, index) in readingItems ?? componentData"
        :key="item.id"
        :active="!!curComponent && item.id === curComponent['id']"
        :config-item="item"
        :canvas-view-info="canvasViewInfo"
        :show-position="showPosition"
        :canvas-id="canvasId"
        :fit-saved-result="fitsSavedResult(item)"
        :style="nowItemStyle(item)"
        :index="index"
      />
    </div>
  </div>
</template>

<style lang="less" scoped>
.canvas-container {
  background: #f5f6f7;
  background-size: 100% 100% !important;
  width: 100%;
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  position: relative;
  &::-webkit-scrollbar {
    width: 0 !important;
    height: 0 !important;
  }

  div::-webkit-scrollbar {
    width: 0 !important;
    height: 0 !important;
  }

  div {
    -ms-overflow-style: none; /* IE and Edge */
    scrollbar-width: none; /* Firefox */
  }
}

.preview-layout {
  display: contents;
}

.preview-layout.content-aware-grid {
  display: grid;
  align-content: start;
}

.preview-layout.reading-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 540px), 1fr));
  align-items: start;
  gap: 16px;
  padding: 16px;

  :deep(.saved-run .saved-metrics) {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, max(142px, calc((100% - 36px) / 3))), 1fr));
  }
}

.preview-layout.reading-grid.comparison-reading {
  grid-template-columns: minmax(0, 1fr);
}

.fix-button {
  position: fixed !important;
}

.datav-preview {
  overflow-y: hidden !important;
}

.datav-preview-unpublish {
  background-color: inherit !important;
}
</style>
