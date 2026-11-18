import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const evidence = process.env.CBI_DEV06_EVIDENCE
const sourceName = process.env.CBI_DEV06_SOURCE ?? 'Northwind 订单明细'
const view = process.env.CBI_DEV06_VIEW ?? 'northwind_bi.order_detail'
const topic = process.env.CBI_DEV06_THEME ?? 'Northwind 订单经营'
const sourceVersion = process.env.CBI_DEV06_SOURCE_VERSION
const amountField = process.env.CBI_DEV06_AMOUNT_FIELD ?? 'net_line_value'
const draftOnly = process.env.CBI_DEV06_DRAFT_ONLY === '1'
const expectedAmountInAugust = 47287.67
const resumeId = process.env.CBI_DEV06_RESUME_DRAFT_ID
const start = '1996-07-04'
const end = '1998-05-07'
const previewStart = '1997-08-01'
const previewEnd = '1997-09-01'
const metricNames = ['订单行折后金额', '订单数', '商品件数']
const fieldLabels = {
  doc_no: '订单号', booked_on: '业务日期', item_key: '商品键', item_label: '商品',
  family_key: '品类键', family_label: '品类', qty_units: '商品件数', [amountField]: '订单行折后金额',
}

test.skip(!process.env.PLAYWRIGHT_BASE_URL || !process.env.PLAYWRIGHT_STORAGE_STATE || !evidence,
  'Requires the local DEV-06 fixture, maintainer login, and explicit evidence directory')
test.describe.configure({ timeout: 180_000 })

async function capture(page, step) {
  await mkdir(evidence, { recursive: true })
  await page.screenshot({ path: join(evidence, step + '.png') })
}

async function note(name, body) {
  await mkdir(evidence, { recursive: true })
  await writeFile(join(evidence, name + '.json'), JSON.stringify(body, null, 2) + '\n')
}

function responseFor(page, suffix, method = 'POST') {
  return page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(suffix) && response.request().method() === method
  )
}

async function getResult(response, action) {
  const body = await response.json()
  expect(response.ok(), action + ' returned ' + JSON.stringify(body)).toBe(true)
  return body.code === 0 ? body.data : body
}

function input(page, key) {
  const element = page.getByTestId(key)
  return element.and(page.locator('input,textarea')).or(element.locator('input,textarea'))
}

async function choose(page, target, value, { open = true } = {}) {
  const field = typeof target === 'string' ? page.getByTestId(target) : target
  if (open) await field.click()
  await page.locator('.ed-select-dropdown__item:visible').getByText(value, { exact: true }).last().click()
}

async function pickMultiple(page, target, values, closeAt) {
  for (const [index, value] of values.entries()) {
    await choose(page, target, value, { open: index === 0 })
  }
  await closeAt.click()
}

async function metric(page, { id, label, description, unit, field, op, precision }) {
  await page.getByTestId('theme-add-metric').click()
  await input(page, 'theme-metric-key').fill(id)
  await input(page, 'theme-metric-label').fill(label)
  await input(page, 'theme-metric-description').fill(description)
  await input(page, 'theme-metric-unit').fill(unit)
  await choose(page, 'theme-metric-field', field)
  await choose(page, 'theme-metric-op', op)
  await input(page, 'theme-metric-precision').fill(String(precision))
  await page.getByTestId('theme-metric-save').click()
  await expect(page.locator('[data-metric-id="' + id + '"]')).toBeVisible()
}

async function dimension(page, id, name, field) {
  await page.getByTestId('theme-add-dimension').click()
  await input(page, 'theme-dimension-key').fill(id)
  await input(page, 'theme-dimension-label').fill(name)
  await choose(page, 'theme-dimension-field', field)
  await page.getByTestId('theme-dimension-save').click()
  await expect(page.getByTestId('theme-edit-dimension-' + id)).toBeVisible()
}

