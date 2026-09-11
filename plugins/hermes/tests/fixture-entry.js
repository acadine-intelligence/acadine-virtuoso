import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import plugin from '../desktop/plugin.js'
const contributions = []
const ctx = {
  register: value => contributions.push(value),
  rest: async (path, opts = {}) => {
    const response = await fetch('/api/plugins/virtuoso' + path, {
      method: opts.method ?? 'GET', headers: { 'Content-Type': 'application/json' },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    })
    if (!response.ok) throw new Error(`Backend unavailable (${response.status})`)
    return response.json()
  },
}
plugin.register(ctx)
const route = contributions.find(row => row.area === 'routes')
createRoot(document.getElementById('root')).render(React.createElement(QueryClientProvider, { client: new QueryClient() }, route.render()))
