<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, RouterLink, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus-secondary'
import { useI18n } from 'vue-i18n'
import {
  themeApi,
  themeError,
  type ThemeDetail,
  type ThemeQualitySummary,
  type ThemeQualityTarget,
  type ThemeDraft,
  type ThemePreviewIntent,
  type ThemeValidation,
  type TopicSemantics,
} from '@/api/theme'
import ThemeOverview from './ThemeOverview.vue'
import ThemeDefinitions from './ThemeDefinitions.vue'
import ThemePreview from './ThemePreview.vue'
import ThemePublication from './ThemePublication.vue'
import ThemeQuality from './ThemeQuality.vue'
import ThemeEvaluation from './ThemeEvaluation.vue'

const { t } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id ?? ''))
const theme = shallowRef<ThemeDetail>()
const draftBuffer = shallowRef<TopicSemantics>()
const viewMode = ref<'published' | 'draft'>('published')
const tab = ref('overview')
const busy = ref(false)
const errorKey = ref('')
const errorDetail = ref('')
const latestResult = ref('')
let pendingRequest: { key: string; id: string } | undefined
let loadSerial = 0
let qualitySerial = 0
const qualityReports = shallowRef<ThemeQualitySummary[]>([])
const qualityLoading = ref(false)
const qualityError = ref('')
const themeLoad = ref(0)

const draft = computed(() => theme.value?.draft)
const dirty = computed(
  () => !!draft.value && JSON.stringify(draft.value.content) !== JSON.stringify(draftBuffer.value)
)
const staleDraft = computed(
  () =>
    !!draft.value &&
    !!theme.value?.active_version &&
    draft.value.base_version !== theme.value.active_version
)
const editable = computed(
  () => viewMode.value === 'draft' && !!draft.value && !staleDraft.value && !busy.value
)
const displayContent = computed(() =>
  viewMode.value === 'published'
    ? (theme.value?.content ?? null)
    : ((staleDraft.value ? draft.value?.content : draftBuffer.value) ?? null)
)
const showViewControls = computed(() => ['overview', 'definitions', 'quality'].includes(tab.value))
const qualityTarget = computed<ThemeQualityTarget | undefined>(() => {
  const item = theme.value
  if (!item?.managed) return
  if (tab.value === 'quality' && viewMode.value === 'published' && item.active_version) {
    return { version: item.active_version }
  }
  if (['quality', 'preview', 'publication'].includes(tab.value) && item.draft) {
    return { draft_id: item.draft.id }
  }
})
const qualityTargetKey = computed(() => JSON.stringify([
  id.value, qualityTarget.value, draft.value?.revision, themeLoad.value,
]))
const persistedQualityContent = computed(() =>
  viewMode.value === 'draft' ? draft.value?.content : theme.value?.content
)
const currentQualityReports = computed(() => qualityReports.value.filter(
  (item) => item.draft_id === draft.value?.id && item.revision === draft.value?.revision && item.publishable
))
watch(qualityTargetKey, async () => {
  const serial = ++qualitySerial
  const target = qualityTarget.value
  qualityReports.value = []
  qualityError.value = ''
  qualityLoading.value = !!target
  if (!target) return
  try {
    const reports = await themeApi.listQuality(id.value, target)
    if (serial === qualitySerial) qualityReports.value = reports
  } catch (error) {
    if (serial === qualitySerial) qualityError.value = themeError(error).detail || t('themeConfig.loadFailed')
  } finally {
    if (serial === qualitySerial) qualityLoading.value = false
  }
}, { immediate: true })

function addQualityReport(item: ThemeQualitySummary) {
  qualityReports.value = [item, ...qualityReports.value.filter(({ id }) => id !== item.id)]
}

function selectView(mode: 'published' | 'draft') {
  if (busy.value || (mode === 'draft' ? !draft.value : !theme.value?.active_version)) return
  viewMode.value = mode
}

function editDraft(content: TopicSemantics) {
  if (editable.value) draftBuffer.value = content
}

function setError(error: unknown, key = 'actionFailed') {
  const result = themeError(error)
  errorKey.value = result.status === 409 ? 'conflict' : key
  errorDetail.value = result.detail || ''
}

