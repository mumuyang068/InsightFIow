<script setup lang="ts">
import { ref, watch } from 'vue'
import { themeApi, themeError, type ThemeAssetPreview, type ThemeField } from '@/api/theme'

const props = defineProps<{
  sourceId: string
  assetId: string
  fields: ThemeField[]
  labels: Record<string, string>
  editable?: boolean
}>()
const emit = defineEmits<{ edit: [labels: Record<string, string>] }>()
const sample = ref<ThemeAssetPreview>()
const previewBusy = ref(false)
const previewFailed = ref(false)
const previewError = ref('')

let selection = 0
watch(() => [props.sourceId, props.assetId], () => {
  selection += 1
  sample.value = undefined
  previewBusy.value = false
  previewFailed.value = false
  previewError.value = ''
})

function editLabel(name: string, value: string) {
  const labels = { ...props.labels }
  if (value) labels[name] = value
  else delete labels[name]
  emit('edit', labels)
}

async function preview() {
  const current = selection
  previewBusy.value = true
  previewFailed.value = false
  sample.value = undefined
  try {
    const result = await themeApi.assetPreview(props.sourceId, props.assetId)
    if (current === selection) sample.value = result
  } catch (error) {
    if (current !== selection) return
    previewFailed.value = true
    previewError.value = themeError(error).detail
  } finally {
    if (current === selection) previewBusy.value = false
  }
}
</script>

<template>
  <section class="asset-fields" data-testid="theme-asset-fields">
    <details class="field-details">
      <summary>{{ $t('themeRegistration.fields') }} · {{ fields.length }}</summary>
      <div class="field-table" role="region" :aria-label="$t('themeRegistration.fields')" tabindex="0">
        <table>
          <thead>
            <tr>
              <th scope="col">{{ $t('themeConfig.field') }}</th>
              <th scope="col">{{ $t('themeRegistration.type') }}</th>
              <th scope="col">{{ $t('themeConfig.label') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="field in fields" :key="field.name">
              <th scope="row">{{ field.name }}</th>
              <td>{{ field.type }}</td>
              <td>
                <el-input v-if="editable" :model-value="labels[field.name] ?? ''"
                  :aria-label="$t('themeRegistration.fieldLabel', { field: field.name })"
                  :placeholder="field.name" maxlength="80"
                  :data-testid="'theme-field-label-' + field.name"
                  @update:model-value="editLabel(field.name, $event)" />
                <template v-else>{{ labels[field.name] || '—' }}</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
    <div class="preview-actions">
      <el-button :loading="previewBusy" data-testid="theme-asset-preview" @click="preview">
        {{ $t('themeRegistration.previewAsset') }}
      </el-button>
      <p v-if="sample" role="status" data-testid="theme-asset-preview-count">
        {{ $t('themeRegistration.previewQueries', { count: sample.query_count }) }}
      </p>
    </div>
    <el-alert v-if="previewFailed" :title="$t('themeRegistration.previewFailed')"
      :description="previewError" type="error" :closable="false" />
    <div v-else-if="sample && sample.rows.length" class="data-table" role="region"
      :aria-label="$t('themeRegistration.previewAsset')" data-testid="theme-asset-preview-rows" tabindex="0">
      <table>
        <thead>
          <tr><th v-for="field in sample.fields" :key="field.name" scope="col">{{ field.name }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in sample.rows" :key="index">
            <td v-for="field in sample.fields" :key="field.name">
              {{ row[field.name] ?? $t('themeConfig.nullValue') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="sample">{{ $t('themeConfig.noRows') }}</p>
  </section>
</template>

<style scoped lang="less">
.asset-fields { min-width: 0; display: grid; gap: 10px; margin-bottom: 20px; }
.field-details {
  padding: 10px 12px;
  border: 1px solid var(--ed-border-color);
  border-radius: 6px;
}
summary { cursor: pointer; font-weight: 600; }
.field-table { margin-top: 8px; }
.field-table, .data-table { max-width: 100%; overflow: auto; }
.data-table { max-height: 230px; }
table { border-collapse: collapse; width: 100%; }
.data-table table { width: max-content; min-width: 100%; }
.data-table th { white-space: nowrap; }
.data-table td { max-width: 220px; }
th, td {
  text-align: left;
  padding: 6px 8px;
  border: 1px solid var(--ed-border-color-lighter);
  overflow-wrap: anywhere;
}
td :deep(.ed-input) { min-width: 112px; }
thead th { background: var(--ed-fill-color-light); }
.preview-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
p { margin: 0; color: var(--ed-text-color-regular); }
</style>
