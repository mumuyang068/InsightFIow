<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import icon_searchOutline_outlined from '@/assets/svg/icon_search-outline_outlined.svg'
import { themeApi, themeError, type ThemeSummary } from '@/api/theme'

import { useUserStore } from '@/stores/user'
import ThemeRegistration from './ThemeRegistration.vue'

const router = useRouter()
const userStore = useUserStore()
const registerOpen = ref(false)
const topics = ref<ThemeSummary[]>([])
const query = ref('')
const loading = ref(false)
const error = ref('')
const failed = ref(false)
const matches = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return topics.value.filter((theme) =>
    [theme.label, theme.description, theme.view].some((value) =>
      value?.toLowerCase().includes(keyword)
    )
  )
})

async function load() {
  failed.value = false
  loading.value = true
  try {
    topics.value = await themeApi.list()
  } catch (cause) {
    error.value = themeError(cause).detail || ''
    failed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)

function openTheme(id: string) {
  return router.push({ name: 'cbi-theme-detail', params: { id } })
}
</script>

<template>
  <section v-loading="loading" class="theme-list" data-testid="theme-list">
    <RouterLink class="theme-breadcrumb" :to="{ name: 'ds' }">{{ $t('ds.title') }}</RouterLink>
    <header class="list-header">
      <h1>{{ $t('themeConfig.catalog') }}</h1>
      <div class="list-actions">
        <el-input
          v-model="query"
          clearable
          class="search"
          :aria-label="$t('themeConfig.search')"
          :placeholder="$t('themeConfig.search')"
        >
          <template #prefix>
            <el-icon><icon_searchOutline_outlined /></el-icon>
          </template>
        </el-input>
        <el-button v-if="userStore.isSpaceAdmin" type="primary" data-testid="theme-register"
          @click="registerOpen = true">{{ $t('themeRegistration.register') }}</el-button>
      </div>
    </header>

    <div v-if="failed" class="error">
      <el-alert
        :title="$t('themeConfig.loadFailed')"
        :description="error"
        type="error"
        :closable="false"
      />
      <el-button @click="load">{{ $t('themeConfig.retry') }}</el-button>
    </div>
    <el-empty v-else-if="!loading && !matches.length" :description="$t('themeConfig.empty')" />
    <div v-else class="catalog-grid">
      <article v-for="topic in matches" :key="topic.id" class="topic">
        <header class="topic-header">
          <h2>
            <RouterLink
              :to="{ name: 'cbi-theme-detail', params: { id: topic.id } }"
              :data-testid="'theme-open-' + topic.id"
            >
              {{ topic.label }}
            </RouterLink>
          </h2>
          <el-tag v-if="topic.registered && topic.status"
            :type="topic.status === 'published' ? 'success' : topic.status === 'draft' ? 'warning' : 'info'"
            effect="plain" :data-testid="'theme-status-' + topic.id">
            {{ $t('themeRegistration.status.' + topic.status) }}
          </el-tag>
          <el-tag v-else :type="topic.managed ? 'primary' : 'info'" effect="plain">
            {{ $t(topic.managed ? 'themeConfig.managed' : 'themeConfig.legacy') }}
          </el-tag>
        </header>
        <p v-if="topic.description">{{ topic.description }}</p>
        <dl>
          <dt>{{ $t('themeConfig.source') }}</dt>
          <dd>{{ topic.view || '—' }}</dd>
          <dt>{{ $t('themeConfig.activeVersion') }}</dt>
          <dd>{{ topic.active_version || '—' }}</dd>
          <dt>{{ $t('themeConfig.draft') }}</dt>
          <dd>
            {{
              topic.draft_revision
                ? $t('themeConfig.revision', { revision: topic.draft_revision })
                : '—'
            }}
          </dd>
          <dt>{{ $t('themeConfig.lastVerified') }}</dt>
          <dd>
            {{
              topic.last_verified ? new Date(topic.last_verified).toLocaleString($i18n.locale) : '—'
            }}
          </dd>
          <dt>{{ $t('themeConfig.owner') }}</dt>
          <dd>{{ topic.owner ?? '—' }}</dd>
        </dl>
      </article>
    </div>
    <ThemeRegistration v-if="userStore.isSpaceAdmin" v-model="registerOpen" @registered="openTheme" />
  </section>
</template>

<style scoped lang="less">
.theme-list {
  height: 100%;
  overflow: auto;
  padding: 8px 0 16px;
}
.theme-breadcrumb,
.topic h2 a {
  color: var(--ed-color-primary);
}
.list-header,
.list-actions,
.topic-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.list-header {
  margin: 12px 0 20px;
}
h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 500;
}
h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}
.search {
  width: min(250px, 100%);
}
.catalog-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(285px, 100%), 1fr));
  gap: 16px;
}
.topic {
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--ed-border-color);
  border-radius: 8px;
}
p {
  color: var(--ed-text-color-regular);
  margin: 12px 0;
}
dl {
  display: grid;
  grid-template-columns: minmax(0, 108px) minmax(0, 1fr);
  gap: 9px 12px;
  margin: 14px 0 0;
}
dt {
  color: var(--ed-text-color-secondary);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.error .ed-button {
  margin-top: 12px;
}
</style>