async function qualityRule(page, { id, type, fields, description, suggestion, metrics }) {
  await page.getByTestId('theme-quality-add-rule').click()
  const dialog = page.getByRole('dialog').last()
  await input(page, 'theme-quality-rule-id').fill(id)
  const selects = dialog.locator('.ed-select')
  await choose(page, selects.nth(0), type)
  const closeAt = dialog.locator('.ed-dialog__header')
  await pickMultiple(page, 'theme-quality-rule-fields', fields, closeAt)
  await pickMultiple(page, 'theme-quality-rule-metrics', metrics, closeAt)
  await input(page, 'theme-quality-rule-description').fill(description)
  await input(page, 'theme-quality-rule-suggestion').fill(suggestion)
  await page.getByTestId('theme-quality-rule-save').click()
  await expect(page.getByTestId('theme-quality-edit-' + id)).toBeVisible()
}

async function register(page) {
  await page.goto('/#/ds/index')
  await page.getByTestId('theme-catalog-entry').click()
  await expect(page.getByTestId('theme-list')).toBeVisible()
  await expect(page.getByRole('link', { name: topic, exact: true })).toHaveCount(0)
  await capture(page, 'A03-01-theme-catalogue-1440')

  let assetReads = 0
  const reads = (request) => {
    if (new URL(request.url()).pathname.endsWith('/asset-preview')) assetReads++
  }
  page.on('request', reads)
  const listResponse = responseFor(page, '/cbi/themes/assets', 'GET')
  const registerButton = page.getByTestId('theme-register')
  await registerButton.focus()
  await expect(registerButton).toBeFocused()
  await registerButton.press('Enter')
  const assets = await getResult(await listResponse, 'Discover approved views')
  expect(assets.find(({ label }) => label === sourceName)?.assets.map(({ view }) => view)).toContain(view)
  await choose(page, 'theme-register-source', sourceName)
  await choose(page, 'theme-register-asset', view)
  await page.getByTestId('theme-asset-fields').locator('summary').click()
  await expect(page.getByRole('rowheader', { name: amountField, exact: true })).toBeVisible()
  expect(assetReads).toBe(0)
  const assetResponse = responseFor(page, '/asset-preview')
  await page.getByTestId('theme-asset-preview').click()
  const sample = await getResult(await assetResponse, 'Preview sample rows')
  expect(sample.query_count).toBe(1)
  expect(sample.rows.length).toBeGreaterThan(0)
  expect(assetReads).toBe(1)
  page.off('request', reads)
  await note('A03-02-asset-preview', {
    view: sample.view, fields: sample.fields, query_count: sample.query_count, sql: sample.sql,
  })
  await expect(page.getByTestId('theme-asset-preview-count')).toContainText('1')
  await capture(page, 'A03-02-source-fields-1440')
  await page.getByTestId('theme-asset-fields').locator('summary').click()

  await input(page, 'theme-register-label').fill(topic)
  await input(page, 'theme-register-description').fill('以订单号与商品键组合为明细粒度。折后金额与件数可加，订单按订单号去重。来源没有实际支付或退款记录。')
  await choose(page, 'theme-register-date-field', 'booked_on')
  await pickMultiple(page, 'theme-register-grain', ['doc_no', 'item_key'],
    input(page, 'theme-register-description'))
  await input(page, 'theme-register-start').fill(start)
  await input(page, 'theme-register-end').fill(end)
  await page.getByTestId('theme-register-missing').getByText('实际支付', { exact: true }).click()
  await page.getByTestId('theme-register-missing').getByText('实际退款', { exact: true }).click()
  if (sourceVersion) await input(page, 'theme-register-source-version').fill(sourceVersion)
  await page.getByTestId('theme-asset-fields').locator('summary').click()
  for (const [field, label] of Object.entries(fieldLabels)) {
    await input(page, 'theme-field-label-' + field).fill(label)
  }
  await page.setViewportSize({ width: 1024, height: 900 })
  await expect(input(page, 'theme-field-label-' + amountField)).toHaveValue('订单行折后金额')
  await capture(page, 'A03-02-source-mappings-1024')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByTestId('theme-asset-fields').locator('summary').click()

  await page.getByTestId('theme-register-next').click()
  await metric(page, { id: 'net_amount', label: metricNames[0], description: '逐行折后金额求和。来源使用示例金额单位。', unit: '示例金额单位', field: amountField, op: '求和', precision: 2 })
  await metric(page, { id: 'orders', label: metricNames[1], description: '按订单号去重。同一订单可在多个分组出现。', unit: '单', field: 'doc_no', op: '去重计数', precision: 0 })
  await metric(page, { id: 'units', label: metricNames[2], description: '订单明细商品件数求和。', unit: '件', field: 'qty_units', op: '求和', precision: 0 })
  await dimension(page, 'category', '品类', 'family_label')
  await dimension(page, 'product', '商品', 'item_label')
  const decomposition = page.getByTestId('theme-decomposition')
  await decomposition.locator('summary').click()
  await decomposition.getByRole('checkbox', { name: metricNames[0], exact: true }).check()
  await decomposition.getByRole('checkbox', { name: metricNames[2], exact: true }).check()
  await decomposition.getByRole('checkbox', { name: '品类', exact: true }).check()
  await decomposition.getByRole('checkbox', { name: '商品', exact: true }).check()
  await choose(page, decomposition.locator('.ed-select').filter({ has: page.getByRole('combobox', { name: /品类/ }) }), '商品')
  await page.getByTestId('theme-apply-decomposition').click()
  await decomposition.locator('summary').click()
  await capture(page, 'A03-03-definitions-1440')

  const registration = responseFor(page, '/register')
  await page.getByTestId('theme-register-submit').click()
  const created = await getResult(await registration, 'Create through the topic catalogue')
  await note('A03-04-registration', created)
  expect(created.status).toBe('draft')
  expect(created.active_version).toBeNull()
  expect(Object.keys(created.draft.content.metrics).sort()).toEqual(['net_amount', 'orders', 'units'])
  expect(created.draft.content.metrics.net_amount.field).toBe(amountField)
  expect(Object.keys(created.draft.content.dimensions).sort()).toEqual(['category', 'product'])
  return created.id
}

