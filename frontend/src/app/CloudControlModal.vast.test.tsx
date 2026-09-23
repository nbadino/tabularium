// @vitest-environment jsdom
/**
 * Percorso guidato Vast.ai contro un backend finto: ciò che si verifica è
 * la promessa fatta all'utente — dopo un refresh la pagina sa ancora che
 * cosa sta succedendo sulla GPU, e non attribuisce a un modello lo stato di
 * un altro.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import { ConfirmProvider } from './confirm'
import { CloudControlModal, readVastJob, writeVastJob } from './CloudControlModal'
import { apiPost } from '../lib/api'

type LogReply = { present: boolean; ready: boolean; failed: boolean; phase: string; error: string; lines: string[]; adapter_id?: string }
const fake = {
  log: { present: false, ready: false, failed: false, phase: 'absent', error: '', lines: [] } as LogReply,
  tunnel: { running: false } as Record<string, unknown>,
}

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path.startsWith('/system/secrets/')) return { configured: true }
    if (path === '/system/cloud/vast/ssh-key') return { exists: true, fingerprint: 'SHA256:abcdefghijklmnopqrstuvwxyz' }
    if (path === '/system/cloud/vast/monkeyocr-ref') return { ref: 'abc12345' }
    if (path === '/system/cloud/vast/models') {
      return { items: [{ adapter_id: 'monkeyocrv2-parsing', hf_repo: 'zenosai/M', served_model_name: 'MonkeyOCRv2', runtime: 'vllm', supported: true, needs_own_image: false, docker_image: '', min_free_disk_gb: 22, min_free_vram_gb: 10 }] }
    }
    if (path === '/system/cloud/tunnel') return fake.tunnel
    if (path.startsWith('/system/cloud/modal')) return { cli: true, token: false, templates: [], template: 'monkeyocrv2', endpoint: null, task: null }
    return {}
  }),
  apiPost: vi.fn(async (path: string, body: Record<string, unknown>) => {
    if (path === '/system/cloud/vast/account') return { id: 1, email: 'a@b.c', balance: 5, balance_ok: true }
    if (path === '/system/cloud/vast/instances') {
      return {
        items: [{ id: 7, status: 'running', is_running: true, label: '1x RTX 4090', gpu_name: 'RTX 4090', num_gpus: 1, dph_total: 0.3, ssh_host: 'h.example', ssh_port: 2222, ssh_via: 'direct' }],
      }
    }
    if (path === '/system/cloud/vast/hostkey') return { host: body.host, port: body.port, key_types: ['ed25519'] }
    if (path === '/system/cloud/vast/provision/log') return fake.log
    if (path === '/system/cloud/tunnel/start') {
      fake.tunnel = { running: true, host: 'h.example', port: 2222, local_port: 9999 }
      return fake.tunnel
    }
    if (path === '/system/inference/test') return { ok: false }
    return {}
  }),
  apiPut: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
  ApiError: class ApiError extends Error {},
}))

const renderVast = () =>
  render(
    <MemoryRouter>
      <ConfirmProvider>
        <CloudControlModal
          open
          inline
          onClose={() => {}}
          onlyProvider="vast"
          focusProvider="vast"
          focusAdapterId="monkeyocrv2-parsing"
          focusModelLabel="MonkeyOCRv2-Parsing"
        />
      </ConfirmProvider>
    </MemoryRouter>,
  )

const logCalls = () => vi.mocked(apiPost).mock.calls.filter(([path]) => path === '/system/cloud/vast/provision/log')

describe('Vast.ai guidato', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(apiPost).mockClear()
    fake.log = { present: false, ready: false, failed: false, phase: 'absent', error: '', lines: [] }
    fake.tunnel = { running: false }
  })
  afterEach(cleanup)

  it('su una GPU mai toccata la tappa server dice «da preparare»', async () => {
    renderVast()
    expect(await screen.findByText('Da preparare')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prepara il server' })).toBeEnabled()
  })

  it('dopo un refresh riprende a seguire la preparazione in corso', async () => {
    writeVastJob({ instanceId: '7', host: 'h.example', port: 2222, servedName: 'MonkeyOCRv2', adapterId: 'monkeyocrv2-parsing' })
    fake.log = { present: true, ready: false, failed: false, phase: 'weights', error: '', lines: ['>> Download pesi'], adapter_id: 'monkeyocrv2-parsing' }
    renderVast()
    expect(await screen.findByText(/Ripresa la preparazione dell.istanza 7/)).toBeInTheDocument()
    await waitFor(() => expect(logCalls().length).toBeGreaterThan(0))
    // Nessun pulsante «Prepara»: sta già preparando, e lo dice.
    expect(await screen.findByText(/Continua anche se chiudi o ricarichi/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Prepara il server' })).not.toBeInTheDocument()
  })

  it('senza promemoria, la sonda del log scopre una preparazione in corso e la segue', async () => {
    fake.log = { present: true, ready: false, failed: false, phase: 'python', error: '', lines: ['>> pip'], adapter_id: 'monkeyocrv2-parsing' }
    renderVast()
    expect(await screen.findByText(/Continua anche se chiudi o ricarichi/)).toBeInTheDocument()
  })

  it('a preparazione finita apre il tunnel da solo', async () => {
    writeVastJob({ instanceId: '7', host: 'h.example', port: 2222, servedName: 'MonkeyOCRv2', adapterId: 'monkeyocrv2-parsing' })
    fake.log = { present: true, ready: true, failed: false, phase: 'ready', error: '', lines: [], adapter_id: 'monkeyocrv2-parsing' }
    renderVast()
    await waitFor(() =>
      expect(vi.mocked(apiPost).mock.calls.some(([path]) => path === '/system/cloud/tunnel/start')).toBe(true),
    )
  })

  it('il fallimento di un altro modello non viene attribuito a quello scelto', async () => {
    fake.log = { present: true, ready: false, failed: true, phase: 'failed', error: 'Engine core initialization failed', lines: ['boom'], adapter_id: 'qwen3-vl-8b' }
    renderVast()
    expect(await screen.findByText(/l.ultima preparazione era di qwen3-vl-8b ed è fallita/)).toBeInTheDocument()
    expect(screen.getByText('Da preparare')).toBeInTheDocument()
    expect(screen.queryByText(/Setup interrotto/)).not.toBeInTheDocument()
  })

  it('un promemoria di un altro modello non viene ripreso', async () => {
    writeVastJob({ instanceId: '7', host: 'h.example', port: 2222, servedName: 'Qwen', adapterId: 'qwen3-vl-8b' })
    renderVast()
    expect(await screen.findByText('Da preparare')).toBeInTheDocument()
    expect(screen.queryByText(/Ripresa la preparazione/)).not.toBeInTheDocument()
    // Il promemoria resta: appartiene all'altro percorso.
    expect(readVastJob()?.adapterId).toBe('qwen3-vl-8b')
  })
})
