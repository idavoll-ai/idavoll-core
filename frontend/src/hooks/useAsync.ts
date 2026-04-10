import { useState, useCallback } from 'react'

export function useAsync<T, A extends unknown[]>(fn: (...args: A) => Promise<T>) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async (...args: A): Promise<T | undefined> => {
    setLoading(true)
    setError(null)
    try {
      const result = await fn(...args)
      return result
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return undefined
    } finally {
      setLoading(false)
    }
  }, [fn])

  return { run, loading, error }
}
