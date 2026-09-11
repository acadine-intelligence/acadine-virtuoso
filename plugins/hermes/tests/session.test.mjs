import assert from 'node:assert/strict'
import { readFile, access } from 'node:fs/promises'
import { test } from 'node:test'
import vm from 'node:vm'
import { webcrypto } from 'node:crypto'

async function loadPlugin() {
  const file = new URL('../desktop/plugin.js', import.meta.url)
  await assert.doesNotReject(access(file), 'Ship the real desktop module')
  const context = vm.createContext({ Date, Math, JSON, Error, crypto: webcrypto, performance, encodeURIComponent })
  const module = new vm.SourceTextModule(await readFile(file, 'utf8'), { context })
  await module.link(async name => {
    const exports = name === '@hermes/plugin-sdk'
      ? { host: {}, useValue() {}, useQuery() {}, Button() {}, Textarea() {}, Input() {}, PALETTE_AREA: 'palette', ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav' }
      : name === 'react'
        ? { useEffect() {}, useMemo() {}, useState() {} }
        : { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value)
    }, { context })
  })
  await module.evaluate()
  return module.namespace
}

const context = { schema: 'virtuoso/desktop-context@0.1', context_id: 'c'.repeat(64), workspace_name: 'synthetic' }
const item = { item_id: 'retrieval', content_hash: 'd'.repeat(64), title: 'Retrieve it', focus: 'learning', prompt: 'What did you learn?', answer: 'Practice retrieval.', hint: 'Try to remember.', follow_up: 'Use an example.', learning_context: 'atomic-recall' }
const loaded = { schema: 'virtuoso/desktop-item@0.1', context_id: context.context_id, action: 'practice', reason: 'New item.', item }

test('native plugin stays opt-in and registers a reachable practice page', async () => {
  const module = await loadPlugin()
  assert.equal(module.default?.id, 'virtuoso')
  assert.equal(module.default.defaultEnabled, false)
  const contributions = []
  module.default.register({ register: value => contributions.push(value) })
  assert.ok(contributions.some(row => row.area === 'routes' && row.data.path === '/virtuoso' && typeof row.render === 'function'))
  assert.ok(contributions.some(row => row.area === 'nav' && row.data.path === '/virtuoso'))
})

test('practice hides feedback until a response and explicit reveal', async () => {
  const { PracticeSession } = await loadPlugin()
  let elapsed = 0
  const session = new PracticeSession(async () => loaded, () => {}, { now: () => 1000000 + elapsed, monotonic: () => elapsed })
  await session.start(context)
  assert.equal(session.state.phase, 'prompt')
  assert.equal(session.visible().answer, undefined)
  assert.equal(session.reveal(), false)
  elapsed = 1500
  assert.equal(session.answer('My response'), true)
  assert.equal(session.visible().answer, undefined)
  assert.equal(session.reveal(), true)
  assert.equal(session.visible().answer, item.answer)
})

test('lost reply keeps one submission and preserves measured support', async () => {
  const { PracticeSession } = await loadPlugin()
  let elapsed = 0
  const writes = []
  const rest = async (path, opts) => {
    if (!opts.body) return loaded
    writes.push(JSON.parse(JSON.stringify(opts.body)))
    if (writes.length === 1) throw new Error('Connection lost after write')
    return { schema: 'virtuoso/desktop-error@0.1', error: { code: 'already-recorded', message: 'Already recorded.', recovery: 'advance-card' } }
  }
  const session = new PracticeSession(rest, () => {}, { now: () => 1000000, monotonic: () => elapsed })
  await session.start(context)
  elapsed = 1700
  session.answer('First response')
  session.beginRetry()
  elapsed = 2500
  session.answerRetry('Unaided retry')
  session.useHint()
  session.setOpenNotes(true)
  session.reveal()
  elapsed = 4000
  assert.equal(await session.grade('partial', 3), false)
  assert.equal(session.state.phase, 'grade')
  assert.equal(await session.grade('demonstrated', 5), false)
  assert.equal(await session.retrySubmit(), true)
  assert.deepEqual(writes[1], writes[0])
  assert.equal(writes[0].request.initial_response, 'First response')
  assert.equal(Date.parse(writes[0].request.initial_answered_at) - Date.parse(writes[0].request.started_at), 1700)
  assert.equal(writes[0].request.retry.latency_ms, 800)
  assert.equal(writes[0].request.hint_used, true)
  assert.equal(writes[0].request.open_notes, true)
  assert.equal(session.state.phase, 'complete')
})

test('study requires an explicit finish and stopping writes nothing', async () => {
  const { PracticeSession } = await loadPlugin()
  const lesson = { ...item, learning_unit: 'Read this lesson.', learning_unit_hash: 'e'.repeat(64) }
  let writes = 0
  const rest = async (_path, opts) => {
    if (!opts.body) return { ...loaded, action: 'learn', item: lesson }
    writes += 1
    return { schema: 'virtuoso/study-result@0.1', study: { item_id: item.item_id, item_content_hash: item.content_hash, learning_unit_hash: lesson.learning_unit_hash, claims_mastery: false, event_id: 'study-test' } }
  }
  const session = new PracticeSession(rest)
  await session.start(context)
  assert.equal(session.visible().answer, undefined)
  session.stop()
  assert.equal(writes, 0)
  await session.start(context)
  assert.equal(await session.finishStudy(), true)
  assert.equal(writes, 1)
  assert.equal(session.state.phase, 'complete')
})

test('reset ignores late responses and a blank recall cannot be demonstrated', async () => {
  const { PracticeSession } = await loadPlugin()
  let resolve
  const session = new PracticeSession(() => new Promise(done => { resolve = done }))
  const started = session.start(context)
  session.stop()
  resolve(loaded)
  await started
  assert.equal(session.state.phase, 'idle')
  session.rest = async () => loaded
  await session.start(context)
  session.answer('')
  session.reveal()
  assert.equal(await session.grade('demonstrated', 3), false)
})
