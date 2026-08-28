import { useState } from 'react'
import { IconCloud, IconCopy } from './icons'

interface CloudGuideModalProps {
  open: boolean
  onClose: () => void
}

export function CloudGuideModal({ open, onClose }: CloudGuideModalProps) {
  const [tab, setTab] = useState<'vast_ssh' | 'runpod' | 'vast_direct' | 'costs'>('vast_ssh')
  const [copied, setCopied] = useState<string | null>(null)

  if (!open) return null

  const copy = async (text: string, id: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(id)
      setTimeout(() => setCopied(null), 2500)
    } catch {
      // clipboard fallback
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cloud-guide-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-panel)] px-4 py-3">
          <div className="flex items-center gap-2">
            <IconCloud size={16} />
            <h2 id="cloud-guide-title" className="text-[15px] font-bold">
              Guida Rapida: Offloading Inferenza su Cloud GPU
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-sm"
            aria-label="Chiudi guida"
          >
            ✕ Chiudi
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex border-b border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-4 pt-2">
          <button
            type="button"
            onClick={() => setTab('vast_ssh')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'vast_ssh'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            1. Vast.ai + Tunnel SSH (Consigliato)
          </button>
          <button
            type="button"
            onClick={() => setTab('runpod')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'runpod'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            2. RunPod (Proxy HTTPS)
          </button>
          <button
            type="button"
            onClick={() => setTab('vast_direct')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'vast_direct'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            3. IP Diretto + API Key
          </button>
          <button
            type="button"
            onClick={() => setTab('costs')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'costs'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            4. Costi & GPU
          </button>
        </div>

        {/* Content Body */}
        <div className="overflow-y-auto p-4 text-[13px] leading-relaxed text-[color:var(--color-ink)]">
          {tab === 'vast_ssh' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">Perché è il metodo migliore?</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  Crea un tunnel cifrato punto-punto. Nessuna porta aperta su internet, massima sicurezza e costi minimi ($0.25/h per RTX 4090).
                </p>
              </div>

              <div>
                <h4 className="font-semibold">Passo 1: Noleggia una GPU su Vast.ai</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Vai su <a href="https://vast.ai" target="_blank" rel="noreferrer" className="underline font-mono">vast.ai</a> e seleziona <strong>1x RTX 4090 o RTX 3090</strong> (template PyTorch / CUDA, 40GB disco).
                </p>
              </div>

              <div>
                <h4 className="font-semibold">Passo 2: Avvia vLLM sull'istanza Vast.ai</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Connettiti via SSH all'istanza ed esegui questo comando di setup automatico:
                </p>
                <div className="mt-1.5 flex items-center gap-2 rounded border border-[color:var(--color-rule-strong)] bg-neutral-900 p-2 text-neutral-100 font-mono text-[11px]">
                  <code className="flex-1 overflow-x-auto">
                    curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/scripts/cloud/setup_cloud_vllm.sh | bash
                  </code>
                  <button
                    type="button"
                    onClick={() =>
                      copy(
                        'curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/scripts/cloud/setup_cloud_vllm.sh | bash',
                        'c1',
                      )
                    }
                    className="btn btn-sm !bg-neutral-800 !text-neutral-200"
                  >
                    <IconCopy size={12} />
                    {copied === 'c1' ? 'Copiato' : 'Copia'}
                  </button>
                </div>
              </div>

              <div>
                <h4 className="font-semibold">Passo 3: Apri il Tunnel SSH sul tuo PC locale</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Sul tuo PC (nel terminale locale), lancia l'helper di tunneling indicando host e porta forniti da Vast.ai:
                </p>
                <div className="mt-1.5 flex items-center gap-2 rounded border border-[color:var(--color-rule-strong)] bg-neutral-900 p-2 text-neutral-100 font-mono text-[11px]">
                  <code className="flex-1 overflow-x-auto">
                    ./scripts/cloud/ssh_tunnel.sh -p &lt;PORTA&gt; root@&lt;HOST_VAST&gt;
                  </code>
                  <button
                    type="button"
                    onClick={() =>
                      copy('./scripts/cloud/ssh_tunnel.sh -p <PORTA> root@<HOST_VAST>', 'c2')
                    }
                    className="btn btn-sm !bg-neutral-800 !text-neutral-200"
                  >
                    <IconCopy size={12} />
                    {copied === 'c2' ? 'Copiato' : 'Copia'}
                  </button>
                </div>
              </div>

              <div>
                <h4 className="font-semibold">Passo 4: Salva in Tabularium</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Lascia l'URL impostato su <code className="font-mono bg-neutral-100 dark:bg-neutral-800 px-1 py-0.5 rounded">http://127.0.0.1:8888/v1</code> e clicca <strong>Test Connessione</strong>.
                </p>
              </div>
            </div>
          )}

          {tab === 'runpod' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">Proxy HTTPS Senza Tunnel Locale</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  RunPod fornisce un indirizzo HTTPS pubblico protetto da certificato SSL. Non serve tenere aperto un terminale per il tunnel SSH sul proprio PC.
                </p>
              </div>

              <div>
                <h4 className="font-semibold">Passo 1: Crea un Pod su RunPod</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Scegli 1x RTX 4090 o A5000 con template PyTorch. Nella configurazione delle porte esponi la porta HTTP <strong>8888</strong>.
                </p>
              </div>

              <div>
                <h4 className="font-semibold">Passo 2: Avvia vLLM con API Key</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Nel Web Terminal di RunPod esegui:
                </p>
                <div className="mt-1.5 flex items-center gap-2 rounded border border-[color:var(--color-rule-strong)] bg-neutral-900 p-2 text-neutral-100 font-mono text-[11px]">
                  <code className="flex-1 overflow-x-auto">
                    curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/scripts/cloud/setup_cloud_vllm.sh | bash -s -- --port 8888 --api-key "CHIAVE_SEGRETA"
                  </code>
                  <button
                    type="button"
                    onClick={() =>
                      copy(
                        'curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/scripts/cloud/setup_cloud_vllm.sh | bash -s -- --port 8888 --api-key "CHIAVE_SEGRETA"',
                        'c3',
                      )
                    }
                    className="btn btn-sm !bg-neutral-800 !text-neutral-200"
                  >
                    <IconCopy size={12} />
                    {copied === 'c3' ? 'Copiato' : 'Copia'}
                  </button>
                </div>
              </div>

              <div>
                <h4 className="font-semibold">Passo 3: Inserisci l'URL Proxy in Tabularium</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  Dalla dashboard RunPod copia l'URL proxy (formato: <code className="font-mono text-[11px]">https://&lt;POD_ID&gt;-8888.proxy.runpod.net/v1</code>) e incollalo nel campo Server vLLM insieme alla tua chiave API.
                </p>
              </div>
            </div>
          )}

          {tab === 'vast_direct' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">Connessione Diretta IP:Porta</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  Utile per VPS GPU dedicate o istanze Vast.ai con porte pubbliche mappate direttamente.
                </p>
              </div>

              <div>
                <h4 className="font-semibold">Avvio con porta pubblica e API Key</h4>
                <div className="mt-1.5 flex items-center gap-2 rounded border border-[color:var(--color-rule-strong)] bg-neutral-900 p-2 text-neutral-100 font-mono text-[11px]">
                  <code className="flex-1 overflow-x-auto">
                    bash setup_cloud_vllm.sh --port 8888 --api-key "tua_chiave_segreta"
                  </code>
                  <button
                    type="button"
                    onClick={() =>
                      copy('bash setup_cloud_vllm.sh --port 8888 --api-key "tua_chiave_segreta"', 'c4')
                    }
                    className="btn btn-sm !bg-neutral-800 !text-neutral-200"
                  >
                    <IconCopy size={12} />
                    {copied === 'c4' ? 'Copiato' : 'Copia'}
                  </button>
                </div>
                <p className="mt-2 text-[12px] text-[color:var(--color-ink-2)]">
                  In Tabularium inserisci <code className="font-mono">http://&lt;IP_PUBBLICO&gt;:&lt;PORTA_MAPPATA&gt;/v1</code> e la relativa API Key.
                </p>
              </div>
            </div>
          )}

          {tab === 'costs' && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                  <span className="lbl !mb-0 text-[11px]">NVIDIA RTX 4090 (24 GB)</span>
                  <p className="text-[18px] font-bold mt-1 text-[color:var(--color-sig)]">
                    ~$0.28 / ora
                  </p>
                  <p className="text-[12px] text-[color:var(--color-ink-2)] mt-1">
                    Scelta raccomandata. Inferenza &lt; 400ms a ritaglio, ~100 token/s.
                  </p>
                </div>
                <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                  <span className="lbl !mb-0 text-[11px]">NVIDIA RTX 3090 (24 GB)</span>
                  <p className="text-[18px] font-bold mt-1 text-[color:var(--color-sig)]">
                    ~$0.20 / ora
                  </p>
                  <p className="text-[12px] text-[color:var(--color-ink-2)] mt-1">
                    Massimo risparmio. Inferenza ~600ms a ritaglio, ~70 token/s.
                  </p>
                </div>
              </div>

              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] p-3 text-[12px]">
                <h4 className="font-semibold text-[13px]">💡 Suggerimento Risparmio</h4>
                <p className="mt-1 text-[color:var(--color-ink-2)]">
                  Puoi preparare le scansioni offline in locale. Quando vuoi annotare o lanciare prefill e valutazioni, avvii la GPU cloud per 1 o 2 ore (costo: ~0.50$), fai il lavoro e poi metti l'istanza in <strong>Stop/Pause</strong>.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-4 py-2.5">
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
            Guida completa: docs/CLOUD_INFERENCE_GUIDE.md
          </span>
          <button type="button" onClick={onClose} className="btn btn-primary btn-sm">
            Ho capito
          </button>
        </div>
      </div>
    </div>
  )
}
