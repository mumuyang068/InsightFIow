import { writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

const boardId = process.env.PLAYWRIGHT_CBI_SAVED_BOARD
const evidence = process.env.PLAYWRIGHT_CBI_LAYOUT_EVIDENCE
const phase = process.env.PLAYWRIGHT_CBI_LAYOUT_PHASE || 'reading'

test.skip(!boardId || !process.env.PLAYWRIGHT_BASE_URL || !process.env.PLAYWRIGHT_STORAGE_STATE,
  'Requires an authenticated isolated dashboard fixture')

async function geometry(page) {
  return page.locator('[data-saved-kind]').evaluateAll(cards => {
    const box = element => {
      const { x, y, width, height, right, bottom } = element.getBoundingClientRect()
      return { x, y, width, height, right, bottom }
    }
    return {
      viewport: innerWidth,
      pageWidth: document.documentElement.scrollWidth,
      canvas: box(document.querySelector('#sq-preview-content')),
      cards: cards.map(card => {
        const area = card.closest('.chart-show-area')
        const wrapper = card.closest('.wrapper-outer')
        const plot = card.querySelector('canvas')
        return {
          id: wrapper.id, wrapper: box(wrapper), plot: box(plot), inside: plot.getBoundingClientRect().bottom <= area.getBoundingClientRect().bottom + 1,
          overflow: area.scrollHeight - area.clientHeight,
          metricRows: new Set([...card.querySelectorAll('[data-saved-output]')]
            .map(metric => metric.getBoundingClientRect().top)).size,
        }
      }),
    }
  })
}

test('the full dashboard leaves space to read a previously saved board', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto('/#/dashboard/index?dashboardId=' + encodeURIComponent(boardId))
  const cards = page.locator('[data-saved-kind]')
  await expect(cards).toHaveCount(2)
  await expect(cards.last().locator('canvas')).toBeVisible()
  const dimensions = []
  for (const [width, height] of [[1024, 768], [1440, 900], [1280, 800], [1423, 850], [1424, 850], [1423, 850], [1424, 850], [800, 720], [1024, 768]]) {
    await page.setViewportSize({width, height})
    await expect(cards.first().locator('.measure-chart[data-rendered=true] canvas')).toBeVisible()
    const state = await geometry(page)
    dimensions.push(state)
    if (evidence && [800, 1024, 1280, 1440].includes(width)) await page.screenshot({
      path: join(evidence, phase + '-main-' + width + '.png'), animations: 'disabled',
    })
  }
  if (evidence) await writeFile(join(evidence, phase + '-main.json'), JSON.stringify(dimensions, null, 2) + '\n')
  expect(dimensions[0].cards[0].wrapper.width, 'a saved card fills the main reading area at 1024').toBeGreaterThanOrEqual(480)
  for(const frame of dimensions) {
    expect(frame.pageWidth).toBeLessThanOrEqual(frame.viewport)
    expect(frame.cards.every(card=>card.inside && card.overflow<=1)).toBe(true)
    const [first, second] = frame.cards.map(card=>card.wrapper)
    expect(first.bottom<=second.y || first.right<=second.x).toBe(true)
    expect(Math.max(first.right, second.right)).toBeLessThanOrEqual(frame.canvas.right+1)
  }
  expect(dimensions.at(-1).cards.map(card=>card.id)).toEqual(dimensions[0].cards.map(card=>card.id))
  expect(dimensions[0].cards.every(card => !card.metricRows || card.metricRows === 2)).toBe(true)
  for (const frame of dimensions.filter(frame => frame.viewport === 1423 || frame.viewport === 1424)) {
    expect(frame.cards[0].wrapper.y === frame.cards[1].wrapper.y).toBe(frame.viewport === 1424)
  }
})


test('search and keyboard selection return to the dashboard with the selected result', async ({ page }) => {
  await page.goto('/#/dashboard/index?dashboardId=' + encodeURIComponent(boardId))
  const heading = page.locator('.preview-head h1')
  await expect(page.locator('[data-saved-kind]')).toHaveCount(2)
  const name = await heading.innerText()
  const trigger = page.getByRole('button', { name: '仪表板目录', exact: true })
  const drawer = page.getByRole('dialog', { name: '仪表板目录', exact: true })
  const search = drawer.getByRole('textbox', { name: '搜索' })
  await trigger.press('Enter')
  await expect(search).toBeFocused()
  await search.fill(name)
  const selected = drawer.getByRole('button', { name, exact: true })
  await expect(selected).toHaveAttribute('aria-current', 'page')
  await selected.press('Enter')
  await expect(drawer).toBeHidden()
  await expect(trigger).toBeFocused()
  await expect(heading).toHaveText(name)
  await expect(page.locator('[data-saved-kind]')).toHaveCount(2)
  await trigger.press('Enter')
  await expect(search).toHaveValue(name)
  await expect(search).toBeFocused()
  await search.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(trigger).toBeFocused()
})
