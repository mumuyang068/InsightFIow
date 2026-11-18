<script setup lang="ts">
import icon_add_outlined from '@/assets/svg/icon_add_outlined.svg'
import { treeDraggableChart } from '@/views/dashboard/utils/treeDraggableChart'
import icon_searchOutline_outlined from '@/assets/svg/icon_search-outline_outlined.svg'
import icon_folder from '@/assets/svg/icon_folder.svg'
import ope_add from '@/assets/svg/operate/ope-add.svg'
import icon_dashboard from '@/assets/permission/icon_dashboard.svg'
import icon_edit_outlined from '@/assets/svg/icon_edit_outlined.svg'
import icon_rename from '@/assets/svg/icon_rename.svg'
import icon_delete from '@/assets/svg/icon_delete.svg'
import icon_more_outlined from '@/assets/svg/icon_more_outlined.svg'
import dv_sort_asc from '@/assets/svg/dv-sort-asc.svg'
import dv_sort_desc from '@/assets/svg/dv-sort-desc.svg'
import { onMounted, reactive, ref, watch, nextTick, computed } from 'vue'
import { ElIcon, ElScrollbar } from 'element-plus-secondary'
import { Icon } from '@/components/icon-custom'
import { type SQTreeNode } from '@/views/dashboard/utils/treeNode'
import { useUserStore } from '@/stores/user'
import { dashboardStoreWithOut } from '@/stores/dashboard/dashboard.ts'
import ResourceGroupOpt from '@/views/dashboard/common/ResourceGroupOpt.vue'
import { dashboardApi } from '@/api/dashboard.ts'
import HandleMore from '@/views/dashboard/common/HandleMore.vue'
import { useI18n } from 'vue-i18n'
import treeSort from '@/views/dashboard/utils/treeSortUtils.ts'
import { useCache } from '@/utils/useCache.ts'
const { wsCache } = useCache()
const { wsCache: sessionCache } = useCache('sessionStorage')
const user = useUserStore()
const directoryKey = 'dashboard-directory-' + user.getUid + '-' + user.getOid

const { t } = useI18n()
const dashboardStore = dashboardStoreWithOut()
const resourceGroupOptRef = ref(null)

const props = defineProps<{ selectedId?: string }>()
const defaultProps = { children: 'children', label: 'name' }
const filterText = ref<string>(sessionCache.get(directoryKey + '-search') || '')
const expandedArray = ref<(string | number)[]>([])
const searchInput = ref<{ focus: () => void }>()
const scrollbar = ref<{ setScrollTop: (value: number) => void }>()
const resourceListTree = ref()
const loading = ref(false)
const unavailable = ref(false)
let scrollTop = Number(sessionCache.get(directoryKey + '-scroll')) || 0
let requestNumber = 0

const state = reactive({
  curSortType: 'name_asc',
  curSortTypePrefix: 'name',
  curSortTypeSuffix: '_asc',
  resourceTree: [] as SQTreeNode[],
  originResourceTree: [] as SQTreeNode[],
  sortType: [],
  templateCreatePid: 0,
  menuList: [
    {
      label: t('dashboard.edit'),
      command: 'edit',
      svgName: icon_edit_outlined,
    },
    {
      label: t('dashboard.rename'),
      command: 'rename',
      svgName: icon_rename,
    },
    {
      label: t('dashboard.delete'),
      command: 'delete',
      svgName: icon_delete,
      divided: true,
    },
  ],
})

const { handleDrop, handleDragStart } = treeDraggableChart(state, 'resourceTree', 'dashboard')

const emit = defineEmits<{
  nodeClick: [node: SQTreeNode]
  deleteCurResource: [id: string]
  refreshSelection: []
}>()

const nodeExpand = (data: SQTreeNode) => {
  if (!expandedArray.value.includes(data.id)) expandedArray.value.push(data.id)
}

const nodeCollapse = (data: SQTreeNode) => {
  expandedArray.value = expandedArray.value.filter(id => id !== data.id)
}

const filterNode = (value: string, data: SQTreeNode) => {
  if (!value) return true
  return data.name?.toLocaleLowerCase().includes(value.toLocaleLowerCase())
}

function nodeClick(data: SQTreeNode, node: { disabled: boolean }) {
  if (node.disabled || data.node_type !== 'leaf') {
    resourceListTree.value?.setCurrentKey(props.selectedId ?? null)
    return
  }
  dashboardStore.setCurComponent({ component: null, index: null })
  emit('nodeClick', data)
}

function getParentKeys(nodes: SQTreeNode[], key: string): (string | number)[] | undefined {
  for (const node of nodes) {
    if (String(node.id) === key) return []
    const parents = node.children && getParentKeys(node.children, key)
    if (parents) return [node.id, ...parents]
  }
}

