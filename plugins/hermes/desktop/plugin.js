// Plain ESM. Hermes supplies React and its SDK on the interface machine.
import { host, useValue, useQuery, Button, Textarea, Input, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useMemo, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const stack = { display: 'flex', flexDirection: 'column', gap: 16 }
const row = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }
const prose = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.7, margin: 0 }
const muted = { color: 'var(--ui-text-secondary)', fontSize: 13 }
const button = (label, onClick, disabled = false, secondary = false) => jsx(Button, { type: 'button', onClick, disabled, style: secondary ? { background: 'transparent', border: 'none', color: 'var(--ui-text-secondary)' } : undefined, children: label }, label)

export function VirtuosoPane({ ctx }) {
  const profile = useValue(host.state.profile)
  const gateway = useValue(host.state.gateway)
  const [, redraw] = useState(0)
  const [draft, setDraft] = useState('')
  const [result, setResult] = useState('partial')
  const [confidence, setConfidence] = useState(3)
  const session = useMemo(() => new PracticeSession(ctx.rest, () => redraw(value => value + 1)), [ctx, profile, gateway])
  useEffect(() => () => session.dispose(), [session])
  const connection = useQuery({
    queryKey: ['virtuoso', 'desktop-context', profile, gateway],
    queryFn: async () => {
      const value = envelope(await ctx.rest('/context', { timeoutMs: 10000 }), 'virtuoso/desktop-context@0.1')
      check(hash(value.context_id) && text(value.workspace_name))
      return value
    },
    refetchInterval: 10000, retry: false, gcTime: 0, staleTime: 0,
  })
  useEffect(() => {
    if (session.context && connection.data && session.context !== connection.data.context_id) session.stop()
  }, [connection.data, session])
  const state = session.state
  useEffect(() => { setDraft(''); setResult('partial'); setConfidence(3) }, [state.item, state.phase])
  const disabled = state.busy || Boolean(session.pending) || Boolean(state.error)
  const shown = session.visible()
  const start = async () => {
    const refreshed = await connection.refetch()
    if (refreshed.data && !refreshed.error) await session.start(refreshed.data)
  }
  const reconnect = () => { session.stop(); void connection.refetch() }
  const panels = []

  if (connection.isPending) panels.push(jsx('p', { role: 'status', children: 'Checking backend...' }, 'loading'))
  else if (connection.error) panels.push(jsxs('div', { role: 'alert', style: stack, children: [
    jsx('p', { children: 'The Virtuoso backend is unavailable.' }),
    jsx('p', { style: muted, children: 'Install and enable the Python plugin on the connected backend. Set its Virtuoso executable and workspace there. Restart that backend after adding API routes.' }),
    jsx('p', { style: prose, children: connection.error.message }),
    button('Check connection', reconnect),
  ] }, 'connection-error'))
  else {
    if (state.error) panels.push(jsxs('div', { role: 'alert', style: stack, children: [
      jsx('p', { style: prose, children: state.error.message }),
      session.pending && (!state.error.recovery || state.error.recovery === 'retry-submit')
        ? button('Retry same submission', () => void session.retrySubmit(), state.busy) : null,
      button('Reconnect and clear this response', reconnect, state.busy),
    ] }, 'error'))

    if (['idle', 'empty', 'complete'].includes(state.phase)) {
      if (state.phase === 'idle') panels.push(jsx('p', { children: 'Virtuoso will choose study or recall from your learning queue.' }, 'intro'))
      if (state.phase === 'empty') panels.push(jsx('p', { children: state.reason }, 'empty'))
      if (state.phase === 'complete') {
        const message = {
          study: 'Study recorded. Your first recall is ready.', record: 'Practice recorded.',
          skip: 'Skipped. The due date is unchanged, so this item may be offered again.',
          'already-recorded': 'This submission was already recorded on the backend.',
        }[state.result.kind]
        panels.push(jsx('p', { role: 'status', children: message }, 'complete'))
        if (state.result.kind === 'record') panels.push(jsx('p', { style: muted, children: `Next review: ${new Date(state.result.proposal.due_at).toLocaleString()} (${state.result.proposal.algorithm}).` }, 'due'))
      }
      panels.push(button('Start next action', () => void start(), state.busy || connection.isFetching))
    } else if (state.item) {
      panels.push(jsxs('article', { style: { ...stack, border: '1px solid var(--ui-stroke-secondary)', borderRadius: 10, padding: 20 }, children: [
        jsx('p', { style: muted, children: `${state.phase === 'study' ? 'Study' : 'Recall'} · ${state.item.focus}` }),
        jsx('h2', { style: { margin: 0, fontSize: 21, lineHeight: 1.35, overflowWrap: 'anywhere' }, children: state.item.title }),
        jsxs('details', { style: muted, children: [jsx('summary', { children: 'Why this item' }), jsx('p', { style: prose, children: state.reason })] }),
        state.phase === 'study' ? jsxs('div', { style: stack, children: [
          jsx('p', { style: prose, children: shown.learning_unit }),
          jsx('p', { style: muted, children: 'Finishing records study only. Recall comes next.' }),
          button('Finish study', () => void session.finishStudy(), disabled),
        ] }) : jsxs('div', { style: stack, children: [
          jsx('p', { style: { ...prose, fontSize: 18 }, children: shown.prompt }),
          ['prompt', 'retry'].includes(state.phase) ? jsxs('form', { style: stack, onSubmit: event => {
            event.preventDefault()
            if (state.phase === 'prompt') session.answer(draft)
            else session.answerRetry(draft)
          }, children: [
            jsxs('label', { style: stack, children: [
              jsx('span', { children: state.phase === 'prompt' ? 'Your response' : 'Your retry' }),
              jsx(Textarea, { value: draft, onChange: event => setDraft(event.target.value), rows: 5, maxLength: 12000, disabled, style: { width: '100%', resize: 'vertical' } }),
            ] }),
            jsxs('div', { style: row, children: [
              jsx(Button, { type: 'submit', disabled: disabled || !draft.trim(), children: state.phase === 'prompt' ? 'Save response' : 'Save retry' }),
              button("I don't know yet", () => state.phase === 'prompt' ? session.answer('') : session.answerRetry(''), disabled),
            ] }),
          ] }) : null,
          state.response ? jsx('p', { style: prose, children: `Your response: ${state.response}` }) : null,
          state.retry ? jsx('p', { style: prose, children: `Your retry: ${state.retry.response}` }) : null,
          shown.hint ? jsx('p', { style: prose, children: `Hint: ${shown.hint}` }) : null,
          state.phase === 'support' ? jsxs('div', { style: row, children: [
            !state.retry && !state.hintUsed ? button('Try once more unaided', () => session.beginRetry(), disabled) : null,
            state.item.hint && !state.hintUsed ? button('Show hint', () => session.useHint(), disabled) : null,
            button('Reveal answer', () => session.reveal(), disabled),
          ] }) : null,
          state.phase === 'grade' ? jsxs('div', { style: stack, children: [
            jsx('h3', { style: { margin: 0 }, children: 'Compare your answer' }),
            jsx('p', { style: prose, children: shown.answer }),
            shown.follow_up ? jsx('p', { style: prose, children: `Try next: ${shown.follow_up}` }) : null,
            jsxs('label', { style: stack, children: [
              jsx('span', { children: 'Result' }),
              jsx('select', { 'aria-label': 'Result', value: result, disabled, onChange: event => setResult(event.target.value), style: { color: 'var(--ui-text-primary)', background: 'transparent', padding: 8, border: '1px solid var(--ui-stroke-secondary)', borderRadius: 5 }, children: [
                jsx('option', { value: 'not-demonstrated', children: 'Not demonstrated' }, 'no'),
                jsx('option', { value: 'partial', children: 'Partial' }, 'partial'),
                jsx('option', { value: 'demonstrated', disabled: !(state.response.trim() || state.retry?.response.trim()), children: 'Demonstrated' }, 'yes'),
              ] }),
            ] }),
            jsxs('label', { style: stack, children: [jsx('span', { children: 'Confidence (1 to 5)' }), jsx(Input, { type: 'number', min: 1, max: 5, step: 1, value: confidence, disabled, onChange: event => setConfidence(Number(event.target.value)) })] }),
            button('Record result', () => void session.grade(result, confidence), disabled || !Number.isInteger(confidence) || confidence < 1 || confidence > 5),
          ] }) : null,
          jsxs('label', { style: row, children: [jsx('input', { type: 'checkbox', checked: state.openNotes, disabled, onChange: event => session.setOpenNotes(event.target.checked) }), jsx('span', { children: 'I used notes' })] }),
          button('Skip this practice', () => void session.skip(), disabled),
        ] }),
        button('Stop without recording', () => session.stop(), state.busy || Boolean(session.pending), true),
      ] }, 'item'))
    }
    if (state.busy) panels.push(jsx('p', { role: 'status', children: 'Waiting for the backend...' }, 'busy'))
  }

  return jsxs('section', { 'aria-label': 'Virtuoso', style: { height: '100%', overflow: 'auto', color: 'var(--ui-text-primary)' }, children: [
    jsxs('div', { style: { ...stack, width: '100%', maxWidth: 800, padding: 24 }, children: [
      jsx('h1', { style: { margin: 0, fontSize: 28 }, children: 'Virtuoso' }),
      jsx('p', { style: muted, children: connection.data && !connection.error ? `Connected workspace: ${connection.data.workspace_name}. Learning records stay on your Hermes backend.` : 'Use your learning workspace through the current Hermes connection.' }),
      ...panels,
      jsx('p', { style: muted, children: 'Your answers stay out of the agent chat. Closing this page clears unfinished responses. Saved records stay on the backend.' }),
    ] }),
  ] })
}

