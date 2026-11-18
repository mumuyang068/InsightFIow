<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { Chat, chatApi, ChatInfo, type ChatMessage, ChatRecord, questionApi } from '@/api/chat.ts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
import JSONBig from 'json-bigint'
import AnalysisCard from '@/views/chat/analysis/AnalysisCard.vue'
import type { AnalysisAction, AnalysisScopeChange, InvestigationAction } from '@/api/analysis.ts'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    recordId?: number
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
    reasoningName: 'sql_answer' | 'chart_answer' | Array<'sql_answer' | 'chart_answer'>
  }>(),
  {
    recordId: undefined,
    chatList: () => [],
    currentChatId: undefined,
    currentChat: () => new ChatInfo(),
    message: undefined,
    loading: false,
  }
)

const emits = defineEmits([
  'finish',
  'error',
  'stop',
  'scrollBottom',
  'follow',
  'retryInterpretation',
  'unavailable',
  'update:loading',
  'update:chatList',
  'update:currentChat',
  'update:currentChatId',
])

const index = computed(() => {
  if (props.message?.index) {
    return props.message.index
  }
  if (props.message?.index === 0) {
    return 0
  }
  return -1
})

const _currentChatId = computed({
  get() {
    return props.currentChatId
  },
  set(v) {
    emits('update:currentChatId', v)
  },
})

const _currentChat = computed({
  get() {
    return props.currentChat
  },
  set(v) {
    emits('update:currentChat', v)
  },
})

const _chatList = computed({
  get() {
    return props.chatList
  },
  set(v) {
    emits('update:chatList', v)
  },
})

const _loading = computed({
  get() {
    return props.loading
  },
  set(v) {
    emits('update:loading', v)
  },
})

const stopFlag = ref(false)
const { t } = useI18n()
const actionError = ref('')
const activeController = ref<AbortController>()
const queryDialog = ref(false)
const queryText = ref('')
let pendingAction: AnalysisAction | undefined
const questionRequests = new WeakMap<ChatRecord, { id: string; parent?: number }>()

async function inspectQuery(queryId: string) {
  const id = props.message?.record?.id
  if (!id) return
  try {
    queryText.value = (await questionApi.analysisQuery(id, queryId)).sql
    queryDialog.value = true
  } catch {
    actionError.value = t('cbi.action_error')
  }
}

async function act(choice?: string, recalculate = false, publication?: boolean, scopeChange?: AnalysisScopeChange) {
  const state = props.message?.record?.analysis_state
  const kind = scopeChange ? 'revise_scope' : publication !== undefined
    ? publication ? 'confirm_publication' : 'cancel_publication'
    : recalculate ? 'recalculate_latest' : choice ? 'confirm_definition' : 'continue_evidence'
  return submitAction(kind, choice, publication !== undefined ? state?.publication_confirmation?.id : state?.decision.id, scopeChange)
}

function investigationAction(kind: InvestigationAction, choice?: string) {
  return submitAction(kind, choice, props.message?.record?.analysis_state?.investigation?.source_confirmation?.id)
}

async function submitAction(kind: AnalysisAction['action'], choice?: string, decisionId?: string, scopeChange?: AnalysisScopeChange) {
  const record = props.message?.record
  const state = record?.analysis_state
  if (!record?.id || !state || _loading.value) return
  if (
    !pendingAction ||
    pendingAction.action !== kind ||
    pendingAction.choice_id !== choice ||
    pendingAction.decision_id !== decisionId ||
    JSON.stringify(pendingAction.scope_change) !== JSON.stringify(scopeChange) ||
    pendingAction.expected_revision !== state.revision
  ) {
    pendingAction = {
      action: kind,
      choice_id: choice,
      scope_change: scopeChange,
      decision_id: decisionId,
      expected_revision: state.revision,
      request_id: Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
        value.toString(16).padStart(2, '0')
      ).join(''),
    }
  }
  await sendMessage(pendingAction)
}