test('A03 registers, checks and publishes an approved view through the product UI', async ({ page }) => {
  let id = resumeId
  if (id) await page.goto('/#/ds/themes/' + encodeURIComponent(id))
  else id = await register(page)

  await expect(page.getByTestId('theme-availability-status')).toHaveText('待发布')
  await expect(page.getByTestId('theme-view-status')).toHaveAttribute('data-view-mode', 'draft')
  await expect(page.getByTestId('theme-ask')).toHaveCount(0)
  await expect(page.getByTestId('theme-view-published')).toBeDisabled()
  await page.setViewportSize({ width: 1024, height: 900 })
  const detail = page.getByTestId('theme-detail')
  await expect(detail).toContainText(view)
  await expect(detail).toContainText('booked_on')
  await expect(detail).toContainText('doc_no')
  await capture(page, 'A03-05-draft-source-1024')

  await page.getByRole('tab', { name: '数据检查' }).click()
  if (!(await page.getByTestId('theme-quality-edit-unique_order_line').count())) {
    await qualityRule(page, {
      id: 'unique_order_line', type: '唯一性', fields: ['doc_no', 'item_key'],
      description: '订单号与商品键唯一', suggestion: '核对订单明细重复与来源关联基数。', metrics: metricNames,
    })
    await qualityRule(page, {
      id: 'amount_present', type: '非空', fields: [amountField],
      description: '折后金额有值', suggestion: '检查单价、件数和折扣的来源计算。', metrics: [metricNames[0]],
    })
    const saved = responseFor(page, '/draft')
    await page.getByTestId('theme-save-draft').click()
    await getResult(await saved, 'Save quality rules to the draft')
  }
  await page.getByTestId('theme-quality-start').fill(previewStart)
  await page.getByTestId('theme-quality-end').fill(previewEnd)
  const qualityResponse = responseFor(page, '/quality')
  await page.getByTestId('theme-quality-check').click()
  const quality = await getResult(await qualityResponse, 'Check current draft quality')
  await note('A03-06-quality', quality)
  expect(quality.publishable).toBe(true)
  await expect(page.getByTestId('theme-quality-report')).toBeVisible()
  await capture(page, 'A03-06-quality-1024')

  await page.getByRole('tab', { name: '验证与试查' }).click()
  const validationResponse = responseFor(page, '/validate')
  await page.getByTestId('theme-validate').click()
  const validation = await getResult(await validationResponse, 'Validate draft')
  await note('A03-07-static-validation', validation)
  await input(page, 'theme-preview-start').fill(previewStart)
  await input(page, 'theme-preview-end').fill(previewEnd)
  const previewResponse = responseFor(page, '/preview')
  await page.getByTestId('theme-preview-run').click()
  const preview = await getResult(await previewResponse, 'Run draft preview')
  await note('A03-08-draft-preview', preview)
  expect(preview.receipt.execution_status).toBe('succeeded')
  const resultRows = preview.receipt.presentation?.table?.rows ?? preview.receipt.table?.rows ?? preview.receipt.rows
  expect(resultRows.flatMap((row) => Object.values(row)).map(Number)).toContain(expectedAmountInAugust)
  await expect(page.getByTestId('theme-preview-results')).toContainText(metricNames[0])
  await capture(page, 'A03-08-try-query-1024')
  await input(page, 'theme-preview-confirmation-note')
    .fill('按冻结的官方源表独立预期核对 1997 年 8 月订单行折后金额。')
  const confirmResponse = responseFor(page, '/confirm')
  await page.getByTestId('theme-preview-confirm').click()
  expect((await getResult(await confirmResponse, 'Confirm preview')).checked).toBe(true)
  if (draftOnly) return

  await page.getByRole('tab', { name: '发布记录' }).click()
  const publication = page.getByTestId('theme-publish-quality-report')
  await publication.click()
  await page.locator('.ed-select-dropdown__item:visible').filter({ hasText: quality.id }).click()
  await expect(page.getByTestId('theme-publish')).toBeEnabled()
  await expect(page.locator('.ed-select-dropdown:visible')).toHaveCount(0)
  await capture(page, 'A03-09-first-publication-1024')
  await page.getByTestId('theme-publish').click()
  const publishResponse = responseFor(page, '/publish')
  await page.getByRole('dialog').getByRole('button', { name: '发布', exact: true }).click()
  const published = await getResult(await publishResponse, 'Publish first version')
  await expect(page.getByTestId('theme-availability-status')).toHaveText('已发布')
  await expect(page.getByTestId('theme-view-status')).toHaveAttribute('data-version', published.version)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByTestId('theme-ask')).toHaveText('开启问数')
  await expect(page.getByTestId('theme-evaluation-open')).toBeVisible()
  await note('A03-10-published', { datasource_id: id, ...published })
  await page.setViewportSize({ width: 1440, height: 900 })
  await capture(page, 'A03-10-published-overview-1440')
})