function clearError() {
  errorKey.value = ''
  errorDetail.value = ''
}

function replaceTheme(detail: ThemeDetail) {
  theme.value = detail
  draftBuffer.value = detail.draft?.content
  if (!detail.draft) viewMode.value = 'published'
  else if (!detail.active_version) viewMode.value = 'draft'
}

async function load() {
  const current = ++loadSerial
  themeLoad.value += 1
  busy.value = true
  clearError()
  try {
    const detail = await themeApi.detail(id.value)
    if (current === loadSerial) replaceTheme(detail)
  } catch (error) {
    if (current === loadSerial) setError(error, 'loadFailed')
  } finally {
    if (current === loadSerial) busy.value = false
  }
}
watch(
  id,
  () => {
    theme.value = undefined
    draftBuffer.value = undefined
    pendingRequest = undefined
    latestResult.value = ''
    tab.value = 'overview'
    viewMode.value = 'published'
    if (id.value) void load()
  },
  { immediate: true }
)

async function confirmAction(message: string, title: string, action: string) {
  try {
    await ElMessageBox.confirm(message, title, {
      type: 'warning',
      confirmButtonText: action,
      cancelButtonText: t('themeConfig.cancel'),
    })
    return true
  } catch {
    return false
  }
}

const discard = () =>
  confirmAction(t('themeConfig.discardEdits'), t('themeConfig.draft'), t('themeConfig.discard'))
async function refresh() {
  if (busy.value || (dirty.value && !(await discard()))) return
  await load()
}

function warnBeforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value || busy.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onBeforeUnmount(() => {
  qualitySerial += 1
  window.removeEventListener('beforeunload', warnBeforeUnload)
})
onBeforeRouteLeave(() => !busy.value && (!dirty.value || discard()))
onBeforeRouteUpdate(
  (to, from) => to.params.id === from.params.id || (!busy.value && (!dirty.value || discard()))
)

async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  clearError()
  try {
    await action()
  } catch (error) {
    setError(error)
  } finally {
    busy.value = false
  }
}

function replaceDraft(draft: ThemeDraft) {
  if (!theme.value) return
  theme.value = { ...theme.value, draft, draft_revision: draft.revision }
  draftBuffer.value = draft.content
}

async function createDraft() {
  const current = theme.value
  if (
    !current?.content ||
    !current.active_version ||
    busy.value ||
    (current.draft && !staleDraft.value)
  )
    return
  if (
    current.draft &&
    !(await confirmAction(
      t('themeConfig.freshDraftPrompt'),
      t('themeConfig.createDraft'),
      t('themeConfig.createDraft')
    ))
  )
    return
  if (theme.value !== current) return
  const { content, active_version } = current
  await run(async () => {
    replaceDraft(await themeApi.save(current.id, { base_version: active_version, content }))
    viewMode.value = 'draft'
    latestResult.value = ''
    ElMessage.success(t('themeConfig.draftSaved'))
  })
}

async function saveDraft() {
  const currentDraft = draft.value
  const content = draftBuffer.value
  if (!editable.value || !currentDraft || !content || !dirty.value) return
  if (!content.start || !content.end || content.start >= content.end) {
    errorKey.value = 'invalidDateWindow'
    errorDetail.value = ''
    tab.value = 'overview'
    return
  }
  await run(async () => {
    replaceDraft(
      await themeApi.save(id.value, {
        draft_id: currentDraft.id,
        revision: currentDraft.revision,
        base_version: currentDraft.base_version,
        content,
      })
    )
    ElMessage.success(t('themeConfig.draftSaved'))
  })
}

function addReceipt(item: ThemeValidation) {
  if (!theme.value) return
  theme.value = {
    ...theme.value,
    validations: [item, ...theme.value.validations.filter(({ id }) => id !== item.id)],
  }
  latestResult.value = item.id
}

function performValidation(intent?: ThemePreviewIntent) {
  const currentDraft = draft.value
  if (!currentDraft || dirty.value || staleDraft.value) return
  return run(async () => {
    const reference = { draft_id: currentDraft.id, revision: currentDraft.revision }
    addReceipt(
      intent
        ? await themeApi.preview(id.value, { ...reference, intent })
        : await themeApi.validate(id.value, reference)
    )
  })
}