const sendMessage = async (action?: AnalysisAction, parentRecordId?: number) => {
  stopFlag.value = false
  _loading.value = true

  if (index.value < 0) {
    _loading.value = false
    return
  }

  let currentRecord: ChatRecord = _currentChat.value.records[index.value]
  const pendingRecord = currentRecord
  let replacePending = false
  actionError.value = ''

  let error: boolean = false
  if (_currentChatId.value === undefined) {
    error = true
  }
  if (error) return

  try {
    _currentChat.value.latest_record_time = new Date()
    _chatList.value.forEach((c: Chat) => {
      if (c.id === _currentChat.value.id) {
        c.latest_record_time = _currentChat.value.latest_record_time
      }
    })

    const controller = new AbortController()
    activeController.value = controller
    let questionRequest = questionRequests.get(currentRecord)
    if (!action && !questionRequest) {
      const parent = parentRecordId ?? _currentChat.value.records
        .slice(0, index.value)
        .reverse()
        .find((record) => record.analysis_state &&
          ['completed', 'partial', 'awaiting_confirmation'].includes(record.analysis_state.task_status))?.id
      questionRequest = { id: crypto.randomUUID(), parent }
      questionRequests.set(currentRecord, questionRequest)
    }
    const param = {
      question: currentRecord.question,
      chat_id: _currentChatId.value,
      parent_record_id: questionRequest?.parent,
      client_request_id: questionRequest?.id,
    }
    const response =
      action && currentRecord.id
        ? await questionApi.analysisAction(currentRecord.id, action, controller)
        : await questionApi.add(param, controller)
    if (!response.ok) throw new Error(String(response.status))
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')

    let sql_answer = ''
    let chart_answer = ''

    let tempResult = ''

    while (true) {
      if (stopFlag.value) {
        controller.abort()
        break
      }

      const { done, value } = await reader.read()
      if (done) {
        _loading.value = false
        break
      }

      let chunk = decoder.decode(value, { stream: true })
      tempResult += chunk
      const split = tempResult.match(/data:.*}\n\n/g)
      if (split) {
        chunk = split.join('')
        tempResult = tempResult.replace(chunk, '')
      } else {
        continue
      }
      if (chunk && chunk.startsWith('data:{')) {
        if (split) {
          for (const str of split) {
            let data
            try {
              data = JSONBig.parse(str.replace('data:{', '{'))
            } catch (err) {
              console.error('JSON string:', str)
              throw err
            }

            if (data.code && data.code !== 200) {
              ElMessage({
                message: data.msg,
                type: 'error',
                showClose: true,
              })
              _loading.value = false
              return
            }

            switch (data.type) {
              case 'id':
                if (data.forked) {
                  const existing = _currentChat.value.records.find(
                    (record) => record.id === data.id
                  )
                  if (existing) {
                    currentRecord = existing
                  } else {
                    const next = new ChatRecord()
                    next.id = data.id
                    next.chat_id = _currentChatId.value
                    next.question = currentRecord.question
                    next.datasource = currentRecord.datasource
                    next.create_time = new Date()
                    _currentChat.value.records.push(next)
                    currentRecord =
                      _currentChat.value.records[_currentChat.value.records.length - 1]
                  }
                }
                if (data.replace_pending) {
                  const oldRecord = _currentChat.value.records.find(
                    (record) => record.id === data.id
                  )
                  if (oldRecord && oldRecord !== currentRecord) {
                    currentRecord = oldRecord
                    replacePending = true
                  }
                }
                currentRecord.id = data.id
                break
              case 'analysis-parent-state': {
                const parent = _currentChat.value.records.find(
                  (record) => String(record.id) === data.record_id
                )
                if (parent) parent.analysis_state = data.state
                break
              }
              case 'analysis-state':
                currentRecord.analysis_state = data.state
                currentRecord.finish = data.state.task_status !== 'running'
                break
              case 'regenerate_record_id':
                currentRecord.regenerate_record_id = data.regenerate_record_id
                _currentChat.value.records[index.value].regenerate_record_id =
                  data.regenerate_record_id
                break
              case 'question':
                currentRecord.question = data.question
                _currentChat.value.records[index.value].question = data.question
                break
              case 'info':
                console.info(data.msg)
                break
              case 'brief':
                _currentChat.value.brief = data.brief
                _chatList.value.forEach((c: Chat) => {
                  if (c.id === _currentChat.value.id) {
                    c.brief = _currentChat.value.brief
                  }
                })
                break
              case 'error':
                currentRecord.error = data.content
                emits('error', currentRecord.id)
                break
              case 'sql-result':
                sql_answer += data.reasoning_content
                _currentChat.value.records[index.value].sql_answer = sql_answer
                break
              case 'sql':
                currentRecord.sql = data.content
                break
              case 'sql-data':
                getChatData(currentRecord.id)
                break
              case 'chart-result':
                chart_answer += data.reasoning_content
                _currentChat.value.records[index.value].chart_answer = chart_answer
                break
              case 'chart':
                currentRecord.chart = data.content
                break
              case 'datasource':
                if (!_currentChat.value.datasource) {
                  _currentChat.value.datasource = data.id
                }
                break
              case 'finish':
                if (action) pendingAction = undefined
                emits('finish', currentRecord.id)
                break
            }
            await nextTick()
          }
        }
      }
    }
  } catch (error) {
    if (action) {
      if (!stopFlag.value) actionError.value = t('cbi.action_error')
      return
    }
    if (stopFlag.value) return
    if (!currentRecord.error) {
      currentRecord.error = ''
    }
    if (currentRecord.error.trim().length !== 0) {
      currentRecord.error = currentRecord.error + '\n'
    }
    currentRecord.error = currentRecord.error + 'Error:' + error
    console.error('Error:', error)
    emits('error')
  } finally {
    activeController.value = undefined
    if (replacePending) {
      const pendingIndex = _currentChat.value.records.indexOf(pendingRecord)
      if (pendingIndex >= 0) _currentChat.value.records.splice(pendingIndex, 1)
    }
    _loading.value = false
  }
}