function restoreSelection() {
  resourceListTree.value?.setCurrentKey(props.selectedId ?? null)
}

async function getTree() {
  const request = ++requestNumber
  const savedScroll = scrollTop
  loading.value = true
  unavailable.value = false
  state.originResourceTree = []
  state.resourceTree = []
  try {
    const resources: SQTreeNode[] = await dashboardApi.list_resource({})
    if (request !== requestNumber) return
    state.originResourceTree = resources || []
    handleSortTypeChange(state.curSortType)
    const parents = props.selectedId && getParentKeys(state.resourceTree, props.selectedId)
    if (parents) expandedArray.value = [...new Set([...expandedArray.value, ...parents])]
    await nextTick()
    if (request !== requestNumber) return
    restoreSelection()
    resourceListTree.value?.filter(filterText.value)
    await nextTick()
    if (request === requestNumber) scrollbar.value?.setScrollTop(savedScroll)
  } catch {
    if (request === requestNumber) unavailable.value = true
  } finally {
    if (request === requestNumber) loading.value = false
  }
}

function focusSearch() {
  searchInput.value?.focus()
  scrollbar.value?.setScrollTop(scrollTop)
}

function rememberScroll({ scrollTop: value }: { scrollTop: number }) {
  if (loading.value) return
  scrollTop = value
  sessionCache.set(directoryKey + '-scroll', value)
}

watch(filterText, (value) => {
  sessionCache.set(directoryKey + '-search', value)
  resourceListTree.value?.filter(value)
})
watch(() => props.selectedId, restoreSelection)
onMounted(getTree)

const resourceEdit = (resourceId: SQTreeNode['id']) => {
  window.open('#/canvas?resourceId=' + encodeURIComponent(resourceId), '_self')
}

const addOperation = (params: any) => {
  if (params.opt === 'newLeaf') {
    const newCanvasUrl = '#/canvas?opt=create' + (params?.id ? `&pid=${params?.id}` : '')
    window.open(newCanvasUrl, '_self')
    dashboardStore.canvasDataInit()
  } else {
    // @ts-expect-error eslint-disable-next-line @typescript-eslint/ban-ts-comment
    resourceGroupOptRef.value?.optInit(params)
  }
}

const operation = (opt: string, data: SQTreeNode) => {
  if (opt === 'delete') {
    const msg = data.node_type === 'leaf' ? '' : t('dashboard.delete_tips')
    ElMessageBox.confirm(t('dashboard.delete_dashboard_warn', [data.name]), {
      confirmButtonType: 'danger',
      type: 'warning',
      tip: msg,
      autofocus: false,
      showClose: false,
    }).then(() => {
      dashboardApi.delete_resource({ id: data.id, name: data.name }).then(() => {
        ElMessage.success(t('dashboard.delete_success'))
        getTree()
        dashboardStore.canvasDataInit()
        emit('deleteCurResource', String(data.id))
        emit('refreshSelection')
      })
    })
  } else if (opt === 'rename') {
    // @ts-expect-error eslint-disable-next-line @typescript-eslint/ban-ts-comment
    resourceGroupOptRef.value?.optInit({ opt: 'rename', id: data.id, name: data.name })
  } else if (opt === 'edit') {
    resourceEdit(data.id)
  }
}

const baseInfoChangeFinish = () => {
  getTree()
  emit('refreshSelection')
}

const handleSortTypeChange = (menuSortType: string) => {
  state.curSortTypePrefix = ['name', 'time'].includes(menuSortType)
    ? menuSortType
    : state.curSortTypePrefix
  state.curSortTypeSuffix = ['_asc', '_desc'].includes(menuSortType)
    ? menuSortType
    : state.curSortTypeSuffix
  const curMenuSortType = state.curSortTypePrefix + state.curSortTypeSuffix
  state.resourceTree = treeSort(state.originResourceTree, curMenuSortType)
  state.curSortType = curMenuSortType
  wsCache.set('TreeSort-dashboard', state.curSortType)
}

const sortColumnList = [
  {
    name: t('dashboard.time'),
    value: 'time',
  },
  {
    name: t('dashboard.name'),
    value: 'name',
    divided: true,
  },
]

const sortTypeList = [
  {
    name: t('dashboard.sort_asc'),
    value: '_asc',
  },
  {
    name: t('dashboard.sort_desc'),
    value: '_desc',
  },
]

const sortList = [
  {
    name: t('dashboard.time_asc'),
    value: 'time_asc',
  },
  {
    name: t('dashboard.time_desc'),
    value: 'time_desc',
    divided: true,
  },
  {
    name: t('dashboard.name_asc'),
    value: 'name_asc',
  },
  {
    name: t('dashboard.name_desc'),
    value: 'name_desc',
  },
]

