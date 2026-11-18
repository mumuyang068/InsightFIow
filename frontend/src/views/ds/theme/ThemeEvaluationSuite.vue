<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type {
  EvaluationCase,
  EvaluationDatasource,
  EvaluationSuite,
  EvaluationSuiteInput,
} from '@/api/themeEvaluation'
import { evaluationValue } from '@/api/themeEvaluation'

const props = defineProps<{
  modelValue: boolean
  suite?: EvaluationSuite
  datasources: EvaluationDatasource[]
  fixtures: string[]
  writable: boolean
  busy: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [open: boolean]
  save: [payload: EvaluationSuiteInput]
}>()
const { t } = useI18n()
const title = ref('')
const description = ref('')
const cases = ref<EvaluationCase[]>([])
const index = ref(0)
const editing = ref(false)
const error = ref('')
const input = ref<HTMLInputElement>()
const expected = ref('')
const protections = ref('')
const selected = computed(() => cases.value[index.value])
const canEdit = computed(() => props.writable && !props.busy && editing.value)
const tr = (key: string) => t('themeEvaluation.' + key)
const label = (item: EvaluationCase) =>
  [item.id, item.turns[0]?.question].filter(Boolean).join(' · ')

function object(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value))
    return value as Record<string, unknown>
  throw new Error()
}

function loadCaseFields() {
  expected.value = selected.value?.expected ? JSON.stringify(selected.value.expected, null, 2) : ''
  protections.value = selected.value?.protects?.join(', ') ?? ''
}

watch(
  () => [props.modelValue, props.suite] as const,
  ([open, suite]) => {
    if (!open) return
    title.value = suite?.title ?? ''
    description.value = suite?.description ?? ''
    cases.value = JSON.parse(JSON.stringify(suite?.cases ?? []))
    index.value = 0
    error.value = ''
    editing.value = props.writable && suite?.status !== 'confirmed'
    loadCaseFields()
  },
  { immediate: true }
)

function saveCaseFields() {
  if (!selected.value || !editing.value) return true
  try {
    selected.value.expected = expected.value.trim() ? object(JSON.parse(expected.value)) : null
    selected.value.protects = protections.value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    error.value = ''
    return true
  } catch {
    error.value = 'invalidCaseJson'
    return false
  }
}

function selectCase(value: number) {
  if (!saveCaseFields()) return
  index.value = value
  loadCaseFields()
}

function addCase() {
  if (!saveCaseFields()) return
  let number = cases.value.length + 1
  while (cases.value.some((item) => item.id === 'T' + String(number).padStart(2, '0'))) number += 1
  cases.value.push({
    id: 'T' + String(number).padStart(2, '0'),
    category: '',
    datasource_id: props.datasources[0]?.id ?? '',
    fixture: 'clean',
    turns: [{ question: '' }],
    expected: null,
  })
  index.value = cases.value.length - 1
  loadCaseFields()
}

function removeCase() {
  cases.value.splice(index.value, 1)
  index.value = Math.max(0, index.value - 1)
  loadCaseFields()
}

function removeTurn(turn: number) {
  if (!selected.value) return
  selected.value.turns.splice(turn, 1)
  selected.value.turns.forEach((item, index) => {
    if (index === 0) delete item.parent
    else if (item.parent === turn) item.parent = index - 1
    else if (item.parent !== undefined && item.parent > turn) item.parent -= 1
  })
}

