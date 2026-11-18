<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ElMessageBox } from 'element-plus-secondary'
import { useI18n } from 'vue-i18n'
import {
  themeApi,
  themeError,
  type ThemeAssetSource,
  type ThemeMissingFact,
  type TopicSemantics,
} from '@/api/theme'
import ThemeAssetFields from './ThemeAssetFields.vue'
import ThemeDefinitions from './ThemeDefinitions.vue'

const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ registered: [themeId: string] }>()
const { t } = useI18n()
const step = ref<'source' | 'definitions'>('source')
const sources = shallowRef<ThemeAssetSource[]>([])
const sourceId = ref('')
const assetId = ref('')
const label = ref('')
const sourceVersion = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const errorDetail = ref('')
const loadFailed = ref(false)
const labels = ref<Record<string, string>>({})
const missingFacts: ThemeMissingFact[] = ['payment', 'refund']
const newContent = (): TopicSemantics => ({
  date_field: '',
  start: '',
  end: '',
  default_metric: '',
  metrics: {},
  dimensions: {},
  detail: {},
  guidance: [],
  description: '',
  grain: [],
  missing_facts: [],
})
const content = ref(newContent())
const source = computed(() => sources.value.find(({ id }) => id === sourceId.value))
const asset = computed(() => source.value?.assets.find(({ id }) => id === assetId.value))
const dateFields = computed(() => asset.value?.fields.filter(({ type }) => type === 'date') ?? [])
const changed = computed(() => !!assetId.value || !!label.value.trim())
let serial = 0
let pendingRequest: { signature: string; id: string } | undefined
onBeforeUnmount(() => { serial += 1 })

async function load() {
  const current = ++serial
  loading.value = true
  loadFailed.value = false
  errorDetail.value = ''
  try {
    const result = await themeApi.assets()
    if (current === serial) sources.value = result
  } catch (cause) {
    if (current !== serial) return
    loadFailed.value = true
    errorDetail.value = themeError(cause).detail
  } finally {
    if (current === serial) loading.value = false
  }
}

watch(open, (visible) => {
  if (!visible) {
    serial += 1
    return
  }
  step.value = 'source'
  sources.value = []
  sourceId.value = ''
  assetId.value = ''
  label.value = ''
  sourceVersion.value = ''
  labels.value = {}
  content.value = newContent()
  pendingRequest = undefined
  error.value = ''
  void load()
})

async function discard() {
  try {
    await ElMessageBox.confirm(t('themeConfig.discardEdits'), t('themeRegistration.register'), {
      confirmButtonText: t('themeConfig.discard'),
      cancelButtonText: t('themeConfig.cancel'),
    })
    return true
  } catch {
    return false
  }
}

async function close(done?: () => void) {
  if (saving.value || (changed.value && !(await discard()))) return
  if (done) done()
  else open.value = false
}

function selectSource(id: string) {
  sourceId.value = id
  selectAsset('')
}

function selectAsset(id: string) {
  assetId.value = id
  const { description, start, end, missing_facts } = content.value
  content.value = { ...newContent(), description, start, end, missing_facts }
  labels.value = {}
  sourceVersion.value = ''
  error.value = ''
}

function next() {
  errorDetail.value = ''
  if (!asset.value || !label.value.trim() || !content.value.date_field || !content.value.grain?.length) {
    error.value = 'sourceRequired'
    return
  }
  const days = (Date.parse(content.value.end) - Date.parse(content.value.start)) / 86_400_000
  if (!(days > 0 && days <= 731)) {
    error.value = 'dateWindow'
    return
  }
  error.value = ''
  step.value = 'definitions'
}

