import { expect, test } from '@playwright/test'

const hostChat = process.env.PLAYWRIGHT_CBI_HOST_CHAT

test.skip(!hostChat || !process.env.PLAYWRIGHT_BASE_URL || !process.env.PLAYWRIGHT_STORAGE_STATE,
  'Requires an authorized browser state and a local HOST result fixture')

test.beforeEach(async ({ page }) => {
  await page.goto('/#/chat/index?chat=' + encodeURIComponent(hostChat))
  await expect(page.locator('.analysis-card').first()).toBeVisible()
})

test('the composer leaves IME confirmation to the browser and sends once after composition', async ({ page }) => {
  const submitted = []
  await page.route('**/chat/question', async route => {
    submitted.push(route.request().postDataJSON().question)
    await route.fulfill({ status: 503, body: 'Local keyboard fixture' })
  })
  const input = page.locator('.input-area textarea')
  await input.fill('查询净销售额')
  const untouched = await input.evaluate(element => [
    { isComposing: true },
    { keyCode: 229 },
    { isComposing: true, ctrlKey: true },
  ].map(options => element.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Enter', code: 'Enter', bubbles: true, cancelable: true, ...options,
  }))))
  expect(untouched).toEqual([true, true, true])
  expect(submitted).toEqual([])
  await input.press('End')
  await input.press('Control+Enter')
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('查询净销售额\n\n')
  await input.fill('查询净销售额')
  await input.evaluate(element => {
    const send = element.closest('.input-wrapper').querySelector('button[aria-label="发送问题"]')
    element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    send.click()
  })
  await expect.poll(() => submitted).toEqual(['查询净销售额'])
  await expect(input).toBeEnabled()
  expect(submitted).toHaveLength(1)
})

test('the evidence drawer keeps keyboard focus and Escape returns it to the invoker', async ({ page }) => {
  const result = page.locator('.analysis-card').first()
  const trigger = result.getByRole('button', { name: '查看依据', exact: true })
  await trigger.focus()
  await trigger.press('Enter')
  const drawer = page.getByRole('dialog', { name: '查看依据', exact: true })
  await expect(drawer).toBeVisible()
  for (const key of ['Tab', 'Tab', 'Shift+Tab']) {
    await page.keyboard.press(key)
    expect(await drawer.evaluate(element => element.contains(document.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(trigger).toBeFocused()
})