function confirmValidation(validation_id: string, note: string, quality_report_id?: string) {
  const currentDraft = draft.value
  if (!currentDraft || !note || dirty.value || staleDraft.value) return
  const receipt = theme.value?.validations.find(({ id }) => id === validation_id)
  if (receipt?.draft_id !== currentDraft.id || receipt.revision !== currentDraft.revision) return
  return run(async () =>
    addReceipt(await themeApi.confirm(id.value, { validation_id, note, quality_report_id }))
  )
}

async function reloadPublished() {
  viewMode.value = 'published'
  tab.value = 'overview'
  theme.value = undefined
  draftBuffer.value = undefined
  await load()
}

function requestId(key: string) {
  if (!pendingRequest || pendingRequest.key !== key) {
    pendingRequest = {
      key,
      id: Array.from(crypto.getRandomValues(new Uint8Array(16)), (number) =>
        number.toString(16).padStart(2, '0')
      ).join(''),
    }
  }
  return pendingRequest.id
}

async function publish(validation_id: string, quality_report_id?: string) {
  const current = theme.value
  const draft = current?.draft
  const expected_version = current?.active_version ?? draft?.base_version
  if (!current || !draft || !expected_version || dirty.value || staleDraft.value || busy.value)
    return
  if (
    !(await confirmAction(
      t('themeConfig.publishPrompt', { revision: draft.revision }),
      t('themeConfig.publish'),
      t('themeConfig.publish')
    ))
  )
    return
  if (theme.value !== current || dirty.value) return
  await run(async () => {
    const publication = await themeApi.publish(current.id, {
      draft_id: draft.id,
      revision: draft.revision,
      expected_version,
      validation_id,
      quality_report_id,
      request_id: requestId(
        ['publish', draft.id, draft.revision, expected_version, validation_id, quality_report_id].join(':')
      ),
    })
    ElMessage.success(t('themeConfig.published', { version: publication.version }))
    await reloadPublished()
  })
}

async function changeAvailability() {
  const current = theme.value
  const expected_version = current?.active_version
  if (!current?.registered || !expected_version || dirty.value || busy.value) return
  const enabled = current.status === 'disabled'
  const action = enabled ? 'enable' : 'disable'
  if (!(await confirmAction(
    t('themeRegistration.' + action + 'Prompt'),
    t('themeRegistration.' + action),
    t('themeRegistration.' + action)
  ))) return
  if (theme.value !== current || dirty.value) return
  await run(async () => {
    await themeApi.availability(current.id, {
      request_id: requestId(['availability', expected_version, enabled].join(':')),
      expected_version,
      enabled,
    })
    pendingRequest = undefined
    await load()
  })
}

async function rollback(target_version: string) {
  const current = theme.value
  const expected_version = current?.active_version
  if (!current || !expected_version || dirty.value || busy.value) return
  if (
    !(await confirmAction(
      t('themeConfig.rollbackPrompt', { version: target_version }),
      t('themeConfig.rollback'),
      t('themeConfig.rollback')
    ))
  )
    return
  if (theme.value !== current || dirty.value) return
  await run(async () => {
    const publication = await themeApi.rollback(current.id, {
      target_version,
      expected_version,
      request_id: requestId(['rollback', expected_version, target_version].join(':')),
    })
    ElMessage.success(t('themeConfig.rolledBack', { version: publication.version }))
    await reloadPublished()
  })
}
</script>

