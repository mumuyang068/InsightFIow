import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'

const proof = process.env.CBI_EVIDENCE_DIR
const python = process.env.CBI_TEST_PYTHON
const root = proof ? path.dirname(proof) : ''
const control = (mode) => execFileSync(python, [path.join(root, 'scripts/fixture_control.py'), mode])
const r01 = 'CBI R01 双口径（v1）'
const q = 'A 店 2026 年 11 月 11 日的净销售额是多少？与 11 月 4 日相比变化多少？'
const members = '同样的店铺和期间，改按固定 24 小时订单口径再算一次，并核查它与刚才经营流水口径的差异来自哪些退款。'
const save = (name, value) => fs.writeFile(path.join(proof,name+'.json'),JSON.stringify(value,null,2)+'\n')
const route = (res) => new URL(res.url()).pathname
test.skip(!(proof && python && process.env.PLAYWRIGHT_STORAGE_STATE), 'Requires the isolated local fixture')
test.setTimeout(150_000)

async function open(page) {
  await page.goto('/#/chat/index')
  await page.getByRole('button',{name:'新建对话'}).click()
  await page.getByText(r01,{exact:true}).last().click()
  await page.getByRole('button',{name:'确定',exact:true}).click()
}
async function ask(page, text) {
  const response=page.waitForResponse(r=>route(r).endsWith('/chat/question'),{timeout:40000})
  await page.locator('textarea:visible').last().fill(text)
  await page.locator('textarea:visible').last().press('Enter')
  return state(await response)
}
async function state(response) {
  const captured = (await response.text()).split('\n').filter(line=>line.startsWith('data:'))
    .map(line=>JSON.parse(line.slice(5))).find(event=>event.type==='analysis-state')
  expect(captured).toBeTruthy()
  return captured
}
test('keeps totals if members are unavailable and resumes only the missing step',async({page})=>{
  await open(page)
  await ask(page,q)
  const confirm=page.waitForResponse(r=>route(r).endsWith('/analysis-action'))
  await page.getByRole('button',{name:'经营流水',exact:true}).click()
  await state(await confirm)
  await expect(page.locator('textarea:visible').last()).toBeEditable()
  control('hide-members')
  try {
    const partial=await ask(page,members)
    expect(partial.state.task_status).toBe('partial')
    expect(partial.state.outputs.find(x=>x.id==='current').value).toBe(2250)
    expect(partial.state.evidence.reason).toBe('member_query_failed')
    await save('AT09-partial',partial)
    await page.screenshot({path:path.join(proof,'AT09-partial.png')})
    control('restore-members')
    const completion=page.waitForResponse(r=>route(r).endsWith('/analysis-action'))
    await page.getByRole('button',{name:'继续核查退款'}).click()
    const done=await state(await completion)
    expect(done.record_id).toBe(partial.record_id)
    expect(done.state.task_status).toBe('completed')
    expect(done.state.steps.filter(x=>x.kind==='summary')).toEqual(partial.state.steps.filter(x=>x.kind==='summary'))
    expect(done.state.evidence.members.map(x=>x.refund_id)).toEqual(['r1','r2'])
    await save('AT09-resumed',done)
  } finally { control('restore-members') }
})
