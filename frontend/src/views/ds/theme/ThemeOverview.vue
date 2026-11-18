<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { ThemeDetail, ThemeMissingFact, TopicSemantics } from '@/api/theme'
import ThemeAssetFields from './ThemeAssetFields.vue'

const props = defineProps<{
  theme: ThemeDetail
  content: TopicSemantics | null
  editable: boolean
  viewingPublished: boolean
}>()
const emit = defineEmits<{ edit: [content: TopicSemantics]; evaluate: [] }>()
const missingFacts: ThemeMissingFact[] = ['payment', 'refund']
const dateFields = computed(() => props.theme.fields.filter(({ type }) => type === 'date'))

function edit<K extends keyof TopicSemantics>(key: K, value: TopicSemantics[K]) {
  if (props.editable && props.content) emit('edit', { ...props.content, [key]: value })
}
</script>

<template>
  <section class="overview">
    <h2>{{ $t('themeConfig.source') }}</h2>
    <dl>
      <dt>{{ $t('themeConfig.sourceView') }}</dt>
      <dd>
        <span>{{ theme.view || '—' }}</span>
        <RouterLink :to="{ name: 'dsTable', params: { dsId: theme.id, dsName: theme.label } }">
          {{ $t('themeConfig.openSource') }}
        </RouterLink>
      </dd>
      <template v-if="theme.registered">
        <dt>{{ $t('themeRegistration.sourceVersion') }}</dt>
        <dd>{{ theme.source_version || $t('themeRegistration.sourceVersionUnknown') }}</dd>
      </template>
      <template v-if="theme.owner">
        <dt>{{ $t('themeConfig.owner') }}</dt>
        <dd>{{ theme.owner }}</dd>
      </template>
    </dl>

    <div v-if="theme.registered && theme.status === 'published' && viewingPublished"
      class="overview-actions">
      <RouterLink class="ed-button ed-button--primary is-plain"
        :to="{ name: 'chat', query: { start_chat: theme.id } }" data-testid="theme-ask">
        {{ $t('themeRegistration.ask') }}
      </RouterLink>
      <el-button data-testid="theme-evaluation-open" @click="emit('evaluate')">
        {{ $t('themeEvaluation.tab') }}
      </el-button>
    </div>
    <ThemeAssetFields v-if="theme.registered && theme.asset_id && content" :source-id="theme.id"
      :asset-id="theme.asset_id" :fields="theme.fields" :labels="content.detail" :editable="editable"
      @edit="edit('detail', $event)" />

    <template v-if="content">
      <h2>{{ $t('themeConfig.definition') }}</h2>
      <el-form v-if="editable" label-position="top" class="overview-form" @submit.prevent>
        <el-form-item :label="$t('themeConfig.description')">
          <el-input
            :model-value="content.description ?? ''"
            type="textarea"
            :rows="3"
            maxlength="600"
            data-testid="theme-overview-description"
            @update:model-value="edit('description', $event)"
          />
        </el-form-item>
        <div class="date-fields">
          <el-form-item :label="$t('themeConfig.dateField')" required>
            <el-select
              :model-value="content.date_field"
              data-testid="theme-overview-date-field"
              @update:model-value="edit('date_field', $event)"
            >
              <el-option
                v-for="field in dateFields"
                :key="field.name"
                :label="field.name"
                :value="field.name"
              />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('themeConfig.start')" required>
            <el-input
              :model-value="content.start"
              type="date"
              data-testid="theme-overview-start"
              @update:model-value="edit('start', $event)"
            />
          </el-form-item>
          <el-form-item :label="$t('themeConfig.end')" required>
            <el-input
              :model-value="content.end"
              type="date"
              data-testid="theme-overview-end"
              @update:model-value="edit('end', $event)"
            />
          </el-form-item>
        </div>
        <template v-if="theme.registered">
          <el-form-item :label="$t('themeRegistration.grain')" required>
            <el-select :model-value="content.grain ?? []" multiple filterable :multiple-limit="4"
              @update:model-value="edit('grain', $event)">
              <el-option v-for="field in theme.fields" :key="field.name" :value="field.name"
                :label="field.name" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('themeRegistration.missingFacts')">
            <el-checkbox-group :model-value="content.missing_facts ?? []"
              @update:model-value="edit('missing_facts', $event as ThemeMissingFact[])">
              <el-checkbox v-for="fact in missingFacts" :key="fact" :value="fact">
                {{ $t('themeRegistration.fact.' + fact) }}
              </el-checkbox>
            </el-checkbox-group>
          </el-form-item>
        </template>
      </el-form>
      <dl v-else>
        <dt>{{ $t('themeConfig.description') }}</dt>
        <dd data-testid="theme-view-description">{{ content.description || '—' }}</dd>
        <dt>{{ $t('themeConfig.dateField') }}</dt>
        <dd>{{ content.date_field }}</dd>
        <dt>{{ $t('themeConfig.dateRange') }}</dt>
        <dd>{{ content.start }} – {{ content.end }} · {{ $t('themeConfig.endExclusive') }}</dd>
        <template v-if="content.grain?.length">
          <dt>{{ $t('themeRegistration.grain') }}</dt>
          <dd>{{ content.grain.join(' · ') }}</dd>
        </template>
        <template v-if="content.missing_facts?.length">
          <dt>{{ $t('themeRegistration.missingFacts') }}</dt>
          <dd>{{ content.missing_facts.map((fact) => $t('themeRegistration.fact.' + fact)).join(' · ') }}</dd>
        </template>
      </dl>

      <dl>
        <dt>{{ $t('themeConfig.defaultMetric') }}</dt>
        <dd>
          {{ content.metrics[content.default_metric]?.label }}
          <template v-if="content.metrics[content.default_metric]?.unit">
            · {{ content.metrics[content.default_metric].unit }}</template
          >
        </dd>
        <template v-if="theme.registered && viewingPublished">
          <dt>{{ $t('themeConfig.metrics') }}</dt>
          <dd>{{ Object.values(content.metrics).map(({ label }) => label).join(' · ') }}</dd>
          <dt>{{ $t('themeConfig.dimensions') }}</dt>
          <dd>{{ Object.values(content.dimensions).map(({ label }) => label).join(' · ') || '—' }}</dd>
        </template>
        <dt>{{ $t('themeConfig.recordFields') }}</dt>
        <dd>{{ Object.values(content.detail).join(' · ') }}</dd>
      </dl>
      <div v-if="content.guidance.length" class="guidance">
        <h2>{{ $t('themeConfig.limits') }}</h2>
        <ul>
          <li v-for="item in content.guidance" :key="item">{{ item }}</li>
        </ul>
      </div>
    </template>
    <el-alert v-else :title="$t('themeConfig.legacyInfo')" type="info" :closable="false" />
  </section>
</template>

<style scoped lang="less">
h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 12px;
}
dl {
  display: grid;
  grid-template-columns: minmax(90px, 132px) minmax(0, 1fr);
  gap: 9px 16px;
  margin: 0 0 20px;
  padding: 14px;
  border: 1px solid var(--ed-border-color);
  border-radius: 6px;
}
dt,
li {
  color: var(--ed-text-color-regular);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
dd a {
  margin-left: 12px;
  color: var(--ed-color-primary);
}
.overview-form {
  margin-bottom: 14px;
}
.overview-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 16px;
}
.overview-actions :deep(.ed-button + .ed-button) { margin-left: 0; }
.date-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(180px, 100%), 1fr));
  gap: 0 12px;
}
.ed-select {
  width: 100%;
}
li + li {
  margin-top: 4px;
}
</style>
