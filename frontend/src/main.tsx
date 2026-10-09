import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

// Le immagini con `data-fade` affiorano quando sono pronte (index.css). Gli
// eventi `load`/`error` non risalgono il DOM, ma si catturano: un ascoltatore
// solo, prima che esista qualunque immagine.
const markLoaded = (event: Event) => {
  if (event.target instanceof HTMLImageElement) event.target.setAttribute('data-loaded', '')
}
document.addEventListener('load', markLoaded, true)
document.addEventListener('error', markLoaded, true)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