async function importSuite(event: Event) {
  const element = event.target as HTMLInputElement
  const file = element.files?.[0]
  element.value = ''
  if (!file) return
  try {
    if (file.size > 1024 * 1024) throw new Error()
    const document = object(JSON.parse(await file.text()))
    if (
      !Array.isArray(document.cases) ||
      !document.cases.length ||
      typeof document.title !== 'string'
    )
      throw new Error()
    const items = document.cases.map((raw) => {
      const item = object(raw)
      if (
        typeof item.id !== 'string' ||
        typeof item.datasource_id !== 'string' ||
        typeof item.category !== 'string' ||
        !Array.isArray(item.turns) ||
        !item.turns.length
      )
        throw new Error()
      const turns = item.turns.map((rawTurn) => {
        const turn = object(rawTurn)
        if (
          typeof turn.question !== 'string' ||
          (turn.parent != null && !Number.isInteger(turn.parent))
        )
          throw new Error()
        return {
          question: turn.question,
          ...(turn.parent == null ? {} : { parent: Number(turn.parent) }),
        }
      })
      const protects = Array.isArray(item.protects)
        ? item.protects.filter((value): value is string => typeof value === 'string')
        : []
      return {
        id: item.id,
        datasource_id: item.datasource_id,
        category: item.category,
        turns,
        protects,
        title: typeof item.title === 'string' ? item.title : undefined,
        fixture: typeof item.fixture === 'string' ? item.fixture : 'clean',
        expected: item.expected == null ? null : object(item.expected),
        origin: { ...object(item.origin ?? {}), import_name: file.name },
      }
    })
    title.value = document.title
    description.value = typeof document.description === 'string' ? document.description : ''
    cases.value = items
    index.value = 0
    error.value = ''
    loadCaseFields()
  } catch {
    error.value = 'invalidImport'
  }
}