<template>
  <section v-loading="busy" class="theme-detail" data-testid="theme-detail">
    <div class="breadcrumb">
      <RouterLink :to="{ name: 'cbi-theme-list' }">{{ $t('themeConfig.catalog') }}</RouterLink>
      <template v-if="theme"
        ><span aria-hidden="true">/</span><span>{{ theme.label }}</span></template
      >
    </div>
    <template v-if="theme">
      <header class="detail-header">
        <div class="heading">
          <h1>{{ theme.label }}</h1>
          <el-tag v-if="theme.registered && theme.status" data-testid="theme-availability-status"
            :type="theme.status === 'published' ? 'success' : theme.status === 'draft' ? 'warning' : 'info'"
            effect="plain">{{ $t('themeRegistration.status.' + theme.status) }}</el-tag>
          <span v-if="!theme.managed && theme.active_version">
            {{ $t('themeConfig.activeVersion') }} · {{ theme.active_version }}
          </span>
        </div>
        <div class="header-actions">
          <span v-if="dirty" role="status" class="unsaved" data-testid="theme-unsaved">
            {{ $t('themeConfig.unsaved') }}
          </span>
          <el-button data-testid="theme-refresh" :disabled="busy" @click="refresh">{{
            $t('themeConfig.refresh')
          }}</el-button>
        </div>
      </header>
    </template>

    <div v-if="errorKey" class="request-error" role="alert">
      <el-alert
        :title="$t('themeConfig.' + errorKey)"
        :description="errorDetail"
        type="error"
        :closable="false"
      />
      <el-button v-if="!theme" :disabled="busy" @click="refresh">
        {{ $t('themeConfig.refresh') }}
      </el-button>
    </div>

    <div v-if="theme?.managed && showViewControls" class="config-view">
      <div class="view-toolbar">
        <div
          class="view-status"
          role="status"
          aria-atomic="true"
          data-testid="theme-view-status"
          :data-view-mode="viewMode"
          :data-version="viewMode === 'published' ? theme.active_version : undefined"
          :data-draft-id="viewMode === 'draft' ? draft?.id : undefined"
          :data-revision="viewMode === 'draft' ? draft?.revision : undefined"
          :data-base-version="viewMode === 'draft' ? draft?.base_version : undefined"
        >
          <strong v-if="viewMode === 'published'">
            {{ $t('themeConfig.viewingPublished', { version: theme.active_version }) }}
          </strong>
          <template v-else-if="draft">
            <strong data-testid="theme-draft-status">
              {{ $t('themeConfig.viewingDraft', { revision: draft.revision }) }}
            </strong>
            <span v-if="theme.active_version">{{ $t('themeConfig.baseVersion', { version: draft.base_version }) }}</span>
            <span v-else>{{ $t('themeRegistration.firstDraft') }}</span>
          </template>
        </div>
        <div class="view-actions">
          <div
            class="view-controls"
            role="group"
            :aria-label="$t('themeConfig.viewMode')"
            data-testid="theme-view-mode"
          >
            <el-button
              :type="viewMode === 'published' ? 'primary' : 'default'"
              plain
              :aria-pressed="viewMode === 'published'"
              :disabled="busy || !theme.active_version"
              data-testid="theme-view-published"
              @click="selectView('published')"
              >{{ $t('themeConfig.activeVersion') }}</el-button
            >
            <el-button
              :type="viewMode === 'draft' ? 'primary' : 'default'"
              plain
              :aria-pressed="viewMode === 'draft'"
              :disabled="!draft || busy"
              data-testid="theme-view-draft"
              @click="selectView('draft')"
              >{{ $t(draft ? 'themeConfig.draft' : 'themeConfig.noDraft') }}</el-button
            >
          </div>
          <el-button
            v-if="theme.active_version && (!draft || staleDraft)"
            type="primary"
            data-testid="theme-create-draft"
            :disabled="busy"
            @click="createDraft"
            >{{ $t('themeConfig.createFromPublished') }}</el-button
          >
          <el-button
            v-if="viewMode === 'draft' && draft && !staleDraft"
            type="primary"
            data-testid="theme-save-draft"
            :disabled="!dirty || busy"
            @click="saveDraft"
            >{{ $t('themeConfig.saveDraft') }}</el-button
          >
        </div>
      </div>
      <el-alert
        v-if="viewMode === 'draft' && draft && staleDraft"
        data-testid="theme-stale-draft"
        :title="
          $t('themeConfig.staleDraft', { base: draft.base_version, current: theme.active_version })
        "
        type="warning"
        :closable="false"
      />
    </div>

    <el-tabs v-if="theme" v-model="tab" class="detail-tabs">
      <el-tab-pane :label="$t('themeConfig.overview')" name="overview">
        <ThemeOverview
          :theme="theme"
          :content="displayContent"
          :editable="editable"
          :viewing-published="viewMode === 'published'"
          @edit="editDraft"
          @evaluate="tab = 'evaluation'"
        />
      </el-tab-pane>
      <template v-if="theme.managed">
        <el-tab-pane :label="$t('themeConfig.definitions')" name="definitions">
          <ThemeDefinitions
            v-if="displayContent"
            :content="displayContent"
            :published-dimensions="theme.content?.dimensions"
            :fields="theme.fields"
            :editable="editable"
            :draft="theme.draft"
            :configure-dimensions="theme.registered"
            @edit="editDraft"
          />
        </el-tab-pane>
        <el-tab-pane :label="$t('themeQuality.tab')" name="quality">
          <ThemeQuality
            v-if="tab === 'quality' && qualityTarget && displayContent && persistedQualityContent"
            :key="qualityTargetKey"
            :theme="theme"
            :target="qualityTarget"
            :content="displayContent"
            :persisted="persistedQualityContent"
            :editable="editable"
            :dirty="dirty"
            :stale="staleDraft"
            :reports="qualityReports"
            :history-loading="qualityLoading"
            :history-error="qualityError"
            @edit="editDraft"
            @checked="addQualityReport"
          />
        </el-tab-pane>
        <el-tab-pane v-if="theme.active_version && theme.status !== 'disabled'"
          :label="$t('themeEvaluation.tab')" name="evaluation">
          <ThemeEvaluation
            v-if="tab === 'evaluation'"
            :key="theme.id"
            :theme="theme"
            :dirty="dirty"
            :stale="staleDraft"
            :refresh-token="themeLoad"
          />
        </el-tab-pane>
        <el-tab-pane :label="$t('themeConfig.verifyPreview')" name="preview">
          <ThemePreview
            :draft="theme.draft"
            :active-version="theme.active_version"
            :stale-draft="staleDraft"
            :validations="theme.validations"
            :busy="busy"
            :dirty="dirty"
            :latest-result="latestResult"
            :theme-id="theme.id"
            :quality-reports="currentQualityReports"
            @validate="performValidation()"
            @preview="performValidation"
            @confirm="confirmValidation"
          />
        </el-tab-pane>
        <el-tab-pane :label="$t('themeConfig.publications')" name="publication">
          <ThemePublication
            :theme="theme"
            :stale-draft="staleDraft"
            :dirty="dirty"
            :busy="busy"
            :quality-reports="currentQualityReports"
            :quality-loading="qualityLoading"
            :quality-error="qualityError"
            @publish="publish"
            @rollback="rollback"
            @availability="changeAvailability"
          />
        </el-tab-pane>
      </template>
    </el-tabs>
  </section>
