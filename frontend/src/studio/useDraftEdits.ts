/** Le bozze non passano dal canvas: navigazione e consegna devono attendere anche le loro scritture. */
import { useEffect, useRef, useState } from 'react'
export function useDraftEdits(write: (id: number, content: string) => Promise<unknown>, onError: (error: unknown) => void) {
  const pending = useRef(new Map<number, string>())
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const queue = useRef<Promise<void>>(Promise.resolve())
  const latest = useRef(write); latest.current = write
  const fail = useRef(onError); fail.current = onError
  const [dirty, setDirty] = useState(false)
  const flush = () => {
    clearTimeout(timer.current)
    const operation = queue.current.catch(() => {}).then(async () => {
      for (const [id, content] of pending.current) {
        await latest.current(id, content)
        if (pending.current.get(id) === content) pending.current.delete(id)
      }
      setDirty(pending.current.size > 0)
    })
    queue.current = operation
    return operation
  }
  const flushRef = useRef(flush); flushRef.current = flush
  useEffect(() => () => { clearTimeout(timer.current); void flushRef.current().catch((e) => fail.current(e)) }, [])
  return {
    dirty,
    flush,
    async discard() {
      clearTimeout(timer.current)
      await queue.current.catch(() => {})
      pending.current.clear(); setDirty(false)
    },
    schedule(id: number, content: string) {
      pending.current.set(id, content); setDirty(true); clearTimeout(timer.current)
      timer.current = setTimeout(() => void flushRef.current().catch((e) => fail.current(e)), 800)
    },
  }
}