function save(confirm: boolean) {
  if (!canEdit.value || !saveCaseFields()) return
  if (
    !title.value.trim() ||
    !cases.value.length ||
    cases.value.some(
      (item) =>
        !item.id.trim() ||
        !item.category.trim() ||
        !item.datasource_id ||
        !item.turns.length ||
        item.turns.some((turn) => !turn.question.trim())
    )
  ) {
    error.value = 'requiredSuiteFields'
    return
  }
  if (confirm && cases.value.some((item) => !item.expected || !Object.keys(item.expected).length)) {
    error.value = 'requiredExpected'
    return
  }
  error.value = ''
  emit('save', {
    title: title.value.trim(),
    description: description.value.trim(),
    cases: cases.value,
    previous_id: props.suite?.id,
    confirm,
  })
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    :title="tr(suite ? 'suiteDetails' : 'newSuite')"
    size="min(720px, 100%)"
    :close-on-click-modal="false"
    close-on-press-escape
    :before-close="
      (close: () => void) => {
        if (!busy) close()
      }
    "
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="suite-form">
      <div v-if="suite" class="suite-meta">
        <strong>{{ suite.title }}</strong>
        <span
          >{{ t('themeEvaluation.version', { version: suite.revision }) }} ·
          {{ tr('status.' + suite.status) }}</span
        >
        <span v-if="suite.confirmed_by">{{ tr('confirmedBy') }} · {{ suite.confirmed_by }}</span>
      </div>
      <template v-if="editing">
        <label
          >{{ tr('suiteTitle') }}<el-input v-model="title" :disabled="!canEdit" maxlength="120"
        /></label>
        <label
          >{{ tr('purpose')
          }}<el-input v-model="description" type="textarea" :rows="2" :disabled="!canEdit"
        /></label>
      </template>
      <p v-else-if="description">{{ description }}</p>
      <el-alert v-if="error" type="error" :title="tr(error)" :closable="false" />
      <div class="suite-section">
        <h3>{{ tr('cases') }}</h3>
        <div v-if="editing" class="actions">
          <input
            ref="input"
            type="file"
            accept=".json,application/json"
            hidden
            @change="importSuite"
          />
          <el-button :disabled="!canEdit" @click="input?.click()">{{
            tr('importSuite')
          }}</el-button>
          <el-button :disabled="!canEdit" @click="addCase">{{ tr('addCase') }}</el-button>
        </div>
      </div>
      <el-select
        v-if="cases.length"
        :model-value="index"
        :aria-label="tr('cases')"
        @change="selectCase"
      >
        <el-option v-for="(item, i) in cases" :key="i" :value="i" :label="label(item)" />
      </el-select>
      <el-empty v-else :description="tr('noCases')" :image-size="54" />
      <template v-if="selected">
        <div class="case-fields">
          <label>{{ tr('caseId') }}<el-input v-model="selected.id" :disabled="!canEdit" /></label>
          <label
            >{{ tr('category') }}<el-input v-model="selected.category" :disabled="!canEdit"
          /></label>
          <label
            >{{ tr('datasource') }}
            <el-select v-model="selected.datasource_id" :disabled="!canEdit">
              <el-option
                v-if="
                  selected.datasource_id &&
                  !datasources.some(({ id }) => id === selected?.datasource_id)
                "
                :value="selected.datasource_id"
                :label="selected.datasource_id"
              />
              <el-option
                v-for="item in datasources"
                :key="item.id"
                :value="item.id"
                :label="item.name"
              />
            </el-select>
          </label>
        </div>
        <div v-for="(turn, i) in selected.turns" :key="i" class="turn">
          <label
            >{{ t('themeEvaluation.turn', { number: i + 1 }) }}
            <el-input v-model="turn.question" type="textarea" :rows="2" :disabled="!canEdit" />
          </label>
          <label v-if="i" class="turn-parent"
            >{{ tr('parentTurn') }}
            <el-select v-model="turn.parent" :disabled="!canEdit" :placeholder="tr('parentTurn')">
              <el-option
                v-for="number in i"
                :key="number"
                :value="number - 1"
                :label="t('themeEvaluation.turn', { number })"
              />
            </el-select>
          </label>
          <el-button
            v-if="canEdit && selected.turns.length > 1"
            text
            type="danger"
            @click="removeTurn(i)"
            >{{ tr('remove') }}</el-button
          >
        </div>
        <el-button
          v-if="editing"
          :disabled="!canEdit"
          @click="selected.turns.push({ question: '', parent: selected.turns.length - 1 })"
        >
          {{ tr('addTurn') }}
        </el-button>
        <label
          >{{ tr('expected') }}
          <el-input
            v-if="editing"
            v-model="expected"
            type="textarea"
            :rows="6"
            :disabled="!canEdit"
            :placeholder="tr('expectedPlaceholder')"
          />
          <pre v-else>{{ evaluationValue(selected.expected) }}</pre>
        </label>
        <label
          >{{ tr('fixture') }}
          <el-select v-model="selected.fixture" :disabled="!canEdit">
            <el-option
              v-if="!fixtures.includes(selected.fixture)"
              :value="selected.fixture"
              :label="selected.fixture"
            />
            <el-option v-for="item in fixtures" :key="item" :value="item" :label="item" />
          </el-select>
        </label>
        <label
          >{{ tr('protectionCases') }}<el-input v-model="protections" :disabled="!canEdit"
        /></label>
        <details v-if="selected.origin && Object.keys(selected.origin).length">
          <summary>{{ tr('source') }}</summary>
          <pre>{{ evaluationValue(selected.origin) }}</pre>
        </details>
        <el-button v-if="editing" text type="danger" :disabled="!canEdit" @click="removeCase">{{
          tr('removeCase')
        }}</el-button>
      </template>
    </div>
    <template #footer>
      <el-button v-if="writable && !editing" type="primary" @click="editing = true">{{
        tr('reviseSuite')
      }}</el-button>
      <template v-if="editing">
        <el-button :disabled="busy" @click="save(false)">{{ tr('saveDraft') }}</el-button>
        <el-button type="primary" :loading="busy" @click="save(true)">{{
          tr('confirmSuite')
        }}</el-button>
      </template>
    </template>
  </el-drawer>
</template>

<style scoped lang="less">
.suite-form,
label {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.suite-form {
  gap: 14px;
  overflow-wrap: anywhere;
}
.suite-section,
.suite-meta,
.actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.suite-section {
  justify-content: space-between;
}
h3,
p {
  margin: 0;
}
.case-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(146px, 100%), 1fr));
  gap: 8px;
}
.turn {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.turn label {
  flex: 1 1 220px;
}
.turn .turn-parent {
  flex: 0 1 130px;
}
.actions :deep(.ed-button + .ed-button) {
  margin-left: 0;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 4px 0;
  padding: 8px;
  background: var(--ed-fill-color-light);
}
summary {
  cursor: pointer;
}
</style>