</template>

<style scoped lang="less">
.theme-detail {
  height: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding-top: 8px;
}
.breadcrumb,
.detail-header,
.header-actions,
.request-error,
.view-toolbar,
.view-actions,
.view-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.breadcrumb {
  color: var(--ed-text-color-regular);
}
.breadcrumb a {
  color: var(--ed-color-primary);
}
.detail-header {
  justify-content: space-between;
  margin: 12px 0 10px;
}
.heading {
  flex: 1 1 280px;
  min-width: 0;
}
h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.config-view {
  margin-bottom: 8px;
}
.view-toolbar {
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--ed-fill-color-light);
  border: 1px solid var(--ed-border-color-lighter);
  border-radius: 6px;
}
.view-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.view-status span {
  color: var(--ed-text-color-regular);
}
.view-controls :deep(.ed-button + .ed-button),
.view-actions :deep(.ed-button + .ed-button) {
  margin-left: 0;
}
.view-controls :deep(.ed-button:focus) {
  outline: 2px solid var(--ed-color-primary-dark-2);
  outline-offset: 2px;
}
.config-view :deep(.ed-alert) {
  margin-top: 8px;
  overflow-wrap: anywhere;
}
.unsaved {
  color: var(--ed-text-color-regular);
}
.request-error {
  margin: 12px 0;
}
.request-error .ed-alert {
  flex: 1 1 240px;
}
.detail-tabs {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}
.detail-tabs :deep(.ed-tabs__header) {
  order: -1;
  flex: none;
}
.detail-tabs :deep(.ed-tabs__content) {
  overflow: auto;
  flex: 1;
  min-height: 0;
  padding: 4px 0 24px;
}
</style>