export default {
  id: 'virtuoso', name: 'Virtuoso', defaultEnabled: false,
  register(ctx) {
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: '/virtuoso' }, render: () => jsx(VirtuosoPane, { ctx }) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: '/virtuoso', label: 'Virtuoso', codicon: 'book' } })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: 'virtuoso.open', label: 'Open Virtuoso', keywords: ['learn', 'practice'], run: () => host.navigate('/virtuoso') } })
  },
}

function check(condition, message = 'The backend returned an unsupported response.') {
  if (!condition) throw new Error(message)
}

function envelope(value, schema) {
  if (value?.schema === 'virtuoso/desktop-error@0.1') {
    const error = new Error(value.error.message)
    error.code = value.error.code
    error.recovery = value.error.recovery
    throw error
  }
  check(value && value.schema === schema)
  return value
}

const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const text = value => typeof value === 'string'

export class PracticeSession {
  constructor(rest, changed = () => {}, clock = {}) {
    this.rest = rest
    this.changed = changed
    this.now = clock.now ?? (() => Date.now())
    this.monotonic = clock.monotonic ?? (() => performance.now())
    this.generation = 0
    this.reset()
  }

  reset() {
    this.generation += 1
    this.pending = null
    this.context = null
    this.state = { phase: 'idle', item: null, reason: '', response: '', retry: null, hintUsed: false, openNotes: false, busy: false, error: null, result: null }
  }

