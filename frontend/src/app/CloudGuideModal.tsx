import { useState } from 'react'
import { useI18n } from '../i18n'
import { IconCloud, IconCopy } from './icons'

interface CloudGuideModalProps {
  open: boolean
  onClose: () => void
}

type Tab = 'vast_ssh' | 'runpod' | 'vast_direct' | 'serverless' | 'costs'

export function CloudGuideModal({ open, onClose }: CloudGuideModalProps) {
  const { t } = useI18n()
  const [tab, setTab] = useState<Tab>('vast_ssh')
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

  const tabs: { id: Tab; label: string }[] = [
    { id: 'vast_ssh', label: t('cloud.guide.tabVastSsh') },
    { id: 'runpod', label: t('cloud.guide.tabRunpod') },
    { id: 'vast_direct', label: t('cloud.guide.tabVastDirect') },
    { id: 'serverless', label: t('cloud.guide.tabServerless') },
    { id: 'costs', label: t('cloud.guide.tabCosts') },
  ]

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
              {t('cloud.guide.title')}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-sm"
            aria-label={t('cloud.guide.close')}
          >
            {t('cloud.guide.close')}
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex flex-wrap border-b border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-4 pt-2">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
                tab === item.id
                  ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                  : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        {/* Content Body */}
        <div className="overflow-y-auto p-4 text-[13px] leading-relaxed text-[color:var(--color-ink)]">
          {tab === 'vast_ssh' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">{t('cloud.guide.vastSshWhyTitle')}</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastSshWhyBody')}
                </p>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.vastSshStep1Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastSshStep1Body')}
                </p>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.vastSshStep2Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastSshStep2Body')}
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
                    {copied === 'c1' ? t('cloud.guide.copied') : t('cloud.guide.copy')}
                  </button>
                </div>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.vastSshStep3Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastSshStep3Body')}
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
                    {copied === 'c2' ? t('cloud.guide.copied') : t('cloud.guide.copy')}
                  </button>
                </div>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.vastSshStep4Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastSshStep4Body')}
                </p>
              </div>
            </div>
          )}

          {tab === 'runpod' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">{t('cloud.guide.runpodWhyTitle')}</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.runpodWhyBody')}
                </p>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.runpodStep1Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.runpodStep1Body')}
                </p>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.runpodStep2Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.runpodStep2Body')}
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
                    {copied === 'c3' ? t('cloud.guide.copied') : t('cloud.guide.copy')}
                  </button>
                </div>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.runpodStep3Title')}</h4>
                <p className="text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.runpodStep3Body')}
                </p>
              </div>
            </div>
          )}

          {tab === 'vast_direct' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">{t('cloud.guide.vastDirectWhyTitle')}</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastDirectWhyBody')}
                </p>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.vastDirectStep1Title')}</h4>
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
                    {copied === 'c4' ? t('cloud.guide.copied') : t('cloud.guide.copy')}
                  </button>
                </div>
                <p className="mt-2 text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.vastDirectStep1Body')}
                </p>
              </div>
            </div>
          )}

          {tab === 'serverless' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">{t('cloud.guide.serverlessIntroTitle')}</h3>
                <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.serverlessIntroBody')}
                </p>
              </div>

              <div>
                <h4 className="font-semibold">{t('cloud.guide.modalTitle')}</h4>
                <div className="mt-2 space-y-3">
                  <div>
                    <p className="text-[12px] font-medium">{t('cloud.guide.modalStep1Title')}</p>
                    <p className="text-[12px] text-[color:var(--color-ink-2)]">
                      {t('cloud.guide.modalStep1Body')}
                    </p>
                    <div className="mt-1.5 flex items-center gap-2 rounded border border-[color:var(--color-rule-strong)] bg-neutral-900 p-2 text-neutral-100 font-mono text-[11px]">
                      <code className="flex-1 overflow-x-auto">
                        pip install modal && modal setup
                      </code>
                      <button
                        type="button"
                        onClick={() => copy('pip install modal && modal setup', 'c5')}
                        className="btn btn-sm !bg-neutral-800 !text-neutral-200"
                      >
                        <IconCopy size={12} />
                        {copied === 'c5' ? t('cloud.guide.copied') : t('cloud.guide.copy')}
                      </button>
                    </div>
                  </div>

                  <div>
                    <p className="text-[12px] font-medium">{t('cloud.guide.modalStep2Title')}</p>
                    <p className="text-[12px] text-[color:var(--color-ink-2)]">
                      {t('cloud.guide.modalStep2Body')}
                    </p>
                    <div className="mt-1.5 flex items-center gap-2 rounded border border-[color:var(--color-rule-strong)] bg-neutral-900 p-2 text-neutral-100 font-mono text-[11px]">
                      <code className="flex-1 overflow-x-auto">
                        modal deploy scripts/cloud/modal_vllm.py
                      </code>
                      <button
                        type="button"
                        onClick={() => copy('modal deploy scripts/cloud/modal_vllm.py', 'c6')}
                        className="btn btn-sm !bg-neutral-800 !text-neutral-200"
                      >
                        <IconCopy size={12} />
                        {copied === 'c6' ? t('cloud.guide.copied') : t('cloud.guide.copy')}
                      </button>
                    </div>
                    <p className="mt-1.5 text-[12px] text-[color:var(--color-ink-2)]">
                      {t('cloud.guide.modalStep3Body')}
                    </p>
                  </div>
                </div>
              </div>

              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] p-3 text-[12px]">
                <h4 className="font-semibold text-[13px]">{t('cloud.guide.runpodSlsTitle')}</h4>
                <p className="mt-1 text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.runpodSlsBody')}
                </p>
              </div>

              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] p-3 text-[12px]">
                <h4 className="font-semibold text-[13px]">{t('cloud.guide.modalWarmNote')}</h4>
              </div>

              <div className="rounded border border-amber-600/50 bg-amber-950/20 p-3 text-[12px]">
                <h4 className="font-semibold text-[13px] text-amber-600 dark:text-amber-400">
                  {t('cloud.guide.t4Title')}
                </h4>
                <p className="mt-1 text-[color:var(--color-ink-2)]">{t('cloud.guide.t4Body')}</p>
              </div>
            </div>
          )}

          {tab === 'costs' && (
            <div className="space-y-4">
              <p className="text-[11px] text-[color:var(--color-ink-3)]">
                {t('cloud.guide.costsVerified')}
              </p>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                  <span className="lbl !mb-0 text-[11px]">{t('cloud.guide.costs4090')}</span>
                  <p className="text-[16px] font-bold mt-1 text-[color:var(--color-sig)]">
                    {t('cloud.guide.costs4090Price')}
                  </p>
                  <p className="text-[12px] text-[color:var(--color-ink-2)] mt-1">
                    {t('cloud.guide.costs4090Note')}
                  </p>
                </div>
                <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                  <span className="lbl !mb-0 text-[11px]">{t('cloud.guide.costs3090')}</span>
                  <p className="text-[16px] font-bold mt-1 text-[color:var(--color-sig)]">
                    {t('cloud.guide.costs3090Price')}
                  </p>
                  <p className="text-[12px] text-[color:var(--color-ink-2)] mt-1">
                    {t('cloud.guide.costs3090Note')}
                  </p>
                </div>
                <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                  <span className="lbl !mb-0 text-[11px]">{t('cloud.guide.costsL4')}</span>
                  <p className="text-[16px] font-bold mt-1 text-[color:var(--color-sig)]">
                    {t('cloud.guide.costsL4Price')}
                  </p>
                  <p className="text-[12px] text-[color:var(--color-ink-2)] mt-1">
                    {t('cloud.guide.costsL4Note')}
                  </p>
                </div>
              </div>

              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] p-3 text-[12px]">
                <h4 className="font-semibold text-[13px]">{t('cloud.guide.costsTipTitle')}</h4>
                <p className="mt-1 text-[color:var(--color-ink-2)]">
                  {t('cloud.guide.costsTipBody')}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-4 py-2.5">
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
            {t('cloud.guide.footerDoc')}
          </span>
          <button type="button" onClick={onClose} className="btn btn-primary btn-sm">
            {t('cloud.guide.understood')}
          </button>
        </div>
      </div>
    </div>
  )
}
