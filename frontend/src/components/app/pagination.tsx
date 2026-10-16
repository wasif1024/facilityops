import { useSearchParams } from "react-router"
import { ChevronLeft, ChevronRight } from "lucide-react"

import { Button } from "@/components/ui/button"

export const PAGE_SIZE = 20

/** Page position lives in the URL (?page=2) so views survive reloads and can be shared. */
export function usePage(): { offset: number; limit: number } {
  const [params] = useSearchParams()
  const page = Math.max(1, Number(params.get("page")) || 1)
  return { offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE }
}

export function Pagination({ total }: { total: number }) {
  const [params, setParams] = useSearchParams()
  const page = Math.max(1, Number(params.get("page")) || 1)
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  if (pages <= 1) return null

  const go = (next: number) => {
    const updated = new URLSearchParams(params)
    updated.set("page", String(next))
    setParams(updated)
  }

  return (
    <nav aria-label="Pagination" className="mt-4 flex items-center justify-end gap-2 text-sm">
      <span className="text-muted-foreground">
        Page {page} of {pages} · {total} total
      </span>
      <Button size="icon" variant="outline" disabled={page <= 1} onClick={() => go(page - 1)} aria-label="Previous page">
        <ChevronLeft />
      </Button>
      <Button size="icon" variant="outline" disabled={page >= pages} onClick={() => go(page + 1)} aria-label="Next page">
        <ChevronRight />
      </Button>
    </nav>
  )
}

/** Change a filter in the URL and go back to page 1. */
export function useFilterParam(name: string, fallback = ""): [string, (value: string) => void] {
  const [params, setParams] = useSearchParams()
  const value = params.get(name) ?? fallback
  const set = (next: string) => {
    const updated = new URLSearchParams(params)
    if (next && next !== fallback) updated.set(name, next)
    else updated.delete(name)
    updated.delete("page")
    setParams(updated)
  }
  return [value, set]
}
