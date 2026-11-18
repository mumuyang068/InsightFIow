import { writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { test, expect } from '@playwright/test'

const boardId = process.env.PLAYWRIGHT_CBI_SAVED_BOARD
const kind = process.env.PLAYWRIGHT_CBI_SAVED_KIND || 'operating'
const evidence = process.env.PLAYWRIGHT_CBI_LAYOUT_EVIDENCE
const phase = process.env.PLAYWRIGHT_CBI_LAYOUT_PHASE || 'layout'

test.skip(!boardId || !process.env.PLAYWRIGHT_BASE_URL || !process.env.PLAYWRIGHT_STORAGE_STATE,
  'Requires an authorized isolated dashboard and saved browser state')

async function measure(page, cards) {
  return cards.evaluateAll(results => {
    const bounds = element => {
      const { top, right, bottom, left, height } = element.getBoundingClientRect()
      return { top, right, bottom, left, height }
    }
    return results.map(run => {
      const tile = run.closest('.saved-chart-container')
      const area = run.closest('.chart-show-area')
      const wrapper = tile.closest('.wrapper-outer')
      const metric = run.querySelector('.saved-metrics')
      const chart = run.querySelector('.measure-chart')
      const actions = tile.querySelector('.saved-chart-actions')
      return {
        title: bounds(tile.querySelector('.header-bar')),
        wrapper: bounds(wrapper),
        area: bounds(area),
        metric: bounds(metric),
        chart: bounds(chart),
        canvas: bounds(chart.querySelector('canvas')),
        actions: bounds(actions),
        insetScroll: area.scrollTop,
        insetOverflow: area.scrollHeight - area.clientHeight,
        pageWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth,
        values: metric.innerText,
      }
    })
  })
}

async function checkLayout(page, cards, width, key) {
  await expect(cards).toHaveCount(2)
  for (const card of await cards.all()) {
    await expect(card.locator('.measure-chart[data-rendered=true] canvas')).toBeVisible()
  }
  const records = await measure(page, cards)
  if (evidence) {
    await writeFile(join(evidence, key + '.json'), JSON.stringify(records, null, 2) + '\n')
    await page.screenshot({ path: join(evidence, key + '.png'), animations: 'disabled' })
  }
  for (const record of records) {
    expect(record.pageWidth).toBeLessThanOrEqual(width)
    expect(record.insetScroll).toBe(0)
    expect(record.insetOverflow, 'default card body contains all metrics and the entire canvas').toBeLessThanOrEqual(1)
    expect(record.metric.top + 1).toBeGreaterThanOrEqual(record.title.bottom)
    expect(record.metric.bottom).toBeLessThanOrEqual(record.canvas.top + 1)
    expect(record.canvas.bottom).toBeLessThanOrEqual(record.area.bottom + 1)
    expect(record.canvas.bottom).toBeLessThanOrEqual(record.actions.top + 1)
    expect(record.actions.bottom).toBeLessThanOrEqual(record.wrapper.bottom + 1)
    expect(record.chart.height).toBeGreaterThanOrEqual(96)
  }
  expect(records[1].wrapper.top).toBeGreaterThanOrEqual(records[0].wrapper.bottom)
}

test('saved result cards show the complete summary, plot and actions at their original size', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto('/#/dashboard-preview?resourceId=' + encodeURIComponent(boardId))
  const cards = page.locator('[data-saved-kind="' + kind + '"]')
  await expect(cards).toHaveCount(2)
  const values = kind === 'operating' ? ['2,000 元', '900 元'] : ['33.33%', '+33.33 个百分点']
  await expect(cards.first()).toContainText(values[0])
  await expect(cards.last()).toContainText(values[1])
  for (const [width, height] of [[1024, 768], [1280, 800], [1440, 900], [1024, 768]]) {
    await page.setViewportSize({ width, height })
    await checkLayout(page, cards, width, phase + '-' + kind + '-' + width)
  }
  await page.reload()
  await checkLayout(page, cards, 1024, phase + '-' + kind + '-reopen')
})

test('long headings and metric labels can wrap without covering the plot', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto('/#/dashboard-preview?resourceId=' + encodeURIComponent(boardId))
  const cards = page.locator('[data-saved-kind="' + kind + '"]')
  await expect(cards).toHaveCount(2)
  await cards.first().evaluate(run => {
    const header = run.closest('.saved-chart-container').querySelector('.header-bar .title')
    header.prepend('门店经营成本及渠道来源对照后续补充核查 · '.repeat(4))
    const labels = run.querySelectorAll('.saved-metrics dt')
    labels[0].textContent += '（报告期范围内已核查的分类口径和已确认观察窗口）'.repeat(3)
    labels[1].textContent += '（与原记录的比较期间一致）'.repeat(2)
  })
  await checkLayout(page, cards, 1024, phase + '-' + kind + '-long-copy')
})
