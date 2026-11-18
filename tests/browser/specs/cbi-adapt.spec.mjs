import { expect, test } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const proof = process.env.CBI_EVIDENCE_DIR
const enabled = Boolean(proof && process.env.PLAYWRIGHT_STORAGE_STATE)
const url = (response) => new URL(response.url()).pathname
const host = 'CBI HOST 店铺日经营（回补前）'
const r01 = 'CBI R01 双口径（v1）'
const q1 = 'A 店 2026 年 11 月 1 日的净销售额是多少？比 10 月 31 日增加还是减少了多少，增长率是多少？当天的目标金额和目标完成率分别是多少？'
const q2 = '改看 2026 年 11 月 2 日，店铺和口径不变，只看净销售额、当天目标和目标完成率。'
const q3 = 'A 店 2026 年 11 月 11 日的净销售额是多少？与 11 月 4 日相比变化多少？'
const q4 = '同样的店铺和期间，改按固定 24 小时订单口径再算一次，并核查它与刚才经营流水口径的差异来自哪些退款。'

test.skip(!enabled, 'Requires the explicit local CBI fixture and a test login')
test.describe.configure({ mode: 'serial', timeout: 180_000 })

async function save(name, body) {
  await fs.writeFile(path.join(proof, name + '.json'), JSON.stringify(body, null, 2) + '\n')
}
async function capture(page, name) {
  await page.screenshot({ path: path.join(proof, name + '.png') })
}
async function topic(page, name) {
  await page.goto('/#/chat/index')
  await page.getByRole('button', { name: '新建对话' }).click()
  await page.getByText(name, { exact: true }).last().click()
  await page.getByRole('button', { name: '确定', exact: true }).click()
  await expect(page.locator('textarea:visible').last()).toBeEditable()
}
async function eventResult(response, input, name) {
  const body = await response.text()
  const events = body.split('\n').filter(line => line.startsWith('data:'))
    .map(line => JSON.parse(line.slice(5)))
  const state = events.find(item => item.type === 'analysis-state')
  await save(name, { input, status: response.status(), state, events })
  return state
}
function values(state) {
  return Object.fromEntries(state.state.outputs.map(item => [item.id, item.value]))
}
async function ask(page, input, name, status) {
  const response = page.waitForResponse(r => url(r).endsWith('/chat/question') && r.request().method() === 'POST', { timeout: 50000 })
  const textarea = page.locator('textarea:visible').last()
  await textarea.fill(input)
  await textarea.press('Enter')
  const result = await eventResult(await response, input, name)
  expect(result?.state.task_status).toBe(status)
  await expect(page.locator('.analysis-card').last()).toHaveAttribute('data-status', status)
  await expect(textarea).toBeEditable()
  return result
}
async function action(page, name) {
  const response = page.waitForResponse(r => url(r).endsWith('/analysis-action'), { timeout: 50000 })
  await page.getByRole('button', { name: '经营流水', exact: true }).click()
  return await eventResult(await response, '经营流水', name)
}
async function reopen(page, question) {
  const text = new RegExp(question.trim().slice(0, 20).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  await page.locator('.chat-list-item').filter({ hasText: text }).first().click()
}
async function chartTool(page, label) {
  const buttons = await page.locator('.chart-component-container').last().locator('.buttons-bar button:visible').all()
  for (const button of buttons) {
    await button.hover()
    await page.waitForTimeout(540)
    if (await page.locator('[role=tooltip]:visible').filter({ hasText: label }).count()) {
      await button.click()
      return
    }
  }
  throw new Error('Chart tool missing: ' + label)
}

test('HOST complete values, date continuation and unchanged historical result', async ({ page }) => {
  await topic(page, host)
  const first = await ask(page, q1, 'AT05-host', 'completed')
  expect(values(first)).toEqual({ current: 2000, baseline: 1500, delta: 500, rate: 33.333333, target: 2500, attainment: 80 })
  await capture(page, 'AT05-host')
  const next = await ask(page, q2, 'AT04-followup', 'completed')
  expect(values(next)).toEqual({ current: 900, target: 1500, attainment: 60 })
  expect(next.state.request.parent_record_id).toBe(first.record_id)
  expect(next.state.scope.baseline).toBeNull()
  expect(next.state.scope.store).toBe('A')
  await expect(page.locator('.analysis-card').first().locator('[data-output=current]')).toContainText('2,000')
  await capture(page, 'AT04-followup')
})

test('R01 first clarifies, reloads, confirms and compares with refund members', async ({ page }) => {
  await topic(page, r01)
  const pending = await ask(page, q3, 'AT01-clarification', 'awaiting_confirmation')
  expect(pending.state.steps).toEqual([])
  expect(pending.state.decision.options.map(item => item.id)).toEqual(['net_sales_cashflow:v1','net_sales_order24h:v1'])
  await capture(page, 'AT01-clarification')
  await page.reload()
  await reopen(page, q3)
  await expect(page.locator('.analysis-card').last()).toHaveAttribute('data-status', 'awaiting_confirmation')
  let command
  let headers
  const captureAction = async (request) => {
    if (!new URL(request.url()).pathname.endsWith('/analysis-action')) return
    command = request.postDataJSON()
    headers = await request.allHeaders()
  }
  page.on('request', captureAction)
  const confirmed = await action(page, 'AT02-confirmation')
  page.off('request', captureAction)
  expect(command).toBeTruthy()
  const route = '/api/v1/chat/record/' + pending.record_id + '/analysis-action'
  const duplicate = await page.request.post(route, { headers, data: command })
  const dup = await eventResult(duplicate, 'identical request id', 'AT02-duplicate')
  expect(dup.state.revision).toBe(confirmed.state.revision)
  expect(dup.state.steps).toEqual(confirmed.state.steps)
  const stale = await page.request.post(route, { headers, data: { ...command, request_id: 'stale-decision-test' } })
  expect(stale.status()).toBe(409)
  await save('AT03-stale', { status: stale.status() })
  expect(confirmed.record_id).toBe(pending.record_id)
  expect(confirmed.state.scope.current).toBe('2026-11-11')
  expect(values(confirmed)).toEqual({ current: 2000, baseline: 1500, delta: 500, rate: 33.333333 })
  await expect(page.locator('.analysis-card').last()).toHaveAttribute('data-status', 'completed')
  const again = await ask(page, q4, 'AT08-refunds', 'completed')
  expect(again.state.request.parent_record_id).toBe(confirmed.record_id)
  expect(values(again)).toEqual({ current: 2250, baseline: 1600, delta: 650, rate: 40.625, refund_members: undefined })
  expect(again.state.evidence.differences).toEqual({ '2026-11-04': 100, '2026-11-11': 250 })
  expect(again.state.evidence.members.map(member => member.refund_id)).toEqual(['r1','r2'])
  expect(again.state.presentation.series_mode).toBe('grouped')
  await page.getByRole('button', { name: '查看查询 3', exact: true }).click()
  await expect(page.getByRole('dialog').last()).toContainText('r01_refund_difference')
  await capture(page, 'AT08-refund-query')
  await page.getByRole('dialog').last().locator('.ed-dialog__headerbtn').click()
  await expect(page.getByRole('dialog',{ name: '查看SQL' })).toBeHidden()
  await capture(page, 'AT08-refunds')
  await expect(page.locator('.chart-component-container').last().locator('canvas')).toBeVisible()
  await chartTool(page, '导出为')
  const excel = page.locator('.export_to_select:visible .popover-item').filter({ hasText: 'Excel' })
  const download = page.waitForEvent('download')
  await excel.click()
  await (await download).saveAs(path.join(proof, 'AT13-r01.xlsx'))
  await chartTool(page, '导出为')
  const picture = page.locator('.export_to_select:visible .popover-item').filter({ hasText: '图片' })
  const image = page.waitForEvent('download')
  await picture.click()
  await (await image).saveAs(path.join(proof, 'AT13-native-chart.png'))
  await chartTool(page, '添加到仪表板')
  const dialog = page.getByRole('dialog', { name: '添加到仪表板' })
  await dialog.getByText('新建仪表板', { exact: true }).click()
  const name = 'CBI-ADAPT R01 ' + again.record_id
  await dialog.getByRole('textbox').fill(name)
  const created = page.waitForResponse(r => url(r).endsWith('/dashboard/create_canvas'))
  await dialog.getByRole('button', { name: '确认', exact: true }).click()
  const saved = await (await created).json()
  await page.getByText('仪表板', { exact: true }).first().click()
  const loaded = page.waitForResponse(r => url(r).endsWith('/dashboard/load_resource'))
  await page.getByText(name, { exact: true }).last().click()
  const reopened = await (await loaded).json()
  await expect(page.locator('canvas').first()).toBeVisible()
  const frozen = Object.values(JSON.parse(reopened.data.canvas_view_info))[0]
  expect(frozen.chart.series_mode).toBe('grouped')
  expect(frozen.chart.series_semantics).toBe('alternative_definitions')
  expect(frozen.data.data.map(row => row.amount).sort((a,b) => a-b)).toEqual([1500,1600,2000,2250])
  await save('AT13-dashboard', { name, saved, reopened })
  const details = page.getByRole('button', { name: '分析条件', exact: true }).first()
  await details.click()
  await expect(page.locator('.saved-analysis [data-output=current]').first()).toContainText('2,250')
  await expect(page.locator('.saved-analysis').first()).toContainText('r2')
  await capture(page, 'AT13-dashboard')
})

test('natural-language confirmation applies to the original task', async ({ page }) => {
  await topic(page, r01)
  const pending = await ask(page, q3, 'AT02-text-pending', 'awaiting_confirmation')
  const resumed = await ask(page, '按经营流水口径。', 'AT02-text-confirm', 'completed')
  expect(resumed.record_id).toBe(pending.record_id)
  expect(resumed.state.request.text).toBe(q3)
  expect(resumed.state.scope.source).toBe('text')
  await expect(page.locator('.analysis-card')).toHaveCount(1)
  await capture(page, 'AT02-text-confirm')
})


test('a different store and dates use independently generated transactions', async ({ page }) => {
  await topic(page, host)
  const result = await ask(page, 'Zeta 店 2026 年 11 月 18 日的净销售额和 11 月 17 日比是多少？请给本期、基期、增减额、增长率、当天目标金额及目标完成率。', 'AT14-alternate', 'completed')
  expect(values(result)).toEqual({ current: 750, baseline: 510, delta: 240, rate: 47.058824, target: 1000, attainment: 75 })
  expect(result.state.scope.store).toBe('Zeta')
  await capture(page, 'AT14-alternate')
  const only = await ask(page, '还是该店，只看当天净销售额。', 'AT06-only-amount', 'completed')
  expect(values(only)).toEqual({ current: 750 })
  expect(only.state.steps).toHaveLength(1)
})
