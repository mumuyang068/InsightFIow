<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type {
  ThemeDimension,
  ThemeDraft,
  ThemeField,
  ThemeMetric,
  TopicSemantics,
} from '@/api/theme'
import MetricEditor from './MetricEditor.vue'
import ThemeDecomposition from './ThemeDecomposition.vue'

const props = defineProps<{
  content: TopicSemantics
  publishedDimensions?: Record<string, ThemeDimension>
  fields: ThemeField[]
  editable: boolean
  draft: ThemeDraft | null
  configureDimensions?: boolean
}>()
const emit = defineEmits<{ edit: [content: TopicSemantics] }>()
const metricOpen = ref(false)
const metricKey = ref<string | null>(null)
const returnFocus = ref<HTMLElement>()
const omittedDimensions = ref<Record<string, ThemeDimension>>({})
const dimensionField = ref('')
const dimensionOpen = ref(false)
const dimensionKey = ref('')
const dimensionName = ref('')
const dimensionEditKey = ref('')
const dimensionEditorField = ref('')
const dimensionError = ref('')
const numericField = /^(int[248]?|integer|bigint|smallint)$/i
const allDimensions = computed(() => ({
  ...(props.editable ? { ...props.publishedDimensions, ...omittedDimensions.value } : {}),
  ...props.content.dimensions,
}))
const safeIdentifier = /^[a-z][a-z0-9_]{0,62}$/
const dimensionFields = computed(() =>
  props.fields.filter(
    ({ name, type }) =>
      safeIdentifier.test(name) &&
      !['day', 'none'].includes(name) &&
      /^(text|character|varchar|char|string|name|citext|bpchar|int[248]?|integer|bigint|smallint)$/i.test(
        type.split('(')[0].trim()
      )
  )
)
const dimensionChoices = computed(() =>
  dimensionFields.value.filter(({ name }) =>
    !Object.prototype.hasOwnProperty.call(allDimensions.value, name)
  )
)
watch(
  () => props.draft,
  () => {
    omittedDimensions.value = {}
    dimensionField.value = ''
  }
)

function openMetric(key: string | null, event: MouseEvent) {
  if (!props.editable) return
  returnFocus.value = event.currentTarget as HTMLElement
  metricKey.value = key
  metricOpen.value = true
}

function saveMetric(key: string, metric: ThemeMetric, makeDefault: boolean) {
  if (!props.editable) return
  emit('edit', {
    ...props.content,
    metrics: { ...props.content.metrics, [key]: metric },
    default_metric: makeDefault || !props.content.default_metric ? key : props.content.default_metric,
  })
}

function toggleDimension(key: string, enabled: boolean) {
  if (!props.editable) return
  const dimensions = { ...props.content.dimensions }
  if (enabled) {
    const previous = allDimensions.value[key]
    if (!previous) return
    dimensions[key] = previous
    delete omittedDimensions.value[key]
  } else {
    omittedDimensions.value[key] = dimensions[key]
    delete dimensions[key]
  }
  emit('edit', { ...props.content, dimensions })
}

function addDimension() {
  if (!props.editable) return
  const field = dimensionChoices.value.find(({ name }) => name === dimensionField.value)
  if (!field) return
  emit('edit', {
    ...props.content,
    dimensions: {
      ...props.content.dimensions,
      [field.name]: {
        field: field.name,
        label: props.content.detail[field.name] ?? field.name,
        numeric: numericField.test(field.type.split('(')[0].trim()),
      },
    },
  })
  dimensionField.value = ''
}

function openDimension(event: MouseEvent, key = '') {
  if (!props.editable) return
  returnFocus.value = event.currentTarget as HTMLElement
  const dimension = props.content.dimensions[key]
  dimensionKey.value = key
  dimensionEditKey.value = key
  dimensionName.value = dimension?.label ?? ''
  dimensionEditorField.value = dimension?.field ?? ''
  dimensionError.value = ''
  dimensionOpen.value = true
}

function saveDimension() {
  if (!props.editable) return
  const key = dimensionKey.value.trim()
  const label = dimensionName.value.trim()
  const field = dimensionFields.value.find(({ name }) => name === dimensionEditorField.value)
  if (!safeIdentifier.test(key) || ['none', 'day'].includes(key)) {
    dimensionError.value = 'themeConfig.metricKeyError'
    return
  }
  if (key !== dimensionEditKey.value && Object.prototype.hasOwnProperty.call(allDimensions.value, key)) {
    dimensionError.value = 'themeRegistration.dimensionDuplicate'
    return
  }
  if (!field || !label) {
    dimensionError.value = 'themeRegistration.dimensionRequired'
    return
  }
  const previous = props.content.dimensions[dimensionEditKey.value]
  emit('edit', {
    ...props.content,
    dimensions: {
      ...props.content.dimensions,
      [key]: {
        ...previous,
        field: field.name,
        label,
        numeric: numericField.test(field.type.split('(')[0].trim()),
      },
    },
  })
  dimensionOpen.value = false
}

function restoreFocus() {
  nextTick(() => returnFocus.value?.focus())
}
</script>