async function save() {
  if (!asset.value || saving.value) return
  const definition = content.value
  if (!definition.default_metric || !definition.metrics[definition.default_metric]) {
    error.value = 'metricNeeded'
    errorDetail.value = ''
    return
  }
  const fields = new Set([
    definition.date_field,
    ...(definition.grain ?? []),
    ...Object.values(definition.metrics).map(({ field }) => field),
    ...Object.values(definition.dimensions).map(({ field }) => field),
    ...Object.keys(labels.value),
  ])
  const body = {
    asset_id: asset.value.id,
    label: label.value.trim(),
    source_data_version: sourceVersion.value.trim() || undefined,
    content: {
      ...definition,
      description: definition.description?.trim(),
      detail: Object.fromEntries(Array.from(fields, (name) => [name, labels.value[name]?.trim() || name])),
    },
  }
  const signature = JSON.stringify([sourceId.value, body])
  if (pendingRequest?.signature !== signature) {
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (number) =>
      number.toString(16).padStart(2, '0')
    ).join('')
    pendingRequest = { signature, id }
  }
  saving.value = true
  error.value = ''
  errorDetail.value = ''
  try {
    const theme = await themeApi.register(sourceId.value, { ...body, request_id: pendingRequest.id })
    open.value = false
    emit('registered', theme.id)
  } catch (cause) {
    error.value = 'registerFailed'
    errorDetail.value = themeError(cause).detail
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-drawer v-model="open" :title="$t('themeRegistration.register')" size="min(620px, 100vw)"
    :before-close="close" append-to-body>
    <div v-loading="loading" class="registration" data-testid="theme-registration">
      <el-alert v-if="loadFailed" :title="$t('themeRegistration.assetsFailed')" :description="errorDetail"
        type="error" :closable="false" />
      <el-button v-if="loadFailed" @click="load">{{ $t('themeConfig.retry') }}</el-button>
      <el-empty v-else-if="!loading && !sources.length" :description="$t('themeRegistration.noAssets')" />
      <template v-else-if="step === 'source' && sources.length">
        <el-form label-position="top" @submit.prevent="next">
          <div class="registration-grid">
            <el-form-item :label="$t('themeRegistration.connection')" required>
              <el-select :model-value="sourceId" filterable data-testid="theme-register-source"
                @change="selectSource">
                <el-option v-for="item in sources" :key="item.id" :label="item.label" :value="item.id" />
              </el-select>
            </el-form-item>
            <el-form-item :label="$t('themeConfig.sourceView')" required>
              <el-select :model-value="assetId" :disabled="!source" filterable
                data-testid="theme-register-asset" @change="selectAsset">
                <el-option v-for="item in source?.assets ?? []" :key="item.id" :label="item.view"
                  :value="item.id" />
              </el-select>
            </el-form-item>
          </div>
          <template v-if="asset">
            <ThemeAssetFields :source-id="sourceId" :asset-id="asset.id" :fields="asset.fields"
              :labels="labels" editable @edit="labels = $event" />
            <el-form-item :label="$t('themeRegistration.topicName')" required>
              <el-input v-model="label" maxlength="128" data-testid="theme-register-label" />
            </el-form-item>
            <el-form-item :label="$t('themeConfig.description')">
              <el-input v-model="content.description" type="textarea" :rows="2" maxlength="600"
                data-testid="theme-register-description" />
            </el-form-item>
            <div class="registration-grid">
              <el-form-item :label="$t('themeConfig.dateField')" required>
                <el-select v-model="content.date_field" filterable data-testid="theme-register-date-field">
                  <el-option v-for="field in dateFields" :key="field.name" :label="field.name"
                    :value="field.name" />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('themeRegistration.grain')" required>
                <el-select v-model="content.grain" multiple filterable :multiple-limit="4"
                  data-testid="theme-register-grain">
                  <el-option v-for="field in asset.fields" :key="field.name" :label="field.name"
                    :value="field.name" />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('themeConfig.start')" required>
                <el-input v-model="content.start" type="date" data-testid="theme-register-start" />
              </el-form-item>
              <el-form-item :label="$t('themeConfig.end')" required>
                <el-input v-model="content.end" type="date" data-testid="theme-register-end" />
              </el-form-item>
            </div>
            <el-form-item :label="$t('themeRegistration.missingFacts')">
              <el-checkbox-group v-model="content.missing_facts" data-testid="theme-register-missing">
                <el-checkbox v-for="fact in missingFacts" :key="fact" :value="fact">
                  {{ $t('themeRegistration.fact.' + fact) }}
                </el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item :label="$t('themeRegistration.sourceVersion')">
              <el-input v-model="sourceVersion" :placeholder="$t('themeRegistration.sourceVersionUnknown')"
                maxlength="100" data-testid="theme-register-source-version" />
            </el-form-item>
          </template>
        </el-form>
      </template>
      <ThemeDefinitions v-else-if="asset && step === 'definitions'" :content="content"
        :fields="asset.fields" :draft="null" :editable="!saving" configure-dimensions
        @edit="content = $event" />
      <el-alert v-if="error" :title="$t('themeRegistration.' + error)" :description="errorDetail"
        type="error" :closable="false" data-testid="theme-register-error" />
    </div>
    <template #footer>
      <el-button v-if="step === 'definitions'" :disabled="saving" @click="step = 'source'; error = ''">
        {{ $t('themeRegistration.back') }}
      </el-button>
      <el-button v-else @click="close()">{{ $t('themeConfig.cancel') }}</el-button>
      <el-button v-if="step === 'source' && asset" type="primary" data-testid="theme-register-next"
        @click="next">{{ $t('themeRegistration.next') }}</el-button>
      <el-button v-else-if="step === 'definitions'" type="primary" :loading="saving"
        data-testid="theme-register-submit" @click="save">
        {{ $t('themeConfig.createDraft') }}
      </el-button>
    </template>
  </el-drawer>
</template>

<style scoped lang="less">
.registration { min-height: 100px; min-width: 0; }
.registration > .ed-alert + .ed-button { margin-top: 12px; }
.registration :deep(.ed-select) { width: 100%; }
.registration > .ed-alert { margin-top: 12px; overflow-wrap: anywhere; }
.registration-grid {
  display: grid;
  gap: 0 12px;
  grid-template-columns: repeat(auto-fit, minmax(min(190px, 100%), 1fr));
}
</style>