const loadingData = ref(false)

function getChatData(recordId?: number) {
  loadingData.value = true
  chatApi
    .get_chart_data(recordId)
    .then((response) => {
      _currentChat.value.records.forEach((record) => {
        if (record.id === recordId) {
          record.data = response
        }
      })
    })
    .finally(() => {
      loadingData.value = false
      emits('scrollBottom')
    })
}

function stop() {
  stopFlag.value = true
  activeController.value?.abort()
  _loading.value = false
  emits('stop')
}

const enableThousandsSeparatorList = ref<Array<string>>([])
const showLabel = ref<boolean>(false)

onBeforeUnmount(() => {
  stop()
})

onMounted(() => {
  if (props.message?.record?.id && props.message?.record?.finish && !props.message.record.analysis_state) {
    getChatData(props.message.record.id)
  }
})

function showSavedData(data: unknown) {
  const record = _currentChat.value.records.find(item => item.id === props.recordId)
  if (record) record.data = data
}
defineExpose({ sendMessage, index: () => index.value, stop })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="_loading">
    <AnalysisCard
      v-if="message.record?.analysis_state && !message.record.analysis_state.multi"
      :state="message.record.analysis_state"
      :record-id="recordId"
      :data-record-id="recordId"
      :busy="loading"
      :error="actionError"
      @confirm="act"
      @continue="act()"
      @recalculate="act(undefined, true)"
      @retry-quality="submitAction('retry_quality')"
      @retry-interpretation="(question) => emits('retryInterpretation', recordId, question)"
      @publication="(confirmed) => act(undefined, false, confirmed)"
      @revise-scope="(conditions) => act(undefined, false, undefined, conditions)"
      @query="inspectQuery"
      @follow="(draft) => emits('follow', recordId, draft)"
      @investigate="investigationAction"
      @table="showSavedData"
      @unavailable="emits('unavailable', currentChatId)"
    >
      <template #toolbar="{ view, comparisonView }">
        <ChartBlock v-if="message.record?.chart"
          v-model:show-label="showLabel" v-model:thousands-separator-list="enableThousandsSeparatorList"
          :message="message" :record-id="recordId" :loading-data="false" chat-type="table" fixed-table toolbar-only
          :result-view="view" :comparison-view="comparisonView" />
      </template>
      <template #default="{ series }">
      <ChartBlock
        v-if="message.record?.chart"
        v-model:show-label="showLabel"
        v-model:thousands-separator-list="enableThousandsSeparatorList"
        :chat-type="(message.record.analysis_state?.presentation.shape || message.record.analysis_state?.presentation.table) ? 'table' : undefined"
        :fixed-table="!!(message.record.analysis_state?.presentation.shape || message.record.analysis_state?.presentation.table)"
        :message="message"
        :record-id="recordId"
        :loading-data="loadingData"
      >
        <template #fixed-table><component :is="series" /></template>
      </ChartBlock>
      </template>
    </AnalysisCard>
    <el-dialog v-model="queryDialog" :title="t('chat.show_sql')" append-to-body>
      <pre class="query-receipt">{{ queryText }}</pre>
    </el-dialog>
    <ChartBlock
      v-if="!message.record?.analysis_state || message.record.analysis_state.multi"
      v-model:show-label="showLabel"
      v-model:thousands-separator-list="enableThousandsSeparatorList"
      style="margin-top: 6px"
      :message="message"
      :record-id="recordId"
      :loading-data="loadingData"
    />
    <slot></slot>
    <template #tool>
      <slot name="tool"></slot>
    </template>
    <template #footer>
      <slot name="footer"></slot>
    </template>
  </BaseAnswer>
</template>

<style scoped lang="less">
.query-receipt {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