<template>
  <section class="definitions">
    <div class="section-header">
      <h2>{{ $t('themeConfig.metrics') }}</h2>
      <el-button v-if="editable" data-testid="theme-add-metric" @click="openMetric(null, $event)">{{
        $t('themeConfig.addMetric')
      }}</el-button>
    </div>
    <ul v-if="Object.keys(content.metrics).length" class="definitions-list">
      <li v-for="(metric, key) in content.metrics" :key="key" :data-metric-id="key">
        <div class="definition-main">
          <div class="definition-title">
            <strong>{{ metric.label }}</strong>
            <el-tag v-if="key === content.default_metric" effect="plain">{{
              $t('themeConfig.defaultMetric')
            }}</el-tag>
          </div>
          <p>{{ metric.description }}</p>
        </div>
        <div class="definition-source">
          <span>{{ $t('themeConfig.operators.' + metric.op) }} · {{ metric.field }}</span>
          <span>
            {{ key }}<template v-if="metric.unit"> · {{ metric.unit }}</template>
            <template v-if="metric.precision != null">
              · {{ $t('themeRegistration.precisionValue', { digits: metric.precision }) }}
            </template>
          </span>
        </div>
        <el-button
          v-if="editable"
          link
          type="primary"
          :aria-label="$t('themeConfig.editNamedMetric', { name: metric.label })"
          :data-testid="'theme-edit-metric-' + key"
          @click="openMetric(String(key), $event)"
          >{{ $t('themeConfig.edit') }}</el-button
        >
      </li>
    </ul>

    <el-empty v-else :description="$t('themeConfig.empty')" />

    <div class="section-header dimensions-header">
      <h2>{{ $t('themeConfig.dimensions') }}</h2>
      <el-button v-if="editable && configureDimensions" data-testid="theme-add-dimension"
        @click="openDimension($event)">
        {{ $t('themeRegistration.addDimension') }}
      </el-button>
      <div v-else-if="editable" class="dimension-controls">
        <el-select
          v-model="dimensionField"
          :aria-label="$t('themeConfig.field')"
          :placeholder="$t('themeConfig.field')"
          filterable
        >
          <el-option
            v-for="field in dimensionChoices"
            :key="field.name"
            :label="field.name"
            :value="field.name"
          />
        </el-select>
        <el-button :disabled="!dimensionField" @click="addDimension">{{
          $t('themeConfig.openDimension')
        }}</el-button>
      </div>
    </div>
    <el-empty v-if="!Object.keys(allDimensions).length" :description="$t('themeConfig.empty')" />
    <ul v-else class="definitions-list">
      <li v-for="(dimension, key) in allDimensions" :key="key">
        <div class="definition-main">
          <strong>{{ dimension.label }}</strong>
          <p>
            {{ dimension.field
            }}<template v-if="dimension.identity">
              · {{ $t('themeConfig.identity') }} {{ dimension.identity }}</template
            >
          </p>
        </div>
        <el-button
          v-if="editable && configureDimensions && content.dimensions[key]"
          link
          type="primary"
          :aria-label="$t('themeConfig.editNamedMetric', { name: dimension.label })"
          :data-testid="'theme-edit-dimension-' + key"
          @click="openDimension($event, String(key))"
        >{{ $t('themeConfig.edit') }}</el-button>
        <el-switch
          v-if="editable"
          :model-value="Object.prototype.hasOwnProperty.call(content.dimensions, key)"
          :aria-label="$t('themeConfig.dimensionEnabled', { name: dimension.label })"
          @change="toggleDimension(String(key), Boolean($event))"
        />
      </li>
    </ul>
    <ThemeDecomposition :content="content" :editable="editable"
      @update="value => emit('edit', { ...content, decomposition: value })" />
    <el-dialog v-if="configureDimensions" v-model="dimensionOpen" append-to-body
      :title="$t(dimensionEditKey ? 'themeRegistration.editDimension' : 'themeRegistration.addDimension')"
      width="min(440px, calc(100vw - 32px))" @closed="restoreFocus">
      <el-form label-position="top" @submit.prevent="saveDimension">
        <el-form-item :label="$t('themeConfig.key')" required>
          <el-input v-model="dimensionKey" :disabled="!!dimensionEditKey"
            maxlength="63" data-testid="theme-dimension-key" />
        </el-form-item>
        <el-form-item :label="$t('themeConfig.label')" required>
          <el-input v-model="dimensionName" maxlength="80" data-testid="theme-dimension-label" />
        </el-form-item>
        <el-form-item :label="$t('themeConfig.field')" required>
          <el-select v-model="dimensionEditorField" filterable data-testid="theme-dimension-field">
            <el-option v-for="field in dimensionFields" :key="field.name" :value="field.name"
              :label="field.name" />
          </el-select>
        </el-form-item>
        <el-alert v-if="dimensionError" :title="$t(dimensionError)" type="error" :closable="false" />
        <div class="dimension-controls">
          <el-button @click="dimensionOpen = false">{{ $t('themeConfig.cancel') }}</el-button>
          <el-button type="primary" native-type="submit" data-testid="theme-dimension-save">
            {{ $t('themeRegistration.applyDimension') }}
          </el-button>
        </div>
      </el-form>
    </el-dialog>
    <MetricEditor
      v-model="metricOpen"
      :fields="fields"
      :metric-key="metricKey"
      :metrics="content.metrics"
      :default-metric="content.default_metric"
      :configure-precision="configureDimensions"
      @save="saveMetric"
      @closed="restoreFocus"
    />
  </section>
</template>

<style scoped lang="less">
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}
.definitions-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--ed-border-color);
  border-radius: 6px;
}
.definitions-list li {
  display: flex;
  align-items: center;
  gap: 12px 18px;
  padding: 12px 14px;
  flex-wrap: wrap;
}
.definitions-list li + li {
  border-top: 1px solid var(--ed-border-color-lighter);
}
.definition-main {
  min-width: 0;
  flex: 3 1 230px;
  overflow-wrap: anywhere;
}
.definition-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.definition-source {
  min-width: 0;
  flex: 1 1 150px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow-wrap: anywhere;
  color: var(--ed-text-color-regular);
}
p {
  margin: 4px 0 0;
  color: var(--ed-text-color-regular);
}
.dimensions-header {
  margin-top: 24px;
}
.dimension-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.dimension-controls .ed-select {
  width: 180px;
}
.ed-form-item .ed-select {
  width: 100%;
}
</style>