  dispose() { this.reset(); this.changed = () => {} }

  async start(context) {
    if (this.state.busy || this.pending) return false
    this.reset()
    const generation = this.generation
    this.state.busy = true
    this.changed()
    try {
      envelope(context, 'virtuoso/desktop-context@0.1')
      check(hash(context.context_id))
      this.context = context.context_id
      const value = envelope(await this.rest(`/next?context_id=${encodeURIComponent(this.context)}`, { timeoutMs: 35000 }), 'virtuoso/desktop-item@0.1')
      if (generation !== this.generation) return false
      check(value.context_id === this.context)
      check(['learn', 'practice', null].includes(value.action) && text(value.reason))
      this.state.reason = value.reason
      if (value.action === null) {
        check(value.item === null)
        this.state.phase = 'empty'
        return true
      }
      const item = value.item
      check(item && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.item_id) && hash(item.content_hash) && text(item.title) && text(item.focus))
      if (value.action === 'learn') check(hash(item.learning_unit_hash) && text(item.learning_unit))
      else check(text(item.prompt) && text(item.answer) && (item.hint === null || text(item.hint)) && (item.follow_up === null || text(item.follow_up)))
      this.state.item = item
      this.state.phase = value.action === 'learn' ? 'study' : 'prompt'
      this.wallStart = this.now()
      this.monoStart = this.monotonic()
      this.submissionId = crypto.randomUUID().replaceAll('-', '')
      return true
    } catch (error) {
      if (generation === this.generation) this.state.error = error
      return false
    } finally {
      if (generation === this.generation) { this.state.busy = false; this.changed() }
    }
  }

  timestamp() { return new Date(this.wallStart + Math.max(0, this.monotonic() - this.monoStart)).toISOString() }
  active(phase) { return this.state.phase === phase && !this.state.busy && !this.pending && !this.state.error }

  answer(response) {
    if (!this.active('prompt') || !text(response)) return false
    this.state.response = response
    this.answeredAt = this.timestamp()
    this.state.phase = 'support'
    this.changed()
    return true
  }

  reveal() {
    if (!this.active('support')) return false
    this.state.phase = 'grade'
    this.changed()
    return true
  }

  stop() { this.reset(); this.changed() }

  beginRetry() {
    if (!this.active('support') || this.state.hintUsed || this.state.retry) return false
    this.retryStart = this.monotonic()
    this.state.phase = 'retry'
    this.changed()
    return true
  }

  answerRetry(response) {
    if (!this.active('retry') || !text(response)) return false
    this.state.retry = { response, latency_ms: Math.round(Math.max(0, this.monotonic() - this.retryStart)) }
    this.state.phase = 'support'
    this.changed()
    return true
  }

  useHint() {
    if (!this.active('support') || !this.state.item.hint) return false
    this.state.hintUsed = true
    this.changed()
    return true
  }

  setOpenNotes(open) {
    if (!this.state.item || this.state.busy || this.pending || this.state.error || typeof open !== 'boolean') return false
    this.state.openNotes = open
    this.changed()
    return true
  }

  async finishStudy() {
    if (!this.active('study')) return false
    const item = this.state.item
    this.pending = { operation: 'study', request: {
      schema: 'virtuoso/study-completion@0.1', item_id: item.item_id,
      item_content_hash: item.content_hash, learning_unit_hash: item.learning_unit_hash,
      completed: true, surface: 'hermes-desktop',
    } }
    return this.retrySubmit()
  }

  async grade(result, confidence) {
    if (!this.active('grade') || !['demonstrated', 'partial', 'not-demonstrated'].includes(result) || !Number.isInteger(confidence) || confidence < 1 || confidence > 5) return false
    if (result === 'demonstrated' && !(this.state.response.trim() || this.state.retry?.response.trim())) return false
    this.pending = { operation: 'record', request: {
      schema: 'virtuoso/review-attempt@0.1', submission_id: this.submissionId,
      item_id: this.state.item.item_id, item_content_hash: this.state.item.content_hash,
      started_at: new Date(this.wallStart).toISOString(), initial_answered_at: this.answeredAt,
      completed_at: this.timestamp(), initial_response: this.state.response,
      retry: this.state.retry, hint_used: this.state.hintUsed, answer_revealed: true,
      result, confidence, open_notes: this.state.openNotes,
    } }
    return this.retrySubmit()
  }

  async skip() {
    if (!['prompt', 'support', 'retry', 'grade'].some(phase => this.active(phase))) return false
    this.pending = { operation: 'skip', request: {
      schema: 'virtuoso/review-skip@0.1', submission_id: this.submissionId,
      item_id: this.state.item.item_id, item_content_hash: this.state.item.content_hash,
      occurred_at: this.timestamp(), surface: 'hermes-desktop',
    } }
    return this.retrySubmit()
  }

  async retrySubmit() {
    if (!this.pending || this.state.busy) return false
    const generation = this.generation
    const { operation, request } = this.pending
    this.state.busy = true
    this.state.error = null
    this.changed()
    try {
      const schemas = { study: 'virtuoso/study-result@0.1', record: 'virtuoso/review-attempt-result@0.1', skip: 'virtuoso/review-skip-result@0.1' }
      const value = envelope(await this.rest(`/${operation}`, {
        method: 'POST', body: { context_id: this.context, request }, timeoutMs: 35000,
      }), schemas[operation])
      if (generation !== this.generation) return false
      const event = value[{ study: 'study', record: 'attempt', skip: 'skip' }[operation]]
      check(event && event.item_id === request.item_id && event.item_content_hash === request.item_content_hash)
      if (operation === 'study') check(event.learning_unit_hash === request.learning_unit_hash && event.claims_mastery === false)
      if (operation === 'skip') check(event.event_id === `skip-${request.submission_id}` && event.surface === 'hermes-desktop')
      if (operation === 'record') {
        check(event.event_id === `attempt-${request.submission_id}` && event.administered === false && event.result === request.result && event.confidence === request.confidence)
        check(event.initial_latency_ms === Date.parse(request.initial_answered_at) - Date.parse(request.started_at))
        check(value.proposal && text(value.proposal.algorithm) && Number.isFinite(Date.parse(value.proposal.due_at)))
      }
      this.state.result = { kind: operation, ...value }
      this.state.phase = 'complete'
      this.pending = null
      return true
    } catch (error) {
      if (generation !== this.generation) return false
      if (error.code === 'already-recorded') {
        this.state.result = { kind: 'already-recorded' }
        this.state.phase = 'complete'
        this.pending = null
        return true
      }
      this.state.error = error
      return false
    } finally {
      if (generation === this.generation) { this.state.busy = false; this.changed() }
    }
  }

  visible() {
    const item = this.state.item
    if (!item) return {}
    if (this.state.phase === 'study') return { learning_unit: item.learning_unit }
    return {
      prompt: item.prompt,
      ...(this.state.hintUsed ? { hint: item.hint } : {}),
      ...(this.state.phase === 'grade' ? { answer: item.answer, follow_up: item.follow_up } : {}),
    }
  }
}
