// Test host only. The shipped plugin imports Hermes' own SDK.
import { createElement, useSyncExternalStore } from 'react'
export { useQuery } from '@tanstack/react-query'
const atom = value => {
  const listeners = new Set()
  return { get: () => value, subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn) }, set: next => { value = next; for (const fn of listeners) fn() } }
}
export const host = { state: { profile: atom('synthetic'), gateway: atom('open') }, navigate() {} }
export const useValue = value => useSyncExternalStore(value.subscribe, value.get)
export const Button = props => createElement('button', props)
export const Textarea = props => createElement('textarea', props)
export const Input = props => createElement('input', props)
export const PALETTE_AREA = 'palette'
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'nav'
window.fixtureHost = host