const sortTypeTip = computed(() => {
  // @ts-expect-error eslint-disable-next-line @typescript-eslint/ban-ts-comment
  return sortList.find((ele) => ele.value === state.curSortType).name
})

defineExpose({ getTree, focusSearch })
</script>

<template>
  <div class="resource-tree">
    <div class="tree-header">
      <div class="icon-methods">
        <el-tooltip
          :offset="12"
          :content="t('dashboard.new_dashboard')"
          placement="top"
          effect="dark"
        >
          <el-button
            text
            class="directory-add"
            :aria-label="t('dashboard.new_dashboard')"
            @click="addOperation({ opt: 'newLeaf', type: 'dashboard' })"
          >
            <el-icon><Icon name="dv-new-folder"><ope_add class="svg-icon" /></Icon></el-icon>
          </el-button>
        </el-tooltip>
      </div>
      <el-input
        ref="searchInput"
        v-model="filterText"
        :aria-label="t('dashboard.search')"
        :placeholder="t('dashboard.search')"
        clearable
        class="search-bar"
      >
        <template #prefix>
          <el-icon>
            <Icon name="icon_search-outline_outlined">
              <icon_searchOutline_outlined class="svg-icon" />
            </Icon>
          </el-icon>
        </template>
      </el-input>
      <el-dropdown
        popper-class="tree-sort-menu-custom"
        trigger="click"
        placement="bottom-end"
        @command="handleSortTypeChange"
      >
        <el-icon class="filter-icon-span" :class="state.curSortType !== 'name_asc' && 'active'">
          <el-tooltip :offset="16" effect="dark" :content="sortTypeTip" placement="top">
            <Icon v-if="state.curSortType.includes('asc')" name="dv-sort-asc" class="opt-icon"
              ><dv_sort_asc class="svg-icon opt-icon"
            /></Icon>
          </el-tooltip>
          <el-tooltip :offset="16" effect="dark" :content="sortTypeTip" placement="top">
            <Icon v-if="state.curSortType.includes('desc')" name="dv-sort-desc" class="opt-icon"
              ><dv_sort_desc class="svg-icon opt-icon"
            /></Icon>
          </el-tooltip>
        </el-icon>
        <template #dropdown>
          <el-dropdown-menu style="width: 120px">
            <span class="sort_menu">{{ t('dashboard.sort_column') }}</span>
            <template v-for="ele in sortColumnList" :key="ele.value">
              <el-dropdown-item
                class="ed-select-dropdown__item"
                :class="state.curSortType.includes(ele.value) && 'selected'"
                :command="ele.value"
              >
                {{ ele.name }}
              </el-dropdown-item>
              <li v-if="ele.divided" class="ed-dropdown-menu__item--divided"></li>
            </template>
            <span class="sort_menu">{{ t('dashboard.sort_type') }}</span>
            <template v-for="ele in sortTypeList" :key="ele.value">
              <el-dropdown-item
                class="ed-select-dropdown__item"
                :class="state.curSortType.includes(ele.value) && 'selected'"
                :command="ele.value"
              >
                {{ ele.name }}
              </el-dropdown-item>
            </template>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
    <el-scrollbar ref="scrollbar" v-loading="loading" class="custom-tree" @scroll="rememberScroll">
      <el-tree
        ref="resourceListTree"
        style="overflow-x: hidden"
        menu
        :empty-text="t(unavailable ? 'cbiWorkspace.resultUnavailable' : 'dashboard.no_dashboard')"
        :default-expanded-keys="expandedArray"
        :data="state.resourceTree"
        :props="defaultProps"
        node-key="id"
        highlight-current
        :expand-on-click-node="true"
        :filter-node-method="filterNode"
        draggable
        @node-expand="nodeExpand"
        @node-collapse="nodeCollapse"
        @node-click="nodeClick"
        @node-drag-start="handleDragStart"
        @node-drop="handleDrop"
      >
        <template #default="{ node, data }">
          <span class="custom-tree-node">
            <el-icon v-if="data.node_type !== 'leaf'" style="font-size: 18px">
              <Icon name="icon_folder"><icon_folder class="svg-icon" /></Icon>
            </el-icon>
            <el-icon v-else class="icon-primary" style="font-size: 18px">
              <Icon name="icon_dashboard"><icon_dashboard class="svg-icon" /></Icon>
            </el-icon>
            <button
              v-if="data.node_type === 'leaf'"
              type="button"
              class="directory-leaf"
              :title="node.label"
              :disabled="!!node.disabled"
              :aria-current="String(data.id) === selectedId ? 'page' : undefined"
              @click.stop="nodeClick(data, node)"
              @keydown.enter.stop
              @keydown.space.stop
            >
              {{ node.label }}
            </button>
            <span v-else :title="node.label" class="label-tooltip">{{ node.label }}</span>
            <div class="icon-more">
              <el-icon
                v-if="data.node_type !== 'leaf'"
                class="hover-icon"
                @click.stop
                @click="addOperation({ opt: 'newLeaf', type: 'dashboard', id: data.id })"
              >
                <Icon><icon_add_outlined class="svg-icon" /></Icon>
              </el-icon>
              <HandleMore
                :menu-list="state.menuList"
                :icon-name="icon_more_outlined"
                placement="bottom-end"
                @handle-command="(opt: string) => operation(opt, data)"
              ></HandleMore>
            </div>
          </span>
        </template>
      </el-tree>
    </el-scrollbar>
    <ResourceGroupOpt ref="resourceGroupOptRef" @finish="baseInfoChangeFinish"></ResourceGroupOpt>
  </div>
