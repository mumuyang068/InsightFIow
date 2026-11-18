<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus-secondary'
import { useI18n } from 'vue-i18n'
import type { ThemeField, ThemeMetric } from '@/api/theme'

const props = defineProps<{
  fields: ThemeField[]
  metrics: Record<string, ThemeMetric>
  metricKey: string | null
  defaultMetric: string
  configurePrecision?: boolean
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{
  save: [key: string, metric: ThemeMetric, makeDefault: boolean]
  closed: []
}>()
const { t } = useI18n()
const keyInput = ref<{ focus: () => void }>()
const nameInput = ref<{ focus: () => void }>()
const focusEditor = () => (props.metricKey ? nameInput.value : keyInput.value)?.focus()
const key = ref('')
const metric = ref<ThemeMetric>({ op: 'average', field: '', label: '', unit: '', description: '' })
const makeDefault = ref(false)
const precision = ref<number>()
const original = ref('')
const snapshot = () => JSON.stringify([key.value, metric.value, precision.value, makeDefault.value])
const error = ref('')
const metricFields = computed(() => props.fields.filter((field) => field.operators.length > 0))
const operators = computed(
  () => metricFields.value.find((field) => field.name === metric.value.field)?.operators ?? []
)
watch(open, (visible) => {
  if (!visible) return
  key.value = props.metricKey ?? ''
  metric.value = props.metricKey
    ? { ...props.metrics[props.metricKey] }
    : { op: 'average', field: '', label: '', unit: '', description: '' }
  makeDefault.value = !props.defaultMetric || key.value === props.defaultMetric
  precision.value = metric.value.precision ?? undefined
  error.value = ''
  original.value = snapshot()
})

function changeField() {
  if (!operators.value.includes(metric.value.op) && operators.value[0]) {
    metric.value.op = operators.value[0]
  }
}

async function cancel(done?: () => void) {
  if (snapshot() !== original.value) {
    try {
      await ElMessageBox.confirm(t('themeConfig.discardEdits'), t('themeConfig.editMetric'), {
        confirmButtonText: t('themeConfig.discard'),
        cancelButtonText: t('themeConfig.cancel'),
      })
    } catch {
      return
    }
  }
  if (done) done()
  else open.value = false
}

function save() {
  const id = key.value.trim()
  if (!/^[a-z][a-z0-9_]{0,62}$/.test(id)) {
    error.value = t('themeConfig.metricKeyError')
  } else if (!props.metricKey && Object.prototype.hasOwnProperty.call(props.metrics, id)) {
    error.value = t('themeConfig.metricDuplicate')
  } else if (
    !metric.value.label.trim() ||
    !metric.value.description.trim() ||
    !operators.value.includes(metric.value.op)
  ) {
    error.value = t('themeConfig.metricRequired')
  } else {
    emit(
      'save',
      id,
      {
        ...metric.value,
        ...(props.configurePrecision ? { precision: precision.value ?? null } : {}),
        label: metric.value.label.trim(),
        unit: metric.value.unit.trim(),
        description: metric.value.description.trim(),
      },
      makeDefault.value
    )
    open.value = false
  }
}
</script>

<template>
  <el-drawer
    v-model="open"
    :title="$t(metricKey ? 'themeConfig.editMetric' : 'themeConfig.addMetric')"
    size="min(480px, 100vw)"
    :before-close="cancel"
    append-to-body
    @opened="focusEditor"
    @closed="emit('closed')"
  >
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item :label="$t('themeConfig.key')" required>
        <el-input
          ref="keyInput"
          v-model="key"
          :disabled="!!metricKey"
          maxlength="63"
          data-testid="theme-metric-key"
        />
      </el-form-item>
      <el-form-item :label="$t('themeConfig.label')" required>
        <el-input
          ref="nameInput"
          v-model="metric.label"
          maxlength="80"
          data-testid="theme-metric-label"
        />
      </el-form-item>
      <el-form-item :label="$t('themeConfig.description')" required>
        <el-input
          v-model="metric.description"
          type="textarea"
          :rows="3"
          maxlength="500"
          data-testid="theme-metric-description"
        />
      </el-form-item>
      <el-form-item :label="$t('themeConfig.unit')">
        <el-input v-model="metric.unit" maxlength="32" data-testid="theme-metric-unit" />
      </el-form-item>
      <div class="metric-form-columns">
        <el-form-item :label="$t('themeConfig.field')" required>
          <el-select
            v-model="metric.field"
            filterable
            data-testid="theme-metric-field"
            @change="changeField"
          >
            <el-option
              v-for="field in metricFields"
              :key="field.name"
              :label="field.name"
              :value="field.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('themeConfig.operator')" required>
          <el-select v-model="metric.op" :disabled="!metric.field" data-testid="theme-metric-op">
            <el-option
              v-for="op in operators"
              :key="op"
              :label="$t('themeConfig.operators.' + op)"
              :value="op"
            />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item v-if="configurePrecision" :label="$t('themeRegistration.precision')">
        <el-input-number v-model="precision" :min="0" :max="6" :precision="0"
          data-testid="theme-metric-precision" />
      </el-form-item>
      <el-checkbox
        v-model="makeDefault"
        :disabled="!defaultMetric || (!!metricKey && metricKey === defaultMetric)"
        data-testid="theme-metric-default"
      >
        {{ $t('themeConfig.makeDefault') }}
      </el-checkbox>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
    </el-form>
    <template #footer>
      <el-button @click="cancel()">{{ $t('themeConfig.cancel') }}</el-button>
      <el-button type="primary" data-testid="theme-metric-save" @click="save">{{
        $t('themeConfig.applyMetric')
      }}</el-button>
    </template>
  </el-drawer>
</template>

<style scoped lang="less">
.metric-form-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.ed-select {
  width: 100%;
}
.ed-alert {
  margin-top: 12px;
}
</style>