test('A10 rejects stale review after editing and publishes a new revision', async ({ page }) => {
  const id = process.env.CBI_DEV06_REVISE_PUBLISHED
  test.skip(!id, 'Requires an explicit dedicated published DEV-06 datasource')
  await page.goto('/#/ds/themes/' + encodeURIComponent(id))
  const status = page.getByTestId('theme-view-status')
  await expect(status).toHaveAttribute('data-view-mode', 'published')
  const priorVersion = await status.getAttribute('data-version')
  const draftResponse = responseFor(page, '/draft')
  await page.getByTestId('theme-create-draft').click()
  const draft = await getResult(await draftResponse, 'Create a draft from the current publication')
  const previewTab = page.getByRole('tab', { name: '验证与试查' })
  await previewTab.click()
  await input(page, 'theme-preview-start').fill(previewStart)
  await input(page, 'theme-preview-end').fill(previewEnd)
  const oldPreviewResponse = responseFor(page, '/preview')
  await page.getByTestId('theme-preview-run').click()
  const oldPreview = await getResult(await oldPreviewResponse, 'Run a preview before changing the draft')
  await input(page, 'theme-preview-confirmation-note').fill('本次仅核对变更前的 1997 年 8 月订单行折后金额。')
  const oldConfirmResponse = responseFor(page, '/confirm')
  await page.getByTestId('theme-preview-confirm').click()
  const oldConfirmed = await getResult(await oldConfirmResponse, 'Mark the first draft preview verified')
  expect(oldConfirmed.checked).toBe(true)

  await page.getByRole('tab', { name: '概览与来源' }).click()
  const description = input(page, 'theme-overview-description')
  await description.fill((await description.inputValue()) + ' 两期来源覆盖完整时，某个品类或商品在其中一期没有订单明细的，该期可加指标计作零。')
  await page.getByRole('tab', { name: '指标与维度' }).click()
  const decomposition = page.getByTestId('theme-decomposition')
  await decomposition.locator('summary').click()
  for (const name of [metricNames[0], metricNames[2]]) {
    const row = decomposition.locator('.decomposition-option')
      .filter({ has: page.getByRole('checkbox', { name, exact: true }) })
    await row.getByRole('checkbox', { name: '完整空集合可计为零' }).check()
  }
  await page.getByTestId('theme-apply-decomposition').click()
  const savedResponse = responseFor(page, '/draft')
  await page.getByTestId('theme-save-draft').click()
  const saved = await getResult(await savedResponse, 'Persist the complete-source zero handling')
  expect(saved.revision).toBe(draft.revision + 1)
  expect(saved.content.decomposition.metrics).toEqual({ net_amount: true, units: true })

  const publicationTab = page.getByRole('tab', { name: '发布记录' })
  await publicationTab.click()
  await expect(page.getByText('请完成当前草稿修订的试查和业务核对。')).toBeVisible()
  await expect(page.getByTestId('theme-publish')).toBeDisabled()
  await capture(page, 'A10-stale-preview-blocked')
  await note('A10-stale-preview-blocked', {
    draftId: draft.id, priorVersion, priorReceipt: oldPreview.id, checkedRevision: oldConfirmed.revision,
    savedRevision: saved.revision, publishEnabled: await page.getByTestId('theme-publish').isEnabled(),
  })

  await page.getByRole('tab', { name: '数据检查' }).click()
  await page.getByTestId('theme-quality-start').fill(previewStart)
  await page.getByTestId('theme-quality-end').fill(previewEnd)
  const qualityResponse = responseFor(page, '/quality')
  await page.getByTestId('theme-quality-check').click()
  const quality = await getResult(await qualityResponse, 'Check quality on the revised draft')
  expect(quality.publishable).toBe(true)
  await previewTab.click()
  const validationResponse = responseFor(page, '/validate')
  await page.getByTestId('theme-validate').click()
  const validation = await getResult(await validationResponse, 'Validate new revision')
  await input(page, 'theme-preview-start').fill(previewStart)
  await input(page, 'theme-preview-end').fill(previewEnd)
  const previewResponse = responseFor(page, '/preview')
  await page.getByTestId('theme-preview-run').click()
  const preview = await getResult(await previewResponse, 'Run preview on new revision')
  expect(preview.receipt.execution_status).toBe('succeeded')
  await input(page, 'theme-preview-confirmation-note').fill('复核当前修订的 1997 年 8 月折后金额；完整期间内空分组可计为零。')
  const confirmResponse = responseFor(page, '/confirm')
  await page.getByTestId('theme-preview-confirm').click()
  const confirmed = await getResult(await confirmResponse, 'Verify the latest preview')
  expect(confirmed.revision).toBe(saved.revision)
  expect(confirmed.checked).toBe(true)

  await publicationTab.click()
  await page.getByTestId('theme-publish-quality-report').click()
  await page.locator('.ed-select-dropdown__item:visible').filter({ hasText: quality.id }).click()
  await page.getByTestId('theme-publish').click()
  const publishResponse = responseFor(page, '/publish')
  await page.getByRole('dialog').getByRole('button', { name: '发布', exact: true }).click()
  const published = await getResult(await publishResponse, 'Publish updated configuration')
  expect(published.version).not.toBe(priorVersion)
  expect(published.prior_version).toBe(priorVersion)
  await expect(status).toHaveAttribute('data-version', published.version)
  await note('A10-v2-published', { datasource_id: id, previous: priorVersion, published, quality, validation, preview })
})