</template>
<style lang="less" scoped>
.filter-icon-span {
  border: 1px solid #d9dcdf;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  color: #1f2329;
  padding: 8px;
  margin-left: 8px;
  font-size: 16px;
  cursor: pointer;

  .opt-icon:focus {
    outline: none !important;
  }

  &:hover,
  &:focus {
    background: #f5f6f7;
  }

  &:active {
    background: #eff0f1;
  }

  &.active {
    border: 1px solid var(--ed-color-primary);
    color: var(--ed-color-primary);

    &:hover,
    &:focus {
      background: var(--ed-color-primary-80, #d2f1e9);
    }

    &:active {
      background: var(--ed-color-primary-60, #a4e3d3);
    }
  }
}

.resource-tree {
  padding: 16px 0 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;

  .tree-header {
    padding: 0 16px;
  }

  .icon-methods {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding-bottom: 8px;
  }

  .directory-add {
    color: var(--ed-color-primary);
    font-size: 18px;
    padding: 8px;
  }

  .search-bar {
    padding-bottom: 10px;
    width: calc(100% - 40px);
  }
}

.title-area {
  margin-left: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.title-area-outer {
  display: flex;
  flex: 1 1 0%;
  width: 0px;
}

.custom-tree-node-list {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  padding: 0 8px;
}

.father .child {
  visibility: hidden;
}

.father:hover .child {
  visibility: visible;
}

:deep(.ed-input__wrapper) {
  width: 80px;
}

.custom-tree {
  flex: 1;
  min-height: 0;
  padding: 0 8px;
  :deep(.ed-tree.is-menu .ed-tree-node__content) {
    height: auto;
    min-height: 40px;
  }
  :deep(.ed-tree-node) {
    margin-bottom: 2px;
  }
}

.custom-tree-node {
  width: calc(100% - 30px);
  display: flex;
  align-items: center;
  box-sizing: content-box;
  padding-right: 12px;

  .label-tooltip {
    width: 100%;
    margin-left: 8.75px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .directory-leaf {
    flex: 1;
    min-width: 0;
    margin-left: 8px;
    padding: 6px 3px;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    white-space: normal;
    overflow-wrap: anywhere;
    cursor: pointer;

    &:focus-visible {
      outline: 2px solid var(--ed-color-primary);
      outline-offset: -2px;
    }

    &:disabled {
      cursor: not-allowed;
    }
  }

  .icon-more {
    margin-left: auto;
    display: none;
    color: #bbbfc4;
  }

  &:hover {
    .label-tooltip {
      width: calc(100% - 78px);
    }

    .icon-more {
      display: inline-flex;
    }
  }

  .icon-screen-new {
    border-radius: 6px;
    color: #fff;
    padding: 3px;
  }
}
</style>

<style lang="less">
.tree-sort-menu-custom {
  padding: 4px !important;
  li {
    border-radius: 6px;
    padding: 0 8px !important;
  }
  .ed-dropdown-menu__item:not(.is-disabled):not(.selected):hover {
    color: #1f2329;
  }
}
.menu-outer-dv_popper {
  min-width: 140px;
  margin-top: -2px !important;

  .ed-icon {
    border-radius: 6px;
  }
}

.sort-type-normal {
  i {
    display: none;
  }
}

.sort-type-checked {
  color: var(--ed-color-primary);

  i {
    display: block;
  }
}

.node-disabled-custom {
  color: rgba(187, 191, 196, 1);
  cursor: not-allowed;
}

.color-dataV-disabled {
  background: #bbbfc4 !important;
}

.sort_menu {
  color: rgba(143, 149, 158, 1);
  font-size: 14px;
  margin-left: 8px;
}
</style>
