<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ThemeDecomposition, TopicSemantics } from '@/api/theme'

const props = defineProps<{ content: TopicSemantics; editable: boolean }>()
const emit = defineEmits<{ update: [value: ThemeDecomposition | null] }>()
const enabledMetrics = ref<string[]>([])
const enabledDimensions = ref<string[]>([])
const metrics = ref<ThemeDecomposition['metrics']>({})
const dimensions = ref<ThemeDecomposition['dimensions']>({})
const tolerance = ref('0')

const additiveMetrics = computed(() =>
  Object.entries(props.content.metrics).filter(
    ([, metric]) => metric.op === 'sum' || metric.op === 'sum_zero'
  )
)
const choices = computed(() =>
  Object.entries(props.content.dimensions).filter(
    ([key, dimension]) =>
      !['none', 'day'].includes(key) &&
      (!dimension.identity ||
        Object.values(props.content.dimensions).some(
          (item) => item.field === dimension.identity && !item.identity
        ))
  )
)
watch(
  () => [props.content.decomposition, props.editable] as const,
  ([config]) => {
    metrics.value = { ...(config?.metrics ?? {}) }
    dimensions.value = { ...(config?.dimensions ?? {}) }
    enabledMetrics.value = Object.keys(metrics.value)
    enabledDimensions.value = Object.keys(dimensions.value)
    tolerance.value = String(config?.tolerance ?? '0')
  },
  { immediate: true }
)
const metricSet = computed(() =>
  enabledMetrics.value.filter((key) => additiveMetrics.value.some(([id]) => id === key))
)
const dimensionSet = computed(() =>
  enabledDimensions.value.filter((key) => choices.value.some(([id]) => id === key))
)
const valid = computed(
  () =>
    metricSet.value.length &&
    dimensionSet.value.length &&
    String(tolerance.value).trim() !== '' &&
    Number(tolerance.value) >= 0 &&
    Number(tolerance.value) <= 0.001
)
function apply() {
  if (valid.value && props.editable) {
    emit('update', {
      metrics: Object.fromEntries(metricSet.value.map((key) => [key, !!metrics.value[key]])),
      dimensions: Object.fromEntries(
        dimensionSet.value.map((key) => [
          key,
          (dimensions.value[key] ?? []).filter(
            (child) => child !== key && dimensionSet.value.includes(child)
          ),
        ])
      ),
      tolerance: String(tolerance.value),
    })
  }
}
</script>

<template>
  <details
    v-if="content.decomposition || (editable && additiveMetrics.length && choices.length)"
    class="theme-decomposition"
    data-testid="theme-decomposition"
  >
    <summary>{{ $t('resultExplorer.configuration.title') }}</summary>
    <template v-if="editable">
      <fieldset>
        <legend>{{ $t('themeConfig.metrics') }}</legend>
        <div v-for="[key, metric] in additiveMetrics" :key="key" class="decomposition-option">
          <label
            ><input v-model="enabledMetrics" type="checkbox" :value="key" />
            {{ metric.label }}</label
          >
          <label v-if="enabledMetrics.includes(key)"
            ><input v-model="metrics[key]" type="checkbox" />
            {{ $t('resultExplorer.configuration.allowEmpty') }}</label
          >
        </div>
      </fieldset>
      <fieldset>
        <legend>{{ $t('themeConfig.dimensions') }}</legend>
        <div v-for="[key, dimension] in choices" :key="key" class="decomposition-option">
          <label
            ><input v-model="enabledDimensions" type="checkbox" :value="key" />
            {{ dimension.label }}</label
          >
          <el-select
            v-if="enabledDimensions.includes(key) && dimensionSet.length > 1"
            v-model="dimensions[key]"
            :aria-label="$t('resultExplorer.configuration.childrenFor', { name: dimension.label })"
            :placeholder="$t('resultExplorer.configuration.children')"
            multiple
          >
            <el-option
              v-for="child in dimensionSet.filter((id) => id !== key)"
              :key="child"
              :label="content.dimensions[child]?.label ?? child"
              :value="child"
            />
          </el-select>
        </div>
      </fieldset>
      <label class="tolerance-field"
        >{{ $t('resultExplorer.configuration.tolerance') }}
        <input v-model="tolerance" type="number" min="0" max="0.001" step="any" />
      </label>
      <div class="configuration-actions">
        <el-button :disabled="!valid" data-testid="theme-apply-decomposition" @click="apply">{{
          $t('resultExplorer.configuration.apply')
        }}</el-button>
        <el-button v-if="content.decomposition" plain @click="emit('update', null)">{{
          $t('resultExplorer.configuration.disable')
        }}</el-button>
      </div>
    </template>
    <template v-else-if="content.decomposition">
      <p>
        {{
          Object.keys(content.decomposition.metrics)
            .map((key) => content.metrics[key]?.label ?? key)
            .join(' · ')
        }}
      </p>
      <p v-for="(children, key) in content.decomposition.dimensions" :key="key">
        {{ content.dimensions[key]?.label ?? key }}
        <template v-if="children.length"
          >→
          {{
            children.map((child) => content.dimensions[child]?.label ?? child).join(' · ')
          }}</template
        >
      </p>
    </template>
  </details>
</template>

<style scoped>
.theme-decomposition {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--ed-border-color);
  border-radius: 6px;
}
.theme-decomposition summary {
  cursor: pointer;
  font-weight: 600;
}
.theme-decomposition fieldset {
  border: 0;
  padding: 0;
  margin: 12px 0;
}
.theme-decomposition legend {
  font-weight: 600;
  margin-bottom: 4px;
}
.decomposition-option,
.configuration-actions,
.tolerance-field {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  padding: 3px 0;
}
.decomposition-option label,
.tolerance-field {
  display: flex;
  align-items: center;
  gap: 5px;
}
.decomposition-option :deep(.ed-select) {
  width: min(100%, 320px);
}
.tolerance-field input {
  border: 1px solid var(--ed-border-color);
  border-radius: 4px;
  font: inherit;
  min-height: 30px;
}
.configuration-actions {
  margin-top: 9px;
}
</style>
